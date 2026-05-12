"""
Sistema de ratings Elo para equipos NBA.

Elo es el predictor más potente a nivel de equipo en basketball. A diferencia
del win% de temporada, actualiza dinámicamente tras cada partido y pondera más
los resultados recientes.

Fórmula estándar FiveThirtyEight/NBA:
  expected_home = 1 / (1 + 10^((elo_visitor - (elo_home + HOME_ADV)) / 400))
  delta = K * (resultado - expected)

Parámetros calibrados para NBA:
  K = 20          (sensibilidad por partido)
  HOME_ADV = 100  (≈ +3.5% de win probability por jugar de local)
  REGR = 0.75     (regresión a 1500 al inicio de cada temporada)

Funciones públicas:
  enrich_with_elo(games_df)    → añade home_elo_pre, visitor_elo_pre, elo_diff, elo_home_win_prob
  get_current_elos(games_df)   → {team_id: elo_actual}
  save_current_elos(elos, path)
  load_current_elos(path)      → {team_id: elo}
"""
from __future__ import annotations

import json
import os
from typing import Dict, Optional, Tuple

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

# ── Constantes calibradas para NBA ───────────────────────────────────────────
ELO_BASE        = 1500.0   # Rating de partida para todos los equipos
ELO_K           = 20.0     # Factor K — cuánto mueve cada partido
ELO_HOME_ADV    = 100.0    # Ventaja de local en unidades Elo (~3.5% win prob)
ELO_SEASON_REGR = 0.75     # Regresión a la media entre temporadas
                            # new_elo = 0.75 * old_elo + 0.25 * 1500


# ── Fórmula Elo ───────────────────────────────────────────────────────────────

def _expected_home_win(home_elo: float, away_elo: float) -> float:
    """Probabilidad esperada de victoria local según Elo."""
    return 1.0 / (1.0 + 10.0 ** ((away_elo - (home_elo + ELO_HOME_ADV)) / 400.0))


def _update_elo(
    home_elo: float,
    away_elo: float,
    home_won: int,
) -> Tuple[float, float]:
    """
    Actualiza Elo de local y visitante tras un partido.

    Args:
        home_elo:  Elo del equipo local antes del partido.
        away_elo:  Elo del equipo visitante antes del partido.
        home_won:  1 si ganó el local, 0 si ganó el visitante.

    Returns:
        (nuevo_elo_local, nuevo_elo_visitante)
    """
    expected = _expected_home_win(home_elo, away_elo)
    delta    = ELO_K * (home_won - expected)
    return round(home_elo + delta, 2), round(away_elo - delta, 2)


def _date_to_season(d) -> str:
    """
    Convierte una fecha a temporada NBA (ej: 2020-10-15 → '2020-21').
    La temporada NBA empieza en octubre y termina en junio del siguiente año.
    """
    if isinstance(d, str):
        d = pd.to_datetime(d).date()
    elif hasattr(d, "to_pydatetime"):
        d = d.to_pydatetime().date()
    year  = d.year
    month = d.month
    if month >= 10:
        return f"{year}-{str(year + 1)[2:]}"
    return f"{year - 1}-{str(year)[2:]}"


# ── Función principal de enriquecimiento ──────────────────────────────────────

def enrich_with_elo(
    games_df: pd.DataFrame,
    initial_elos: Optional[Dict[int, float]] = None,
) -> pd.DataFrame:
    """
    Enriquece games_df con ratings Elo PRE-PARTIDO para cada equipo.

    El Elo PRE-PARTIDO es el rating que tenía cada equipo ANTES de jugarse
    el partido → es válido como feature de predicción (sin data leakage temporal).

    Los partidos se ordenan cronológicamente. Al cambiar de temporada, los Elos
    se regresan 25% hacia la media (1500) para reflejar la incertidumbre inicial.

    Args:
        games_df:     DataFrame con columnas:
                        home_team_id, visitor_team_id, home_win (1/0),
                        opcionalmente game_date.
        initial_elos: Dict {team_id: elo} de ratings de partida.
                      Si None, todos los equipos arrancan en ELO_BASE=1500.

    Returns:
        games_df con columnas añadidas:
          home_elo_pre      — Elo del local antes del partido
          visitor_elo_pre   — Elo del visitante antes del partido
          elo_diff          — home_elo_pre - visitor_elo_pre
          elo_home_win_prob — Win probability implícita del Elo para el local
    """
    df = games_df.copy()

    # Ordenar cronológicamente si hay columna de fecha
    date_col = None
    for col in ("game_date", "game_date_est", "fetch_date"):
        if col in df.columns:
            date_col = col
            break

    if date_col:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.sort_values(date_col).reset_index(drop=True)

    elos: Dict[int, float] = dict(initial_elos) if initial_elos else {}
    current_season: Optional[str] = None

    home_elo_list    = []
    visitor_elo_list = []

    for _, row in df.iterrows():
        home_id    = int(row["home_team_id"])
        visitor_id = int(row["visitor_team_id"])

        # Detectar cambio de temporada → aplicar regresión a la media
        if date_col and pd.notna(row[date_col]):
            season = _date_to_season(row[date_col])
            if season != current_season:
                if current_season is not None:
                    # Regresión: nueva_elo = 0.75 * elo + 0.25 * 1500
                    for tid in list(elos.keys()):
                        elos[tid] = round(
                            ELO_SEASON_REGR * elos[tid] + (1 - ELO_SEASON_REGR) * ELO_BASE,
                            2,
                        )
                    logger.debug(
                        "Elo: regresión aplicada al inicio de temporada %s (→ %d equipos)",
                        season, len(elos),
                    )
                current_season = season

        # Rating PRE-partido (equipos nuevos empiezan en ELO_BASE)
        home_elo_pre    = elos.get(home_id,    ELO_BASE)
        visitor_elo_pre = elos.get(visitor_id, ELO_BASE)

        home_elo_list.append(home_elo_pre)
        visitor_elo_list.append(visitor_elo_pre)

        # Actualizar Elo solo si el resultado es conocido (partido ya jugado)
        if "home_win" in row and pd.notna(row["home_win"]):
            new_home, new_vis = _update_elo(home_elo_pre, visitor_elo_pre, int(row["home_win"]))
            elos[home_id]    = new_home
            elos[visitor_id] = new_vis

    df["home_elo_pre"]      = home_elo_list
    df["visitor_elo_pre"]   = visitor_elo_list
    df["elo_diff"]          = df["home_elo_pre"] - df["visitor_elo_pre"]
    df["elo_home_win_prob"] = df.apply(
        lambda r: round(_expected_home_win(r["home_elo_pre"], r["visitor_elo_pre"]), 4),
        axis=1,
    )

    elo_range = (df["home_elo_pre"].min(), df["home_elo_pre"].max())
    logger.info(
        "enrich_with_elo: %d partidos | Elo range: [%.0f, %.0f] | temporadas: %s→%s",
        len(df),
        elo_range[0],
        elo_range[1],
        current_season or "?",
        current_season or "?",
    )
    return df


# ── Ratings actuales ──────────────────────────────────────────────────────────

def get_current_elos(games_df: pd.DataFrame) -> Dict[int, float]:
    """
    Procesa todos los partidos históricos y devuelve el Elo ACTUAL de cada equipo.

    Útil para inferencia en tiempo real: se aplica el Elo final a los partidos
    de hoy (que todavía no han ocurrido → el Elo pre-partido = Elo actual).

    Args:
        games_df: DataFrame con home_team_id, visitor_team_id, home_win, game_date.

    Returns:
        Dict {team_id: elo_actual}
    """
    if games_df.empty:
        logger.warning("get_current_elos: DataFrame vacío — devolviendo dict vacío")
        return {}

    elos: Dict[int, float] = {}
    df = games_df.copy()

    date_col = None
    for col in ("game_date", "game_date_est", "fetch_date"):
        if col in df.columns:
            date_col = col
            break

    if date_col:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.sort_values(date_col).reset_index(drop=True)

    current_season: Optional[str] = None

    for _, row in df.iterrows():
        home_id    = int(row["home_team_id"])
        visitor_id = int(row["visitor_team_id"])

        if date_col and pd.notna(row[date_col]):
            season = _date_to_season(row[date_col])
            if season != current_season:
                if current_season is not None:
                    for tid in list(elos.keys()):
                        elos[tid] = round(
                            ELO_SEASON_REGR * elos[tid] + (1 - ELO_SEASON_REGR) * ELO_BASE, 2
                        )
                current_season = season

        home_elo_pre    = elos.get(home_id,    ELO_BASE)
        visitor_elo_pre = elos.get(visitor_id, ELO_BASE)

        if "home_win" in row and pd.notna(row["home_win"]):
            new_home, new_vis = _update_elo(home_elo_pre, visitor_elo_pre, int(row["home_win"]))
            elos[home_id]    = new_home
            elos[visitor_id] = new_vis
        else:
            if home_id not in elos:
                elos[home_id] = home_elo_pre
            if visitor_id not in elos:
                elos[visitor_id] = visitor_elo_pre

    logger.info("get_current_elos: %d equipos con Elo calculado", len(elos))
    return elos


# ── Persistencia ──────────────────────────────────────────────────────────────

def save_current_elos(elos: Dict[int, float], path: str = "models/current_elos.json") -> None:
    """Guarda los Elos actuales en JSON para uso en producción."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # JSON requiere claves string
    serializable = {str(k): v for k, v in elos.items()}
    with open(path, "w") as f:
        json.dump(serializable, f, indent=2)
    logger.info("Elos guardados en %s (%d equipos)", path, len(elos))


def load_current_elos(path: str = "models/current_elos.json") -> Dict[int, float]:
    """
    Carga Elos desde JSON.

    Returns:
        Dict {team_id (int): elo (float)} o dict vacío si no existe el archivo.
    """
    if not os.path.exists(path):
        logger.warning("load_current_elos: archivo no encontrado en %s — usando ELO_BASE=1500", path)
        return {}
    with open(path) as f:
        raw = json.load(f)
    elos = {int(k): float(v) for k, v in raw.items()}
    logger.info("Elos cargados desde %s (%d equipos)", path, len(elos))
    return elos


def apply_elos_to_games(
    games_df: pd.DataFrame,
    current_elos: Dict[int, float],
) -> pd.DataFrame:
    """
    Aplica Elos pre-calculados a un DataFrame de partidos (sin resultado).
    Usado en producción para los partidos de hoy.

    Args:
        games_df:     DataFrame con home_team_id, visitor_team_id.
        current_elos: Dict {team_id: elo_actual} de load_current_elos().

    Returns:
        games_df con columnas: home_elo_pre, visitor_elo_pre, elo_diff, elo_home_win_prob
    """
    df = games_df.copy()

    home_elos = df["home_team_id"].map(
        lambda tid: current_elos.get(int(tid), ELO_BASE)
    )
    vis_elos = df["visitor_team_id"].map(
        lambda tid: current_elos.get(int(tid), ELO_BASE)
    )

    df["home_elo_pre"]      = home_elos.round(2)
    df["visitor_elo_pre"]   = vis_elos.round(2)
    df["elo_diff"]          = (df["home_elo_pre"] - df["visitor_elo_pre"]).round(2)
    df["elo_home_win_prob"] = df.apply(
        lambda r: round(_expected_home_win(r["home_elo_pre"], r["visitor_elo_pre"]), 4),
        axis=1,
    )
    return df
