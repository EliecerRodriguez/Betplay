"""
Cliente ESPN para Liga BetPlay (fútbol Colombia).

Obtiene:
  - Partidos del día (scoreboard): equipos, estado, forma reciente
  - Stats completas por partido (summary): tarjetas amarillas, córners,
    faltas, posesión, tiros — los datos clave para el modelo 50/50.

La API de ESPN es gratuita y sin clave: site.api.espn.com/apis/site/v2/sports/soccer/col.1

Fuentes de datos por partido (confirmado en 2026-10-21):
  - boxscore.teams[].statistics:  yellowCards, redCards, wonCorners,
    foulsCommitted, possessionPct, totalShots, shotsOnTarget, ...
  - competitors[].team.form:       forma reciente (W/D/L últimos 5)
  - standings:                     tabla de posiciones
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import requests

from sports.betplay.config.settings import (
    BETPLAY_LEAGUE,
    ESPN_BASE_URL,
    ESPN_DELAY,
    ESPN_HEADERS,
    ESPN_TIMEOUT,
)
from utils.logger import get_logger

logger = get_logger(__name__)


# ── Helpers HTTP ─────────────────────────────────────────────────────────────

def _get(url: str, params: Optional[dict] = None) -> Optional[dict]:
    """GET a ESPN API con retry simple. Devuelve JSON o None."""
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=ESPN_HEADERS, timeout=ESPN_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            logger.warning("ESPN HTTP %s para %s (intento %d)", r.status_code, url, attempt + 1)
        except Exception as exc:
            logger.warning("ESPN error %s (intento %d): %s", url, attempt + 1, exc)
        time.sleep(ESPN_DELAY * (attempt + 1))
    return None


def _today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


# ── Scoreboard (partidos de una fecha) ───────────────────────────────────────

def get_daily_games(game_date: Optional[str] = None) -> pd.DataFrame:
    """
    Obtiene los partidos de una fecha de la Liga BetPlay.

    Args:
        game_date: 'YYYY-MM-DD'. Si None usa hoy.

    Returns:
        DataFrame: game_id, game_date, home_team_id, away_team_id,
                   home_team, away_team, home_abbr, away_abbr, status,
                   home_form, away_form, home_record, away_record, home_points, away_points
    """
    game_date = game_date or _today_str()
    url = f"{ESPN_BASE_URL}/{BETPLAY_LEAGUE}/scoreboard"
    data = _get(url, params={"dates": game_date.replace("-", "")})
    if not data:
        logger.warning("ESPN scoreboard vacío para %s", game_date)
        return pd.DataFrame()

    rows = []
    for event in data.get("events", []):
        comp = (event.get("competitions") or [{}])[0]
        status = (comp.get("status") or {}).get("type", {}).get("name", "")
        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            continue

        by_side = {c.get("homeAway"): c for c in competitors}
        home_c, away_c = by_side.get("home"), by_side.get("away")
        if not home_c or not away_c:
            continue

        home_team = (home_c.get("team") or {}).get("displayName", "")
        away_team = (away_c.get("team") or {}).get("displayName", "")
        home_id = (home_c.get("team") or {}).get("id", "")
        away_id = (away_c.get("team") or {}).get("id", "")

        def _form(c: dict) -> str:
            return (c.get("team") or {}).get("form", "")

        def _record(c: dict) -> str:
            for r in c.get("records", []):
                if r.get("type") == "total":
                    return r.get("summary", "")
            return ""

        def _points(c: dict) -> Optional[int]:
            for r in c.get("records", []):
                if r.get("type") == "points":
                    try:
                        return int(r.get("displayValue", 0))
                    except (TypeError, ValueError):
                        return None
            return None

        rows.append({
            "game_id":       str(event.get("id", "")),
            "game_date":     game_date,
            "home_team_id":  home_id,
            "away_team_id":  away_id,
            "home_team":     home_team,
            "away_team":     away_team,
            "home_abbr":     (home_c.get("team") or {}).get("abbreviation", ""),
            "away_abbr":     (away_c.get("team") or {}).get("abbreviation", ""),
            "status":        status,
            "home_form":     _form(home_c),
            "away_form":     _form(away_c),
            "home_record":   _record(home_c),
            "away_record":   _record(away_c),
            "home_points":   _points(home_c),
            "away_points":   _points(away_c),
        })

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["game_id"])
    logger.info("ESPN: %d partidos Liga BetPlay para %s", len(df), game_date)
    return df


# ── Summary (stats completas de un partido) ──────────────────────────────────

def get_match_summary(event_id: str) -> Optional[dict]:
    """
    Obtiene las estadísticas completas de un partido (tarjetas, córners, etc.).

    Args:
        event_id: ID del evento (game_id).

    Returns:
        dict con:
          home/away: yellow_cards, red_cards, corners, fouls, possession,
                     shots, shots_on_target, passes, pass_pct, crosses, tackles
          home_team/away_team, status
        None si falla.
    """
    url = f"{ESPN_BASE_URL}/{BETPLAY_LEAGUE}/summary"
    data = _get(url, params={"event": event_id})
    if not data:
        return None

    boxscore = data.get("boxscore") or {}
    teams_stats = boxscore.get("teams") or []
    if len(teams_stats) < 2:
        return None

    def _stat(team_block: dict, name: str) -> Optional[float]:
        for s in team_block.get("statistics", []):
            if s.get("name") == name:
                try:
                    return float(s.get("displayValue", 0))
                except (TypeError, ValueError):
                    return None
        return None

    result = {
        "game_id": str(event_id),
        "status": (data.get("header") or {}).get("competitions", [{}])[0]
                  .get("status", {}).get("type", {}).get("name", ""),
    }

    # Forma reciente (últimos 5 partidos W/D/L) desde lastFiveGames
    last_five = data.get("lastFiveGames") or []
    for block in last_five:
        team = block.get("team") or {}
        side = block.get("displayOrder", 0)
        prefix = "home" if side == 1 else "away"
        events = block.get("events") or []
        # gameResult: W = win, L = loss, D = draw
        form = "".join(
            e.get("gameResult", "?")[:1]
            for e in events[:5]
            if e.get("gameResult")
        )
        if form:
            result[f"{prefix}_form_recent"] = form

    for block in teams_stats:
        team = block.get("team") or {}
        side = block.get("homeAway", "")
        prefix = "home" if side == "home" else "away"
        result[f"{prefix}_team"] = team.get("displayName", "")
        result[f"{prefix}_id"] = team.get("id", "")
        result[f"{prefix}_yellow_cards"] = _stat(block, "yellowCards")
        result[f"{prefix}_red_cards"] = _stat(block, "redCards")
        result[f"{prefix}_corners"] = _stat(block, "wonCorners")
        result[f"{prefix}_fouls"] = _stat(block, "foulsCommitted")
        result[f"{prefix}_possession"] = _stat(block, "possessionPct")
        result[f"{prefix}_shots"] = _stat(block, "totalShots")
        result[f"{prefix}_shots_on_target"] = _stat(block, "shotsOnTarget")
        result[f"{prefix}_passes"] = _stat(block, "totalPasses")
        result[f"{prefix}_pass_pct"] = _stat(block, "passPct")
        result[f"{prefix}_crosses"] = _stat(block, "totalCrosses")
        result[f"{prefix}_tackles"] = _stat(block, "totalTackles")

    # Total de tarjetas y córners del partido (las variables objetivo del modelo)
    h_yellow = result.get("home_yellow_cards")
    a_yellow = result.get("away_yellow_cards")
    h_corners = result.get("home_corners")
    a_corners = result.get("away_corners")
    if h_yellow is not None and a_yellow is not None:
        result["total_yellow_cards"] = h_yellow + a_yellow
    if h_corners is not None and a_corners is not None:
        result["total_corners"] = h_corners + a_corners

    logger.info(
        "ESPN summary %s: tarjetas=%s córners=%s",
        event_id,
        result.get("total_yellow_cards"),
        result.get("total_corners"),
    )
    return result
