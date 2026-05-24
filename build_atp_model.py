"""
Entrena el modelo predictivo ATP v2.

Flujo:
  1. Descarga y cachea partidos históricos (2010–presente, incluye 2026 parcial)
  2. Construye 17 features de entrenamiento (loop progresivo sin look-ahead bias)
     + 4 nuevas features de saque rolling (1st serve %, bp save rate, ace rate)
  3. Evaluación honesta: train 2013–2024 | val 2025 (año no visto)
  4. Modelo producción: reentrena en 2013–2025 (máximo de datos completos)
  5. Arquitectura: StackingClassifier (XGBoost + RandomForest) + meta LogisticRegression
  6. Evalúa: accuracy, ROC-AUC, Brier score
  7. Guarda modelo en sports/atp/models/atp_model_v2.joblib

Uso:
  python build_atp_model.py --force
  python build_atp_model.py --force --train-until 2025

El modelo se guarda con el pipeline completo (imputer + scaler + stacking),
listo para usar con model.predict_proba(X).
"""
from __future__ import annotations

import argparse
import os
import sys

import joblib
import numpy as np
import pandas as pd

# Asegurar PYTHONPATH
sys.path.insert(0, os.path.dirname(__file__))

from sports.atp.ingestion.historical_client import download_atp_matches
from sports.atp.processing.features import FEATURE_COLUMNS, build_training_features
from utils.logger import get_logger

logger = get_logger("build_atp_model")

_MODEL_DIR  = "sports/atp/models"
_MODEL_PATH = os.path.join(_MODEL_DIR, "atp_model_v2.joblib")
_END_YEAR   = 2026   # siempre descarga hasta el año actual para calentar Elos


def _build_pipeline():
    """Construye el pipeline StackingClassifier (XGB + RF + meta-LR)."""
    from sklearn.ensemble import RandomForestClassifier, StackingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    try:
        from xgboost import XGBClassifier
        xgb = XGBClassifier(
            n_estimators=400,
            max_depth=4,
            learning_rate=0.04,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_weight=5,
            gamma=0.1,
            use_label_encoder=False,
            eval_metric="logloss",
            random_state=42,
            n_jobs=-1,
        )
        logger.info("XGBoost disponible")
    except ImportError:
        from sklearn.linear_model import LogisticRegression as _LR
        xgb = _LR(C=1.0, max_iter=1000, random_state=42)
        logger.info("XGBoost no disponible — usando LogisticRegression como base")

    rf = RandomForestClassifier(
        n_estimators=300,
        max_depth=7,
        min_samples_leaf=10,
        random_state=42,
        n_jobs=-1,
    )

    stacking = StackingClassifier(
        estimators=[("xgb", xgb), ("rf", rf)],
        final_estimator=LogisticRegression(C=0.5, max_iter=1000, random_state=42),
        cv=5,
        stack_method="predict_proba",
        passthrough=False,
        n_jobs=1,   # evitar conflictos con n_jobs interno de XGB/RF
    )
    logger.info("StackingClassifier: XGB + RF → meta LogisticRegression")

    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler",  StandardScaler()),
        ("clf",     stacking),
    ])


def _evaluate(model, X: pd.DataFrame, y: pd.Series, label: str = "val") -> dict:
    """Calcula métricas de evaluación."""
    from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score

    probs = model.predict_proba(X)[:, 1]
    preds = (probs >= 0.5).astype(int)

    acc    = accuracy_score(y, preds)
    auc    = roc_auc_score(y, probs)
    brier  = brier_score_loss(y, probs)
    lloss  = log_loss(y, probs)

    metrics = {
        "split":    label,
        "n_samples": len(y),
        "accuracy": round(acc, 4),
        "roc_auc":  round(auc, 4),
        "brier":    round(brier, 4),
        "log_loss": round(lloss, 4),
    }

    print(f"\n── Evaluación [{label}] ────────────────────────────────────────────")
    for k, v in metrics.items():
        print(f"   {k:<12}: {v}")

    # Calibración rápida: bins de probabilidad
    bins = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0)]
    print(f"\n   Calibración por bins de confianza:")
    for lo, hi in bins:
        mask = (probs >= lo) & (probs < hi)
        if mask.sum() >= 10:
            actual_rate = y[mask].mean()
            print(f"   [{lo:.1f}, {hi:.1f}): n={mask.sum():>5}  "
                  f"predicted≈{(lo+hi)/2:.2f}  actual={actual_rate:.3f}")

    return metrics


def main(
    start_year: int = 2010,
    train_until: int = 2025,
    min_year: int = 2013,
    force: bool = False,
) -> None:
    if os.path.exists(_MODEL_PATH) and not force:
        print(f"\nModelo ya existe: {_MODEL_PATH}")
        print("Usa --force para re-entrenar.")
        return

    # ── 1. Descargar datos hasta el año actual (incluye 2026 parcial para Elo warmup)
    print(f"\n[1/5] Descargando partidos ATP {start_year}–{_END_YEAR} (2026 parcial para Elo)...")
    matches = download_atp_matches(start_year=start_year, end_year=_END_YEAR)
    print(f"      {len(matches):,} partidos descargados")

    # ── 2. Feature engineering ────────────────────────────────────────────────
    print(f"\n[2/5] Construyendo {len(FEATURE_COLUMNS)} features (min_year={min_year}, sin look-ahead)...")
    feat_df = build_training_features(matches, min_year=min_year)
    if feat_df.empty:
        logger.error("No se generaron features — abortar")
        sys.exit(1)
    print(f"      {len(feat_df):,} filas ({len(feat_df)//2:,} partidos únicos)")
    print(f"      Features ({len(FEATURE_COLUMNS)}): {FEATURE_COLUMNS}")

    feat_df["year"] = pd.to_datetime(feat_df["tourney_date"]).dt.year

    # ── 3. Evaluación honesta: train 2013–2024 | val 2025 (no visto) ──────────
    print(f"\n[3/5] Evaluación honesta: train ≤ 2024  |  val = 2025...")
    eval_train_mask = feat_df["year"] <= 2024
    eval_val_mask   = feat_df["year"] == 2025
    X_eval_train = feat_df.loc[eval_train_mask, FEATURE_COLUMNS]
    y_eval_train = feat_df.loc[eval_train_mask, "target"]
    X_eval_val   = feat_df.loc[eval_val_mask,   FEATURE_COLUMNS]
    y_eval_val   = feat_df.loc[eval_val_mask,   "target"]
    print(f"      Train eval: {len(X_eval_train):,}  |  Val 2025: {len(X_eval_val):,}")
    if len(X_eval_val) < 200:
        print(f"      ADVERTENCIA: pocos ejemplos de validación ({len(X_eval_val)}) — continuando")
    eval_model = _build_pipeline()
    print("      Entrenando modelo de evaluación (esto puede tardar ~5-10 min)...")
    eval_model.fit(X_eval_train, y_eval_train)
    train_metrics = _evaluate(eval_model, X_eval_train, y_eval_train, label="train (2013-2024)")
    if not X_eval_val.empty:
        val_metrics = _evaluate(eval_model, X_eval_val, y_eval_val, label="val 2025")
    else:
        val_metrics = train_metrics
        print("      Sin datos de 2025 disponibles — mostrando métricas de train")

    # ── 4. Modelo producción: reentrena en 2013–train_until ───────────────────
    print(f"\n[4/5] Modelo producción: train ≤ {train_until} (todos los años completos)...")
    prod_train_mask = feat_df["year"] <= train_until
    X_prod = feat_df.loc[prod_train_mask, FEATURE_COLUMNS]
    y_prod = feat_df.loc[prod_train_mask, "target"]
    print(f"      Producción train: {len(X_prod):,} filas")
    prod_model = _build_pipeline()
    print("      Entrenando modelo de producción...")
    prod_model.fit(X_prod, y_prod)
    print("      Entrenamiento de producción completado")

    # ── 5. Guardar modelo producción ──────────────────────────────────────────
    acc = val_metrics["accuracy"]
    auc = val_metrics["roc_auc"]
    print(f"\n{'='*65}")
    print(f"  RESULTADO FINAL (val 2025)")
    print(f"  Accuracy: {acc:.1%}  |  ROC-AUC: {auc:.4f}")
    if acc < 0.62:
        print("  ADVERTENCIA: accuracy < 62% — revisar features o datos")
    elif acc >= 0.68:
        print("  Excelente: accuracy >= 68%")
    else:
        print("  Aceptable: accuracy en rango 62–68%")
    print(f"{'='*65}\n")

    os.makedirs(_MODEL_DIR, exist_ok=True)
    joblib.dump(prod_model, _MODEL_PATH)
    print(f"Modelo producción guardado: {_MODEL_PATH}")

    metrics_path = os.path.join(_MODEL_DIR, "training_metrics.txt")
    with open(metrics_path, "w", encoding="utf-8") as f:
        f.write(f"Model          : atp_model_v2 (StackingClassifier XGB+RF+LR)\n")
        f.write(f"Features       : {len(FEATURE_COLUMNS)} ({', '.join(FEATURE_COLUMNS)})\n")
        f.write(f"Train data     : 2013–{train_until} | Eval split: 2013–2024 train / 2025 val\n")
        f.write(f"Elo warmup     : {start_year}–{_END_YEAR} (incluye 2026 parcial)\n")
        f.write(f"Train accuracy : {train_metrics['accuracy']}\n")
        f.write(f"Train ROC-AUC  : {train_metrics['roc_auc']}\n")
        f.write(f"Val   accuracy : {val_metrics['accuracy']}\n")
        f.write(f"Val   ROC-AUC  : {val_metrics['roc_auc']}\n")
        f.write(f"Val   Brier    : {val_metrics['brier']}\n")
        f.write(f"Val   log_loss : {val_metrics['log_loss']}\n")
    print(f"Métricas guardadas: {metrics_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Entrenar modelo predictivo ATP v2 (StackingClassifier + features de saque)"
    )
    parser.add_argument(
        "--start-year", type=int, default=2010,
        help="Año desde el que descargar datos (default: 2010)"
    )
    parser.add_argument(
        "--train-until", type=int, default=2025,
        help="Último año de entrenamiento producción (default: 2025). Eval siempre en 2024/2025."
    )
    parser.add_argument(
        "--min-year", type=int, default=2013,
        help="Año mínimo para generar ejemplos de entrenamiento (default: 2013)"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Fuerza re-entrenamiento aunque el modelo ya exista"
    )
    args = parser.parse_args()

    main(
        start_year=args.start_year,
        train_until=args.train_until,
        min_year=args.min_year,
        force=args.force,
    )
