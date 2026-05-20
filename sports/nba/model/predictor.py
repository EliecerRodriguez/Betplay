"""
Fase 4: Modelo predictivo de resultados NBA.

Implementa un pipeline de scikit-learn con:
  - Imputación de medianas para NaN residuales
  - Escalado estándar (StandardScaler)
  - Clasificador: XGBoost (por defecto) | RandomForest | Logistic | Ensemble
  - Calibración isotónica de probabilidades (CalibratedClassifierCV)

Funciones:
  - train(X, y)                → guarda modelo en disco, devuelve métricas
  - predict(feature_df)        → DataFrame con probabilidades de victoria
  - load_model()               → carga modelo desde disco
  - evaluate(X, y, model)      → métricas de evaluación

El modelo se guarda en: models/nba_model_<version>.joblib
"""
from __future__ import annotations

import os
from datetime import date
from typing import Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier, StackingClassifier, VotingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.inspection import permutation_importance
from sklearn.metrics import accuracy_score, brier_score_loss, classification_report, log_loss, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    _OPTUNA_AVAILABLE = True
except ImportError:
    _OPTUNA_AVAILABLE = False

try:
    from xgboost import XGBClassifier
    _XGB_AVAILABLE = True
except ImportError:
    _XGB_AVAILABLE = False

from sports.nba.processing.features import get_feature_columns
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Configuración ─────────────────────────────────────────────────────────────

MODEL_DIR     = os.getenv("MODEL_DIR", "models")
MODEL_TYPE    = os.getenv("MODEL_TYPE", "xgboost")   # 'xgboost' | 'random_forest' | 'logistic' | 'ensemble'


def _latest_model_version() -> str:
    """Auto-detecta la versión más reciente disponible en MODEL_DIR."""
    env_version = os.getenv("MODEL_VERSION", "")
    if env_version:
        return env_version
    try:
        import glob
        pattern = os.path.join(MODEL_DIR, "nba_model_v*.joblib")
        files = glob.glob(pattern)
        if files:
            # Extraer número de versión y devolver el mayor
            versions = []
            for f in files:
                name = os.path.basename(f)
                num = name.replace("nba_model_v", "").replace(".joblib", "")
                if num.isdigit():
                    versions.append(int(num))
            if versions:
                return f"v{max(versions)}"
    except Exception:
        pass
    return "v5"   # fallback explícito al último modelo conocido


MODEL_VERSION = _latest_model_version()

_MODEL_PATH_TEMPLATE = os.path.join(MODEL_DIR, "nba_model_{version}.joblib")


def _model_path(version: str = MODEL_VERSION) -> str:
    return _MODEL_PATH_TEMPLATE.format(version=version)


# ── Construcción del pipeline ─────────────────────────────────────────────────

def _build_pipeline(model_type: str = MODEL_TYPE, xgb_params: dict | None = None) -> Pipeline:
    """
    Construye el pipeline de preprocesamiento + clasificador calibrado.

    Tipos disponibles:
      xgboost       — XGBoostClassifier + calibración isotónica (recomendado)
      random_forest — RandomForestClassifier + calibración
      logistic      — LogisticRegression (baseline interpretable)
      ensemble      — Voting entre XGBoost + RF + LR calibrados
      stacking      — Meta-modelo: XGBoost + RF + LR → meta LogisticRegression
                      (usa TimeSeriesSplit para evitar data leakage temporal)

    Args:
        xgb_params: Hiperparámetros XGBoost a sobreescribir (de Optuna).
    """
    xgb_params = xgb_params or {}
    if model_type == "logistic":
        classifier = LogisticRegression(max_iter=1000, random_state=42, solver="lbfgs")

    elif model_type == "random_forest":
        base = RandomForestClassifier(
            n_estimators=300, max_depth=7, min_samples_leaf=4,
            random_state=42, n_jobs=-1,
        )
        classifier = CalibratedClassifierCV(base, method="isotonic", cv=5)

    elif model_type == "stacking" and _XGB_AVAILABLE:
        # ── Meta-modelo de stacking ───────────────────────────────────────────
        # Los modelos base generan predicciones out-of-fold (cv=5 estratificado);
        # el meta-modelo LogisticRegression aprende cuándo confiar en cada uno.
        # No se usa TimeSeriesSplit aquí porque cross_val_predict requiere
        # que TODOS los samples aparezcan en algún fold de test (particiones),
        # y TimeSeriesSplit excluye los primeros samples de cualquier test fold.
        # LogisticRegression como meta ya produce probabilidades bien calibradas.
        xgb_s = XGBClassifier(
            n_estimators=300, max_depth=5, learning_rate=0.04,
            subsample=0.8, colsample_bytree=0.8, min_child_weight=3,
            eval_metric="logloss", random_state=42, n_jobs=-1,
        )
        rf_s  = RandomForestClassifier(
            n_estimators=200, max_depth=6, min_samples_leaf=5,
            random_state=42, n_jobs=-1,
        )
        lr_s  = LogisticRegression(max_iter=1000, random_state=42)
        meta  = LogisticRegression(max_iter=1000, C=0.5, random_state=42)
        classifier = StackingClassifier(
            estimators=[("xgb", xgb_s), ("rf", rf_s), ("lr", lr_s)],
            final_estimator=meta,
            cv=5,                   # StratifiedKFold(5) — crea particiones completas
            stack_method="predict_proba",
            passthrough=False,
            n_jobs=-1,
        )

    elif model_type == "ensemble" and _XGB_AVAILABLE:
        xgb = XGBClassifier(
            n_estimators=300, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            eval_metric="logloss",
            random_state=42, n_jobs=-1,
        )
        rf  = RandomForestClassifier(
            n_estimators=200, max_depth=6, min_samples_leaf=5,
            random_state=42, n_jobs=-1,
        )
        lr  = LogisticRegression(max_iter=1000, random_state=42)
        base = VotingClassifier(
            estimators=[("xgb", xgb), ("rf", rf), ("lr", lr)],
            voting="soft",
        )
        classifier = CalibratedClassifierCV(base, method="isotonic", cv=5)

    else:  # xgboost (default)
        if not _XGB_AVAILABLE:
            logger.warning("XGBoost no disponible, usando RandomForest")
            base = RandomForestClassifier(
                n_estimators=300, max_depth=7, min_samples_leaf=4,
                random_state=42, n_jobs=-1,
            )
        else:
            # Parámetros base — se sobreescriben si Optuna encontró mejores
            default_params = dict(
                n_estimators=400, max_depth=5, learning_rate=0.04,
                subsample=0.8, colsample_bytree=0.8, min_child_weight=3,
                eval_metric="logloss", random_state=42, n_jobs=-1,
            )
            default_params.update(xgb_params)  # Optuna override
            base = XGBClassifier(**default_params)
        classifier = CalibratedClassifierCV(base, method="isotonic", cv=5)

    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler",  StandardScaler()),
        ("clf",     classifier),
    ])


# ── Optimización Bayesiana (Optuna) ───────────────────────────────────────────

def tune_xgboost(X: pd.DataFrame, y: pd.Series, n_trials: int = 50) -> dict:
    """
    Optimización bayesiana de hiperparámetros XGBoost con Optuna.

    Usa TimeSeriesSplit(3) para la evaluación interna → no hay data leakage.
    El espacio de búsqueda cubre los parámetros más influyentes de XGBoost.

    Args:
        X:        Features de entrenamiento (ya ordenadas temporalmente).
        y:        Target binario.
        n_trials: Número de configuraciones a probar (default 50, óptimo 100+).

    Returns:
        Dict con los mejores hiperparámetros encontrados.
    """
    import optuna

    def objective(trial: "optuna.Trial") -> float:
        params = {
            "n_estimators":     trial.suggest_int("n_estimators",     100, 1000),
            "max_depth":        trial.suggest_int("max_depth",         3,   8),
            "learning_rate":    trial.suggest_float("learning_rate",  0.005, 0.15, log=True),
            "subsample":        trial.suggest_float("subsample",      0.5,  1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
            "min_child_weight": trial.suggest_int("min_child_weight",  1,  15),
            "reg_alpha":        trial.suggest_float("reg_alpha",      1e-8, 10.0, log=True),
            "reg_lambda":       trial.suggest_float("reg_lambda",     1e-8, 10.0, log=True),
            "gamma":            trial.suggest_float("gamma",          0.0,   5.0),
        }
        clf  = XGBClassifier(**params, eval_metric="logloss", random_state=42, n_jobs=-1)
        cal  = CalibratedClassifierCV(clf, method="isotonic", cv=3)
        pipe = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler",  StandardScaler()),
            ("clf",     cal),
        ])
        tscv   = TimeSeriesSplit(n_splits=3)
        scores = cross_val_score(pipe, X, y, cv=tscv, scoring="roc_auc", n_jobs=-1)
        return float(scores.mean())

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=10),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=10, n_warmup_steps=3),
    )
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    best = study.best_params
    logger.info(
        "Optuna finalizado: mejor AUC=%.4f en %d trials | params=%s",
        study.best_value, n_trials, best,
    )
    return best


# ── Entrenamiento ─────────────────────────────────────────────────────────────

def train(
    X: pd.DataFrame,
    y: pd.Series,
    model_type: str = MODEL_TYPE,
    version: str = MODEL_VERSION,
    test_size: float = 0.2,
    optimize_trials: int = 0,
) -> dict:
    """
    Entrena el modelo y lo guarda en disco.

    Args:
        X:          DataFrame de features (resultado de prepare_training_dataset).
        y:          Serie binaria de resultados (1=victoria local, 0=derrota).
        model_type: Tipo de clasificador.
        version:    Versión del modelo (tag para el archivo).
        test_size:  Fracción del conjunto de prueba.

    Returns:
        Diccionario con métricas: accuracy, roc_auc, cv_mean, cv_std.
    """
    if len(X) < 20:
        logger.warning(
            "train: solo %d muestras disponibles. "
            "Se necesitan más partidos históricos para un modelo robusto. "
            "Se guarda el modelo de todas formas.",
            len(X),
        )

    logger.info("Entrenando modelo '%s' con %d muestras y %d features",
                model_type, len(X), X.shape[1])

    # Optimización bayesiana de hiperparámetros (opcional)
    xgb_override_params = {}
    if optimize_trials > 0 and model_type == "xgboost" and _XGB_AVAILABLE:
        if _OPTUNA_AVAILABLE:
            logger.info("Iniciando optimización Optuna con %d trials …", optimize_trials)
            xgb_override_params = tune_xgboost(X, y, n_trials=optimize_trials)
        else:
            logger.warning("Optuna no disponible — instala: pip install optuna")

    pipeline = _build_pipeline(model_type, xgb_params=xgb_override_params)

    # Split temporal (los últimos test_size% de partidos → validación)
    # NO se usa train_test_split aleatorio — eso crea data leakage temporal
    if len(X) >= 40:
        split_idx = int(len(X) * (1 - test_size))
        X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
        y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
    else:
        X_train, X_test, y_train, y_test = X, X, y, y
        logger.warning("train: datos insuficientes para split — evaluando en training set")

    pipeline.fit(X_train, y_train)

    # ── Métricas ──────────────────────────────────────────────────────────────
    y_pred  = pipeline.predict(X_test)
    y_proba = pipeline.predict_proba(X_test)[:, 1]

    accuracy  = accuracy_score(y_test, y_pred)
    roc_auc   = roc_auc_score(y_test, y_proba) if len(np.unique(y_test)) > 1 else float("nan")
    logloss   = log_loss(y_test, y_proba)
    brier     = brier_score_loss(y_test, y_proba)

    # Validación cruzada TEMPORAL (TimeSeriesSplit) — sin data leakage
    n_splits  = min(5, max(2, len(X) // 100))
    tscv      = TimeSeriesSplit(n_splits=n_splits)
    cv_scores = cross_val_score(pipeline, X, y, cv=tscv, scoring="roc_auc")

    metrics = {
        "accuracy":   round(accuracy, 4),
        "roc_auc":    round(roc_auc, 4) if not np.isnan(roc_auc) else None,
        "log_loss":   round(logloss, 4),
        "brier":      round(brier, 4),
        "cv_mean":    round(cv_scores.mean(), 4),
        "cv_std":     round(cv_scores.std(), 4),
        "cv_splits":  n_splits,
        "n_samples":  len(X),
        "n_features": X.shape[1],
        "model_type": model_type,
        "version":    version,
    }

    logger.info(
        "Métricas — Accuracy: %.4f | ROC-AUC: %.4f | LogLoss: %.4f | Brier: %.4f | CV(TSS-%d): %.4f±%.4f",
        metrics["accuracy"],
        metrics["roc_auc"] or 0,
        metrics["log_loss"],
        metrics["brier"],
        n_splits,
        metrics["cv_mean"],
        metrics["cv_std"],
    )
    logger.info("\n%s", classification_report(y_test, y_pred, target_names=["Away wins", "Home wins"]))

    # ── Feature importance (permutation) ──────────────────────────────────────
    try:
        perm = permutation_importance(
            pipeline, X_test, y_test,
            n_repeats=10, random_state=42, scoring="roc_auc", n_jobs=-1,
        )
        importance_df = (
            pd.DataFrame({
                "feature":  X.columns,
                "importance": perm.importances_mean,
                "std":        perm.importances_std,
            })
            .sort_values("importance", ascending=False)
            .reset_index(drop=True)
        )
        top10 = importance_df.head(10)
        lines = ["\n── Top 10 Features (Permutation Importance) ──"]
        for _, row in top10.iterrows():
            bar = "█" * max(1, int(row["importance"] * 200))
            lines.append(f"  {row['feature']:<30} {row['importance']:+.4f} ±{row['std']:.4f}  {bar}")
        logger.info("\n".join(lines))
        metrics["feature_importance"] = importance_df.to_dict(orient="records")
    except Exception as exc:
        logger.debug("feature importance falló: %s", exc)

    # ── SHAP values (TreeExplainer para XGBoost) ──────────────────────────────
    try:
        import shap  # type: ignore
        # Extraer el clasificador base del pipeline (paso final)
        clf = pipeline.named_steps.get("clf") or pipeline.steps[-1][1]
        # Si es CalibratedClassifierCV, acceder al estimador base
        if hasattr(clf, "estimator"):
            base_clf = clf.estimator
        elif hasattr(clf, "calibrated_classifiers_"):
            # Usar primer calibrador
            base_clf = clf.calibrated_classifiers_[0].estimator
        else:
            base_clf = clf

        # Transformar X_test a través de los pasos previos al clf
        steps_before_clf = pipeline.steps[:-1]
        X_test_transformed = X_test.copy()
        for _, step in steps_before_clf:
            if hasattr(step, "transform"):
                X_test_transformed = step.transform(X_test_transformed)

        explainer = shap.TreeExplainer(base_clf)
        shap_values = explainer.shap_values(X_test_transformed)

        # Para clasificación binaria, shap_values puede ser lista [neg, pos]
        if isinstance(shap_values, list):
            sv = shap_values[1]
        else:
            sv = shap_values

        shap_mean = np.abs(sv).mean(axis=0)
        shap_df = (
            pd.DataFrame({"feature": list(X.columns), "shap_importance": shap_mean})
            .sort_values("shap_importance", ascending=False)
            .reset_index(drop=True)
        )
        top10_shap = shap_df.head(10)
        lines_shap = ["\n── Top 10 Features (SHAP Mean |value|) ──"]
        for _, row in top10_shap.iterrows():
            bar = "█" * max(1, int(row["shap_importance"] * 400))
            lines_shap.append(f"  {row['feature']:<30} {row['shap_importance']:.4f}  {bar}")
        logger.info("\n".join(lines_shap))
        metrics["shap_importance"] = shap_df.to_dict(orient="records")
    except Exception as exc:
        logger.debug("SHAP falló: %s", exc)

    # ── Guardar modelo ────────────────────────────────────────────────────────
    os.makedirs(MODEL_DIR, exist_ok=True)
    path = _model_path(version)
    joblib.dump({"pipeline": pipeline, "feature_cols": list(X.columns), "metrics": metrics}, path)
    logger.info("Modelo guardado en: %s", path)

    return metrics


# ── Carga ─────────────────────────────────────────────────────────────────────

def load_model(version: str = MODEL_VERSION) -> dict:
    """
    Carga el modelo guardado desde disco.

    Args:
        version: Versión del modelo a cargar.

    Returns:
        Diccionario con 'pipeline', 'feature_cols', 'metrics'.

    Raises:
        FileNotFoundError si el modelo no existe.
    """
    path = _model_path(version)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Modelo no encontrado: {path}. "
            "Ejecuta primero el entrenamiento con datos históricos."
        )
    bundle = joblib.load(path)
    logger.info("Modelo cargado desde: %s (entrenado con %d muestras)",
                path, bundle["metrics"].get("n_samples", "?"))
    return bundle


# ── Predicción ────────────────────────────────────────────────────────────────

def predict(
    feature_df: pd.DataFrame,
    version: str = MODEL_VERSION,
) -> pd.DataFrame:
    """
    Genera predicciones para los partidos en feature_df.

    Si el modelo no existe, devuelve probabilidades basadas en win_pct
    de los equipos (heurística de fallback).

    Args:
        feature_df: Resultado de processing.features.build_features()
        version:    Versión del modelo a usar.

    Returns:
        DataFrame con columnas:
          game_id, game_date, home_team_id, visitor_team_id,
          home_win_prob, away_win_prob, predicted_winner,
          model_version, fetch_date
    """
    if feature_df.empty:
        logger.warning("predict: feature_df vacío — sin predicciones")
        return pd.DataFrame()

    today = date.today().isoformat()

    # ── Intentar usar modelo entrenado ────────────────────────────────────────
    try:
        bundle       = load_model(version)
        pipeline     = bundle["pipeline"]
        feature_cols = bundle["feature_cols"]

        available = [c for c in feature_cols if c in feature_df.columns]
        missing   = [c for c in feature_cols if c not in feature_df.columns]
        if missing:
            logger.warning("predict: columnas faltantes en feature_df: %s — se imputan con 0", missing)
            for col in missing:
                feature_df[col] = 0.0

        X = feature_df[feature_cols].astype(float)
        proba = pipeline.predict_proba(X)

        home_probs = proba[:, 1]
        away_probs = proba[:, 0]
        model_used = version

    except FileNotFoundError:
        logger.warning(
            "predict: modelo no encontrado — usando heurística basada en win_pct. "
            "Entrena el modelo con datos históricos para mejores predicciones."
        )
        home_probs, away_probs = _heuristic_probs(feature_df)
        model_used = "heuristic_wpct"

    # ── Construir DataFrame de resultados ─────────────────────────────────────
    records = []
    for idx, (_, row) in enumerate(feature_df.iterrows()):
        hp = float(home_probs[idx])
        ap = float(away_probs[idx])
        records.append({
            "game_id":          str(row.get("game_id", f"game_{idx}")),
            "game_date":        row.get("game_date") or row.get("game_date_est"),
            "home_team_id":     row.get("home_team_id"),
            "visitor_team_id":  row.get("visitor_team_id"),
            "home_win_prob":    round(hp, 4),
            "away_win_prob":    round(ap, 4),
            "predicted_winner": "home" if hp >= 0.5 else "away",
            "model_version":    model_used,
            "fetch_date":       today,
        })

    result_df = pd.DataFrame(records)
    logger.info(
        "predict: %d predicciones generadas (modelo: %s)",
        len(result_df), model_used,
    )
    return result_df


def _heuristic_probs(feature_df: pd.DataFrame) -> tuple:
    """
    Fallback: estima probabilidades a partir del win % de cada equipo.
    Aplica un bonus de 0.05 por jugar en casa.
    """
    HOME_ADV = 0.05

    if "home_w_pct" in feature_df.columns and "visitor_w_pct" in feature_df.columns:
        hw = feature_df["home_w_pct"].fillna(0.5).values
        vw = feature_df["visitor_w_pct"].fillna(0.5).values
        total = hw + vw
        # Normalizar + ventaja local
        home_p = np.where(total > 0, hw / total, 0.5) + HOME_ADV
        home_p = np.clip(home_p, 0.05, 0.95)
        away_p = 1.0 - home_p
    else:
        # Sin info de win%: 55/45 por ventaja de local
        n = len(feature_df)
        home_p = np.full(n, 0.55)
        away_p = np.full(n, 0.45)

    return home_p, away_p
