"""
Pipeline principal – TODAS LAS FASES.

Uso:
    python main.py                          # pipeline completo de hoy
    python main.py --date 2025-04-20        # fecha específica
    python main.py --season 2024-25         # temporada específica
    python main.py --output csv             # exportar DataFrames a CSV (debug)
    python main.py --skip-db               # omitir escritura en base de datos
    python main.py --skip-predict          # solo ingesta + DB, sin predicciones

Fases ejecutadas en orden:
  1. Ingesta NBA   (partidos, equipos, estadísticas)
  2. Cuotas        (The Odds API o placeholder)
  3. Base de datos (upsert en PostgreSQL/Supabase)
  4. Features      (procesamiento y feature engineering)
  5. Predicciones  (modelo ML o heurística de fallback)
  6. Value bets    (detección de oportunidades de valor)
  7. Persistencia  (guardar predicciones y value bets en DB)
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import DATABASE_URL, NBA_SEASON
from ingestion.nba_client import ingest_all
from ingestion.odds_client import get_odds
from model.predictor import predict
from model.value_detector import detect_value_bets, format_value_bets_report
from processing.features import build_features
from utils.logger import get_logger

logger = get_logger(__name__)


# ── Exportación a CSV ─────────────────────────────────────────────────────────

def _export_to_csv(data: dict, output_dir: str = "output") -> None:
    os.makedirs(output_dir, exist_ok=True)
    for name, df in data.items():
        if df is not None and not df.empty:
            path = os.path.join(output_dir, f"{name}.csv")
            df.to_csv(path, index=False, encoding="utf-8")
            logger.info("Exportado → %s (%d filas)", path, len(df))
        else:
            logger.warning("DataFrame '%s' vacío, no se exporta", name)


# ── Capa de base de datos (importación diferida para no fallar sin DB) ────────

def _get_repo():
    """
    Crea el repositorio de base de datos.
    Si DATABASE_URL no está configurada o la conexión falla, devuelve None
    y el pipeline continúa sin persistencia.
    """
    if not DATABASE_URL or DATABASE_URL.endswith("@localhost:5432/betplay"):
        logger.warning(
            "DATABASE_URL no configurada. "
            "Configura Supabase en .env para habilitar persistencia en BD."
        )
        return None
    try:
        from database.repository import DatabaseRepository
        return DatabaseRepository()
    except Exception as exc:
        logger.error("No se pudo conectar a la base de datos: %s", exc)
        return None


# ── Pipeline principal ────────────────────────────────────────────────────────

def run_pipeline(
    game_date: str,
    season: str,
    output: str,
    skip_db: bool = False,
    skip_predict: bool = False,
) -> dict:
    """
    Ejecuta el pipeline completo de Betplay.

    Returns:
        Diccionario con todos los DataFrames generados en cada fase.
    """
    logger.info("=" * 62)
    logger.info("BETPLAY – Pipeline Completo (Fases 1–6)")
    logger.info("Fecha: %s | Temporada: %s", game_date, season)
    logger.info("=" * 62)

    results = {}

    # ══════════════════════════════════════════════════════════════
    # FASE 1 – Ingesta NBA
    # ══════════════════════════════════════════════════════════════
    logger.info("── FASE 1: Ingesta NBA ──────────────────────────────────")
    nba_data = ingest_all(game_date=game_date, season=season)
    games_df      = nba_data["games"]
    teams_df      = nba_data["teams"]
    team_stats_df = nba_data["team_stats"]
    results.update(nba_data)

    # ── Cuotas ────────────────────────────────────────────────────
    odds_df = get_odds(games_df=games_df)
    results["odds"] = odds_df

    # ══════════════════════════════════════════════════════════════
    # FASE 2 – Base de datos
    # ══════════════════════════════════════════════════════════════
    logger.info("── FASE 2: Base de datos ────────────────────────────────")
    repo = None
    if not skip_db:
        repo = _get_repo()
        if repo:
            repo.upsert_teams(teams_df)
            repo.upsert_team_stats(team_stats_df)
            repo.upsert_games(games_df)
            repo.upsert_odds(odds_df)
            logger.info("Datos de ingesta persistidos en base de datos")
        else:
            logger.warning("Base de datos no disponible — continuando sin persistencia")
    else:
        logger.info("--skip-db activo: omitiendo escritura en BD")

    if skip_predict:
        logger.info("--skip-predict activo: omitiendo fases 3-6")
        _print_summary(results)
        if output == "csv":
            _export_to_csv(results)
        return results

    # ══════════════════════════════════════════════════════════════
    # FASE 3 – Feature Engineering
    # ══════════════════════════════════════════════════════════════
    logger.info("── FASE 3: Feature Engineering ──────────────────────────")
    feature_df = build_features(games_df, team_stats_df)
    results["features"] = feature_df

    if feature_df.empty:
        logger.warning("Sin partidos para procesar — fases 4, 5 y 6 omitidas")
        _print_summary(results)
        if output == "csv":
            _export_to_csv(results)
        return results

    # ══════════════════════════════════════════════════════════════
    # FASE 4 – Predicciones
    # ══════════════════════════════════════════════════════════════
    logger.info("── FASE 4: Predicciones ─────────────────────────────────")
    predictions_df = predict(feature_df)
    results["predictions"] = predictions_df

    # ══════════════════════════════════════════════════════════════
    # FASE 5 – Detección de valor
    # ══════════════════════════════════════════════════════════════
    logger.info("── FASE 5: Detección de value bets ──────────────────────")
    value_bets_df = detect_value_bets(predictions_df, odds_df)
    results["value_bets"] = value_bets_df

    # Imprimir reporte en consola/log
    report = format_value_bets_report(value_bets_df)
    logger.info("\n%s", report)

    # ══════════════════════════════════════════════════════════════
    # FASE 6 (persistencia) – Guardar predicciones y value bets
    # ══════════════════════════════════════════════════════════════
    if repo:
        logger.info("── FASE 6: Persistencia de resultados ───────────────────")
        repo.upsert_predictions(predictions_df)
        repo.upsert_value_bets(value_bets_df)
        repo.close()
        logger.info("Predicciones y value bets guardadas en base de datos")

    # ── Resumen final ─────────────────────────────────────────────
    _print_summary(results)

    if output == "csv":
        _export_to_csv(results)

    logger.info("=" * 62)
    logger.info("Pipeline completado exitosamente.")
    logger.info("=" * 62)
    return results


def _print_summary(results: dict) -> None:
    logger.info("── Resumen ──────────────────────────────────────────────")
    for key, df in results.items():
        rows = len(df) if df is not None and not df.empty else 0
        logger.info("  %-14s: %d filas", key, rows)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Betplay – NBA Prediction Pipeline (Fases 1–6)"
    )
    parser.add_argument(
        "--date",
        default=date.today().strftime("%Y-%m-%d"),
        help="Fecha de partidos (YYYY-MM-DD). Por defecto: hoy.",
    )
    parser.add_argument(
        "--season",
        default=NBA_SEASON,
        help=f"Temporada NBA (e.g. '2024-25'). Por defecto: {NBA_SEASON}.",
    )
    parser.add_argument(
        "--output",
        choices=["csv", "none"],
        default="none",
        help="'csv' exporta todos los DataFrames a /output.",
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Omite la escritura en base de datos (útil para desarrollo).",
    )
    parser.add_argument(
        "--skip-predict",
        action="store_true",
        help="Ejecuta solo ingesta + DB, sin predicciones ni value bets.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_pipeline(
        game_date=args.date,
        season=args.season,
        output=args.output,
        skip_db=args.skip_db,
        skip_predict=args.skip_predict,
    )
