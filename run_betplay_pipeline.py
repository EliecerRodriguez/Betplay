"""
Pipeline diario de la Liga BetPlay (fútbol Colombia).

Orquesta el flujo completo (clon del patrón NBA):
  1. Obtener partidos del día (ESPN)
  2. Cargar features (promedios por equipo, forma, tabla)
  3. Predecir Over/Under córners y tarjetas con los modelos BetPlay
  4. Obtener cuotas de córners/tarjetas (Kambi/Betplay)
  5. Detectar value bets
  6. Guardar resultados (CSV)

Uso:
    python run_betplay_pipeline.py                 # hoy
    python run_betplay_pipeline.py --date 2026-10-22
    python run_betplay_pipeline.py --no-save       # solo consola
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sports.betplay.ingestion.espn_client import get_daily_games
from sports.betplay.ingestion.historical_client import build_historical_dataset
from sports.betplay.ingestion.kambi_client import get_betplay_odds
from sports.betplay.processing.features import (
    build_features,
    build_team_averages,
    get_feature_columns,
)
from sports.betplay.model.predictor import predict_betplay
from sports.betplay.model.value_detector import detect_value_bets
from utils.logger import get_logger

logger = get_logger("run_betplay")

OUTPUT_DIR = "output"


def run(date_str: str) -> dict:
    """Ejecuta el pipeline del día. Devuelve dict con dataframes."""
    today = date.today().isoformat()
    logger.info("=" * 60)
    logger.info("PIPELINE LIGA BETPLAY — %s", date_str)
    logger.info("=" * 60)

    # 1. Partidos del día (ESPN)
    games = get_daily_games(date_str)
    if games.empty:
        logger.warning("Sin partidos de la Liga BetPlay para %s", date_str)
        return {"games": pd.DataFrame(), "predictions": pd.DataFrame(),
                "odds": pd.DataFrame(), "value_bets": pd.DataFrame()}
    logger.info("  → %d partidos", len(games))

    # 2. Dataset histórico para promedios por equipo
    # Usar CSV consolidado si existe (ruta rápida); si no, construir desde ESPN
    hist = pd.DataFrame()
    csv_path = "data/betplay_cache/dataset_completo.csv"
    if os.path.exists(csv_path):
        try:
            hist = pd.read_csv(csv_path)
            logger.info("  → promedios desde CSV consolidado (%d partidos)", len(hist))
        except Exception:
            hist = pd.DataFrame()
    if hist.empty:
        try:
            hist = build_historical_dataset()
            logger.info("  → promedios por equipo desde %d partidos históricos", len(hist))
            try:
                hist.to_csv(csv_path, index=False)
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Histórico falló (%s) — sin promedios", exc)
    team_avgs = build_team_averages(hist)

    # 3. Features
    feature_df = build_features(games, team_avgs)
    if feature_df.empty:
        logger.warning("Sin features — abortando")
        return {"games": games, "predictions": pd.DataFrame(),
                "odds": pd.DataFrame(), "value_bets": pd.DataFrame()}

    # 4. Predicciones Over/Under
    predictions_df = predict_betplay(feature_df)
    logger.info("  → %d predicciones (córners y tarjetas)", len(predictions_df))

    # 5. Cuotas de Betplay (Kambi)
    odds_df = get_betplay_odds()
    logger.info("  → %d partidos con cuotas de córners", len(odds_df))

    # 6. Value bets
    value_bets_df = pd.DataFrame()
    if not predictions_df.empty and not odds_df.empty:
        try:
            value_bets_df = detect_value_bets(predictions_df, odds_df)
        except Exception as exc:
            logger.warning("detect_value_bets falló: %s", exc)

    return {
        "games": games,
        "predictions": predictions_df,
        "odds": odds_df,
        "value_bets": value_bets_df,
    }


def _upsert_csv(path: str, new_df: pd.DataFrame, key_cols: list[str]) -> None:
    if new_df.empty:
        return
    if os.path.exists(path):
        existing = pd.read_csv(path, dtype=str)
        keys_new = set(zip(*[new_df[c].astype(str) for c in key_cols]))
        mask = existing.apply(lambda r: tuple(r[c] for c in key_cols) not in keys_new, axis=1)
        combined = pd.concat([existing[mask], new_df.astype(str)], ignore_index=True)
    else:
        combined = new_df.astype(str)
    combined.to_csv(path, index=False)
    logger.info("  ✓ %s (%d filas)", path, len(combined))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="Fecha YYYY-MM-DD (default: hoy)")
    parser.add_argument("--no-save", action="store_true", help="Solo consola")
    args = parser.parse_args()

    date_str = args.date or date.today().isoformat()
    data = run(date_str)

    if args.no_save:
        return 0

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if not data["predictions"].empty:
        _upsert_csv(f"{OUTPUT_DIR}/betplay_predictions.csv", data["predictions"], ["game_id"])
    if not data["odds"].empty:
        _upsert_csv(f"{OUTPUT_DIR}/betplay_odds.csv", data["odds"], ["event_id"])
    if not data["value_bets"].empty:
        _upsert_csv(f"{OUTPUT_DIR}/betplay_value_bets.csv", data["value_bets"],
                    ["game_id", "market", "side"])
    logger.info("Pipeline Liga BetPlay completado para %s", date_str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
