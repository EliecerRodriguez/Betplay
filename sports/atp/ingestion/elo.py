"""
Sistema de ratings Elo específico por superficie para jugadores ATP.

El Elo de superficie es el predictor individual más potente en tenis.
Un jugador top en hard puede ser mediocre en arcilla (ejemplo: Isner en clay
vs Nadal en clay).  Usar un único Elo global pierde ese diferencial crítico.

Implementación: 3 ratings completamente independientes por jugador:
  - Hard   (pistas duras: Australian Open, US Open, Masters indoor)
  - Clay   (arcilla: Roland Garros, toda la temporada de tierra)
  - Grass  (hierba: Wimbledon, Queen's, Halle)

K-factor calibrado por nivel de torneo (mayor nivel → más impacto en Elo):
  G = Grand Slam       → K = 32
  M = Masters 1000     → K = 24
  A = ATP 500          → K = 20   (Sackmann los etiqueta como 'A')
  A = ATP 250          → K = 16
  F = ATP Finals       → K = 28
  D = Davis Cup        → K = 12

Regresión a la media al inicio de cada temporada (1 enero):
  new_elo = REGR * old_elo + (1 - REGR) * ELO_BASE
  REGR = 0.9  →  borra 10% de ventaja/desventaja acumulada

Fórmula estándar FiveThirtyEight (igual que NBA):
  expected_A = 1 / (1 + 10^((elo_B - elo_A) / 400))
  delta = K * (resultado - expected)

Funciones públicas:
  - compute_elos_from_history(matches_df)
        → dict { player_id: {"Hard": float, "Clay": float, "Grass": float} }
  - enrich_with_elo(matches_df, elos)
        → DataFrame con columnas winner_elo, loser_elo, elo_diff, elo_win_prob
  - save_current_elos(elos, path)
  - load_current_elos(path)  → dict de dicts
"""
from __future__ import annotations

import json
import os
from typing import Dict, Optional, Tuple

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────
ELO_BASE         = 1500.0   # Rating de partida para todos los jugadores
ELO_REGR         = 0.90     # Regresión a la media entre temporadas (0.9 = -10%)
SURFACES         = ("Hard", "Clay", "Grass")

# K-factor por nivel de torneo
_K_MAP: dict[str, int] = {
    "G": 32,   # Grand Slam
    "F": 28,   # ATP Finals / Nitto ATP Finals
    "M": 24,   # Masters 1000
    "A": 16,   # ATP 250 / 500 (Sackmann usa 'A' para ambos)
    "D": 12,   # Davis Cup y competiciones por equipos
}
_K_DEFAULT = 14   # Fallback para niveles desconocidos

# Rango de K según el tamaño del draw para torneos tipo 'A'
# (500 tienen draw_size 32-48; 250 tienen 28-32)
_K_500 = 20
_DRAW_500_MIN = 32   # draw_size >= 32 → asumimos 500


# ── Fórmulas Elo ─────────────────────────────────────────────────────────────

def _expected_win(elo_a: float, elo_b: float) -> float:
    """Probabilidad esperada de victoria de A contra B."""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))


def _k_factor(tourney_level: str, draw_size: int = 0) -> float:
    """
    Devuelve el K-factor para un torneo dado.
    Para nivel 'A' distingue entre ATP 500 (mayor draw) y ATP 250.
    """
    level = str(tourney_level).strip().upper()
    if level == "A":
        return _K_500 if (draw_size or 0) >= _DRAW_500_MIN else _K_MAP["A"]
    return float(_K_MAP.get(level, _K_DEFAULT))


def _normalize_surface(surface: str) -> Optional[str]:
    """Normaliza la superficie a Hard/Clay/Grass.  None si es desconocida."""
    s = str(surface).strip().title()
    if s in SURFACES:
        return s
    # Aliases
    if s in ("Hardcourt", "Indoor Hard", "Outdoor Hard"):
        return "Hard"
    if s in ("Clayc", "Clay (Red)", "Clay (Green)"):
        return "Clay"
    if s in ("Lawn", "Grass Court"):
        return "Grass"
    return None   # Carpet u otro — se ignora en el cálculo por superficie


# ── Motor Elo ─────────────────────────────────────────────────────────────────

def compute_elos_from_history(
    matches_df: pd.DataFrame,
) -> Dict[int, Dict[str, float]]:
    """
    Procesa el historial completo de partidos y devuelve los ratings Elo
    actuales por jugador y por superficie.

    Args:
        matches_df: DataFrame de download_atp_matches().  Debe contener al menos:
                    tourney_date, tourney_level, surface, draw_size,
                    winner_id, loser_id.

    Returns:
        { player_id: {"Hard": float, "Clay": float, "Grass": float} }

    Los partidos se procesan en orden cronológico estricto.
    La regresión a la media se aplica cada 1 de enero del año siguiente.
    """
    if matches_df.empty:
        logger.warning("DataFrame vacío — devolviendo Elos vacíos")
        return {}

    required = {"tourney_date", "tourney_level", "surface", "winner_id", "loser_id"}
    missing  = required - set(matches_df.columns)
    if missing:
        raise ValueError(f"Columnas faltantes en matches_df: {missing}")

    # Ordenar cronológicamente (ya debería estar ordenado por download, pero por seguridad)
    df = matches_df.dropna(subset=["winner_id", "loser_id", "tourney_date"]).copy()
    df = df.sort_values("tourney_date").reset_index(drop=True)

    # Ratings en memoria: {player_id: {surface: elo}}
    elos: Dict[int, Dict[str, float]] = {}
    current_year: Optional[int] = None

    def _get_elo(pid: int, surface: str) -> float:
        return elos.setdefault(int(pid), {}).get(surface, ELO_BASE)

    def _set_elo(pid: int, surface: str, value: float) -> None:
        elos.setdefault(int(pid), {})[surface] = value

    def _apply_regression(year: int) -> None:
        """Aplica regresión a la media a TODOS los jugadores para el nuevo año."""
        for pid in elos:
            for surf in SURFACES:
                old = elos[pid].get(surf, ELO_BASE)
                elos[pid][surf] = ELO_REGR * old + (1.0 - ELO_REGR) * ELO_BASE

    total = len(df)
    logger.info("Procesando Elo para %d partidos…", total)

    for idx, row in df.iterrows():
        # ── Regresión de año nuevo ──────────────────────────────────────────
        match_year = row["tourney_date"].year if pd.notna(row["tourney_date"]) else None
        if match_year and current_year and match_year > current_year:
            _apply_regression(match_year)
        if match_year:
            current_year = match_year

        # ── Superficie del partido ──────────────────────────────────────────
        surface = _normalize_surface(row.get("surface", ""))
        if surface is None:
            continue   # Carpet u otro — no actualiza Elos de superficie

        winner_id = int(row["winner_id"])
        loser_id  = int(row["loser_id"])
        k         = _k_factor(
            row.get("tourney_level", "A"),
            int(row["draw_size"]) if pd.notna(row.get("draw_size")) else 0,
        )

        # ── Elos pre-partido ────────────────────────────────────────────────
        w_elo = _get_elo(winner_id, surface)
        l_elo = _get_elo(loser_id,  surface)

        # ── Actualización ────────────────────────────────────────────────────
        expected = _expected_win(w_elo, l_elo)
        delta    = k * (1.0 - expected)          # ganador: resultado=1
        _set_elo(winner_id, surface, w_elo + delta)
        _set_elo(loser_id,  surface, l_elo - delta)

    # Rellenar superficies faltantes con ELO_BASE para jugadores que nunca
    # jugaron en esa superficie (garantiza que el dict siempre tiene las 3 claves)
    for pid in elos:
        for surf in SURFACES:
            elos[pid].setdefault(surf, ELO_BASE)

    logger.info("Elo calculado para %d jugadores", len(elos))
    return elos


# ── Enriquecimiento de DataFrames ─────────────────────────────────────────────

def enrich_with_elo(
    matches_df: pd.DataFrame,
    elos: Dict[int, Dict[str, float]],
) -> pd.DataFrame:
    """
    Añade las columnas de Elo PRE-PARTIDO a un DataFrame de partidos.

    Columnas añadidas:
      winner_elo_pre    Elo del ganador en la superficie del partido (antes del partido)
      loser_elo_pre     Elo del perdedor
      elo_diff          winner_elo_pre - loser_elo_pre
      elo_win_prob      Probabilidad esperada de victoria del ganador según Elo

    IMPORTANTE: Solo usar para entrenamiento con resultados ya conocidos.
    Para predicción en tiempo real usar apply_elos_to_match().
    """
    df = matches_df.copy()

    winner_elos, loser_elos = [], []
    for _, row in df.iterrows():
        surf    = _normalize_surface(row.get("surface", "")) or "Hard"
        w_id    = int(row.get("winner_id", 0) or 0)
        l_id    = int(row.get("loser_id",  0) or 0)
        w_elo   = elos.get(w_id, {}).get(surf, ELO_BASE)
        l_elo   = elos.get(l_id, {}).get(surf, ELO_BASE)
        winner_elos.append(w_elo)
        loser_elos.append(l_elo)

    df["winner_elo_pre"] = winner_elos
    df["loser_elo_pre"]  = loser_elos
    df["elo_diff"]       = df["winner_elo_pre"] - df["loser_elo_pre"]
    df["elo_win_prob"]   = df.apply(
        lambda r: _expected_win(r["winner_elo_pre"], r["loser_elo_pre"]), axis=1
    )
    return df


def apply_elos_to_matchup(
    player1_id: int,
    player2_id: int,
    surface: str,
    elos: Dict[int, Dict[str, float]],
) -> dict:
    """
    Calcula Elo y probabilidad esperada para un partido entre dos jugadores.

    Args:
        player1_id: ID del jugador 1 (local o primero en el draw).
        player2_id: ID del jugador 2.
        surface:    Superficie del partido ('Hard', 'Clay', 'Grass').
        elos:       Dict de ratings actuales (de load_current_elos).

    Returns:
        {
          'p1_elo': float, 'p2_elo': float,
          'elo_diff': float,          # p1 - p2
          'p1_win_prob': float,       # probabilidad de victoria de p1 según Elo
        }
    """
    surf  = _normalize_surface(surface) or "Hard"
    p1_elo = elos.get(player1_id, {}).get(surf, ELO_BASE)
    p2_elo = elos.get(player2_id, {}).get(surf, ELO_BASE)
    p1_win  = _expected_win(p1_elo, p2_elo)
    return {
        "p1_elo":      p1_elo,
        "p2_elo":      p2_elo,
        "elo_diff":    p1_elo - p2_elo,
        "p1_win_prob": p1_win,
    }


# ── Persistencia ──────────────────────────────────────────────────────────────

def save_current_elos(
    elos: Dict[int, Dict[str, float]],
    path: str = "sports/atp/models/current_elos.json",
) -> None:
    """Guarda el dict de Elos en un archivo JSON."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # JSON no acepta keys enteras — convertir a str
    serializable = {str(pid): surfs for pid, surfs in elos.items()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2)
    logger.info("Elos ATP guardados en %s (%d jugadores)", path, len(elos))


def load_current_elos(
    path: str = "sports/atp/models/current_elos.json",
) -> Dict[int, Dict[str, float]]:
    """Carga los Elos desde JSON.  Devuelve dict vacío si el archivo no existe."""
    if not os.path.exists(path):
        logger.warning("Archivo de Elos no encontrado: %s", path)
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    # Convertir claves string de vuelta a int
    return {int(pid): surfs for pid, surfs in raw.items()}
