"""
Historial directo (H2H) entre jugadores ATP.

Calcula estadísticas de enfrentamientos directos a partir del historial local
de Jeff Sackmann — sin llamadas externas, 100% desde datos cacheados en disco.

El H2H es uno de los predictores más potentes en tenis (especialmente en arcilla)
porque captura dinámicas de juego entre estilos que el Elo no modela explícitamente.

Estadísticas calculadas:
  - total_matches       Total de partidos directos en el historial
  - p1_wins / p2_wins   Victorias de cada jugador
  - p1_win_rate         Tasa de victoria de p1 (0.0–1.0)
  - surface_breakdown   {surface: {"p1": n, "p2": n}} desglose por superficie
  - recent_matches      Últimos N partidos directos (lista de dicts)
  - p1_wins_surface     Victorias de p1 en la superficie del partido actual
  - p2_wins_surface     Victorias de p2 en la superficie actual
  - p1_win_rate_surface Tasa de p1 en esa superficie

Funciones públicas:
  - get_h2h(player1_id, player2_id, surface=None, min_year=2010)
        → dict con todas las estadísticas
  - get_h2h_by_name(name1, name2, surface=None)
        → mismo dict, resuelve nombres a IDs internamente
  - enrich_matchup_with_h2h(row_dict, surface)
        → añade columnas h2h_* a un dict de partido
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from sports.atp.ingestion.historical_client import download_atp_matches
from sports.atp.ingestion.rankings_client import get_player_id_by_name
from utils.logger import get_logger

logger = get_logger(__name__)

# Caché en memoria: {(id1, id2): DataFrame de enfrentamientos}
_H2H_CACHE: Dict[tuple, pd.DataFrame] = {}

# Años mínimos para considerar en el H2H por defecto (evitar resultados
# muy antiguos que no reflejan el juego moderno)
_DEFAULT_MIN_YEAR = 2010

# Número de partidos recientes a incluir en "recent_matches"
_RECENT_N = 10


# ── Carga de historial ────────────────────────────────────────────────────────

_MATCHES_DF: Optional[pd.DataFrame] = None


def _get_matches_df(min_year: int = _DEFAULT_MIN_YEAR) -> pd.DataFrame:
    """Carga el historial completo desde caché local (una sola vez por proceso)."""
    global _MATCHES_DF
    if _MATCHES_DF is None or _MATCHES_DF.empty:
        logger.info("Cargando historial ATP para H2H (año >= %d)…", min_year)
        _MATCHES_DF = download_atp_matches(start_year=min_year)
    return _MATCHES_DF


# ── Motor H2H ─────────────────────────────────────────────────────────────────

def _extract_h2h_matches(
    player1_id: int,
    player2_id: int,
    min_year: int = _DEFAULT_MIN_YEAR,
) -> pd.DataFrame:
    """
    Filtra los partidos directos entre player1 y player2 desde el historial.
    Normaliza el resultado: siempre hay columna 'p1_won' (1 = player1 ganó).
    """
    key = (min(player1_id, player2_id), max(player1_id, player2_id), min_year)
    if key in _H2H_CACHE:
        return _H2H_CACHE[key]

    df = _get_matches_df(min_year=min_year)
    if df.empty or "winner_id" not in df.columns:
        return pd.DataFrame()

    p1, p2 = int(player1_id), int(player2_id)

    # Partidos donde p1 ganó
    p1_won = df[
        (df["winner_id"] == p1) & (df["loser_id"] == p2)
    ].copy()
    p1_won["p1_won"] = 1

    # Partidos donde p2 ganó
    p2_won = df[
        (df["winner_id"] == p2) & (df["loser_id"] == p1)
    ].copy()
    p2_won["p1_won"] = 0

    combined = pd.concat([p1_won, p2_won], ignore_index=True)
    if not combined.empty and "tourney_date" in combined.columns:
        combined = combined.sort_values("tourney_date").reset_index(drop=True)

    _H2H_CACHE[key] = combined
    return combined


# ── API pública ───────────────────────────────────────────────────────────────

def get_h2h(
    player1_id: int,
    player2_id: int,
    surface: Optional[str] = None,
    min_year: int = _DEFAULT_MIN_YEAR,
) -> Dict:
    """
    Devuelve estadísticas de enfrentamientos directos entre dos jugadores.

    Args:
        player1_id: Sackmann player_id del primer jugador.
        player2_id: Sackmann player_id del segundo jugador.
        surface:    Si se proporciona ('Hard', 'Clay', 'Grass'), añade
                    estadísticas específicas por esa superficie.
        min_year:   Año mínimo a considerar (default: 2010).

    Returns:
        {
          'total_matches'       : int,
          'p1_wins'             : int,
          'p2_wins'             : int,
          'p1_win_rate'         : float,   # 0.5 si sin historial
          'surface_breakdown'   : dict,    # {surf: {'p1': n, 'p2': n}}
          'p1_wins_surface'     : int,     # solo si surface especificado
          'p2_wins_surface'     : int,
          'p1_win_rate_surface' : float,
          'recent_matches'      : list,    # últimos _RECENT_N partidos
          'has_history'         : bool,
        }
    """
    matches = _extract_h2h_matches(player1_id, player2_id, min_year=min_year)

    if matches.empty:
        base = {
            "total_matches":        0,
            "p1_wins":              0,
            "p2_wins":              0,
            "p1_win_rate":          0.5,   # sin info → equiprobable
            "surface_breakdown":    {},
            "p1_wins_surface":      0,
            "p2_wins_surface":      0,
            "p1_win_rate_surface":  0.5,
            "recent_matches":       [],
            "has_history":          False,
        }
        return base

    total   = len(matches)
    p1_wins = int(matches["p1_won"].sum())
    p2_wins = total - p1_wins
    p1_rate = p1_wins / total if total > 0 else 0.5

    # Desglose por superficie
    surface_breakdown: Dict[str, Dict[str, int]] = {}
    if "surface" in matches.columns:
        for surf, grp in matches.groupby("surface"):
            s = str(surf)
            surface_breakdown[s] = {
                "p1": int(grp["p1_won"].sum()),
                "p2": int((grp["p1_won"] == 0).sum()),
            }

    # Estadísticas en la superficie del partido actual
    p1_wins_surf = p2_wins_surf = 0
    p1_rate_surf = 0.5
    if surface:
        surf_data = surface_breakdown.get(surface, {})
        p1_wins_surf = surf_data.get("p1", 0)
        p2_wins_surf = surf_data.get("p2", 0)
        total_surf   = p1_wins_surf + p2_wins_surf
        p1_rate_surf = p1_wins_surf / total_surf if total_surf > 0 else 0.5

    # Últimos N partidos recientes (lista de dicts ligeros)
    recent = matches.tail(_RECENT_N).copy()
    recent_list: List[Dict] = []
    for _, row in recent.iterrows():
        recent_list.append({
            "date":    str(row.get("tourney_date", ""))[:10],
            "tourney": str(row.get("tourney_name", "")),
            "surface": str(row.get("surface", "")),
            "winner":  "p1" if row["p1_won"] == 1 else "p2",
            "score":   str(row.get("score", "")),
        })

    return {
        "total_matches":        total,
        "p1_wins":              p1_wins,
        "p2_wins":              p2_wins,
        "p1_win_rate":          round(p1_rate, 4),
        "surface_breakdown":    surface_breakdown,
        "p1_wins_surface":      p1_wins_surf,
        "p2_wins_surface":      p2_wins_surf,
        "p1_win_rate_surface":  round(p1_rate_surf, 4),
        "recent_matches":       recent_list,
        "has_history":          True,
    }


def get_h2h_by_name(
    name1: str,
    name2: str,
    surface: Optional[str] = None,
    min_year: int = _DEFAULT_MIN_YEAR,
) -> Dict:
    """
    Igual que get_h2h() pero acepta nombres de jugadores en lugar de IDs.
    Resuelve internamente los nombres a player_ids Sackmann.

    Returns:
        Mismo dict que get_h2h().  Si algún nombre no se puede resolver,
        devuelve estadísticas vacías (p1_win_rate = 0.5).
    """
    p1_id = get_player_id_by_name(name1)
    p2_id = get_player_id_by_name(name2)

    if p1_id is None or p2_id is None:
        if p1_id is None:
            logger.warning("No se pudo resolver player_id para: '%s'", name1)
        if p2_id is None:
            logger.warning("No se pudo resolver player_id para: '%s'", name2)
        return get_h2h(0, 1, surface=surface)   # devuelve stats vacías

    return get_h2h(p1_id, p2_id, surface=surface, min_year=min_year)


def enrich_matchup_with_h2h(
    matchup: Dict,
    surface: Optional[str] = None,
) -> Dict:
    """
    Añade features H2H a un dict de partido.

    Args:
        matchup: Dict con al menos player1_id y player2_id (o player1_name / player2_name).
        surface: Superficie del partido para el breakdown específico.

    Returns:
        Mismo dict con claves h2h_* añadidas.
    """
    p1_id = matchup.get("player1_id")
    p2_id = matchup.get("player2_id")

    if not p1_id:
        p1_id = get_player_id_by_name(matchup.get("player1_name", ""))
    if not p2_id:
        p2_id = get_player_id_by_name(matchup.get("player2_name", ""))

    if not p1_id or not p2_id:
        matchup.update({
            "h2h_total":        0,
            "h2h_p1_wins":      0,
            "h2h_p2_wins":      0,
            "h2h_p1_win_rate":  0.5,
            "h2h_surface_rate": 0.5,
            "h2h_has_history":  False,
        })
        return matchup

    surf = surface or matchup.get("surface")
    stats = get_h2h(int(p1_id), int(p2_id), surface=surf)

    matchup.update({
        "h2h_total":        stats["total_matches"],
        "h2h_p1_wins":      stats["p1_wins"],
        "h2h_p2_wins":      stats["p2_wins"],
        "h2h_p1_win_rate":  stats["p1_win_rate"],
        "h2h_surface_rate": stats["p1_win_rate_surface"],
        "h2h_has_history":  stats["has_history"],
    })
    return matchup
