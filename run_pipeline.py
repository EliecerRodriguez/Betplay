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
from sports.nba.ingestion.elo import apply_elos_to_games, load_current_elos
from sports.nba.ingestion.injuries_client import adjust_predictions, get_team_injury_impact
from sports.nba.ingestion.nba_client import get_daily_games, get_combined_team_stats, get_line_scores
from sports.nba.ingestion.odds_client import get_odds
from sports.nba.ingestion.recent_form import enrich_with_form, get_season_wpct
from sports.nba.ingestion.standings_client import enrich_with_standings
from sports.nba.ingestion.travel_client import enrich_with_travel
from sports.nba.model.monte_carlo import enrich_predictions_with_mc
from sports.nba.model.predictor import predict
from sports.nba.model.value_detector import detect_value_bets
from sports.nba.processing.features import build_features, clean_team_stats
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
        from sports.nba.ingestion.nba_client import get_team_stats
        team_stats_df = clean_team_stats(get_team_stats(NBA_SEASON))

    # 3. Forma reciente
    logger.info("Enriqueciendo con forma reciente …")
    try:
        games_enriched = enrich_with_form(games_df, NBA_SEASON, n=5)
    except Exception as exc:
        logger.warning("enrich_with_form falló (%s) — sin forma reciente", exc)
        games_enriched = games_df

    # 3b. Override point-in-time: ajusta w_pct en team_stats_df con el acumulado
    #     real de temporada hasta hoy, usando el caché de game log de enrich_with_form.
    #     Esto elimina la asimetría entre entrenamiento (rolling, punto-en-el-tiempo)
    #     y producción (stats de API que pueden ser de toda la temporada completa).
    if not team_stats_df.empty and not games_enriched.empty:
        try:
            all_team_ids: set[int] = set()
            for col in ("home_team_id", "visitor_team_id"):
                if col in games_enriched.columns:
                    all_team_ids.update(games_enriched[col].dropna().astype(int).tolist())
            pit_date = date.today()
            patched = 0
            for tid in all_team_ids:
                wpct = get_season_wpct(tid, pit_date, NBA_SEASON)
                if wpct is None:
                    continue
                mask = team_stats_df["team_id"].astype(int) == tid
                if mask.any():
                    team_stats_df.loc[mask, "w_pct"] = wpct
                    patched += 1
            logger.info("Point-in-time w_pct aplicado: %d/%d equipos actualizados", patched, len(all_team_ids))
        except Exception as exc:
            logger.warning("Point-in-time w_pct override falló (%s) — usando stats de API", exc)

    # 4. Viaje y jet lag
    logger.info("Enriqueciendo con datos de viaje y jet lag …")
    try:
        games_enriched = enrich_with_travel(games_enriched, NBA_SEASON)
    except Exception as exc:
        logger.warning("enrich_with_travel falló (%s) — sin features de viaje", exc)

    # 4b. Contexto de temporada (clasificación)
    logger.info("Enriqueciendo con contexto de clasificación …")
    try:
        games_enriched = enrich_with_standings(games_enriched, NBA_SEASON)
    except Exception as exc:
        logger.warning("enrich_with_standings falló (%s) — sin features de standings", exc)

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

    # 7. Predicciones
    logger.info("Generando predicciones …")
    predictions_df = predict(feature_df) if not feature_df.empty else pd.DataFrame()

    if not predictions_df.empty:
        predictions_df["fetch_date"] = date_str
        try:
            predictions_df = adjust_predictions(predictions_df, games_df, season=NBA_SEASON)
        except Exception as exc:
            logger.warning("adjust_predictions falló: %s", exc)

        # 7b. Monte Carlo — enriquece predicciones con simulaciones
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


def _fetch_pending_line_scores(date_str: str) -> None:
    """
    Descarga y guarda en line_scores.csv los resultados reales de partidos pasados
    que aún no están registrados. Consulta las fechas del bet_journal que no tienen
    línea en line_scores.csv todavía, más el día anterior a date_str.
    Se ejecuta sin fallar aunque la API no esté disponible.
    """
    from datetime import timedelta

    journal_path = os.path.join(OUTPUT_DIR, "bet_journal.csv")
    ls_path      = os.path.join(OUTPUT_DIR, "line_scores.csv")
    today        = date.fromisoformat(date_str)

    # Siempre intentar el día anterior (los partidos de ayer ya terminaron)
    dates_to_fetch: set[str] = {(today - timedelta(days=1)).isoformat()}

    # Añadir todas las fechas pasadas del journal que puedan no tener resultados
    if os.path.exists(journal_path):
        try:
            jdf = pd.read_csv(journal_path, dtype=str)
            for d in jdf["game_date"].dropna().unique():
                d_clean = str(d)[:10]
                if d_clean < date_str:
                    dates_to_fetch.add(d_clean)
        except Exception as exc:
            logger.warning("No se pudo leer bet_journal para detectar fechas pendientes: %s", exc)

    # Detectar qué fechas ya tienen datos en line_scores.csv
    existing_dates: set[str] = set()
    if os.path.exists(ls_path):
        try:
            ls_existing = pd.read_csv(ls_path, usecols=["fetch_date"], dtype=str)
            existing_dates = set(ls_existing["fetch_date"].dropna().str[:10].unique())
        except Exception as exc:
            logger.warning("No se pudo leer line_scores.csv para verificar fechas: %s", exc)

    missing = sorted(dates_to_fetch - existing_dates)
    if not missing:
        logger.info("Line scores ya actualizados — nada que descargar.")
        return

    logger.info("Descargando line scores para %d fecha(s) pendiente(s): %s", len(missing), missing)
    for fetch_date in missing:
        try:
            ls_df = get_line_scores(fetch_date)
            if ls_df.empty:
                logger.info("Sin line scores para %s (sin partidos ese día o sin resultados aún)", fetch_date)
                continue
            _upsert_csv(ls_path, ls_df, ["game_id", "team_id"])
            logger.info("  ✓ Line scores guardados para %s (%d filas)", fetch_date, len(ls_df))
        except Exception as exc:
            logger.warning("Error descargando line scores para %s: %s", fetch_date, exc)


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


def save_to_supabase(data: dict[str, pd.DataFrame]) -> None:
    """
    Sincroniza todos los DataFrames del pipeline con Supabase/PostgreSQL.

    Se llama despues de save_to_csv() en el flujo de produccion.
    Falla de forma silenciosa para no interrumpir el pipeline si la BD no esta
    disponible (red caida, variable DATABASE_URL no configurada, etc.).
    """
    try:
        from config.settings import DATABASE_URL
        if not DATABASE_URL:
            logger.debug("DATABASE_URL no configurado — omitiendo escritura en Supabase")
            return
    except Exception:
        return

    try:
        from sports.nba.database.repository import DatabaseRepository
        repo = DatabaseRepository()

        if not data.get("games", pd.DataFrame()).empty:
            repo.upsert_games(data["games"])

        if not data.get("predictions", pd.DataFrame()).empty:
            repo.upsert_predictions(data["predictions"])

        if not data.get("odds", pd.DataFrame()).empty:
            repo.upsert_odds(data["odds"])

        if not data.get("value_bets", pd.DataFrame()).empty:
            repo.upsert_value_bets(data["value_bets"])

        repo.close()
        logger.info("Supabase sincronizado correctamente")
    except Exception as exc:
        logger.warning("No se pudo sincronizar con Supabase (no critico): %s", exc)

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
    save_to_supabase(data)

    # Descargar resultados reales de partidos pasados para alimentar el módulo de Resultados
    try:
        _fetch_pending_line_scores(date_str)
    except Exception as exc:
        logger.warning("_fetch_pending_line_scores falló (no crítico): %s", exc)

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
