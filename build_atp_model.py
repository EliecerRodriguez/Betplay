"""
Entrena el modelo predictivo ATP.

Flujo:
  1. Descarga y cachea partidos históricos (2010–presente) desde Jeff Sackmann
  2. Construye features de entrenamiento (loop progresivo sin look-ahead bias)
  3. Split temporal: 2013-2025 train | 2024 validación
  4. Entrena XGBoost (con calibración de probabilidades isotónica)
  5. Fallback a LogisticRegression si XGBoost no está disponible
  6. Evalúa: accuracy, ROC-AUC, Brier score
  7. Guarda modelo en sports/atp/models/atp_model_v1.joblib

Uso:
  python build_atp_model.py
  python build_atp_model.py --start-year 2010 --train-until 2025 --val-year 2024

El modelo se guarda con el pipeline completo (imputer + scaler + classifier + calibración),
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

_MODEL_DIR = "sports/atp/models"
_MODEL_PATH = os.path.join(_MODEL_DIR, "atp_model_v1.joblib")


def _build_pipeline(use_xgb: bool = True):
    """Construye el pipeline sklearn completo."""
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    if use_xgb:
        try:
            from xgboost import XGBClassifier
            clf = XGBClassifier(
                n_estimators=400,
                max_depth=5,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                min_child_weight=5,
                gamma=0.1,
                use_label_encoder=False,
                eval_metric="logloss",
                random_state=42,
                n_jobs=-1,
            )
            classifier = CalibratedClassifierCV(clf, cv=5, method="isotonic")
            logger.info("Usando XGBoost + calibración isotónica")
        except ImportError:
            use_xgb = False

    if not use_xgb:
        from sklearn.linear_model import LogisticRegression
        clf = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
        classifier = CalibratedClassifierCV(clf, cv=5, method="isotonic")
        logger.info("Usando LogisticRegression + calibración isotónica")

    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler",  StandardScaler()),
        ("clf",     classifier),
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
    val_year: int = 2024,
    min_year: int = 2013,
    force: bool = False,
) -> None:
    if os.path.exists(_MODEL_PATH) and not force:
        print(f"\nModelo ya existe: {_MODEL_PATH}")
        print("Usa --force para re-entrenar.")
        return

    # ── 1. Descargar datos ────────────────────────────────────────────────────
    print(f"\n[1/5] Descargando partidos ATP {start_year}–{val_year}...")
    matches = download_atp_matches(start_year=start_year, end_year=val_year)
    print(f"      {len(matches):,} partidos descargados")

    # ── 2. Feature engineering ────────────────────────────────────────────────
    print(f"\n[2/5] Construyendo features (min_year={min_year}, sin look-ahead)...")
    feat_df = build_training_features(matches, min_year=min_year)
    if feat_df.empty:
        logger.error("No se generaron features — abortar")
        sys.exit(1)
    print(f"      {len(feat_df):,} filas ({len(feat_df)//2:,} partidos únicos)")
    print(f"      Features: {FEATURE_COLUMNS}")

    # ── 3. Split temporal ─────────────────────────────────────────────────────
    print(f"\n[3/5] Split temporal: train ≤{train_until} | val ={val_year}...")

    feat_df["year"] = pd.to_datetime(feat_df["tourney_date"]).dt.year

    train_mask = feat_df["year"] <= train_until
    val_mask   = feat_df["year"] == val_year

    X_train = feat_df.loc[train_mask, FEATURE_COLUMNS]
    y_train = feat_df.loc[train_mask, "target"]
    X_val   = feat_df.loc[val_mask,   FEATURE_COLUMNS]
    y_val   = feat_df.loc[val_mask,   "target"]

    print(f"      Train: {len(X_train):,} filas  |  Val: {len(X_val):,} filas")

    if len(X_val) < 200:
        print(f"      ADVERTENCIA: pocos ejemplos de validación ({len(X_val)})")

    # ── 4. Entrenar ───────────────────────────────────────────────────────────
    print("\n[4/5] Entrenando modelo...")
    model = _build_pipeline()
    model.fit(X_train, y_train)
    print("      Entrenamiento completado")

    # ── 5. Evaluar ────────────────────────────────────────────────────────────
    print("\n[5/5] Evaluando modelo...")
    train_metrics = _evaluate(model, X_train, y_train, label="train")
    val_metrics   = _evaluate(model, X_val,   y_val,   label="val")

    acc = val_metrics["accuracy"]
    auc = val_metrics["roc_auc"]

    print(f"\n{'='*65}")
    print(f"  RESULTADO FINAL — Accuracy val: {acc:.1%}  |  ROC-AUC: {auc:.4f}")

    if acc < 0.62:
        print("  ADVERTENCIA: accuracy < 62% — revisar features o datos")
    elif acc >= 0.68:
        print("  Excelente: accuracy >= 68% (supera baseline Elo ~67%)")
    else:
        print("  Aceptable: accuracy en rango 62–68%")
    print(f"{'='*65}\n")

    # Guardar
    os.makedirs(_MODEL_DIR, exist_ok=True)
    joblib.dump(model, _MODEL_PATH)
    print(f"Modelo guardado: {_MODEL_PATH}")

    # Guardar también métricas en texto
    metrics_path = os.path.join(_MODEL_DIR, "training_metrics.txt")
    with open(metrics_path, "w", encoding="utf-8") as f:
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
        description="Entrenar modelo predictivo ATP"
    )
    parser.add_argument(
        "--start-year", type=int, default=2010,
        help="Año desde el que descargar datos (default: 2010)"
    )
    parser.add_argument(
        "--train-until", type=int, default=2023,
        help="Último año de entrenamiento (default: 2025)"
    )
    parser.add_argument(
        "--val-year", type=int, default=2024,
        help="Año de validación (default: 2024)"
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
        val_year=args.val_year,
        min_year=args.min_year,
        force=args.force,
    )
