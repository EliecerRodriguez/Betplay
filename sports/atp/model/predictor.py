"""
Predictor ATP — inferencia en tiempo real.

Carga el modelo entrenado (XGBoost + calibración) y genera predicciones
para los partidos del día, combinando:

  1. Probabilidad Elo (la más confiable cuando no hay modelo entrenado)
  2. Probabilidad ML (XGBoost con 13 features)
  3. Blend: 40% Elo + 60% ML  (ajustable vía ELO_BLEND_WEIGHT)

Si no existe modelo entrenado, cae back al 100% Elo.

Funciones públicas:
  - predict(matchups_df)        → DataFrame con predicciones
  - predict_single(p1, p2, surf, tourney, tourney_level) → dict
  - load_model()                → pipeline sklearn
  - is_model_available()        → bool
"""
from __future__ import annotations

import os
from typing import Optional

import joblib
import numpy as np
import pandas as pd

from sports.atp.config.settings import ATP_ELO_PATH, ATP_MIN_MATCHES_FOR_PREDICTION
from sports.atp.ingestion.elo import (
    ELO_BASE, apply_elos_to_matchup, load_current_elos,
)
from sports.atp.ingestion.rankings_client import (
    get_player_id_by_name, get_rankings_dict,
)
from sports.atp.ingestion.historical_client import download_atp_matches
from sports.atp.processing.features import (
    FEATURE_COLUMNS, compute_live_features,
)
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Configuración ─────────────────────────────────────────────────────────────

_ATP_MODEL_DIR    = os.getenv("ATP_MODEL_DIR", "sports/atp/models")
_MODEL_FILENAME   = "atp_model_v1.joblib"
_ELO_BLEND_WEIGHT = float(os.getenv("ATP_ELO_BLEND_WEIGHT", "0.4"))   # 40% Elo

# Caché en memoria
_model_cache: Optional[object]          = None
_elos_cache:  Optional[dict]            = None
_matches_cache: Optional[pd.DataFrame] = None
_rankings_cache: Optional[dict]        = None


# ── Carga de recursos ─────────────────────────────────────────────────────────

def load_model() -> Optional[object]:
    """Carga el modelo ATP desde disco (cached en memoria)."""
    global _model_cache
    if _model_cache is not None:
        return _model_cache

    path = os.path.join(_ATP_MODEL_DIR, _MODEL_FILENAME)
    if not os.path.exists(path):
        logger.info("Modelo ATP no encontrado en %s — usando solo Elo", path)
        return None

    try:
        _model_cache = joblib.load(path)
        logger.info("Modelo ATP cargado: %s", path)
        return _model_cache
    except Exception as exc:
        logger.warning("Error cargando modelo ATP: %s", exc)
        return None


def is_model_available() -> bool:
    """Devuelve True si hay un modelo entrenado disponible."""
    path = os.path.join(_ATP_MODEL_DIR, _MODEL_FILENAME)
    return os.path.exists(path)


def _get_elos() -> dict:
    global _elos_cache
    if _elos_cache is None:
        _elos_cache = load_current_elos(ATP_ELO_PATH)
    return _elos_cache


def _get_matches() -> pd.DataFrame:
    global _matches_cache
    if _matches_cache is None or _matches_cache.empty:
        _matches_cache = download_atp_matches()
    return _matches_cache


def _get_rankings() -> dict:
    global _rankings_cache
    if _rankings_cache is None:
        _rankings_cache = get_rankings_dict(top_n=500)
    return _rankings_cache


# ── Predicción individual ─────────────────────────────────────────────────────

def predict_single(
    player1_name: str,
    player2_name: str,
    surface: str = "Hard",
    tourney_name: str = "",
    tourney_level: str = "A",
    player1_id: Optional[int] = None,
    player2_id: Optional[int] = None,
) -> dict:
    """
    Genera la predicción para un partido ATP individual.

    Args:
        player1_name:  Nombre del jugador 1 (cualquier formato).
        player2_name:  Nombre del jugador 2.
        surface:       'Hard' | 'Clay' | 'Grass'.
        tourney_name:  Nombre del torneo (para el log).
        tourney_level: 'G' | 'M' | 'A'.
        player1_id:    player_id Sackmann (opcional; se resuelve por nombre si falta).
        player2_id:    player_id Sackmann (opcional).

    Returns:
        {
          'player1_name'      : str,
          'player2_name'      : str,
          'player1_id'        : int | None,
          'player2_id'        : int | None,
          'p1_win_prob'       : float,       # probabilidad final (blend)
          'p2_win_prob'       : float,
          'p1_elo_prob'       : float,       # probabilidad Elo puro
          'p2_elo_prob'       : float,
          'p1_elo'            : float,
          'p2_elo'            : float,
          'elo_diff'          : float,
          'model_prob'        : float | None, # solo si hay modelo ML
          'surface'           : str,
          'tourney_name'      : str,
          'tourney_level'     : str,
          'method'            : str,          # 'elo' | 'blend'
          'features'          : dict,         # features usadas (para debug)
        }
    """
    # Resolver player IDs
    p1_id = player1_id or get_player_id_by_name(player1_name)
    p2_id = player2_id or get_player_id_by_name(player2_name)

    elos     = _get_elos()
    rankings = _get_rankings()
    matches  = _get_matches()

    # ── Elo baseline ──────────────────────────────────────────────────────────
    elo_stats = apply_elos_to_matchup(
        p1_id or 0, p2_id or 0, surface, elos
    )
    p1_elo_prob = elo_stats["p1_win_prob"]
    p2_elo_prob = 1.0 - p1_elo_prob

    result = {
        "player1_name":  player1_name,
        "player2_name":  player2_name,
        "player1_id":    p1_id,
        "player2_id":    p2_id,
        "p1_elo":        elo_stats["p1_elo"],
        "p2_elo":        elo_stats["p2_elo"],
        "elo_diff":      elo_stats["elo_diff"],
        "p1_elo_prob":   round(p1_elo_prob, 4),
        "p2_elo_prob":   round(p2_elo_prob, 4),
        "surface":       surface,
        "tourney_name":  tourney_name,
        "tourney_level": tourney_level,
        "method":        "elo",
        "model_prob":    None,
        "features":      {},
    }

    # Si no tenemos IDs, usar solo Elo
    if not p1_id or not p2_id:
        result["p1_win_prob"] = round(p1_elo_prob, 4)
        result["p2_win_prob"] = round(p2_elo_prob, 4)
        return result

    # ── Features completas ────────────────────────────────────────────────────
    try:
        features = compute_live_features(
            p1_id=p1_id,
            p2_id=p2_id,
            surface=surface,
            tourney_level=tourney_level,
            elos=elos,
            rankings=rankings,
            matches_df=matches,
        )
        result["features"] = features
    except Exception as exc:
        logger.warning("Error computando features para %s vs %s: %s", player1_name, player2_name, exc)
        result["p1_win_prob"] = round(p1_elo_prob, 4)
        result["p2_win_prob"] = round(p2_elo_prob, 4)
        return result

    # ── ML model (si disponible) ──────────────────────────────────────────────
    model = load_model()
    ml_prob = None

    if model is not None:
        try:
            feat_array = pd.DataFrame(
                [[features[col] for col in FEATURE_COLUMNS]],
                columns=FEATURE_COLUMNS,
            )
            ml_prob = float(model.predict_proba(feat_array)[0][1])
        except Exception as exc:
            logger.warning("Error en inferencia ML para %s vs %s: %s", player1_name, player2_name, exc)

    # ── Blend ─────────────────────────────────────────────────────────────────
    if ml_prob is not None:
        p1_blend = _ELO_BLEND_WEIGHT * p1_elo_prob + (1 - _ELO_BLEND_WEIGHT) * ml_prob
        result["method"]     = "blend"
        result["model_prob"] = round(ml_prob, 4)
    else:
        p1_blend = p1_elo_prob

    result["p1_win_prob"] = round(p1_blend, 4)
    result["p2_win_prob"] = round(1.0 - p1_blend, 4)

    return result


def predict(matchups_df: pd.DataFrame) -> pd.DataFrame:
    """
    Genera predicciones para un DataFrame de partidos.

    Args:
        matchups_df: DataFrame con columnas mínimas:
                     player1_name, player2_name, surface, tourney_name, tourney_level.
                     Columnas opcionales: player1_id, player2_id.

    Returns:
        DataFrame original enriquecido con columnas de predicción:
          p1_win_prob, p2_win_prob, p1_elo_prob, p1_elo, p2_elo,
          elo_diff, model_prob, method.
    """
    if matchups_df.empty:
        return matchups_df

    results = []
    for _, row in matchups_df.iterrows():
        pred = predict_single(
            player1_name  = str(row.get("player1_name", "")),
            player2_name  = str(row.get("player2_name", "")),
            surface       = str(row.get("surface", "Hard")),
            tourney_name  = str(row.get("tourney_name", "")),
            tourney_level = str(row.get("tourney_level", "A")),
            player1_id    = row.get("player1_id"),
            player2_id    = row.get("player2_id"),
        )
        results.append(pred)

    pred_df = pd.DataFrame(results)

    # Añadir columnas de predicción al DataFrame original
    out = matchups_df.copy()
    for col in [
        "player1_id", "player2_id", "p1_win_prob", "p2_win_prob",
        "p1_elo_prob", "p2_elo_prob", "p1_elo", "p2_elo", "elo_diff",
        "model_prob", "method",
    ]:
        if col in pred_df.columns:
            out[col] = pred_df[col].values

    return out
