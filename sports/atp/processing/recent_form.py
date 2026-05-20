"""
Forma reciente de jugadores ATP.

Calcula la tasa de victorias ponderada de los últimos N partidos de un jugador,
con mayor peso para partidos recientes y por nivel de torneo.

Diseñado para usarse en DOS contextos:
  1. Entrenamiento: se invoca con una ventana temporal (solo partidos ANTES de X fecha)
  2. Predicción en tiempo real: se invoca con los últimos partidos disponibles en caché

Pesos de recencia:
  Los últimos 10 partidos se ponderan por posición:
  posición 1 (más reciente) → peso 10
  posición 2               → peso 9
  ...
  posición 10 (más antiguo) → peso 1

Pesos adicionales por nivel del torneo ganado/perdido:
  Grand Slam   → 1.5×
  Masters 1000 → 1.3×
  ATP 500/250  → 1.0×
  Challenger   → 0.7×
  ITF          → 0.4×

Funciones públicas:
  - get_player_form(player_id, matches_df, surface=None, last_n=10, before_date=None)
        → float  (tasa de victorias ponderada 0.0–1.0)
  - compute_form_for_all(matches_df, last_n=10)
        → dict {player_id: {"overall": float, "Hard": float, "Clay": float, "Grass": float}}
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Dict, Optional

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

# Número de partidos a considerar en la ventana de forma
_DEFAULT_LAST_N = 10

# Pesos por nivel de torneo para el cálculo de forma ponderada
_LEVEL_WEIGHT: dict[str, float] = {
    "G": 1.5,    # Grand Slam
    "F": 1.4,    # ATP Finals
    "M": 1.3,    # Masters 1000
    "A": 1.0,    # ATP 500/250
    "C": 0.7,    # Challenger
    "D": 0.5,    # Davis Cup / por equipos
    "I": 0.4,    # ITF / otros
}
_LEVEL_WEIGHT_DEFAULT = 0.8


# ── Utilidades ────────────────────────────────────────────────────────────────

def _weighted_win_rate(
    results: list[tuple[int, str]],
    last_n: int = _DEFAULT_LAST_N,
) -> float:
    """
    Calcula la tasa de victorias ponderada por recencia y nivel de torneo.

    Args:
        results: Lista de (won: 0/1, tourney_level: str) ordenada cronológicamente
                 (último partido al FINAL de la lista).
        last_n:  Número de partidos recientes a usar.

    Returns:
        Tasa ponderada entre 0.0 y 1.0.  0.5 si no hay historial.
    """
    if not results:
        return 0.5   # sin historial → equiprobable

    window = results[-last_n:]   # últimos N
    n      = len(window)

    total_weight = 0.0
    weighted_wins = 0.0

    for i, (won, level) in enumerate(window):
        # Posición reciente: el último partido tiene peso n, el más antiguo 1
        recency_weight = i + 1
        level_weight   = _LEVEL_WEIGHT.get(str(level).upper(), _LEVEL_WEIGHT_DEFAULT)
        w              = recency_weight * level_weight

        total_weight   += w
        weighted_wins  += w * won

    if total_weight == 0:
        return 0.5

    return round(weighted_wins / total_weight, 4)


# ── API pública ───────────────────────────────────────────────────────────────

def get_player_form(
    player_id: int,
    matches_df: pd.DataFrame,
    surface: Optional[str] = None,
    last_n: int = _DEFAULT_LAST_N,
    before_date: Optional[pd.Timestamp] = None,
) -> float:
    """
    Calcula la forma reciente de un jugador.

    Args:
        player_id:   Sackmann player_id.
        matches_df:  DataFrame completo de partidos ATP (con winner_id, loser_id,
                     tourney_date, tourney_level, surface).
        surface:     Si se especifica, solo considera partidos en esa superficie.
        last_n:      Número de partidos recientes a considerar.
        before_date: Si se especifica, solo considera partidos ANTES de esta fecha
                     (para evitar look-ahead bias en entrenamiento).

    Returns:
        Tasa de victorias ponderada 0.0–1.0.
        0.5 si hay menos de 3 partidos en el historial.
    """
    if matches_df.empty or "winner_id" not in matches_df.columns:
        return 0.5

    df = matches_df
    if before_date is not None:
        df = df[df["tourney_date"] < before_date]

    # Partidos del jugador (ganados o perdidos)
    is_winner = df["winner_id"] == int(player_id)
    is_loser  = df["loser_id"]  == int(player_id)
    player_matches = df[is_winner | is_loser].copy()

    if surface:
        player_matches = player_matches[player_matches["surface"] == surface]

    if player_matches.empty:
        return 0.5

    player_matches = player_matches.sort_values("tourney_date")

    # Construir lista de resultados (won, level)
    results = []
    for _, row in player_matches.iterrows():
        won   = 1 if row["winner_id"] == int(player_id) else 0
        level = str(row.get("tourney_level", "A") or "A")
        results.append((won, level))

    if len(results) < 3:
        return 0.5   # demasiado pocas observaciones → regresar a prior

    return _weighted_win_rate(results, last_n=last_n)


def compute_form_for_all(
    matches_df: pd.DataFrame,
    last_n: int = _DEFAULT_LAST_N,
) -> Dict[int, Dict[str, float]]:
    """
    Calcula la forma reciente de TODOS los jugadores en el dataset.

    Procesa los partidos una vez en orden cronológico, manteniendo
    una ventana deslizante por jugador — sin look-ahead bias.

    Args:
        matches_df: DataFrame de partidos ATP.
        last_n:     Ventana de partidos recientes.

    Returns:
        {
          player_id: {
            "overall": float,
            "Hard":    float,
            "Clay":    float,
            "Grass":   float,
          }
        }
    """
    if matches_df.empty:
        return {}

    df = matches_df.sort_values("tourney_date")

    # Historial por jugador: {player_id: {surface: [(won, level)]}}
    # "all" guarda historial overall
    history: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    def _form(player_id: int, surface_key: str) -> float:
        results = history[player_id][surface_key]
        if len(results) < 2:
            return 0.5
        return _weighted_win_rate(results, last_n=last_n)

    forms: dict[int, dict[str, float]] = {}

    for _, row in df.iterrows():
        w_id = int(row.get("winner_id", 0) or 0)
        l_id = int(row.get("loser_id",  0) or 0)
        surf = str(row.get("surface", "Hard") or "Hard")
        lvl  = str(row.get("tourney_level", "A") or "A")

        if not w_id or not l_id:
            continue

        # Actualizar estado DESPUÉS de registrar (para evitar look-ahead)
        history[w_id]["all"].append((1, lvl))
        history[w_id][surf].append((1, lvl))
        history[l_id]["all"].append((0, lvl))
        history[l_id][surf].append((0, lvl))

    # Calcular forma final para cada jugador
    for pid, surf_history in history.items():
        forms[pid] = {
            "overall": _form(pid, "all"),
            "Hard":    _form(pid, "Hard"),
            "Clay":    _form(pid, "Clay"),
            "Grass":   _form(pid, "Grass"),
        }

    logger.info("Forma reciente calculada para %d jugadores", len(forms))
    return forms
