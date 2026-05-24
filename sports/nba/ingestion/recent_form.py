"""
Módulo de forma reciente y rest days para feature engineering.

Calcula por cada equipo, antes de un partido dado:
  - W% en los últimos N partidos (forma reciente)
  - Puntos promedio anotados últimos N
  - Puntos promedio recibidos últimos N
  - Días de descanso desde el último partido (rest days)
  - Si jugó el día anterior (back-to-back)

Fuente: nba_api TeamGameLog — sin costos, sin API key.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Dict, Optional

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

_DELAY = 0.6   # segundos entre llamadas NBA API
_GAME_LOG_CACHE: dict = {}   # key: (team_id, season) -> {"df": DataFrame, "ts": float}
_GAME_LOG_TTL   = 6 * 3600  # 6 horas — suficiente para un día de partidos


def _get_team_game_log(team_id: int, season: str) -> pd.DataFrame:
    """Descarga el historial de partidos de un equipo en la temporada. Cacheado 6h."""
    key = (team_id, season)
    cached = _GAME_LOG_CACHE.get(key)
    if cached and (time.time() - cached["ts"]) < _GAME_LOG_TTL:
        return cached["df"]
    try:
        from nba_api.stats.endpoints import teamgamelog
        time.sleep(_DELAY)
        tgl = teamgamelog.TeamGameLog(team_id=team_id, season=season, timeout=30)
        df = tgl.get_data_frames()[0]
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns=str.upper)
        # Columnas: Game_ID, GAME_DATE, MATCHUP, WL, PTS, ...
        date_col = "Game_Date" if "Game_Date" in df.columns else "GAME_DATE"
        df["GAME_DATE"] = pd.to_datetime(df[date_col], format="%b %d, %Y", errors="coerce")
        if df["GAME_DATE"].isna().all():
            df["GAME_DATE"] = pd.to_datetime(df[date_col], errors="coerce")
        df["GAME_DATE"] = df["GAME_DATE"].dt.date
        df["GAME_ID"]   = df["Game_ID"].astype(str) if "Game_ID" in df.columns else df["GAME_ID"].astype(str)
        df["PTS_SCORED"] = pd.to_numeric(df.get("PTS", pd.Series(dtype=float)), errors="coerce")
        # Puntos concedidos: PTS_OPP si existe, sino calculamos desde PLUS_MINUS
        if "OPP_PTS" in df.columns:
            df["PTS_ALLOWED"] = pd.to_numeric(df["OPP_PTS"], errors="coerce")
        elif "PLUS_MINUS" in df.columns and "PTS" in df.columns:
            df["PTS_ALLOWED"] = df["PTS_SCORED"] - pd.to_numeric(df["PLUS_MINUS"], errors="coerce")
        else:
            df["PTS_ALLOWED"] = float("nan")
        df["WON"] = (df["WL"] == "W").astype(int) if "WL" in df.columns else float("nan")
        # Ordenar cronológicamente
        df = df.sort_values("GAME_DATE").reset_index(drop=True)
        extra_cols = [c for c in ["MATCHUP"] if c in df.columns]
        result = df[["GAME_ID", "GAME_DATE", "WON", "PTS_SCORED", "PTS_ALLOWED"] + extra_cols]
        _GAME_LOG_CACHE[key] = {"df": result, "ts": time.time()}
        return result
    except Exception as exc:
        logger.warning("_get_team_game_log(%d, %s): %s", team_id, season, exc)
        return pd.DataFrame()


def get_team_form(
    team_id: int,
    before_date: date,
    season: str,
    n: int = 5,
) -> Dict[str, float]:
    """
    Devuelve métricas de forma reciente del equipo ANTES de before_date.

    Returns dict con:
      - recent_wpct_{n}:        W% en últimos n partidos
      - recent_pts_scored_{n}:  Pts promedio anotados
      - recent_pts_allowed_{n}: Pts promedio recibidos
      - rest_days:              Días desde el último partido (cap 10)
      - is_b2b:                 1 si jugó ayer (back-to-back)
      - games_last_7d:          Partidos jugados en los últimos 7 días (densidad)
      - games_last_14d:         Partidos jugados en los últimos 14 días (densidad)
    """
    defaults = {
        f"recent_wpct_{n}":      0.5,
        f"recent_pts_scored_{n}":  100.0,
        f"recent_pts_allowed_{n}": 100.0,
        "rest_days":             3.0,
        "is_b2b":                0,
        "games_last_7d":         2,
        "games_last_14d":        4,
    }

    log = _get_team_game_log(team_id, season)
    if log.empty:
        return defaults

    # Solo partidos estrictamente antes de before_date
    past = log[log["GAME_DATE"] < before_date].copy()
    if past.empty:
        return defaults

    last_n = past.tail(n)

    wpct         = last_n["WON"].mean() if last_n["WON"].notna().any() else 0.5
    pts_scored   = last_n["PTS_SCORED"].mean() if last_n["PTS_SCORED"].notna().any() else 100.0
    pts_allowed  = last_n["PTS_ALLOWED"].mean() if last_n["PTS_ALLOWED"].notna().any() else 100.0

    last_date    = past["GAME_DATE"].iloc[-1]
    rest_days    = min((before_date - last_date).days, 10)
    is_b2b       = 1 if rest_days <= 1 else 0

    # ── Densidad de agenda (fatiga acumulada) ──────────────────────────────
    # Cuenta partidos en los últimos 7 y 14 días antes del partido actual.
    # Más juegos en poco tiempo = mayor fatiga física acumulada.
    cutoff_7d  = before_date - timedelta(days=7)
    cutoff_14d = before_date - timedelta(days=14)
    games_7d   = int((past["GAME_DATE"] >= cutoff_7d).sum())
    games_14d  = int((past["GAME_DATE"] >= cutoff_14d).sum())

    return {
        f"recent_wpct_{n}":       round(float(wpct), 4),
        f"recent_pts_scored_{n}": round(float(pts_scored), 2),
        f"recent_pts_allowed_{n}":round(float(pts_allowed), 2),
        "rest_days":              float(rest_days),
        "is_b2b":                 int(is_b2b),
        "games_last_7d":          games_7d,
        "games_last_14d":         games_14d,
    }


def get_season_wpct(team_id: int, before_date: date, season: str) -> Optional[float]:
    """
    Devuelve el win% acumulado de temporada hasta before_date.

    Usa el caché de TeamGameLog ya descargado por get_team_form — cero API calls extra.
    Devuelve None si hay menos de 5 partidos jugados (inicio de temporada,
    o si el game log no está disponible todavía).

    Propósito: hacer que la producción use el mismo w_pct punto-en-el-tiempo
    que el entrenamiento computa con _compute_rolling_stats_for_training().
    """
    log = _get_team_game_log(team_id, season)
    if log.empty:
        return None
    past = log[log["GAME_DATE"] < before_date]
    if len(past) < 5 or not past["WON"].notna().any():
        return None
    return round(float(past["WON"].mean()), 4)


def enrich_with_form(
    games_df: pd.DataFrame,
    season: str,
    n: int = 5,
) -> pd.DataFrame:
    """
    Agrega columnas de forma reciente a un DataFrame de partidos.

    Espera columnas: game_id, home_team_id, visitor_team_id, game_date
    Agrega columnas con prefijo home_/visitor_ para cada métrica.

    Args:
        games_df: DataFrame de partidos (de get_daily_games o _fetch_completed_games).
        season:   Temporada NBA ('2025-26').
        n:        Ventana de partidos recientes.

    Returns:
        DataFrame enriquecido.
    """
    if games_df.empty:
        return games_df

    df = games_df.copy()

    # Asegurar columna game_date como date
    if "game_date" not in df.columns:
        if "game_date_est" in df.columns:
            df["game_date"] = pd.to_datetime(df["game_date_est"], errors="coerce").dt.date
        else:
            logger.warning("enrich_with_form: sin columna game_date — usando fecha de hoy")
            from datetime import date as _date
            df["game_date"] = _date.today()

    home_records:    list[dict] = []
    visitor_records: list[dict] = []

    for _, row in df.iterrows():
        gdate = row["game_date"]
        if isinstance(gdate, str):
            gdate = datetime.strptime(gdate, "%Y-%m-%d").date()

        home_id    = int(row["home_team_id"])
        visitor_id = int(row["visitor_team_id"])

        h_form = get_team_form(home_id,    gdate, season, n)
        v_form = get_team_form(visitor_id, gdate, season, n)

        home_records.append(h_form)
        visitor_records.append(v_form)

    # Agregar como columnas con prefijo
    home_df    = pd.DataFrame(home_records).add_prefix("home_")
    visitor_df = pd.DataFrame(visitor_records).add_prefix("visitor_")

    df = pd.concat([df.reset_index(drop=True), home_df, visitor_df], axis=1)
    logger.info("enrich_with_form: %d partidos enriquecidos con forma reciente (n=%d)", len(df), n)
    return df
