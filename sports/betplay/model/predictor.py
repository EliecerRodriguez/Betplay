"""
Predictor de Liga BetPlay (fútbol) — mercados 50/50.

Reutiliza el motor de entrenamiento de la NBA (modelo maestro) porque es
genérico: `train(X, y, ...)` acepta cualquier dataset de features y label
binario. Aquí solo se adapta:
  - Directorio de modelos: models/betplay/
  - Versionado: nba_model_vN.joblib → betplay_model_vN.joblib
  - Predicción: Over/Under tarjetas y córners (2 modelos)

Modelos entrenados:
  1. betplay_yellow_model_vN.joblib — P(Over 4.5 tarjetas)
  2. betplay_corners_model_vN.joblib — P(Over 9.5 córners)
"""
from __future__ import annotations

import glob
import os
from datetime import date
from typing import Optional

import joblib
import numpy as np
import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

BETPLAY_MODEL_DIR = os.getenv("BETPLAY_MODEL_DIR", "models/betplay")


# ── Auto-detección de versión (clon del patrón NBA) ──────────────────────────

def _latest_model_version(model_type: str) -> str:
    """Auto-detecta la versión más reciente del tipo de modelo (yellow/corners)."""
    env_version = os.getenv("BETPLAY_MODEL_VERSION", "").strip()
    if env_version:
        return env_version
    try:
        pattern = os.path.join(BETPLAY_MODEL_DIR, f"betplay_{model_type}_model_v*.joblib")
        files = glob.glob(pattern)
        if files:
            newest = max(files, key=os.path.getmtime)
            num = os.path.basename(newest).replace(f"betplay_{model_type}_model_v", "").replace(".joblib", "")
            if num.isdigit():
                return f"v{int(num)}"
    except Exception:
        pass
    return "v1"


def _model_path(model_type: str, version: str) -> str:
    return os.path.join(BETPLAY_MODEL_DIR, f"betplay_{model_type}_model_{version}.joblib")


# ── Entrenamiento (reutiliza el engine genérico de la NBA) ───────────────────

def _build_pipeline_betplay(model_type_clf: str = "xgboost"):
    """Construye el pipeline clonando el patrón NBA (XGB + calibración isotónica)."""
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    try:
        from xgboost import XGBClassifier
        base = XGBClassifier(
            n_estimators=400, max_depth=5, learning_rate=0.04,
            subsample=0.8, colsample_bytree=0.8, min_child_weight=3,
            eval_metric="logloss", random_state=42, n_jobs=-1,
        )
    except ImportError:
        from sklearn.ensemble import RandomForestClassifier
        base = RandomForestClassifier(
            n_estimators=300, max_depth=7, min_samples_leaf=4,
            random_state=42, n_jobs=-1,
        )

    classifier = CalibratedClassifierCV(base, method="isotonic", cv=5)
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler",  StandardScaler()),
        ("clf",     classifier),
    ])


def train_model(
    X: pd.DataFrame,
    y: pd.Series,
    model_type: str,  # 'yellow' | 'corners'
    version: Optional[str] = None,
) -> dict:
    """
    Entrena el modelo de tarjetas o córners (Over/Under).

    Args:
        X: Features (de processing.features.build_features).
        y: Target binario (1=Over línea, 0=Under).
        model_type: 'yellow' | 'corners'.
        version: Versión (default: auto-detect+1).

    Returns:
        Dict con métricas (accuracy, roc_auc, cv_mean, n_samples...).
    """
    from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
    from sklearn.model_selection import TimeSeriesSplit, cross_val_score

    version = version or f"v{int(_latest_model_version('_'.join([model_type]))[1:]) + 1}"
    version = version or "v1"
    # Limpieza del version
    if not version.startswith("v"):
        version = f"v{version}"

    logger.info("Entrenando modelo %s versión %s (%d muestras)", model_type, version, len(X))

    pipeline = _build_pipeline_betplay("xgboost")

    # Split temporal (sin data leakage) — clon del patrón NBA
    if len(X) >= 40:
        split_idx = int(len(X) * 0.8)
        X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
        y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]
    else:
        X_train, X_test, y_train, y_test = X, X, y, y

    pipeline.fit(X_train, y_train)
    y_pred = pipeline.predict(X_test)
    y_proba = pipeline.predict_proba(X_test)[:, 1]

    metrics = {
        "model_type": model_type,
        "version": version,
        "accuracy": round(float(accuracy_score(y_test, y_pred)), 4),
        "roc_auc": round(float(roc_auc_score(y_test, y_proba)), 4) if len(np.unique(y_test)) > 1 else None,
        "log_loss": round(float(log_loss(y_test, y_proba)), 4),
        "brier": round(float(brier_score_loss(y_test, y_proba)), 4),
        "n_samples": len(X),
        "n_features": X.shape[1],
    }

    # CV temporal
    try:
        n_splits = min(3, max(2, len(X) // 30))
        tscv = TimeSeriesSplit(n_splits=n_splits)
        cv_scores = cross_val_score(pipeline, X, y, cv=tscv, scoring="roc_auc", n_jobs=-1)
        metrics["cv_mean"] = round(float(cv_scores.mean()), 4)
        metrics["cv_std"] = round(float(cv_scores.std()), 4)
    except Exception as exc:
        logger.warning("CV falló (%s) — sin cv_mean", exc)
        metrics["cv_mean"] = None
        metrics["cv_std"] = None

    # Guardar
    os.makedirs(BETPLAY_MODEL_DIR, exist_ok=True)
    path = _model_path(model_type, version)
    joblib.dump({"pipeline": pipeline, "feature_cols": list(X.columns), "metrics": metrics}, path)
    logger.info(
        "Modelo %s guardado: %s | acc=%.3f cv=%.3f",
        model_type, path, metrics["accuracy"], metrics.get("cv_mean") or 0,
    )
    return metrics


# ── Predicción ───────────────────────────────────────────────────────────────

def predict_over_under(
    feature_df: pd.DataFrame,
    model_type: str,  # 'yellow' | 'corners'
    version: Optional[str] = None,
) -> pd.Series:
    """
    Predice P(Over línea) para cada partido.

    Args:
        feature_df: Features de build_features.
        model_type: 'yellow' | 'corners'.
        version: Versión del modelo.

    Returns:
        Serie con la probabilidad de Over [0-1], indexada igual que feature_df.
    """
    if feature_df.empty:
        return pd.Series(dtype=float)

    version = version or _latest_model_version(model_type)
    path = _model_path(model_type, version)
    if not os.path.exists(path):
        logger.warning("Modelo %s %s no existe — usando 0.5 (sin sesgo)", model_type, version)
        return pd.Series(0.5, index=feature_df.index)

    bundle = joblib.load(path)
    pipeline = bundle["pipeline"]
    feature_cols = bundle["feature_cols"]

    missing = [c for c in feature_cols if c not in feature_df.columns]
    if missing:
        logger.warning("Columnas faltantes para %s: %s — imputando 0", model_type, missing[:5])
        for col in missing:
            feature_df[col] = 0.0

    X = feature_df[feature_cols].astype(float)
    proba = pipeline.predict_proba(X)
    logger.info("Predicción %s (v%s): %d partidos | media P(over)=%.3f",
                model_type, version, len(X), float(proba[:, 1].mean()))
    return pd.Series(proba[:, 1], index=feature_df.index)


def predict_betplay(
    feature_df: pd.DataFrame,
    yellow_version: Optional[str] = None,
    corners_version: Optional[str] = None,
) -> pd.DataFrame:
    """
    Predicción completa para un día: Over/Under tarjetas y córners.

    Returns:
        DataFrame con game_id, home_team, away_team,
        over_yellow_prob, over_corners_prob,
        over_yellow (bool), over_corners (bool), model_version.
    """
    if feature_df.empty:
        return pd.DataFrame()

    yellow_probs = predict_over_under(feature_df, "yellow", yellow_version)
    corners_probs = predict_over_under(feature_df, "corners", corners_version)

    out = pd.DataFrame({
        "game_id": feature_df["game_id"].values,
        "game_date": feature_df["game_date"].values,
        "home_team": feature_df["home_team"].values,
        "away_team": feature_df["away_team"].values,
        "over_yellow_prob": yellow_probs.round(4),
        "over_corners_prob": corners_probs.round(4),
        "over_yellow": (yellow_probs >= 0.5).astype(int),
        "over_corners": (corners_probs >= 0.5).astype(int),
        "model_version": f"yellow:{_latest_model_version('yellow')}|corners:{_latest_model_version('corners')}",
        "fetch_date": date.today().isoformat(),
    })
    return out
