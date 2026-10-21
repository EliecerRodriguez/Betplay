"""
Cliente NBA para la FASE 1 – Data Ingestion.

Proporciona tres funciones principales:
  - get_daily_games(game_date)  → DataFrame con partidos de un día
  - get_team_stats(season)      → DataFrame con estadísticas agregadas por equipo

Usa nba_api (sin scraping). Respeta el rate‑limit con un delay configurable.
"""
import json
import os
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

# ── Headers para stats.nba.com (necesario en IPs de nube / GitHub Actions) ──
_raw_headers = os.getenv("NBA_DATA_HEADERS", "")
if _raw_headers:
    try:
        from nba_api.library.http import NBAStatsHTTP
        NBAStatsHTTP.headers = json.loads(_raw_headers)
        logger.info("nba_api: headers custom aplicados desde NBA_DATA_HEADERS")
    except Exception as _exc:
        logger.debug("nba_api: no se pudieron aplicar headers custom: %s", _exc)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sleep() -> None:
    """Pausa entre llamadas para respetar el rate‑limit de nba_api."""
    time.sleep(NBA_API_DELAY)


def _retry(fn, retries: int = 3, base_delay: float = 5.0, timeout: int = 120) -> "pd.DataFrame":
    """
    Ejecuta fn() con reintentos y backoff exponencial.

    stats.nba.com es inestable desde IPs de nube (GitHub Actions): a veces
    tarda >60s o devuelve timeout. Con reintentos + backoff se mitiga.

    Args:
        fn:          Callable que devuelve un DataFrame (o lanza excepción).
        retries:     Número de reintentos tras el primer intento.
        base_delay:  Segundos base para el backoff (5, 10, 20...).
        timeout:     Timeout HTTP en segundos por intento.

    Returns:
        DataFrame con el resultado de fn().
    """
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "nba_api: intento %d/%d falló (%s). Reintentando en %.0fs…",
                    attempt + 1, retries + 1, exc, delay,
                )
                time.sleep(delay)
    logger.error("nba_api: agotados %d intentos: %s", retries + 1, last_exc)
    return pd.DataFrame()


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
        # En ScoreboardV3, team_scores tiene 2 filas por partido: primera=home, segunda=visitor
        home_df    = team_scores.groupby("gameId").nth(0).reset_index()
        visitor_df = team_scores.groupby("gameId").nth(1).reset_index()

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
        if df.empty:
            return pd.DataFrame()
        # ScoreboardV3 usa camelCase; normalizar a snake_case con mapeo explícito
        col_map = {
            "gameId":  "game_id",
            "teamId":  "team_id",
            "score":   "pts",
        }
        df = df.rename(columns=col_map)
        df.columns = [c.lower() for c in df.columns]
        # Asegurar que pts es numérico (puede venir como string desde la API)
        if "pts" in df.columns:
            df["pts"] = pd.to_numeric(df["pts"], errors="coerce")
        # Eliminar filas sin resultado (partidos no jugados / sin marcador)
        if "pts" in df.columns:
            df = df[df["pts"].notna() & (df["pts"] > 0)].reset_index(drop=True)
        df["fetch_date"] = game_date
        return df

    except Exception as exc:
        logger.error("Error al obtener line scores de %s: %s", game_date, exc, exc_info=True)
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

    cache_path = os.path.join("data", "stats_cache", f"team_stats_{season}.csv")

    def _fetch() -> "pd.DataFrame":
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
        return df

    try:
        df = _retry(_fetch)
        if df.empty:
            # Fallback a caché local (útil en CI donde stats.nba.com bloquea IPs de nube)
            if os.path.exists(cache_path):
                logger.warning("API stats falló — usando caché local %s", cache_path)
                return pd.read_csv(cache_path)
            logger.error("No se pudieron obtener estadísticas de equipo (timeout persistente, sin caché)")
            return df
        # Guardar caché para CI (stats.nba.com bloquea IPs de nube)
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            df.to_csv(cache_path, index=False)
            logger.info("Stats cacheadas en %s", cache_path)
        except Exception as exc:
            logger.debug("No se pudo cachear stats: %s", exc)
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

    cache_path = os.path.join("data", "stats_cache", f"team_stats_advanced_{season}.csv")

    def _fetch() -> "pd.DataFrame":
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
        return df

    try:
        df = _retry(_fetch)
        if df.empty:
            # Fallback a caché local (útil en CI donde stats.nba.com bloquea IPs de nube)
            if os.path.exists(cache_path):
                logger.warning("API stats avanzadas falló — usando caché local %s", cache_path)
                return pd.read_csv(cache_path)
            logger.warning("No se pudieron obtener estadísticas avanzadas (timeout persistente, sin caché)")
            return df

        # Columnas relevantes para el modelo
        keep_cols = ["team_id", "off_rating", "def_rating", "net_rating",
                     "pace", "ts_pct", "efg_pct", "ast_pct",
                     "oreb_pct", "dreb_pct", "tm_tov_pct", "pie"]
        available = [c for c in keep_cols if c in df.columns]
        df = df[available].copy()

        # Guardar caché para CI (stats.nba.com bloquea IPs de nube)
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            df.to_csv(cache_path, index=False)
            logger.info("Stats avanzadas cacheadas en %s", cache_path)
        except Exception as exc:
            logger.debug("No se pudo cachear stats avanzadas: %s", exc)

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

