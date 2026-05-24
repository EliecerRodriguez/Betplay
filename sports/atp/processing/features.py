"""
Feature engineering para predicciones ATP.

Construye el vector de características para cada partido, tanto para
ENTRENAMIENTO (con loop progresivo sin look-ahead bias) como para
PREDICCIÓN en tiempo real.

──────────────────────────────────────────────────────────────────────
FEATURES INCLUIDAS (17 features finales)
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

Ranking ATP (disponible en predicción Y en entrenamiento vía Sackmann CSV):
  12. ranking_diff_log   log(p2_rank+1) - log(p1_rank+1)
                        positivo = p1 está mejor rankeado

Historial de partidos jugados (proxy de experiencia):
  13. p1_matches_log     log(n_partidos_p1_en_esta_superficie + 1)

──────────────────────────────────────────────────────────────────────
Estadísticas de saque (rolling últimos 20 partidos):
  14. first_serve_in_pct_diff
  15. first_serve_win_pct_diff
  16. bp_save_rate_diff
  17. ace_rate_diff

Descanso y ausencia:
  18. rest_advantage_diff   p1_days_off - p2_days_off (continua, capped 60 días)
                           positivo = p1 lleva más días sin jugar
  19. p1_returning          1 si p1 no jugó en los últimos 30 días (retorno de ausencia)

Fatiga intratorneo:
  20. p1_sets_last_match      sets jugados por p1 en su último partido de ESTE torneo (0=primero)
  21. tourney_rounds_rest_diff rondas de descanso de p1 − rondas de descanso de p2 dentro del torneo
                              positivo = p1 llevó más rondas sin jugar (más descansado)

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
import re
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from sports.atp.ingestion.elo import (
    ELO_BASE, ELO_REGR, SURFACES, _K_MAP,
    _expected_win, _k_factor, _normalize_surface,
)
from utils.logger import get_logger

logger = get_logger(__name__)


# ── Helpers de módulo ─────────────────────────────────────────────────────────

def _count_sets(score: str) -> int:
    """Cuenta sets jugados en un score type '6-3 7-5' o '7-6(4) 4-6 6-4'. RET 0 si inválido."""
    if not score or not isinstance(score, str):
        return 0
    return len(re.findall(r'\d+-\d+', score))


_ROUND_ORDER: Dict[str, int] = {
    "R128": 1, "R64": 2, "R32": 3, "R16": 4,
    "QF": 5, "SF": 6, "F": 7, "RR": 1,
}


def _round_n(s) -> int:
    """Convierte etiqueta de ronda (R128, QF, SF...) a entero ordinal."""
    return _ROUND_ORDER.get(str(s or "").strip().upper(), 0)


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
    # Estadísticas de saque (rolling últimos 20 partidos)
    "first_serve_in_pct_diff",
    "first_serve_win_pct_diff",
    "bp_save_rate_diff",
    "ace_rate_diff",
    # Descanso y retorno de ausencia
    "rest_advantage_diff",   # p1_days_off - p2_days_off, capped 60
    "p1_returning",          # 1 si p1 sin jugar > 30 días
    # Fatiga intratorneo
    "p1_sets_last_match",     # sets jugados por p1 en último partido de este torneo
    "tourney_rounds_rest_diff", # rondas de descanso intra-torneo p1 - p2
]

_SERVE_WINDOW    = 20   # rolling window para stats de saque
_SURF_SERVE_MIN  = 5    # mínimo partidos en la superficie para usar stats específicas
_SERVE_ATP_AVG: Dict[str, float] = {   # promedios ATP circuit (imputación cuando no hay historia)
    "first_serve_in_pct":  0.62,
    "first_serve_win_pct": 0.73,
    "bp_save_rate":        0.63,
    "ace_rate":            0.06,
}


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

    # Stats de saque rolling: {(player_id, surface|'all'): deque}
    # Se guarda en clave surf Y en 'all' para poder hacer fallback
    serve_stats: Dict[Tuple, deque] = defaultdict(
        lambda: deque(maxlen=_SERVE_WINDOW)
    )

    # Última fecha de partido por jugador (para calcular días de descanso)
    _REST_CAP = 60          # cap en días (evita outliers de lesiones multi-año)
    _REST_THRESHOLD = 30    # umbral para flag "retorno de ausencia"
    last_match_date: Dict[int, object] = {}

    # Estado de fatiga intratorneo: {(player_id, tourney_id): (last_date, sets_played)}
    tourney_state: Dict[Tuple, object] = {}

    def _serve_feats(pid: int, surf_key: str = "all") -> Dict[str, float]:
        # Prefer surface-specific if enough history, else fall back to 'all'
        hist = list(serve_stats[(pid, surf_key)])
        if len(hist) < _SURF_SERVE_MIN:
            hist = list(serve_stats[(pid, "all")])
        if len(hist) < 3:
            return dict(_SERVE_ATP_AVG)
        svpt  = sum(h[0] for h in hist)
        f1in  = sum(h[1] for h in hist)
        f1won = sum(h[2] for h in hist)
        bps   = sum(h[3] for h in hist)
        bpf   = sum(h[4] for h in hist)
        ace   = sum(h[5] for h in hist)
        return {
            "first_serve_in_pct":  f1in  / svpt if svpt > 0 else 0.62,
            "first_serve_win_pct": f1won / f1in  if f1in  > 0 else 0.73,
            "bp_save_rate":        bps   / bpf   if bpf   > 0 else 0.63,
            "ace_rate":            ace   / svpt  if svpt  > 0 else 0.06,
        }

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
        w_id      = int(row["winner_id"])
        l_id      = int(row["loser_id"])
        surf      = _normalize_surface(row.get("surface", "")) or "Hard"
        lvl       = str(row.get("tourney_level", "A") or "A")
        date      = row["tourney_date"]
        year      = date.year if pd.notna(date) else None
        draw      = int(row.get("draw_size") or 0)
        tourney_id    = str(row.get("tourney_id", "") or "")
        current_round = _round_n(row.get("round", ""))

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

            # Estadísticas de saque pre-partido (sin look-ahead)
            w_sf = _serve_feats(w_id, surf)
            l_sf = _serve_feats(l_id, surf)
            serve_diff = {
                "first_serve_in_pct_diff":  w_sf["first_serve_in_pct"]  - l_sf["first_serve_in_pct"],
                "first_serve_win_pct_diff": w_sf["first_serve_win_pct"] - l_sf["first_serve_win_pct"],
                "bp_save_rate_diff":        w_sf["bp_save_rate"]         - l_sf["bp_save_rate"],
                "ace_rate_diff":            w_sf["ace_rate"]              - l_sf["ace_rate"],
            }
            serve_diff_inv = {k: -v for k, v in serve_diff.items()}

            # Días de descanso de cada jugador antes de este partido
            _td = pd.Timedelta(days=1)
            if w_id in last_match_date and pd.notna(last_match_date[w_id]):
                w_days = min(int((date - last_match_date[w_id]) / _td), _REST_CAP)
            else:
                w_days = 0
            if l_id in last_match_date and pd.notna(last_match_date[l_id]):
                l_days = min(int((date - last_match_date[l_id]) / _td), _REST_CAP)
            else:
                l_days = 0

            # Fatiga intratorneo: último partido en ESTE torneo
            _td1 = pd.Timedelta(days=1)
            def _t_days(pid: int) -> int:
                key = (pid, tourney_id)
                if key not in tourney_state or not pd.notna(tourney_state[key][0]):
                    return 0
                return max(0, int((date - tourney_state[key][0]) / _td1))
            def _t_sets(pid: int) -> int:
                key = (pid, tourney_id)
                return tourney_state[key][1] if key in tourney_state else 0
            def _t_rounds_back(pid: int) -> int:
                key = (pid, tourney_id)
                if key not in tourney_state or current_round == 0:
                    return current_round   # primer partido: lleva todo el torneo desde el inicio
                return current_round - tourney_state[key][2]

            w_t_days  = _t_days(w_id)
            l_t_days  = _t_days(l_id)
            w_t_sets  = _t_sets(w_id)
            l_t_sets  = _t_sets(l_id)
            w_t_rounds = _t_rounds_back(w_id)
            l_t_rounds = _t_rounds_back(l_id)

            # Rankings históricos del CSV Sackmann (disponibles en entrenamiento)
            def _safe_rank(v) -> float:
                try:
                    r = float(v)
                    return r if r > 0 else 0.0
                except (TypeError, ValueError):
                    return 0.0
            w_rank = _safe_rank(row.get("winner_rank"))
            l_rank = _safe_rank(row.get("loser_rank"))
            # Si ambos ranks son válidos: calcular diferencia logarítmica real
            # Fila A (ganador=p1): positivo porque el ganador suele estar mejor rankeado
            # Fila B (perdedor=p1): se niega — ver abajo
            if w_rank > 0 and l_rank > 0:
                rdl_winner_as_p1 = _safe_log(l_rank) - _safe_log(w_rank)
            else:
                rdl_winner_as_p1 = 0.0   # ranking no disponible para este partido

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
                # ranking_diff_log se añade individualmente a cada fila (signo opuesto)
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
                "ranking_diff_log":  rdl_winner_as_p1,
                "rest_advantage_diff":  float(w_days - l_days),
                "p1_returning":         1.0 if w_days > _REST_THRESHOLD else 0.0,
                "p1_sets_last_match":   float(w_t_sets),
                "tourney_rounds_rest_diff": float(w_t_rounds - l_t_rounds),
                "target": 1,
                **serve_diff,
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
                "ranking_diff_log":  -rdl_winner_as_p1,
                "rest_advantage_diff":  float(l_days - w_days),
                "p1_returning":         1.0 if l_days > _REST_THRESHOLD else 0.0,
                "p1_sets_last_match":   float(l_t_sets),
                "tourney_rounds_rest_diff": float(l_t_rounds - w_t_rounds),
                "target": 0,
                **serve_diff_inv,
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

        # Estadísticas de saque (actualizar después de registrar features)
        def _si(v) -> int:
            try: return max(0, int(float(v or 0)))
            except: return 0
        w_svpt = _si(row.get("w_svpt"));  l_svpt = _si(row.get("l_svpt"))
        if w_svpt > 0:
            w_entry = (
                w_svpt, _si(row.get("w_1stIn")), _si(row.get("w_1stWon")),
                _si(row.get("w_bpSaved")), _si(row.get("w_bpFaced")),
                _si(row.get("w_ace")),
            )
            serve_stats[(w_id, surf)].append(w_entry)
            serve_stats[(w_id, "all")].append(w_entry)
        if l_svpt > 0:
            l_entry = (
                l_svpt, _si(row.get("l_1stIn")), _si(row.get("l_1stWon")),
                _si(row.get("l_bpSaved")), _si(row.get("l_bpFaced")),
                _si(row.get("l_ace")),
            )
            serve_stats[(l_id, surf)].append(l_entry)
            serve_stats[(l_id, "all")].append(l_entry)

        # Última fecha de partido (actualizar después de registrar features)
        if pd.notna(date):
            last_match_date[w_id] = date
            last_match_date[l_id] = date

        # Fatiga intratorneo (actualizar después de registrar features)
        if pd.notna(date) and tourney_id:
            n_sets = _count_sets(str(row.get("score", "") or ""))
            tourney_state[(w_id, tourney_id)] = (date, n_sets, current_round)
            tourney_state[(l_id, tourney_id)] = (date, n_sets, current_round)

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


# ── Stats de saque para predicción en tiempo real ────────────────────────────

def _compute_serve_stats_from_history(
    pid: int,
    matches_df: pd.DataFrame,
    window: int = _SERVE_WINDOW,
    as_of_date=None,
    surface: Optional[str] = None,
) -> Dict[str, float]:
    """
    Calcula rolling serve stats de un jugador a partir del historial de partidos.
    Si se pasa `surface` y hay ≥ _SURF_SERVE_MIN partidos en esa superficie, usa esas stats;
    si no, cae hacia el pool global.
    """
    if matches_df.empty:
        return dict(_SERVE_ATP_AVG)
    mdf = matches_df if as_of_date is None else matches_df[matches_df["tourney_date"] < as_of_date]

    needed_w = ["tourney_date", "w_svpt", "w_1stIn", "w_1stWon", "w_bpSaved", "w_bpFaced", "w_ace"]
    needed_l = ["tourney_date", "l_svpt", "l_1stIn", "l_1stWon", "l_bpSaved", "l_bpFaced", "l_ace"]
    if not all(c in mdf.columns for c in needed_w):
        return dict(_SERVE_ATP_AVG)

    def _build_combined(df: pd.DataFrame) -> pd.DataFrame:
        won  = df[df["winner_id"] == pid][needed_w].copy()
        won.columns  = ["tourney_date", "svpt", "1stIn", "1stWon", "bpSaved", "bpFaced", "ace"]
        lost = df[df["loser_id"]  == pid][needed_l].copy()
        lost.columns = ["tourney_date", "svpt", "1stIn", "1stWon", "bpSaved", "bpFaced", "ace"]
        combined = pd.concat([won, lost]).sort_values("tourney_date").tail(window)
        combined = combined.apply(pd.to_numeric, errors="coerce").fillna(0)
        return combined[combined["svpt"] > 0]

    # Intentar stats específicas de superficie
    if surface and "surface" in mdf.columns:
        surf_combined = _build_combined(mdf[mdf["surface"] == surface])
        if len(surf_combined) >= _SURF_SERVE_MIN:
            combined = surf_combined
        else:
            combined = _build_combined(mdf)   # fallback al pool global
    else:
        combined = _build_combined(mdf)

    if len(combined) < 3:
        return dict(_SERVE_ATP_AVG)

    svpt  = combined["svpt"].sum()
    f1in  = combined["1stIn"].sum()
    f1won = combined["1stWon"].sum()
    bps   = combined["bpSaved"].sum()
    bpf   = combined["bpFaced"].sum()
    ace   = combined["ace"].sum()
    return {
        "first_serve_in_pct":  f1in  / svpt if svpt > 0 else 0.62,
        "first_serve_win_pct": f1won / f1in  if f1in  > 0 else 0.73,
        "bp_save_rate":        bps   / bpf   if bpf   > 0 else 0.63,
        "ace_rate":            ace   / svpt  if svpt  > 0 else 0.06,
    }


# ── Rest days helper ─────────────────────────────────────────────────────────

_REST_CAP_LIVE = 60
_REST_THRESHOLD_LIVE = 30


def _compute_rest_features(
    p1_id: int,
    p2_id: int,
    matches_df: pd.DataFrame,
    as_of_date=None,
) -> Dict[str, float]:
    """Calcula rest_advantage_diff y p1_returning a partir del historial."""
    if matches_df.empty or "winner_id" not in matches_df.columns:
        return {"rest_advantage_diff": 0.0, "p1_returning": 0.0}

    mdf = matches_df if as_of_date is None else matches_df[matches_df["tourney_date"] < as_of_date]
    ref_date = pd.Timestamp(as_of_date) if as_of_date is not None else pd.Timestamp.today()

    def _days(pid: int) -> int:
        played = mdf[(mdf["winner_id"] == pid) | (mdf["loser_id"] == pid)]
        if played.empty:
            return 0
        last = pd.Timestamp(played["tourney_date"].max())
        delta = (ref_date - last).days
        return min(max(0, int(delta)), _REST_CAP_LIVE)

    p1_days = _days(p1_id)
    p2_days = _days(p2_id)
    return {
        "rest_advantage_diff": float(p1_days - p2_days),
        "p1_returning":        1.0 if p1_days > _REST_THRESHOLD_LIVE else 0.0,
    }


# ── Features para predicción en tiempo real ───────────────────────────────────

def _compute_tourney_fatigue(
    p1_id: int,
    p2_id: int,
    tourney_id: Optional[str],
    matches_df: pd.DataFrame,
    current_round: Optional[str] = None,
    as_of_date=None,
) -> Dict[str, float]:
    """Fatiga intratorneo: sets y rondas desde último partido en este torneo."""
    if not tourney_id or matches_df.empty or "tourney_id" not in matches_df.columns:
        return {"p1_sets_last_match": 0.0, "tourney_rounds_rest_diff": 0.0}

    mdf = matches_df if as_of_date is None else matches_df[matches_df["tourney_date"] < as_of_date]
    tmatch = mdf[mdf["tourney_id"].astype(str) == str(tourney_id)]
    cur_round_n = _round_n(current_round)

    def _prev_match(pid: int):
        pm = tmatch[(tmatch["winner_id"] == pid) | (tmatch["loser_id"] == pid)]
        if pm.empty:
            return 0, cur_round_n   # no prev match → (sets=0, rounds_back=full tournament depth)
        last_row = pm.loc[pm.index[-1]] if "round" not in pm.columns else \
                   pm.sort_values(lambda r: r["round"].map(_ROUND_ORDER).fillna(0)).iloc[-1]
        n_sets   = _count_sets(str(last_row.get("score", "") or ""))
        last_r   = _round_n(last_row.get("round", ""))
        rounds_back = cur_round_n - last_r if (cur_round_n > 0 and last_r > 0) else 0
        return n_sets, rounds_back

    p1_sets, p1_rounds = _prev_match(p1_id)
    p2_sets, p2_rounds = _prev_match(p2_id)
    return {
        "p1_sets_last_match":      float(p1_sets),
        "tourney_rounds_rest_diff": float(p1_rounds - p2_rounds),
    }


def compute_live_features(
    p1_id: int,
    p2_id: int,
    surface: str,
    tourney_level: str,
    elos: Dict[int, Dict[str, float]],
    rankings: Dict[int, int],
    matches_df: pd.DataFrame,
    as_of_date: Optional[pd.Timestamp] = None,
    tourney_id: Optional[str] = None,
    tourney_round: Optional[str] = None,
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

    # ── Estadísticas de saque ─────────────────────────────────────────────────
    p1_serve = _compute_serve_stats_from_history(p1_id, matches_df, as_of_date=as_of_date, surface=surf)
    p2_serve = _compute_serve_stats_from_history(p2_id, matches_df, as_of_date=as_of_date, surface=surf)

    return {
        "elo_diff":                  elo_diff,
        "elo_win_prob":              elo_prob,
        "h2h_win_rate":              h2h_win_rate,
        "h2h_surface_rate":          h2h_surface_rate,
        "h2h_total_log":             _safe_log(h2h_total),
        "form_diff":                 form_diff,
        "form_surface_diff":         form_surface_diff,
        "is_clay":                   1.0 if surf == "Clay"  else 0.0,
        "is_grass":                  1.0 if surf == "Grass" else 0.0,
        "is_grand_slam":             1.0 if lvl == "G"      else 0.0,
        "is_masters":                1.0 if lvl == "M"      else 0.0,
        "ranking_diff_log":          ranking_diff_log,
        "p1_matches_log":            p1_matches_log,
        "first_serve_in_pct_diff":   p1_serve["first_serve_in_pct"]  - p2_serve["first_serve_in_pct"],
        "first_serve_win_pct_diff":  p1_serve["first_serve_win_pct"] - p2_serve["first_serve_win_pct"],
        "bp_save_rate_diff":         p1_serve["bp_save_rate"]         - p2_serve["bp_save_rate"],
        "ace_rate_diff":             p1_serve["ace_rate"]              - p2_serve["ace_rate"],
        # Descanso y retorno de ausencia
        **_compute_rest_features(p1_id, p2_id, matches_df, as_of_date),
        # Fatiga intratorneo
        **_compute_tourney_fatigue(p1_id, p2_id, tourney_id, matches_df, tourney_round, as_of_date),
    }
