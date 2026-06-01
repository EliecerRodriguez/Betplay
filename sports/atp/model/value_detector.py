"""
Detección de value bets ATP.

Fórmula de valor esperado:
    value = (probabilidad_modelo × cuota_decimal) - 1

  value > 0  → apuesta con esperanza positiva
  value ≤ 0  → sin valor

La probabilidad del modelo (Elo + features) se compara contra la
probabilidad implícita de las cuotas de Betplay/Rushbet/The Odds API.

Umbrales configurables:
  VALUE_THRESHOLD        → valor mínimo para reportar (default: 0.05 = 5%)
  MIN_MODEL_CONFIDENCE   → el modelo debe tener al menos X% de certeza
  MIN_ODDS               → cuota mínima para considerar (evitar favoritos extremos)
  MAX_ODDS               → cuota máxima (evitar +20 que suelen ser ruido)
  MAX_MODEL_MARKET_GAP   → diferencia máxima permitida entre probabilidad del
                           modelo y probabilidad implícita del mercado.
                           Si el modelo difiere >N pp del mercado, descartar:
                           el mercado de Betplay/Rushbet es más informado cuando
                           el desacuerdo es grande. (default: 0.10 = 10 pp)
  EXCLUDE_QUALIFYING     → si True, excluye torneos de clasificatorias donde
                           los Elos de jugadores desconocidos son poco fiables.

Funciones públicas:
  - detect_value_bets(predictions_df, odds_df)  → DataFrame de value bets
  - format_value_report(value_bets_df)           → string legible para logs
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

# ── Umbrales ──────────────────────────────────────────────────────────────────
VALUE_THRESHOLD      = 0.05   # 5% de valor mínimo (filtra ruido leve)
MIN_MODEL_CONFIDENCE = 0.68   # el modelo debe tener al menos 68% para considerar
MIN_ODDS             = 1.40   # no analizar cuotas menores (favorito muy claro)
MAX_ODDS             = 15.0   # no analizar cuotas mayores (demasiado inciertas)
MAX_MODEL_MARKET_GAP = 0.10   # descarta si modelo difiere >10pp del mercado
EXCLUDE_QUALIFYING   = True   # excluye partidos de clasificatorias (Elos poco fiables)

# Palabras clave en el nombre del torneo que identifican clasificatorias
_QUALIFYING_KEYWORDS = (
    "clasificator",   # español: "Clasificatorios Open de Francia"
    "qualifying",     # inglés
    "qualif",
    "previa",
    "qualifier",
)


def detect_value_bets(
    predictions_df: pd.DataFrame,
    odds_df: pd.DataFrame,
    value_threshold: float = VALUE_THRESHOLD,
    min_model_confidence: float = MIN_MODEL_CONFIDENCE,
    max_model_market_gap: float = MAX_MODEL_MARKET_GAP,
    exclude_qualifying: bool = EXCLUDE_QUALIFYING,
) -> pd.DataFrame:
    """
    Detecta value bets cruzando predicciones del modelo con cuotas de mercado.

    Args:
        predictions_df: DataFrame del predictor ATP.
            Columnas requeridas:
              - match_id             (str)
              - player1_name / player2_name
              - p1_win_prob          (float 0-1, probabilidad del modelo)
              - p2_win_prob          (float)
              - surface, tourney_name, tourney_level
            Columnas opcionales:
              - elo_diff, elo_win_prob, mc_p1_win_prob, h2h_p1_win_rate

        odds_df: DataFrame de odds_client.get_atp_odds().
            Columnas requeridas:
              - player1_name / player2_name
              - player1_odds / player2_odds
              - player1_implied_prob / player2_implied_prob
              - bookmaker
              - tourney_name, surface

        value_threshold:       Valor mínimo EV para reportar.
        min_model_confidence:  Probabilidad mínima del modelo para el lado apostado.
        max_model_market_gap:  Diferencia máxima entre prob. modelo y prob. implícita
                               del mercado. Descarta apuestas donde el modelo discrepa
                               demasiado del mercado (señal de error en features).
        exclude_qualifying:    Si True, descarta torneos de clasificatorias.

    Returns:
        DataFrame de value bets ordenado por value descendente.
        Columnas: match_id, player, opponent, bookmaker, model_prob,
                  market_odds, market_prob, value, kelly_fraction,
                  tourney_name, surface, tourney_level, game_date.
    """
    if predictions_df.empty or odds_df.empty:
        logger.warning("detect_value_bets: sin predicciones o sin cuotas")
        return pd.DataFrame()

    records = []

    # Pre-filtrar predicciones de clasificatorias si corresponde
    preds_filtered = predictions_df
    if exclude_qualifying and "tourney_name" in predictions_df.columns:
        qualifying_mask = predictions_df["tourney_name"].str.lower().str.contains(
            "|".join(_QUALIFYING_KEYWORDS), na=False
        )
        n_excluded = int(qualifying_mask.sum())
        if n_excluded:
            logger.info(
                "detect_value_bets: excluyendo %d partidos de clasificatorias",
                n_excluded,
            )
        preds_filtered = predictions_df[~qualifying_mask].reset_index(drop=True)

    for _, pred in preds_filtered.iterrows():
        p1 = str(pred.get("player1_name", ""))
        p2 = str(pred.get("player2_name", ""))
        p1_prob = float(pred.get("p1_win_prob", 0.5))
        p2_prob = float(pred.get("p2_win_prob", 1.0 - p1_prob))

        # Buscar cuotas para este partido en odds_df
        p1_n = p1.lower()
        p2_n = p2.lower()
        match_odds = odds_df[
            (
                odds_df["player1_name"].str.lower().str.contains(p1_n, na=False) |
                odds_df["player2_name"].str.lower().str.contains(p1_n, na=False)
            ) & (
                odds_df["player1_name"].str.lower().str.contains(p2_n, na=False) |
                odds_df["player2_name"].str.lower().str.contains(p2_n, na=False)
            )
        ]

        if match_odds.empty:
            continue

        for _, odd_row in match_odds.iterrows():
            bk = str(odd_row.get("bookmaker", ""))
            inverted = p1_n in str(odd_row.get("player2_name", "")).lower()

            # Alinear: p1 del modelo = player1 en la fila de odds
            if inverted:
                p1_odds = float(odd_row.get("player2_odds", 0) or 0)
                p2_odds = float(odd_row.get("player1_odds", 0) or 0)
                p1_mkt  = float(odd_row.get("player2_implied_prob", 0.5) or 0.5)
                p2_mkt  = float(odd_row.get("player1_implied_prob", 0.5) or 0.5)
            else:
                p1_odds = float(odd_row.get("player1_odds", 0) or 0)
                p2_odds = float(odd_row.get("player2_odds", 0) or 0)
                p1_mkt  = float(odd_row.get("player1_implied_prob", 0.5) or 0.5)
                p2_mkt  = float(odd_row.get("player2_implied_prob", 0.5) or 0.5)

            tourney = str(odd_row.get("tourney_name", pred.get("tourney_name", "")))
            surface = str(odd_row.get("surface", pred.get("surface", "")))
            t_level = str(odd_row.get("tourney_level", pred.get("tourney_level", "")))
            gdate   = str(odd_row.get("game_date", date.today().isoformat()))

            # Evaluar ambos lados del partido
            for player, opponent, model_prob, market_odds, market_prob in [
                (p1, p2, p1_prob, p1_odds, p1_mkt),
                (p2, p1, p2_prob, p2_odds, p2_mkt),
            ]:
                if market_odds < MIN_ODDS or market_odds > MAX_ODDS:
                    continue
                if model_prob < min_model_confidence:
                    continue

                # Descartar cuando el modelo discrepa demasiado del mercado
                # (el mercado de Betplay/Rushbet es más fiable en esos casos)
                gap = abs(model_prob - market_prob)
                if gap > max_model_market_gap:
                    logger.debug(
                        "Descartando %s (gap=%.0f%% > límite=%.0f%%)",
                        player, gap * 100, max_model_market_gap * 100,
                    )
                    continue

                value = round((model_prob * market_odds) - 1, 4)

                if value < value_threshold:
                    continue

                # Criterio de Kelly fraccionario (Kelly/8 = muy conservador mientras
                # la accuracy real del modelo se establece > 55%)
                # f = (p × b - q) / b  donde b = odds-1, p = prob_modelo, q = 1-p
                b = market_odds - 1.0
                q = 1.0 - model_prob
                kelly_full     = (model_prob * b - q) / b if b > 0 else 0
                kelly_fraction = round(max(0, kelly_full / 8), 4)   # ⅛ Kelly

                records.append({
                    "match_id":       str(pred.get("match_id", "")),
                    "player":         player,
                    "opponent":       opponent,
                    "bookmaker":      bk,
                    "model_prob":     round(model_prob, 4),
                    "market_odds":    round(market_odds, 3),
                    "market_prob":    round(market_prob, 4),
                    "value":          value,
                    "kelly_fraction": kelly_fraction,
                    "tourney_name":   tourney,
                    "surface":        surface,
                    "tourney_level":  t_level,
                    "game_date":      gdate,
                })

    if not records:
        logger.info("No se encontraron value bets ATP para los parámetros dados")
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df = df.sort_values("value", ascending=False).reset_index(drop=True)
    logger.info(
        "Value bets ATP detectadas: %d oportunidades (threshold=%.0f%%)",
        len(df), value_threshold * 100,
    )
    return df


def format_value_report(value_bets_df: pd.DataFrame) -> str:
    """
    Genera un reporte legible de value bets para consola/log.

    Returns:
        String multi-línea con el resumen de todas las oportunidades.
    """
    if value_bets_df.empty:
        return "Sin value bets ATP detectadas."

    lines = [
        "=" * 65,
        f"  VALUE BETS ATP — {date.today().isoformat()}",
        f"  {len(value_bets_df)} oportunidades detectadas",
        "=" * 65,
    ]

    for i, row in value_bets_df.iterrows():
        level_tag = {"G": "🏆 GS", "M": "⭐ M1000", "A": "ATP 500/250"}.get(
            str(row.get("tourney_level", "")), "ATP"
        )
        lines.append(
            f"  #{i+1:02d} | {level_tag} {row['tourney_name']} [{row['surface']}]"
        )
        lines.append(
            f"       APOSTAR: {row['player']} vs {row['opponent']}"
        )
        lines.append(
            f"       Casa: {row['bookmaker']} @ {row['market_odds']:.2f}"
        )
        lines.append(
            f"       Modelo: {row['model_prob']*100:.1f}% | Mercado: {row['market_prob']*100:.1f}% | Valor: +{row['value']*100:.1f}%"
        )
        lines.append(
            f"       Kelly (1/8): {row['kelly_fraction']*100:.1f}% del bankroll"
        )
        lines.append("")

    lines.append("=" * 65)
    return "\n".join(lines)
