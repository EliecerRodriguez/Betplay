"""
Simulación Monte Carlo para predicción de partidos NBA.

Para cada partido, simula N=50,000 juegos muestreando de distribuciones
normales de puntos para cada equipo, basadas en su rendimiento reciente.

Metodología:
  - Pts esperados local    = (home_scored_recent + visitor_allowed_recent) / 2
  - Pts esperados visitante = (visitor_scored_recent + home_allowed_recent) / 2
  - Std = SCORE_STD_DEFAULT = 11.5 pts (desviación histórica NBA, calibrada empíricamente)
  - Se simula N veces; la fracción de victorias locales es mc_home_win_prob

Por qué Monte Carlo añade valor sobre el modelo base:
  - El modelo base da P(victoria) sin modelar la distribución de puntos
  - Monte Carlo captura la incertidumbre de cada marcador individual
  - Permite calcular spreads y totales con intervalos de confianza
  - La combinación ponderada (blend) reduce el error de calibración

Outputs por partido:
  - mc_home_win_prob   Fracción de simulaciones ganadas por el local
  - mc_spread          Spread medio simulado (local - visitante; positivo = home gana)
  - mc_spread_std      Desviación estándar del spread simulado
  - mc_total           Total de puntos medio simulado
  - mc_over_225_prob   P(total > 225 pts)
  - mc_confidence      max(mc_home_win_prob, 1 - mc_home_win_prob)
  - mc_blend_prob      Media ponderada 60/40 modelo-base / MC (reduce sobreajuste)

Uso:
  from model.monte_carlo import enrich_predictions_with_mc
  predictions_df = enrich_predictions_with_mc(predictions_df, feature_df)
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

# ── Constantes calibradas para NBA ───────────────────────────────────────────
N_SIMULATIONS    = 50_000   # balance precisión / velocidad (~0.1s por partido)
SCORE_STD        = 11.5     # desviación típica pts/partido histórica NBA
HOME_ADV_PTS     = 3.0      # ventaja local en puntos (media histórica NBA)
OVER_THRESHOLD   = 225.0    # umbral over/under por defecto
MC_BLEND_WEIGHT  = 0.40     # peso del resultado MC en mc_blend_prob (modelo base = 0.60)

# Pts medios de temporada regular NBA 2024-25 (fallback si no hay forma reciente)
_NBA_MEAN_PTS = 113.0
_RNG = np.random.default_rng(42)   # generador determinista global


def _expected_pts(
    scored_recent: float,
    allowed_recent: float,
    opponent_allowed_recent: float,
    opponent_scored_recent: float,
    is_home: bool,
) -> float:
    """
    Calcula la media esperada de puntos de un equipo usando cuatro factores:
    - Promedio simple: (own_scored + opp_allowed) / 2
    - Ajuste por ventaja local: ±HOME_ADV_PTS / 2

    Args:
        scored_recent:          Pts/partido anotados por el equipo (forma reciente).
        allowed_recent:         Pts/partido recibidos por el equipo.
        opponent_allowed_recent: Pts/partido recibidos por el rival.
        opponent_scored_recent:  Pts/partido anotados por el rival.
        is_home:                 True si el equipo juega en casa.

    Returns:
        Media esperada de puntos para este partido.
    """
    # Blend ofensa propia vs defensa rival
    base = (scored_recent + opponent_allowed_recent) / 2.0
    # Ajuste de local
    adj  = HOME_ADV_PTS / 2.0 if is_home else -(HOME_ADV_PTS / 2.0)
    return base + adj


def simulate_game(
    home_mean: float,
    visitor_mean: float,
    n: int = N_SIMULATIONS,
    rng: Optional[np.random.Generator] = None,
) -> dict:
    """
    Simula un partido N veces muestreando de distribuciones normales.

    Args:
        home_mean:    Pts esperados del equipo local.
        visitor_mean: Pts esperados del equipo visitante.
        n:            Número de simulaciones.
        rng:          Generador de números aleatorios (opcional; usa global si None).

    Returns:
        dict con mc_home_win_prob, mc_spread, mc_spread_std, mc_total,
              mc_over_225_prob, mc_confidence.
    """
    if rng is None:
        rng = _RNG

    home_pts    = rng.normal(home_mean,    SCORE_STD, n)
    visitor_pts = rng.normal(visitor_mean, SCORE_STD, n)

    # Empates (~1 en 10M): se resuelven en OT con la media
    spread    = home_pts - visitor_pts
    total     = home_pts + visitor_pts
    home_wins = (spread > 0).sum()

    return {
        "mc_home_win_prob": round(float(home_wins / n), 4),
        "mc_spread":        round(float(spread.mean()), 2),
        "mc_spread_std":    round(float(spread.std()), 2),
        "mc_total":         round(float(total.mean()), 1),
        "mc_over_225_prob": round(float((total > OVER_THRESHOLD).sum() / n), 4),
        "mc_confidence":    round(float(max(home_wins / n, 1 - home_wins / n)), 4),
    }


def enrich_predictions_with_mc(
    predictions_df: pd.DataFrame,
    feature_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Añade columnas Monte Carlo a predictions_df.

    Requiere que feature_df tenga al menos algunas de estas columnas
    (usa fallbacks NBA si faltan):
      - home_recent_pts_scored_5    home_recent_pts_allowed_5
      - visitor_recent_pts_scored_5 visitor_recent_pts_allowed_5
      - home_pts  visitor_pts  (stats de temporada; segundo fallback)

    Args:
        predictions_df: Salida de model.predictor.predict().
        feature_df:     Salida de processing.features.build_features().

    Returns:
        predictions_df con columnas mc_* añadidas.
    """
    if predictions_df.empty or feature_df.empty:
        logger.warning("enrich_predictions_with_mc: DataFrames vacíos — saltando MC")
        return predictions_df

    # Merge predicciones + features para acceder a pts stats
    stats_cols = [
        "game_id",
        "home_recent_pts_scored_5",  "home_recent_pts_allowed_5",
        "visitor_recent_pts_scored_5","visitor_recent_pts_allowed_5",
        "home_pts",   "visitor_pts",  # season avg fallback
    ]
    available = [c for c in stats_cols if c in feature_df.columns]
    merged = predictions_df.merge(
        feature_df[available],
        on="game_id",
        how="left",
        suffixes=("", "_feat"),
    )

    mc_records = []
    for _, row in merged.iterrows():
        # ── Pts esperados: forma reciente > stats temporada > media NBA ───────
        h_scored  = _coerce(row.get("home_recent_pts_scored_5"),
                            row.get("home_pts"), _NBA_MEAN_PTS)
        h_allowed = _coerce(row.get("home_recent_pts_allowed_5"),
                            row.get("visitor_pts"), _NBA_MEAN_PTS)
        v_scored  = _coerce(row.get("visitor_recent_pts_scored_5"),
                            row.get("visitor_pts"), _NBA_MEAN_PTS)
        v_allowed = _coerce(row.get("visitor_recent_pts_allowed_5"),
                            row.get("home_pts"), _NBA_MEAN_PTS)

        home_mean    = _expected_pts(h_scored, h_allowed, v_allowed, v_scored, is_home=True)
        visitor_mean = _expected_pts(v_scored, v_allowed, h_allowed, h_scored, is_home=False)

        result = simulate_game(home_mean, visitor_mean)

        # Blend: combina probabilidad del modelo base con MC
        base_prob = float(row.get("home_win_prob", 0.5))
        result["mc_blend_prob"] = round(
            (1 - MC_BLEND_WEIGHT) * base_prob + MC_BLEND_WEIGHT * result["mc_home_win_prob"],
            4,
        )
        mc_records.append(result)

    mc_df = pd.DataFrame(mc_records)
    out   = predictions_df.copy().reset_index(drop=True)
    for col in mc_df.columns:
        out[col] = mc_df[col].values

    logger.info(
        "Monte Carlo: %d partidos simulados × %d iteraciones | "
        "home_win_prob media=%.3f  mc_blend_prob media=%.3f",
        len(out), N_SIMULATIONS,
        out["home_win_prob"].mean(),
        out["mc_blend_prob"].mean(),
    )
    return out


def _coerce(primary, secondary, default: float) -> float:
    """Devuelve el primer valor no-nulo y positivo entre primary, secondary, default."""
    for v in (primary, secondary, default):
        try:
            f = float(v)
            if f > 0:
                return f
        except (TypeError, ValueError):
            continue
    return default
