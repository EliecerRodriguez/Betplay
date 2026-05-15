"""
Ejecuta el pipeline de predicción para una fecha y actualiza los CSVs en output/.

Uso:
    python run_pipeline.py                    # fecha de hoy
    python run_pipeline.py --date 2026-05-12  # fecha específica
    python run_pipeline.py --date 2026-05-12 --no-append  # reemplaza en lugar de acumular

Los CSVs en output/ se actualizan añadiendo los nuevos registros (evitando duplicados por game_id).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from config.settings import NBA_SEASON
from ingestion.elo import apply_elos_to_games, load_current_elos
from ingestion.injuries_client import adjust_predictions
from ingestion.nba_client import get_daily_games, get_combined_team_stats
from ingestion.odds_client import get_odds
from ingestion.recent_form import enrich_with_form
from ingestion.travel_client import enrich_with_travel
from model.monte_carlo import enrich_predictions_with_mc
from model.predictor import predict
from model.value_detector import detect_value_bets
from processing.features import build_features, clean_team_stats
from utils.logger import get_logger

logger = get_logger("run_pipeline")

OUTPUT_DIR = "output"


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run(date_str: str) -> dict[str, pd.DataFrame]:
    """
    Ejecuta el pipeline completo para date_str.
    Devuelve dict con DataFrames: games, features, predictions, odds, value_bets.
    """
    logger.info("=" * 60)
    logger.info("PIPELINE NBA — %s", date_str)
    logger.info("=" * 60)

    # 1. Partidos del día
    logger.info("Descargando partidos del %s …", date_str)
    games_df = get_daily_games(date_str)
    if games_df.empty:
        logger.warning("No hay partidos NBA para %s.", date_str)
        return {"games": pd.DataFrame(), "features": pd.DataFrame(),
                "predictions": pd.DataFrame(), "odds": pd.DataFrame(),
                "value_bets": pd.DataFrame()}

    games_df["fetch_date"] = date_str
    logger.info("  → %d partidos encontrados", len(games_df))

    # 2. Stats de equipo
    logger.info("Cargando stats de equipo (%s) …", NBA_SEASON)
    try:
        team_stats_df = clean_team_stats(get_combined_team_stats(NBA_SEASON))
    except Exception as exc:
        logger.warning("get_combined_team_stats falló (%s) — stats básicas", exc)
        from ingestion.nba_client import get_team_stats
        team_stats_df = clean_team_stats(get_team_stats(NBA_SEASON))

    # 3. Forma reciente
    logger.info("Enriqueciendo con forma reciente …")
    try:
        games_enriched = enrich_with_form(games_df, NBA_SEASON, n=5)
    except Exception as exc:
        logger.warning("enrich_with_form falló (%s) — sin forma reciente", exc)
        games_enriched = games_df

    # 4. Viaje y jet lag
    logger.info("Enriqueciendo con datos de viaje y jet lag …")
    try:
        games_enriched = enrich_with_travel(games_enriched, NBA_SEASON)
    except Exception as exc:
        logger.warning("enrich_with_travel falló (%s) — sin features de viaje", exc)

    # 5. Elo
    try:
        current_elos = load_current_elos("models/current_elos.json")
        if current_elos:
            games_enriched = apply_elos_to_games(games_enriched, current_elos)
            logger.info("Elo aplicado: %d equipos", len(current_elos))
    except Exception as exc:
        logger.debug("Elo no disponible: %s", exc)

    # 6. Features
    feature_df = build_features(games_enriched, team_stats_df) if not team_stats_df.empty else pd.DataFrame()
    if not feature_df.empty:
        feature_df["fetch_date"] = date_str

    # 6. Predicciones
    logger.info("Generando predicciones …")
    predictions_df = predict(feature_df) if not feature_df.empty else pd.DataFrame()

    if not predictions_df.empty:
        predictions_df["fetch_date"] = date_str
        try:
            predictions_df = adjust_predictions(predictions_df, games_df, season=NBA_SEASON)
        except Exception as exc:
            logger.warning("adjust_predictions falló: %s", exc)

        # 6b. Monte Carlo — enriquece predicciones con simulaciones
        logger.info("Ejecutando simulaciones Monte Carlo …")
        try:
            predictions_df = enrich_predictions_with_mc(predictions_df, feature_df)
        except Exception as exc:
            logger.warning("Monte Carlo falló: %s", exc)

    # 7. Cuotas
    logger.info("Obteniendo cuotas …")
    odds_df = pd.DataFrame()
    try:
        odds_df = get_odds(games_df)
        if not odds_df.empty:
            odds_df["fetch_date"] = date_str
    except Exception as exc:
        logger.warning("get_odds falló: %s", exc)

    # 8. Value bets
    value_bets_df = pd.DataFrame()
    if not predictions_df.empty and not odds_df.empty:
        try:
            value_bets_df = detect_value_bets(predictions_df, odds_df)
            if not value_bets_df.empty:
                value_bets_df["fetch_date"] = date_str
        except Exception as exc:
            logger.warning("detect_value_bets falló: %s", exc)

    logger.info("Pipeline completado.")
    return {
        "games": games_df,
        "features": feature_df,
        "predictions": predictions_df,
        "odds": odds_df,
        "value_bets": value_bets_df,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CSV export
# ─────────────────────────────────────────────────────────────────────────────

def _upsert_csv(path: str, new_df: pd.DataFrame, key_cols: list[str]) -> None:
    """
    Añade new_df al CSV existente en path, reemplazando filas con las mismas key_cols.
    Si el archivo no existe lo crea. Si new_df está vacío, no hace nada.
    """
    if new_df.empty:
        logger.debug("Sin datos para %s — no se actualiza.", path)
        return

    if os.path.exists(path):
        existing = pd.read_csv(path, dtype=str)
        # Eliminar filas que ya existen con las mismas claves
        keys_new = set(zip(*[new_df[c].astype(str) for c in key_cols]))
        mask = existing.apply(
            lambda r: tuple(r[c] for c in key_cols) not in keys_new, axis=1
        )
        combined = pd.concat([existing[mask], new_df.astype(str)], ignore_index=True)
    else:
        combined = new_df.astype(str)

    combined.to_csv(path, index=False)
    logger.info("  ✓ %s actualizado (%d filas totales, +%d nuevas)", path, len(combined), len(new_df))


def save_to_csv(data: dict[str, pd.DataFrame], append: bool = True) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if append:
        _upsert_csv(f"{OUTPUT_DIR}/games.csv",       data["games"],       ["game_id"])
        _upsert_csv(f"{OUTPUT_DIR}/features.csv",    data["features"],    ["game_id"])
        _upsert_csv(f"{OUTPUT_DIR}/predictions.csv", data["predictions"], ["game_id", "fetch_date"])
        _upsert_csv(f"{OUTPUT_DIR}/odds.csv",        data["odds"],        ["game_id", "bookmaker", "home_team"])
        _upsert_csv(f"{OUTPUT_DIR}/value_bets.csv",  data["value_bets"],  ["game_id", "bookmaker", "side"])
    else:
        for name, df in data.items():
            path = f"{OUTPUT_DIR}/{name}.csv"
            if not df.empty:
                df.to_csv(path, index=False)
                logger.info("  ✓ %s guardado (%d filas)", path, len(df))


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default=None, help="Fecha YYYY-MM-DD (default: hoy)")
    parser.add_argument("--no-append", action="store_true", help="Reemplazar CSVs en lugar de acumular")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    date_str = args.date or date.today().isoformat()
    append = not args.no_append

    data = run(date_str)
    save_to_csv(data, append=append)

    predictions = data["predictions"]
    value_bets  = data["value_bets"]

    logger.info("=" * 60)
    logger.info("RESUMEN — %s", date_str)
    logger.info("  Partidos : %d", len(data["games"]))
    logger.info("  Predicciones : %d", len(predictions))
    logger.info("  Value bets   : %d", len(value_bets))
    if not value_bets.empty and "value" in value_bets.columns:
        top = value_bets.sort_values("value", ascending=False).head(3)
        for _, r in top.iterrows():
            logger.info("  → %s | %s | cuota %.2f | value %.4f",
                        r.get("game_id",""), r.get("bookmaker",""),
                        float(r.get("odds", 0)), float(r.get("value", 0)))
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
