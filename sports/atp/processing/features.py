"""
Feature engineering para predicciones ATP.

Construye el vector de características para cada partido, tanto para
ENTRENAMIENTO (con loop progresivo sin look-ahead bias) como para
PREDICCIÓN en tiempo real.

──────────────────────────────────────────────────────────────────────
FEATURES INCLUIDAS (13 features finales)
──────────────────────────────────────────────────────────────────────
Elo (los más predictivos en tenis):
  1.  elo_diff          p1_elo - p2_elo en la superficie del partido
  2.  elo_win_prob      probabilidad Elo de p1  (1/(1+10^(-elo_diff/400)))

H2H:
  3.  h2h_win_rate      tasa H2H de p1 (todo historial)
  4.  h2h_surface_rate  tasa H2H de p1 en esta superficie
  5.  h2h_total_log     log(N_partidos_H2H + 1)  (peso de confianza)

Forma reciente (últimos 10 partidos):
  6.  form_diff         p1_form - p2_form (overall)
  7.  form_surface_diff p1_form_surf - p2_form_surf

Superficie (one-hot):
  8.  is_clay
  9.  is_grass
  (Hard es el baseline implícito)

Nivel del torneo:
  10. is_grand_slam
  11. is_masters

Ranking ATP (disponible en predicción; puede estar ausente en training):
  12. ranking_diff_log   log(p2_rank+1) - log(p1_rank+1)
                        positivo = p1 está mejor rankeado

Historial de partidos jugados (proxy de experiencia):
  13. p1_matches_log     log(n_partidos_p1_en_esta_superficie + 1)

──────────────────────────────────────────────────────────────────────
FUNCIONES PRINCIPALES
──────────────────────────────────────────────────────────────────────
  get_feature_columns()
        → lista de 13 nombres de features

  build_training_features(matches_df, min_year=2013)
        → DataFrame con features para entrenamiento (sin look-ahead bias)
        → Cada partido original genera DOS filas (perspectiva p1 y p2 swapped)

  compute_live_features(p1_id, p2_id, surface, tourney_level,
                        elos, rankings, matches_df)
        → dict con las 13 features para predicción en tiempo real
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from sports.atp.ingestion.elo import (
    ELO_BASE, ELO_REGR, SURFACES, _K_MAP,
    _expected_win, _k_factor, _normalize_surface,
)
from utils.logger import get_logger

logger = get_logger(__name__)


# ── Columnas del vector de features ──────────────────────────────────────────

FEATURE_COLUMNS: List[str] = [
    # Elo
    "elo_diff",
    "elo_win_prob",
    # H2H
    "h2h_win_rate",
    "h2h_surface_rate",
    "h2h_total_log",
    # Forma
    "form_diff",
    "form_surface_diff",
    # Superficie
    "is_clay",
    "is_grass",
    # Nivel
    "is_grand_slam",
    "is_masters",
    # Ranking
    "ranking_diff_log",
    # Experiencia en superficie
    "p1_matches_log",
]


def get_feature_columns() -> List[str]:
    """Devuelve la lista canónica de features del modelo ATP."""
    return list(FEATURE_COLUMNS)


# ── Helpers matemáticos ───────────────────────────────────────────────────────

def _safe_log(x: float) -> float:
    return math.log(max(x, 0) + 1)


def _win_rate(wins: int, total: int, default: float = 0.5) -> float:
    return wins / total if total > 0 else default


# ── Construcción de datos de entrenamiento ────────────────────────────────────

def build_training_features(
    matches_df: pd.DataFrame,
    min_year: int = 2013,
    min_matches_per_player: int = 10,
) -> pd.DataFrame:
    """
    Construye el DataFrame de entrenamiento procesando los partidos
    CRONOLÓGICAMENTE para evitar look-ahead bias.

    Por cada partido histórico genera DOS filas:
      - fila A: ganador como p1 (target = 1)
      - fila B: perdedor como p1 (target = 0)

    Esto produce clases perfectamente balanceadas y enseña al modelo
    que las features son simétricas.

    Args:
        matches_df:              DataFrame completo de Sackmann (download_atp_matches).
        min_year:                Año mínimo para incluir partidos en el set de entrenamiento
                                 (se procesan datos desde 2010 para calentar Elos,
                                  pero solo se generan filas a partir de min_year).
        min_matches_per_player:  Número mínimo de partidos para incluir a un jugador
                                 (excluye jugadores con Elo poco estable).

    Returns:
        DataFrame con columnas FEATURE_COLUMNS + ['target', 'p1_id', 'p2_id',
        'surface', 'tourney_level', 'tourney_date'].
    """
    if matches_df.empty:
        return pd.DataFrame()

    required = {"tourney_date", "tourney_level", "surface", "winner_id", "loser_id"}
    missing  = required - set(matches_df.columns)
    if missing:
        raise ValueError(f"Columnas faltantes: {missing}")

    df = matches_df.dropna(subset=["winner_id", "loser_id", "tourney_date"]).copy()
    df = df.sort_values("tourney_date").reset_index(drop=True)

    # ── Estado progresivo (sin look-ahead) ────────────────────────────────────
    # Elos por superficie
    elos: Dict[int, Dict[str, float]] = defaultdict(
        lambda: {s: ELO_BASE for s in SURFACES}
    )
    current_year: Optional[int] = None

    # H2H: {(min_id, max_id): {surface: [p_min_wins, p_max_wins], 'all': [...]}}
    h2h: Dict[Tuple, Dict[str, list]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0])
    )

    # Forma reciente: {player_id: {surface: [(won,level)], 'all': [...]}}
    form: Dict[int, Dict[str, list]] = defaultdict(
        lambda: defaultdict(list)
    )

    # Contador de partidos por jugador/superficie (para el filtro min_matches)
    match_count: Dict[int, Dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )

    def _elo(pid: int, surf: str) -> float:
        return elos[pid].get(surf, ELO_BASE)

    def _form_rate(pid: int, surf_key: str, last_n: int = 10) -> float:
        results = form[pid][surf_key]
        if len(results) < 2:
            return 0.5
        window = results[-last_n:]
        n = len(window)
        num = den = 0.0
        for i, (w, _) in enumerate(window):
            wt = i + 1   # recency weight
            num += wt * w
            den += wt
        return num / den if den > 0 else 0.5

    def _h2h_rate(pid1: int, pid2: int, surf_key: str) -> Tuple[float, int]:
        """Devuelve (tasa_pid1, total_partidos)."""
        key = (min(pid1, pid2), max(pid1, pid2))
        counts = h2h[key][surf_key]  # [wins_min_id, wins_max_id]
        w1 = counts[0] if pid1 < pid2 else counts[1]
        w2 = counts[1] if pid1 < pid2 else counts[0]
        total = w1 + w2
        return _win_rate(w1, total), total

    records = []

    for _, row in df.iterrows():
        w_id = int(row["winner_id"])
        l_id = int(row["loser_id"])
        surf = _normalize_surface(row.get("surface", "")) or "Hard"
        lvl  = str(row.get("tourney_level", "A") or "A")
        date = row["tourney_date"]
        year = date.year if pd.notna(date) else None
        draw = int(row.get("draw_size") or 0)

        # Regresión de año nuevo
        if year and current_year and year > current_year:
            for pid in elos:
                for s in SURFACES:
                    old = elos[pid].get(s, ELO_BASE)
                    elos[pid][s] = ELO_REGR * old + (1.0 - ELO_REGR) * ELO_BASE
        if year:
            current_year = year

        # Pre-match Elo
        w_elo = _elo(w_id, surf)
        l_elo = _elo(l_id, surf)

        # Decidir si generamos fila de entrenamiento
        include = (
            year is not None and year >= min_year
            and match_count[w_id]["all"] >= min_matches_per_player
            and match_count[l_id]["all"] >= min_matches_per_player
        )

        if include:
            elo_diff    = w_elo - l_elo
            elo_prob    = _expected_win(w_elo, l_elo)

            w_h2h, h2h_total = _h2h_rate(w_id, l_id, surf)
            w_h2h_all, _     = _h2h_rate(w_id, l_id, "all")

            w_form   = _form_rate(w_id, "all")
            l_form   = _form_rate(l_id, "all")
            w_form_s = _form_rate(w_id, surf)
            l_form_s = _form_rate(l_id, surf)

            w_matches_log = _safe_log(match_count[w_id][surf])
            l_matches_log = _safe_log(match_count[l_id][surf])

            is_clay = 1 if surf == "Clay"  else 0
            is_grs  = 1 if surf == "Grass" else 0
            is_gs   = 1 if lvl == "G"      else 0
            is_m    = 1 if lvl == "M"      else 0

            base = {
                "h2h_total_log":  _safe_log(h2h_total),
                "is_clay":        is_clay,
                "is_grass":       is_grs,
                "is_grand_slam":  is_gs,
                "is_masters":     is_m,
                "ranking_diff_log": 0.0,   # no disponible en training histórico
                "surface":        surf,
                "tourney_level":  lvl,
                "tourney_date":   date,
            }

            # Fila A: ganador = p1 (target=1)
            records.append({
                "p1_id": w_id, "p2_id": l_id,
                "elo_diff":          elo_diff,
                "elo_win_prob":      elo_prob,
                "h2h_win_rate":      w_h2h_all,
                "h2h_surface_rate":  w_h2h,
                "form_diff":         w_form - l_form,
                "form_surface_diff": w_form_s - l_form_s,
                "p1_matches_log":    w_matches_log,
                "target": 1,
                **base,
            })
            # Fila B: perdedor = p1 (target=0)
            l_h2h_all, _ = _h2h_rate(l_id, w_id, "all")
            l_h2h,     _ = _h2h_rate(l_id, w_id, surf)
            records.append({
                "p1_id": l_id, "p2_id": w_id,
                "elo_diff":          -elo_diff,
                "elo_win_prob":      1.0 - elo_prob,
                "h2h_win_rate":      l_h2h_all,
                "h2h_surface_rate":  l_h2h,
                "form_diff":         l_form - w_form,
                "form_surface_diff": l_form_s - w_form_s,
                "p1_matches_log":    l_matches_log,
                "target": 0,
                **base,
            })

        # ── Actualizar estado DESPUÉS de registrar ────────────────────────────
        # Elo
        k = _k_factor(lvl, draw)
        exp = _expected_win(w_elo, l_elo)
        delta = k * (1.0 - exp)
        elos[w_id][surf] = w_elo + delta
        elos[l_id][surf] = l_elo - delta

        # H2H
        hkey = (min(w_id, l_id), max(w_id, l_id))
        for hsurf in (surf, "all"):
            idx_w = 0 if w_id < l_id else 1
            h2h[hkey][hsurf][idx_w] += 1

        # Forma
        for pid, won in [(w_id, 1), (l_id, 0)]:
            form[pid]["all"].append((won, lvl))
            form[pid][surf].append((won, lvl))
            match_count[pid]["all"] += 1
            match_count[pid][surf]  += 1

    if not records:
        return pd.DataFrame()

    result_df = pd.DataFrame(records)

    # Asegurar que todas las feature columns existen (ranking_diff_log = 0 para training)
    for col in FEATURE_COLUMNS:
        if col not in result_df.columns:
            result_df[col] = 0.0

    logger.info(
        "Features de entrenamiento: %d filas | %d partidos únicos (año >= %d)",
        len(result_df), len(result_df) // 2, min_year,
    )
    return result_df


# ── Features para predicción en tiempo real ───────────────────────────────────

def compute_live_features(
    p1_id: int,
    p2_id: int,
    surface: str,
    tourney_level: str,
    elos: Dict[int, Dict[str, float]],
    rankings: Dict[int, int],
    matches_df: pd.DataFrame,
    as_of_date: Optional[pd.Timestamp] = None,
) -> Dict[str, float]:
    """
    Computa las features para un partido en tiempo real.

    Args:
        p1_id, p2_id:    Sackmann player_ids.
        surface:         'Hard' | 'Clay' | 'Grass'.
        tourney_level:   'G' | 'M' | 'A'.
        elos:            Elos actuales (de load_current_elos).
        rankings:        {player_id: rank} del ranking actual.
        matches_df:      DataFrame histórico completo (para H2H y forma).
        as_of_date:      Fecha límite para H2H/forma (None = todo).

    Returns:
        dict con exactamente las claves en FEATURE_COLUMNS.
    """
    surf = _normalize_surface(surface) or "Hard"
    lvl  = str(tourney_level or "A").upper()

    # ── Elo ───────────────────────────────────────────────────────────────────
    p1_elo = elos.get(p1_id, {}).get(surf, ELO_BASE)
    p2_elo = elos.get(p2_id, {}).get(surf, ELO_BASE)
    elo_diff  = p1_elo - p2_elo
    elo_prob  = _expected_win(p1_elo, p2_elo)

    # ── H2H ───────────────────────────────────────────────────────────────────
    h2h_win_rate = h2h_surface_rate = 0.5
    h2h_total    = 0

    if not matches_df.empty and "winner_id" in matches_df.columns:
        mdf = matches_df
        if as_of_date is not None:
            mdf = mdf[mdf["tourney_date"] < as_of_date]

        p1_won_filter = (mdf["winner_id"] == p1_id) & (mdf["loser_id"] == p2_id)
        p2_won_filter = (mdf["winner_id"] == p2_id) & (mdf["loser_id"] == p1_id)

        p1_wins     = int(p1_won_filter.sum())
        p2_wins     = int(p2_won_filter.sum())
        h2h_total   = p1_wins + p2_wins
        h2h_win_rate = _win_rate(p1_wins, h2h_total)

        p1_wins_surf = int((p1_won_filter & (mdf["surface"] == surf)).sum())
        p2_wins_surf = int((p2_won_filter & (mdf["surface"] == surf)).sum())
        h2h_surf_total = p1_wins_surf + p2_wins_surf
        h2h_surface_rate = _win_rate(p1_wins_surf, h2h_surf_total)

    # ── Forma reciente ────────────────────────────────────────────────────────
    form_diff = form_surface_diff = 0.0
    p1_matches_log = 0.0

    if not matches_df.empty:
        from sports.atp.processing.recent_form import get_player_form
        mdf_form = matches_df if as_of_date is None else matches_df[matches_df["tourney_date"] < as_of_date]

        p1_form    = get_player_form(p1_id, mdf_form)
        p2_form    = get_player_form(p2_id, mdf_form)
        p1_form_s  = get_player_form(p1_id, mdf_form, surface=surf)
        p2_form_s  = get_player_form(p2_id, mdf_form, surface=surf)
        form_diff         = p1_form - p2_form
        form_surface_diff = p1_form_s - p2_form_s

        # Experiencia en superficie
        is_p1 = (mdf_form["winner_id"] == p1_id) | (mdf_form["loser_id"] == p1_id)
        n_surf = int(((mdf_form[is_p1]["surface"] == surf)).sum())
        p1_matches_log = _safe_log(n_surf)

    # ── Ranking ───────────────────────────────────────────────────────────────
    p1_rank = rankings.get(p1_id, 500)
    p2_rank = rankings.get(p2_id, 500)
    ranking_diff_log = _safe_log(p2_rank) - _safe_log(p1_rank)

    return {
        "elo_diff":           elo_diff,
        "elo_win_prob":       elo_prob,
        "h2h_win_rate":       h2h_win_rate,
        "h2h_surface_rate":   h2h_surface_rate,
        "h2h_total_log":      _safe_log(h2h_total),
        "form_diff":          form_diff,
        "form_surface_diff":  form_surface_diff,
        "is_clay":            1.0 if surf == "Clay"  else 0.0,
        "is_grass":           1.0 if surf == "Grass" else 0.0,
        "is_grand_slam":      1.0 if lvl == "G"      else 0.0,
        "is_masters":         1.0 if lvl == "M"      else 0.0,
        "ranking_diff_log":   ranking_diff_log,
        "p1_matches_log":     p1_matches_log,
    }
