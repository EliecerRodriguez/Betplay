"""
Cliente NBA para la FASE 1 – Data Ingestion.

Proporciona tres funciones principales:
  - get_daily_games(game_date)  → DataFrame con partidos de un día
  - get_teams()                 → DataFrame con todos los equipos NBA
  - get_team_stats(season)      → DataFrame con estadísticas agregadas por equipo

Usa nba_api (sin scraping). Respeta el rate‑limit con un delay configurable.
"""
import time
from datetime import date, datetime
from typing import Optional

import pandas as pd
from nba_api.stats.endpoints import (
    LeagueDashTeamStats,
    ScoreboardV3,
)
from nba_api.stats.static import teams as nba_teams_static

from config.settings import NBA_API_DELAY, NBA_API_TIMEOUT, NBA_SEASON
from utils.logger import get_logger

logger = get_logger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sleep() -> None:
    """Pausa entre llamadas para respetar el rate‑limit de nba_api."""
    time.sleep(NBA_API_DELAY)


def _today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


# ── Partidos diarios ─────────────────────────────────────────────────────────

def get_daily_games(game_date: Optional[str] = None) -> pd.DataFrame:
    """
    Obtiene los partidos de una fecha específica.

    Args:
        game_date: Fecha en formato 'YYYY-MM-DD'. Si es None usa la fecha de hoy.

    Returns:
        DataFrame con columnas normalizadas de partidos.
        Vacío si no hay partidos ese día.
    """
    if game_date is None:
        game_date = _today_str()

    logger.info("Obteniendo partidos para la fecha: %s", game_date)

    try:
        # ScoreboardV3 es el reemplazo de V2 para la temporada 2025-26
        scoreboard = ScoreboardV3(
            game_date=game_date,
            league_id="00",
        )
        _sleep()

        dfs = scoreboard.get_data_frames()
        # DF[1] = game header  |  DF[2] = team scores
        if len(dfs) < 2 or dfs[1].empty:
            logger.warning("No hay partidos programados para %s", game_date)
            return pd.DataFrame()

        games_hdr = dfs[1].copy()   # gameId, gameStatusText, gameTimeUTC...
        team_scores = dfs[2].copy() # gameId, teamId, teamCity, teamName...

        # Separar home/visitor desde team_scores
        # team_scores tiene 2 filas por partido; primera=visitor, segunda=home
        visitor_df = team_scores.groupby("gameId").nth(0).reset_index()
        home_df    = team_scores.groupby("gameId").nth(1).reset_index()

        games_df = games_hdr.merge(visitor_df[["gameId","teamId"]].rename(
            columns={"teamId": "visitor_team_id"}), on="gameId", how="left")
        games_df = games_df.merge(home_df[["gameId","teamId"]].rename(
            columns={"teamId": "home_team_id"}), on="gameId", how="left")

        games_df = games_df.rename(columns={
            "gameId":          "game_id",
            "gameStatusText":  "game_status_text",
            "gameTimeUTC":     "game_time_utc",
        })
        games_df.columns = [c.lower() for c in games_df.columns]
        # La BD espera 'game_date' (no 'game_date_est')
        games_df["game_date"] = game_date
        games_df["fetch_date"] = game_date

        logger.info("Partidos encontrados: %d", len(games_df))
        return games_df

    except Exception as exc:
        logger.error("Error al obtener partidos de %s: %s", game_date, exc, exc_info=True)
        return pd.DataFrame()


def get_line_scores(game_date: Optional[str] = None) -> pd.DataFrame:
    """
    Obtiene los marcadores por equipo para todos los partidos de una fecha.

    Args:
        game_date: Fecha en formato 'YYYY-MM-DD'. Si es None usa hoy.

    Returns:
        DataFrame con marcadores (puntos por cuarto, totales, etc.).
    """
    if game_date is None:
        game_date = _today_str()

    logger.info("Obteniendo line scores para: %s", game_date)

    try:
        scoreboard = ScoreboardV3(
            game_date=game_date,
            league_id="00",
        )
        _sleep()
        dfs = scoreboard.get_data_frames()
        df = dfs[2].copy() if len(dfs) >= 3 else pd.DataFrame()
        # ScoreboardV3 usa camelCase; normalizar a snake_case con mapeo explícito
        col_map = {
            "gameId":  "game_id",
            "teamId":  "team_id",
            "score":   "pts",
        }
        df = df.rename(columns=col_map)
        df.columns = [c.lower() for c in df.columns]
        df["fetch_date"] = game_date
        return df

    except Exception as exc:
        logger.error("Error al obtener line scores de %s: %s", game_date, exc, exc_info=True)
        return pd.DataFrame()


# ── Equipos ──────────────────────────────────────────────────────────────────

def get_teams() -> pd.DataFrame:
    """
    Obtiene la lista estática de todos los equipos NBA activos.

    Returns:
        DataFrame con id, full_name, abbreviation, nickname, city, state, year_founded.
    """
    logger.info("Obteniendo lista de equipos NBA")

    try:
        all_teams = nba_teams_static.get_teams()
        df = pd.DataFrame(all_teams)
        df.columns = [c.lower() for c in df.columns]
        logger.info("Equipos cargados: %d", len(df))
        return df

    except Exception as exc:
        logger.error("Error al obtener equipos: %s", exc, exc_info=True)
        return pd.DataFrame()


# ── Estadísticas de equipo ────────────────────────────────────────────────────

def get_team_stats(season: Optional[str] = None) -> pd.DataFrame:
    """
    Obtiene estadísticas agregadas por equipo para la temporada indicada.
    Usa LeagueDashTeamStats, que devuelve promedios por partido.

    Args:
        season: Temporada en formato '2024-25'. Si es None usa NBA_SEASON del config.

    Returns:
        DataFrame con estadísticas ofensivas y defensivas por equipo.
    """
    if season is None:
        season = NBA_SEASON

    logger.info("Obteniendo estadísticas de equipo para la temporada: %s", season)

    try:
        stats = LeagueDashTeamStats(
            season=season,
            per_mode_detailed="PerGame",   # promedios por partido
            timeout=NBA_API_TIMEOUT,
        )
        _sleep()

        df: pd.DataFrame = stats.league_dash_team_stats.get_data_frame()
        df.columns = [c.lower() for c in df.columns]
        df["season"] = season
        df["fetch_date"] = _today_str()

        logger.info("Estadísticas obtenidas para %d equipos", len(df))
        return df

    except Exception as exc:
        logger.error("Error al obtener estadísticas de equipo: %s", exc, exc_info=True)
        return pd.DataFrame()


def get_team_advanced_stats(season: Optional[str] = None) -> pd.DataFrame:
    """
    Obtiene estadísticas avanzadas (pace-adjusted) por equipo.

    Usa LeagueDashTeamStats con measure_type="Advanced", que devuelve métricas
    no disponibles en el modo PerGame estándar:
      - off_rating  (ORTG): puntos anotados por 100 posesiones
      - def_rating  (DRTG): puntos encajados por 100 posesiones (menor = mejor)
      - net_rating        : ORTG - DRTG (dominio neto pace-adjusted)
      - pace              : posesiones por 48 minutos
      - ts_pct            : True Shooting % = PTS / (2 * (FGA + 0.44*FTA))
      - efg_pct           : Effective FG% = (FGM + 0.5*FG3M) / FGA
      - ast_pct           : % de canastas con asistencia
      - oreb_pct          : % de rebotes ofensivos capturados
      - dreb_pct          : % de rebotes defensivos capturados

    Args:
        season: Temporada '2024-25'. Si None usa NBA_SEASON.

    Returns:
        DataFrame con team_id y estadísticas avanzadas.
        Columnas clave: team_id, off_rating, def_rating, net_rating,
                        pace, ts_pct, efg_pct, ast_pct, oreb_pct, dreb_pct
    """
    if season is None:
        season = NBA_SEASON

    logger.info("Obteniendo estadísticas avanzadas para la temporada: %s", season)

    try:
        stats = LeagueDashTeamStats(
            season=season,
            measure_type_detailed_defense="Advanced",
            per_mode_detailed="PerGame",
            timeout=NBA_API_TIMEOUT,
        )
        _sleep()

        df: pd.DataFrame = stats.league_dash_team_stats.get_data_frame()
        df.columns = [c.lower() for c in df.columns]
        df["season"] = season
        df["fetch_date"] = _today_str()

        # Columnas relevantes para el modelo
        keep_cols = ["team_id", "off_rating", "def_rating", "net_rating",
                     "pace", "ts_pct", "efg_pct", "ast_pct",
                     "oreb_pct", "dreb_pct", "tm_tov_pct", "pie"]
        available = [c for c in keep_cols if c in df.columns]
        df = df[available].copy()

        logger.info("Estadísticas avanzadas obtenidas para %d equipos (%d métricas)",
                    len(df), len(available) - 1)
        return df

    except Exception as exc:
        logger.warning("Error al obtener estadísticas avanzadas: %s — continuando sin ellas", exc)
        return pd.DataFrame()


def get_combined_team_stats(season: Optional[str] = None) -> pd.DataFrame:
    """
    Combina estadísticas PerGame + Advanced en un solo DataFrame.

    Returns:
        DataFrame con todas las métricas básicas Y avanzadas por equipo.
    """
    basic    = get_team_stats(season)
    advanced = get_team_advanced_stats(season)

    if advanced.empty or basic.empty:
        return basic

    # Merge por team_id (advanced tiene columnas con nombres distintos: off_rating, etc.)
    merged = basic.merge(advanced, on="team_id", how="left", suffixes=("", "_adv"))
    logger.info("get_combined_team_stats: %d equipos | %d columnas totales",
                len(merged), len(merged.columns))
    return merged


# ── Resumen de ingesta ────────────────────────────────────────────────────────

def ingest_all(game_date: Optional[str] = None, season: Optional[str] = None) -> dict:
    """
    Ejecuta la ingesta completa de NBA para una fecha y temporada.

    Args:
        game_date: Fecha a consultar (por defecto hoy).
        season:    Temporada (por defecto NBA_SEASON del config).

    Returns:
        Diccionario con DataFrames bajo las claves:
          'games', 'line_scores', 'teams', 'team_stats'
    """
    logger.info("=== Iniciando ingesta NBA ===")

    result = {
        "games":       get_daily_games(game_date),
        "line_scores": get_line_scores(game_date),
        "teams":       get_teams(),
        "team_stats":  get_team_stats(season),
    }

    for key, df in result.items():
        logger.info("%-12s → %d filas", key, len(df))

    logger.info("=== Ingesta NBA completada ===")
    return result
