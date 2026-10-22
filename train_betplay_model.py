"""
Entrena los modelos de la Liga BetPlay (Over/Under tarjetas y córners).

Clon del patrón NBA (train_model.py) adaptado a los mercados 50/50.

Uso:
    python train_betplay_model.py                      # entrena yellow + corners
    python train_betplay_model.py --market yellow      # solo tarjetas
    python train_betplay_model.py --market corners      # solo córners
    python train_betplay_model.py --force-download     # re-descarga dataset (sin caché)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sports.betplay.ingestion.historical_client import build_historical_dataset
from sports.betplay.processing.features import (
    build_features,
    build_team_averages,
    get_feature_columns,
    make_targets,
)
from sports.betplay.model.predictor import train_model
from utils.logger import get_logger

logger = get_logger("train_betplay")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=["yellow", "corners", "both"], default="both",
                        help="Mercado a entrenar (default: both)")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD inicio (default: 2025-01-01)")
    parser.add_argument("--force-download", action="store_true", help="Ignorar caché y dataset consolidado")
    parser.add_argument("--from-csv", default="data/betplay_cache/dataset_completo.csv",
                        help="Usar dataset consolidado CSV (ruta rápida, default)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # ── 1. Dataset histórico ────────────────────────────────────────────────
    # Ruta rápida: usar el CSV consolidado si existe (evita re-descarga ESPN)
    matches = pd.DataFrame()
    csv_path = args.from_csv
    if not args.force_download and os.path.exists(csv_path):
        try:
            matches = pd.read_csv(csv_path)
            logger.info("Dataset desde CSV consolidado: %s (%d partidos)", csv_path, len(matches))
        except Exception as exc:
            logger.warning("No se pudo leer CSV (%s) — reconstruyendo desde ESPN", exc)
            matches = pd.DataFrame()

    if matches.empty:
        start = date.fromisoformat(args.start_date) if args.start_date else date(2025, 1, 1)
        end = date.today()
        logger.info("Construyendo dataset histórico Liga BetPlay (%s → %s)…", start, end)

        if args.force_download:
            import shutil
            from sports.betplay.config.settings import BETPLAY_CACHE_DIR
            if os.path.exists(BETPLAY_CACHE_DIR):
                shutil.rmtree(BETPLAY_CACHE_DIR)
                logger.info("Caché eliminada (--force-download)")

        matches = build_historical_dataset(start_date=start, end_date=end)
        if matches.empty:
            logger.error("Dataset vacío — no se puede entrenar")
            return 1
        # Actualizar el CSV consolidado
        try:
            matches.to_csv(csv_path, index=False)
            logger.info("CSV consolidado actualizado: %s", csv_path)
        except Exception as exc:
            logger.warning("No se pudo guardar CSV: %s", exc)

    logger.info("Dataset: %d partidos", len(matches))

    # ── 2. Features ──────────────────────────────────────────────────────────
    team_avgs = build_team_averages(matches)
    feature_df = build_features(matches, team_avgs)
    targets = make_targets(matches)

    # Merge features + targets
    train_df = feature_df.merge(targets, on="game_id", how="inner")
    feature_cols = get_feature_columns()
    available = [c for c in feature_cols if c in train_df.columns]
    logger.info("Features disponibles: %d de %d", len(available), len(feature_cols))

    # ── 3. Entrenar modelos ────────────────────────────────────────────────
    results = []
    if args.market in ("yellow", "both"):
        X = train_df[available].astype(float)
        y = train_df["target_yellow"].astype(int)
        if y.nunique() > 1 and len(y) >= 30:
            m = train_model(X, y, model_type="yellow")
            results.append(m)
        else:
            logger.warning("Datos insuficientes para tarjetas (%d muestras, %d clases)", len(y), y.nunique())

    if args.market in ("corners", "both"):
        X = train_df[available].astype(float)
        y = train_df["target_corners"].astype(int)
        if y.nunique() > 1 and len(y) >= 30:
            m = train_model(X, y, model_type="corners")
            results.append(m)
        else:
            logger.warning("Datos insuficientes para córners (%d muestras, %d clases)", len(y), y.nunique())

    if not results:
        logger.error("No se entrenó ningún modelo")
        return 1

    logger.info("===" * 20)
    for m in results:
        logger.info(
            "%s v%s: acc=%.3f | auc=%.4f | cv=%.4f | n=%d",
            m["model_type"], m["version"], m["accuracy"],
            m.get("roc_auc") or 0, m.get("cv_mean") or 0, m["n_samples"],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
