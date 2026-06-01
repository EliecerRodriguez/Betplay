"""
Calibración isotónica del modelo ATP v3.

Problema detectado: el StackingClassifier (XGB+RF+LR) produce probabilidades
mal calibradas — tiende a sobre-estimar la confianza del modelo en casos donde
no tiene información suficiente (jugadores nuevos, clasificatorias, etc.).

Solución: envolver el modelo con CalibratedClassifierCV(method='isotonic',
cv='prefit') usando los datos de 2025 como conjunto de calibración externo.
Esto ajusta la curva de probabilidades sin cambiar las predicciones ordinales.

Salida: sports/atp/models/atp_model_v3_calibrated.joblib

Uso:
    python calibrate_atp_model.py
    python calibrate_atp_model.py --force   # re-calibra aunque exista
"""
from __future__ import annotations

import argparse
import os
import sys

import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sports.atp.ingestion.historical_client import download_atp_matches
from sports.atp.processing.features import FEATURE_COLUMNS, build_training_features
from utils.logger import get_logger

logger = get_logger("calibrate_atp_model")

_MODEL_DIR      = "sports/atp/models"
_BASE_MODEL     = os.path.join(_MODEL_DIR, "atp_model_v3.joblib")
_CALIB_MODEL    = os.path.join(_MODEL_DIR, "atp_model_v3_calibrated.joblib")
_CALIB_YEAR     = 2025   # año usado para calibración (nunca visto durante producción)
_WARMUP_YEAR    = 2010   # datos para calentar Elos antes de generar features


def _calibration_report(y_true: np.ndarray, y_prob_before: np.ndarray, y_prob_after: np.ndarray) -> None:
    """Imprime comparación de calibración antes y después."""
    bins = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0)]
    print("\n  Comparación de calibración (antes vs después):")
    print(f"  {'Bin':<12} {'N':>6}  {'Pred antes':>12}  {'Pred despues':>13}  {'Real':>8}")
    print(f"  {'─'*12} {'─'*6}  {'─'*12}  {'─'*13}  {'─'*8}")
    for lo, hi in bins:
        mask = (y_prob_before >= lo) & (y_prob_before < hi)
        n = int(mask.sum())
        if n < 5:
            continue
        pred_before = y_prob_before[mask].mean()
        pred_after  = y_prob_after[mask].mean()
        actual      = y_true[mask].mean()
        print(f"  [{lo:.1f},{hi:.1f})   {n:>6}  {pred_before:>12.3f}  {pred_after:>13.3f}  {actual:>8.3f}")


def main(force: bool = False) -> None:
    if os.path.exists(_CALIB_MODEL) and not force:
        print(f"\nModelo calibrado ya existe: {_CALIB_MODEL}")
        print("Usa --force para re-calibrar.")
        return

    if not os.path.exists(_BASE_MODEL):
        print(f"\nERROR: Modelo base no encontrado: {_BASE_MODEL}")
        print("Ejecuta primero: python build_atp_model.py --force")
        sys.exit(1)

    # ── 1. Cargar modelo base ─────────────────────────────────────────────────
    print(f"\n[1/4] Cargando modelo base: {_BASE_MODEL}")
    base_model = joblib.load(_BASE_MODEL)
    print("      Modelo cargado OK")

    # ── 2. Construir datos de calibración (2025) ──────────────────────────────
    print(f"\n[2/4] Descargando datos historicos para calibracion...")
    matches = download_atp_matches(start_year=_WARMUP_YEAR, end_year=2026)
    print(f"      {len(matches):,} partidos descargados")

    print(f"      Construyendo features para año {_CALIB_YEAR}...")
    feat_df = build_training_features(matches, min_year=_CALIB_YEAR)

    calib_mask = pd.to_datetime(feat_df["tourney_date"]).dt.year == _CALIB_YEAR
    X_calib = feat_df.loc[calib_mask, FEATURE_COLUMNS]
    y_calib = feat_df.loc[calib_mask, "target"]

    print(f"      Datos de calibracion: {len(X_calib):,} filas (año {_CALIB_YEAR})")

    if len(X_calib) < 200:
        print(f"      ADVERTENCIA: pocos datos de calibracion ({len(X_calib)}) — continuando")

    # ── 3. Calibrar ───────────────────────────────────────────────────────────
    print(f"\n[3/4] Calibrando con IsotonicRegression manual (equivalente a cv='prefit')...")
    # Obtener probabilidades brutas del modelo base sobre los datos de calibración
    y_prob_before = base_model.predict_proba(X_calib)[:, 1]

    # Ajustar regresión isotónica sobre las probabilidades brutas vs resultados reales
    iso_reg = IsotonicRegression(out_of_bounds="clip")
    iso_reg.fit(y_prob_before, y_calib.values)
    print("      Calibracion isotonica completada")

    # Wrapper simple: dict con modelo base + isotonic regressor
    calibrated_bundle = {
        "base_model":    base_model,
        "iso_reg":       iso_reg,
        "feature_cols":  list(FEATURE_COLUMNS),
        "calib_year":    _CALIB_YEAR,
        "model_version": "atp_model_v3_calibrated",
    }

    # ── 4. Comparar probabilidades antes y después ────────────────────────────
    print(f"\n[4/4] Evaluando calibracion...")
    y_prob_after = iso_reg.predict(y_prob_before)

    # Accuracy no cambia (calibración solo ajusta probabilidades, no predicciones)
    preds_before = (y_prob_before >= 0.5).astype(int)
    preds_after  = (y_prob_after  >= 0.5).astype(int)
    acc_before = (preds_before == y_calib.values).mean()
    acc_after  = (preds_after  == y_calib.values).mean()

    from sklearn.metrics import brier_score_loss
    brier_before = brier_score_loss(y_calib, y_prob_before)
    brier_after  = brier_score_loss(y_calib, y_prob_after)

    print(f"      Accuracy antes: {acc_before:.4f}  | despues: {acc_after:.4f}")
    print(f"      Brier antes:    {brier_before:.4f}  | despues: {brier_after:.4f}  (menor = mejor)")

    _calibration_report(y_calib.values, y_prob_before, y_prob_after)

    # ── Guardar ───────────────────────────────────────────────────────────────
    joblib.dump(calibrated_bundle, _CALIB_MODEL)
    print(f"\nModelo calibrado guardado: {_CALIB_MODEL}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Re-calibra aunque ya exista")
    args = parser.parse_args()
    main(force=args.force)
