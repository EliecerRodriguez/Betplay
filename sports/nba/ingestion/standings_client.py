"""
Contexto de temporada / motivación por posición en la clasificación.

Obtiene la tabla de clasificación actual (LeagueStandingsV3) y agrega
features de contexto competitivo a los partidos del día:

  - playoff_seed        : posición en la conferencia (1-8=directo, 9-10=Play-In, 11+=fuera)
  - games_back          : juegos de diferencia con el líder de conferencia (0.0 para el líder)
  - clinched_playoff    : 1 si ya tiene asegurada la clasificación postseason
  - eliminated          : 1 si ya está matemáticamente eliminado

Features derivadas (por pares home/visitor):
  - seed_diff           : visitor_seed - home_seed (>0 = local mejor clasificado)
  - clinched_diff       : home_clinched - visitor_clinched (asimetría de motivación)

Fuente: nba_api LeagueStandingsV3 (sin API key, sin costo)
Cache: 6 horas — no cambia durante el día de partidos
"""
from __future__ import annotations

import time
from typing import Dict, Optional

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

_CACHE_TTL = 6 * 3600   # 6 horas
_DELAY     = 0.6         # pausa para el rate-limit de nba_api

_standings_cache: dict = {}  # {"data": {team_id: dict}, "ts": float}


def _fetch_standings(season: str) -> Dict[int, dict]:
    """
    Descarga LeagueStandingsV3 y devuelve dict indexado por TeamID.
    Cachea el resultado 6 horas.
    """
    global _standings_cache
    cache_key = f"standings_{season}"
    now = time.time()

    cached = _standings_cache.get(cache_key)
    if cached and (now - cached["ts"]) < _CACHE_TTL:
        return cached["data"]

    try:
        from nba_api.stats.endpoints import leaguestandingsv3
        time.sleep(_DELAY)
        standings = leaguestandingsv3.LeagueStandingsV3(season=season, timeout=30)
        df = standings.get_data_frames()[0]
    except Exception as exc:
        logger.warning("standings_client: fallo al descargar standings: %s", exc)
        return _standings_cache.get(cache_key, {}).get("data", {})

    result: Dict[int, dict] = {}
    for _, row in df.iterrows():
        team_id = int(row["TeamID"])

        # EliminatedConference es NaN cuando no está eliminado, 1.0 si lo está
        raw_elim = row.get("EliminatedConference", None)
        eliminated = 1 if (raw_elim is not None and str(raw_elim) not in ("nan", "None", "") and float(raw_elim) == 1) else 0

        result[team_id] = {
            "playoff_seed":     int(row.get("PlayoffRank", 15)),
            "games_back":       float(row.get("ConferenceGamesBack", 0.0) or 0.0),
            "clinched_playoff": int(row.get("ClinchedPostSeason", 0) or 0),
            "eliminated":       eliminated,
        }

    _standings_cache[cache_key] = {"data": result, "ts": now}
    logger.info(
        "standings_client: standings cargados para %s — %d equipos",
        season, len(result),
    )
    return result


def get_team_standings(team_id: int, season: str = "2025-26") -> dict:
    """
    Devuelve las métricas de standings de un equipo.

    Returns:
        {
          "playoff_seed":     int   — 1–15 dentro de la conferencia
          "games_back":       float — juegos de diferencia con el líder (0.0 = líder)
          "clinched_playoff": int   — 1 si ya clasificado, 0 si no
          "eliminated":       int   — 1 si ya eliminado, 0 si no
        }
    """
    standings = _fetch_standings(season)
    return standings.get(
        team_id,
        {"playoff_seed": 8, "games_back": 0.0, "clinched_playoff": 0, "eliminated": 0},
    )


def enrich_with_standings(
    games_df: pd.DataFrame,
    season: str = "2025-26",
) -> pd.DataFrame:
    """
    Agrega columnas de clasificación a un DataFrame de partidos.

    Espera columnas: game_id, home_team_id, visitor_team_id
    Agrega columnas con prefijo home_/visitor_:
      home_playoff_seed, visitor_playoff_seed,
      home_games_back,  visitor_games_back,
      home_clinched_playoff, visitor_clinched_playoff,
      home_eliminated,  visitor_eliminated

    Args:
        games_df: DataFrame de partidos (de get_daily_games o histórico).
        season:   Temporada NBA ('2025-26').

    Returns:
        DataFrame enriquecido.
    """
    if games_df.empty:
        return games_df

    standings = _fetch_standings(season)
    if not standings:
        logger.warning("enrich_with_standings: sin datos de standings — se omite")
        return games_df

    df = games_df.copy()

    _default = {"playoff_seed": 8, "games_back": 0.0, "clinched_playoff": 0, "eliminated": 0}

    home_records:    list[dict] = []
    visitor_records: list[dict] = []

    for _, row in df.iterrows():
        home_id    = int(row.get("home_team_id",    0) or 0)
        visitor_id = int(row.get("visitor_team_id", 0) or 0)
        home_records.append(standings.get(home_id,    _default))
        visitor_records.append(standings.get(visitor_id, _default))

    home_df    = pd.DataFrame(home_records).add_prefix("home_")
    visitor_df = pd.DataFrame(visitor_records).add_prefix("visitor_")

    df = pd.concat([df.reset_index(drop=True), home_df, visitor_df], axis=1)

    logger.info(
        "enrich_with_standings: %d partidos enriquecidos con contexto de clasificación (%s)",
        len(df), season,
    )
    return df
