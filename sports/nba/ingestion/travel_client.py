"""
Datos de viaje y jet lag para equipos NBA.

Calcula el cambio de zona horaria que experimenta cada equipo entre
su último partido y el partido actual. El jet lag es uno de los factores
contextuales más ignorados por el mercado de apuestas.

Evidencia empírica:
  - Equipos cruzando 3 husos horarios tienen ~2-3% menos win probability
  - El efecto se amplifica cuando se combina con back-to-back
  - Referencia: Nutting (2010 NBA travel study), Steenland & Bhatt (2008)

Zonas horarias estáticas (UTC standard; el diferencial relativo es lo que
importa para el modelo):
  ET = -5  |  CT = -6  |  MT = -7  |  PT = -8

Funciones públicas:
  - get_team_prev_location_tz(team_id, game_date, season) → int (UTC offset)
  - enrich_with_travel(games_df, season) → games_df con columnas de viaje
"""
from __future__ import annotations

from datetime import date
from typing import Dict, Optional

import pandas as pd

from sports.nba.ingestion.recent_form import _get_team_game_log
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Mapa equipo (nba_api team_id) → UTC offset standard time ─────────────────
_TEAM_UTC: Dict[int, int] = {
    1610612737: -5,  # Atlanta Hawks          ET
    1610612738: -5,  # Boston Celtics         ET
    1610612751: -5,  # Brooklyn Nets          ET
    1610612766: -5,  # Charlotte Hornets      ET
    1610612741: -6,  # Chicago Bulls          CT
    1610612739: -5,  # Cleveland Cavaliers    ET
    1610612742: -6,  # Dallas Mavericks       CT
    1610612743: -7,  # Denver Nuggets         MT
    1610612765: -5,  # Detroit Pistons        ET
    1610612744: -8,  # Golden State Warriors  PT
    1610612745: -6,  # Houston Rockets        CT
    1610612754: -5,  # Indiana Pacers         ET
    1610612746: -8,  # LA Clippers            PT
    1610612747: -8,  # Los Angeles Lakers     PT
    1610612763: -6,  # Memphis Grizzlies      CT
    1610612748: -5,  # Miami Heat             ET
    1610612749: -6,  # Milwaukee Bucks        CT
    1610612750: -6,  # Minnesota Timberwolves CT
    1610612740: -6,  # New Orleans Pelicans   CT
    1610612752: -5,  # New York Knicks        ET
    1610612760: -6,  # Oklahoma City Thunder  CT
    1610612753: -5,  # Orlando Magic          ET
    1610612755: -5,  # Philadelphia 76ers     ET
    1610612756: -7,  # Phoenix Suns           MT (AZ - no DST)
    1610612757: -8,  # Portland Trail Blazers PT
    1610612758: -8,  # Sacramento Kings       PT
    1610612759: -6,  # San Antonio Spurs      CT
    1610612761: -5,  # Toronto Raptors        ET
    1610612762: -7,  # Utah Jazz              MT
    1610612764: -5,  # Washington Wizards     ET
}

# Mapa abreviatura equipo → UTC offset (para parsear campo MATCHUP)
_ABBREV_UTC: Dict[str, int] = {
    "ATL": -5, "BOS": -5, "BKN": -5, "CHA": -5,
    "CHI": -6, "CLE": -5, "DAL": -6, "DEN": -7,
    "DET": -5, "GSW": -8, "HOU": -6, "IND": -5,
    "LAC": -8, "LAL": -8, "MEM": -6, "MIA": -5,
    "MIL": -6, "MIN": -6, "NOP": -6, "NYK": -5,
    "OKC": -6, "ORL": -5, "PHI": -5, "PHX": -7,
    "POR": -8, "SAC": -8, "SAS": -6, "TOR": -5,
    "UTA": -7, "WAS": -5,
}

_DEFAULT_UTC = -6  # Central time como fallback neutro


def _parse_away_opponent_abbrev(matchup: str) -> Optional[str]:
    """
    Devuelve la abreviatura del equipo local del último partido cuando el
    equipo viajó como visitante.

    Formato MATCHUP en nba_api:
      'LAL vs. GSW' → LAL jugó en casa
      'LAL @ GSW'   → LAL viajó a GSW (opponent = GSW)

    Retorna None si el partido fue en casa o no se puede parsear.
    """
    if not isinstance(matchup, str) or " @ " not in matchup:
        return None
    parts = matchup.split(" @ ")
    return parts[1].strip() if len(parts) == 2 else None


def get_team_prev_location_tz(
    team_id: int,
    game_date: date,
    season: str,
) -> int:
    """
    Devuelve el UTC offset de la ciudad donde jugó el equipo en su ÚLTIMO
    partido antes de game_date.

    Si jugó en casa → su propio UTC offset.
    Si viajó (was away) → UTC offset de la ciudad del rival.
    Si no hay partido anterior → UTC offset de su ciudad (sin jet lag).

    Args:
        team_id:   ID del equipo (nba_api).
        game_date: Fecha del partido ACTUAL (para filtrar previos).
        season:    Temporada NBA (ej: '2024-25').

    Returns:
        UTC offset (entero) de la ubicación del partido anterior.
    """
    try:
        log_df = _get_team_game_log(team_id, season)
        if log_df.empty or "GAME_DATE" not in log_df.columns:
            return _TEAM_UTC.get(team_id, _DEFAULT_UTC)

        prev_games = log_df[log_df["GAME_DATE"] < game_date]
        if prev_games.empty:
            return _TEAM_UTC.get(team_id, _DEFAULT_UTC)

        last = prev_games.iloc[-1]
        matchup = str(last.get("MATCHUP", "")) if "MATCHUP" in last.index else ""

        opp_abbrev = _parse_away_opponent_abbrev(matchup)
        if opp_abbrev and opp_abbrev in _ABBREV_UTC:
            # Equipo viajó: estaba en la ciudad del rival
            return _ABBREV_UTC[opp_abbrev]
        else:
            # Equipo jugó en casa
            return _TEAM_UTC.get(team_id, _DEFAULT_UTC)

    except Exception as exc:
        logger.debug("get_team_prev_location_tz(%d, %s): %s", team_id, game_date, exc)
        return _TEAM_UTC.get(team_id, _DEFAULT_UTC)


def enrich_with_travel(
    games_df: pd.DataFrame,
    season: str,
) -> pd.DataFrame:
    """
    Añade columnas de viaje y jet lag a games_df.

    Columnas añadidas:
      home_tz_shift         — horas de desfase del equipo local desde su último partido
      visitor_tz_shift      — horas de desfase del equipo visitante
      home_crossed_tz       — zonas cruzadas con signo (neg = viajó al oeste)
      visitor_crossed_tz    — zonas cruzadas con signo para el visitante
      travel_tz_disadvantage — visitor_tz_shift - home_tz_shift (>0 = visitante más cansado)

    Args:
        games_df: DataFrame con columnas home_team_id, visitor_team_id, game_date.
        season:   Temporada NBA activa.

    Returns:
        games_df con columnas de viaje añadidas (nunca elimina filas).
    """
    if games_df.empty:
        return games_df

    df = games_df.copy()

    # Inicializar con ceros para garantizar columnas aunque falle la API
    for prefix in ("home", "visitor"):
        df[f"{prefix}_tz_shift"]       = 0.0
        df[f"{prefix}_crossed_tz"]     = 0.0
    df["travel_tz_disadvantage"] = 0.0

    for idx, row in df.iterrows():
        # Parsear game_date
        game_date = row.get("game_date") or row.get("game_date_est")
        if isinstance(game_date, str):
            try:
                from datetime import datetime as _dt
                game_date = _dt.strptime(game_date[:10], "%Y-%m-%d").date()
            except Exception:
                continue
        if game_date is None:
            continue

        home_id    = row.get("home_team_id")
        visitor_id = row.get("visitor_team_id")

        # UTC offset del partido ACTUAL para cada equipo
        # El equipo local juega en SU ciudad; el visitante viaja a esa ciudad.
        home_current_tz    = _TEAM_UTC.get(int(home_id),    _DEFAULT_UTC) if home_id else _DEFAULT_UTC
        visitor_current_tz = home_current_tz  # ambos juegan en la ciudad del local

        if home_id:
            try:
                prev_tz = get_team_prev_location_tz(int(home_id), game_date, season)
                df.at[idx, "home_tz_shift"]   = float(abs(prev_tz - home_current_tz))
                df.at[idx, "home_crossed_tz"] = float(home_current_tz - prev_tz)
            except Exception as exc:
                logger.debug("home travel enrichment error: %s", exc)

        if visitor_id:
            try:
                prev_tz = get_team_prev_location_tz(int(visitor_id), game_date, season)
                df.at[idx, "visitor_tz_shift"]   = float(abs(prev_tz - visitor_current_tz))
                df.at[idx, "visitor_crossed_tz"] = float(visitor_current_tz - prev_tz)
            except Exception as exc:
                logger.debug("visitor travel enrichment error: %s", exc)

    df["travel_tz_disadvantage"] = df["visitor_tz_shift"] - df["home_tz_shift"]

    enriched = (df["home_tz_shift"] > 0).sum() + (df["visitor_tz_shift"] > 0).sum()
    logger.info(
        "enrich_with_travel: %d partidos procesados, %d equipos con jet lag detectado",
        len(df), enriched,
    )
    return df
