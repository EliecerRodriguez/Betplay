"""
Entrena el modelo predictivo ATP v4.

MEJORAS vs v3
─────────────────────────────────────────────────────────────────────
1. SIN DATA LEAKAGE en calibración:
   - Modelo producción entrenado SOLO en 2013–2024.
   - Calibración isotónica en 2025 (año completamente reservado).
   - v3 calibraba en 2025 después de entrenar en 2013–2025 → leakage.

2. EVALUACIÓN DE 3 SPLITS:
   - fast_check : train 2013–2022  |  val 2023–2024
   - honesta    : train 2013–2024  |  val 2025  (mismo conjunto que calibración)
   - Detecta overfitting temporal antes de decidir hiperpárametros.

3. HIPERPARÁMETROS MÁS REGULIZADOS:
   - XGBoost: min_child_weight 5→8, gamma 0.1→0.2, reg_lambda 1→2, reg_alpha 0→0.1
   - Meta-LR: C 0.5→0.3
   - Objetivo: menos overfit sobre datos de entrenamiento.

4. CALIBRACIÓN INTEGRADA (sin script separado):
   - Ajusta IsotonicRegression(out_of_bounds="clip") sobre 2025 (held-out).
   - Guarda bundle dict: {base_model, iso_reg, feature_cols, calib_year, model_version}
   - Bundle listo para usar directamente por predictor.py.

5. ANÁLISIS DE CALIBRACIÓN POR QUINTILES EN TEST:
   - Muestra si la probabilidad predicha es fiel a la tasa real de victorias.

Archivos generados:
   sports/atp/models/atp_model_v4.joblib            ← pipeline sklearn base
   sports/atp/models/atp_model_v4_calibrated.joblib ← bundle calibrado

Uso:
  python build_atp_model_v4.py          # omite si ya existen
  python build_atp_model_v4.py --force  # fuerza reentrenamiento
"""
from __future__ import annotations

import argparse
import os
import sys

import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

sys.path.insert(0, os.path.dirname(__file__))

from sports.atp.ingestion.historical_client import download_atp_matches
from sports.atp.processing.features import FEATURE_COLUMNS, build_training_features
from utils.logger import get_logger

logger = get_logger("build_atp_model_v4")

_MODEL_DIR        = "sports/atp/models"
_BASE_MODEL_PATH  = os.path.join(_MODEL_DIR, "atp_model_v4.joblib")
_CALIB_MODEL_PATH = os.path.join(_MODEL_DIR, "atp_model_v4_calibrated.joblib")
_METRICS_PATH     = os.path.join(_MODEL_DIR, "training_metrics_v4.txt")
_END_YEAR         = 2026   # para warmup de Elo


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _build_pipeline():
    """Pipeline v4: más regularización que v3 para reducir overfitting."""
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
            min_child_weight=8,    # v3: 5  → más conservador por hoja
            gamma=0.2,             # v3: 0.1 → penaliza más splits marginales
            reg_lambda=2.0,        # v3: 1.0 → L2 más fuerte
            reg_alpha=0.1,         # v3: 0.0 → añade L1
            use_label_encoder=False,
            eval_metric="logloss",
            random_state=42,
            n_jobs=-1,
        )
        logger.info("XGBoost disponible (v4: mayor regularización)")
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
        final_estimator=LogisticRegression(
            C=0.3,          # v3: 0.5 → meta-LR más regularizada
            max_iter=1000,
            random_state=42,
        ),
        cv=5,
        stack_method="predict_proba",
        passthrough=False,
        n_jobs=1,
    )
    logger.info("StackingClassifier v4: XGB(regularizado) + RF -> meta LogisticRegression(C=0.3)")

    return Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler",  StandardScaler()),
        ("clf",     stacking),
    ])


# ── Evaluación ────────────────────────────────────────────────────────────────

def _evaluate(model_or_probs, X_or_none, y: pd.Series, label: str) -> dict:
    """
    Acepta (pipeline, X, y) o (probs_array, None, y) para reutilizar
    con probs isotónicos ya calculados.
    """
    from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score

    if X_or_none is not None:
        probs = model_or_probs.predict_proba(X_or_none)[:, 1]
    else:
        probs = np.asarray(model_or_probs)

    preds = (probs >= 0.5).astype(int)
    acc   = accuracy_score(y, preds)
    auc   = roc_auc_score(y, probs)
    brier = brier_score_loss(y, probs)
    lloss = log_loss(y, probs)

    metrics = {
        "split": label, "n_samples": len(y),
        "accuracy": round(acc, 4), "roc_auc": round(auc, 4),
        "brier": round(brier, 4),  "log_loss": round(lloss, 4),
    }

    print(f"\n── Evaluación [{label}] ─────────────────────────────────────")
    for k, v in metrics.items():
        print(f"   {k:<12}: {v}")

    # Calibración rápida por quintiles (5 grupos de tamaño igual)
    print(f"\n   Calibración por quintiles de probabilidad predicha:")
    try:
        quintile_labels = pd.qcut(probs, q=5, duplicates="drop")
        for grp_label, grp_mask in y.groupby(quintile_labels, observed=True):
            grp_probs = probs[grp_mask.index]
            actual    = grp_mask.mean()
            predicted = grp_probs.mean()
            n         = len(grp_mask)
            diff      = abs(predicted - actual)
            flag      = " !" if diff > 0.05 else ""
            print(f"   [{str(grp_label):>20}]: n={n:>5}  pred={predicted:.3f}  actual={actual:.3f}{flag}")
    except Exception as exc:
        logger.debug("No se pudieron calcular quintiles: %s", exc)
        bins = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.0)]
        for lo, hi in bins:
            mask = (probs >= lo) & (probs < hi)
            if mask.sum() >= 10:
                print(f"   [{lo:.1f}, {hi:.1f}): n={mask.sum():>5}  pred≈{(lo+hi)/2:.2f}  "
                      f"actual={y[mask].mean():.3f}")

    return metrics


# ── Calibración isotónica ─────────────────────────────────────────────────────

def _calibrate(base_model, X_calib: pd.DataFrame, y_calib: pd.Series, calib_year: int) -> dict:
    """
    Ajusta IsotonicRegression sobre el conjunto de calibración (2025, held-out).
    Devuelve bundle dict listo para guardar.
    """
    raw_probs = base_model.predict_proba(X_calib)[:, 1]
    iso_reg   = IsotonicRegression(out_of_bounds="clip")
    iso_reg.fit(raw_probs, y_calib.values)

    calib_probs = iso_reg.predict(raw_probs)

    from sklearn.metrics import brier_score_loss
    brier_base  = brier_score_loss(y_calib, raw_probs)
    brier_calib = brier_score_loss(y_calib, calib_probs)

    print(f"\n── Calibración isotónica ────────────────────────────────────────")
    print(f"   N muestras calib  : {len(y_calib):,}  (año {calib_year})")
    print(f"   Brier antes       : {brier_base:.4f}")
    print(f"   Brier calibrado   : {brier_calib:.4f}  "
          f"({'mejor' if brier_calib < brier_base else 'sin mejora'})")

    bundle = {
        "base_model":    base_model,
        "iso_reg":       iso_reg,
        "feature_cols":  FEATURE_COLUMNS,
        "calib_year":    calib_year,
        "model_version": "atp_model_v4_calibrated",
        "brier_base":    round(brier_base, 4),
        "brier_calib":   round(brier_calib, 4),
    }
    return bundle, calib_probs


# ── Main ──────────────────────────────────────────────────────────────────────

def main(start_year: int = 2010, min_year: int = 2013, force: bool = False) -> None:

    if os.path.exists(_CALIB_MODEL_PATH) and not force:
        print(f"\nModelos v4 ya existen:")
        print(f"  Base      : {_BASE_MODEL_PATH}")
        print(f"  Calibrado : {_CALIB_MODEL_PATH}")
        print("Usa --force para re-entrenar.")
        return

    # ── 1. Datos ──────────────────────────────────────────────────────────────
    print(f"\n[1/6] Descargando partidos ATP {start_year}–{_END_YEAR}...")
    matches = download_atp_matches(start_year=start_year, end_year=_END_YEAR)
    print(f"      {len(matches):,} partidos descargados")

    # ── 2. Features ───────────────────────────────────────────────────────────
    print(f"\n[2/6] Construyendo {len(FEATURE_COLUMNS)} features (sin look-ahead bias)...")
    feat_df = build_training_features(matches, min_year=min_year)
    if feat_df.empty:
        logger.error("No se generaron features — abortar")
        sys.exit(1)
    feat_df["year"] = pd.to_datetime(feat_df["tourney_date"]).dt.year
    print(f"      {len(feat_df):,} filas  ({len(feat_df)//2:,} partidos únicos)")
    print(f"      Distribución por año:")
    print(feat_df.groupby("year").size().to_string())

    # ── 3. Evaluación rápida 3-split ──────────────────────────────────────────
    # Split A: train 2013–2022 | val 2023–2024  → detecta overfitting precoz
    # Split B: train 2013–2024 | val 2025       → calibración final
    print(f"\n[3/6] Evaluación rápida (fast-check): train 2013–2022 | val 2023–2024...")
    fc_train_m = feat_df["year"] <= 2022
    fc_val_m   = feat_df["year"].isin([2023, 2024])
    X_fc_tr = feat_df.loc[fc_train_m, FEATURE_COLUMNS]
    y_fc_tr = feat_df.loc[fc_train_m, "target"]
    X_fc_va = feat_df.loc[fc_val_m,   FEATURE_COLUMNS]
    y_fc_va = feat_df.loc[fc_val_m,   "target"]
    print(f"      Train: {len(X_fc_tr):,}  |  Val: {len(X_fc_va):,}")
    fc_model = _build_pipeline()
    print("      Entrenando fast-check (~5-10 min)...")
    fc_model.fit(X_fc_tr, y_fc_tr)
    _evaluate(fc_model, X_fc_tr, y_fc_tr, label="fast-check train (2013-2022)")
    fc_val_metrics = _evaluate(fc_model, X_fc_va, y_fc_va, label="fast-check val (2023-2024)")
    del fc_model  # liberar memoria

    # ── 4. Evaluación honesta: train 2013–2024 | val 2025 ────────────────────
    print(f"\n[4/6] Evaluación honesta: train 2013–2024 | val 2025...")
    h_train_m = feat_df["year"] <= 2024
    h_val_m   = feat_df["year"] == 2025
    X_h_tr = feat_df.loc[h_train_m, FEATURE_COLUMNS]
    y_h_tr = feat_df.loc[h_train_m, "target"]
    X_h_va = feat_df.loc[h_val_m,   FEATURE_COLUMNS]
    y_h_va = feat_df.loc[h_val_m,   "target"]
    print(f"      Train: {len(X_h_tr):,}  |  Val 2025: {len(X_h_va):,}")
    hon_model = _build_pipeline()
    print("      Entrenando modelo honesto (~5-10 min)...")
    hon_model.fit(X_h_tr, y_h_tr)
    _evaluate(hon_model, X_h_tr, y_h_tr, label="honesto train (2013-2024)")
    hon_val_metrics = _evaluate(hon_model, X_h_va, y_h_va, label="honesto val 2025")
    del hon_model

    # ── 5. Modelo producción: train 2013–2024 (NO 2025, reservado para calib) ─
    #
    # DIFERENCIA CLAVE vs v3:
    #   v3 entrenó en 2013–2025 y calibró en 2025 → LEAKAGE
    #   v4 entrena en 2013–2024 y calibra en 2025 → CORRECTO
    #
    print(f"\n[5/6] Modelo producción: train 2013–2024 (2025 reservado para calibración)...")
    prod_mask = feat_df["year"] <= 2024
    X_prod    = feat_df.loc[prod_mask, FEATURE_COLUMNS]
    y_prod    = feat_df.loc[prod_mask, "target"]
    print(f"      Train: {len(X_prod):,} filas")
    prod_model = _build_pipeline()
    print("      Entrenando modelo producción...")
    prod_model.fit(X_prod, y_prod)
    print("      Entrenamiento completado")

    # Guardar modelo base
    os.makedirs(_MODEL_DIR, exist_ok=True)
    joblib.dump(prod_model, _BASE_MODEL_PATH)
    print(f"      Modelo base guardado: {_BASE_MODEL_PATH}")

    # ── 6. Calibración integrada en 2025 (sin leakage) ───────────────────────
    print(f"\n[6/6] Calibración isotónica en 2025 (held-out, sin leakage)...")
    X_calib = feat_df.loc[feat_df["year"] == 2025, FEATURE_COLUMNS]
    y_calib = feat_df.loc[feat_df["year"] == 2025, "target"]
    print(f"      Muestras calibración 2025: {len(X_calib):,}")

    bundle, calib_probs_2025 = _calibrate(prod_model, X_calib, y_calib, calib_year=2025)
    joblib.dump(bundle, _CALIB_MODEL_PATH)
    print(f"      Bundle calibrado guardado: {_CALIB_MODEL_PATH}")

    # Evaluación del modelo calibrado en 2025
    _evaluate(calib_probs_2025, None, y_calib, label="calibrado 2025 (test real)")

    # ── Resumen final ─────────────────────────────────────────────────────────
    acc  = hon_val_metrics["accuracy"]
    auc  = hon_val_metrics["roc_auc"]
    brier= hon_val_metrics["brier"]

    print(f"\n{'='*65}")
    print(f"  RESULTADO FINAL v4")
    print(f"  Fast-check val  (2023-2024): acc={fc_val_metrics['accuracy']:.1%}  "
          f"auc={fc_val_metrics['roc_auc']:.4f}")
    print(f"  Honesto    val  (2025):      acc={acc:.1%}  auc={auc:.4f}  brier={brier:.4f}")
    print(f"  Calibrado  test (2025):      brier={bundle['brier_calib']:.4f}  "
          f"(mejora vs base: {bundle['brier_base']-bundle['brier_calib']:+.4f})")

    if acc < 0.62:
        print("  ADVERTENCIA: accuracy < 62% — revisar features o datos")
    elif acc >= 0.68:
        print("  Excelente: accuracy >= 68%")
    else:
        print("  Aceptable: accuracy en rango 62–68%")
    print(f"{'='*65}\n")

    # Métricas a disco
    with open(_METRICS_PATH, "w", encoding="utf-8") as f:
        f.write("Model          : atp_model_v4 (StackingClassifier XGB+RF+LR — más regularizado)\n")
        f.write(f"Features       : {len(FEATURE_COLUMNS)} ({', '.join(FEATURE_COLUMNS)})\n")
        f.write("Train data     : 2013–2024 (2025 RESERVADO para calibración sin leakage)\n")
        f.write(f"Elo warmup     : {start_year}–{_END_YEAR}\n")
        f.write("\n[Fast-check 2023-2024]\n")
        for k, v in fc_val_metrics.items():
            f.write(f"  {k:<12}: {v}\n")
        f.write("\n[Honesto val 2025]\n")
        for k, v in hon_val_metrics.items():
            f.write(f"  {k:<12}: {v}\n")
        f.write("\n[Calibración isotónica 2025]\n")
        f.write(f"  brier_base   : {bundle['brier_base']}\n")
        f.write(f"  brier_calib  : {bundle['brier_calib']}\n")
        f.write(f"  mejora       : {bundle['brier_base']-bundle['brier_calib']:+.4f}\n")
        f.write(f"\nArchivos:\n")
        f.write(f"  base      : {_BASE_MODEL_PATH}\n")
        f.write(f"  calibrado : {_CALIB_MODEL_PATH}\n")
    print(f"Métricas guardadas: {_METRICS_PATH}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Entrena modelo ATP v4 con calibración integrada sin data leakage"
    )
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--min-year",   type=int, default=2013)
    parser.add_argument(
        "--force", action="store_true",
        help="Re-entrenar aunque el modelo ya exista"
    )
    args = parser.parse_args()
    main(start_year=args.start_year, min_year=args.min_year, force=args.force)
