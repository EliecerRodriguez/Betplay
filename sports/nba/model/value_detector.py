"""
Fase 5: Detección de value bets.

Fórmula de valor esperado:
    value = (probabilidad_modelo × cuota_decimal) - 1

  value > 0  → apuesta de valor (esperanza positiva)
  value ≤ 0  → sin valor

Ejemplo:
  modelo_prob = 0.60  (el modelo cree que hay 60% de prob de victoria)
  cuota       = 2.10  (casa de apuestas paga 2.10 por cada 1 apostado)
  value       = (0.60 × 2.10) - 1 = 1.26 - 1 = 0.26  ← value bet!

Funciones:
  - detect_value_bets(predictions_df, odds_df) → DataFrame de value bets
"""
from __future__ import annotations

from datetime import date

import pandas as pd
from scipy.stats import norm

from utils.logger import get_logger

logger = get_logger(__name__)

# Umbral mínimo de valor para considerar una oportunidad
VALUE_THRESHOLD = float(0)   # value > 0 es suficiente; se puede subir a 0.05 para filtrar ruido

# Desviación estándar para el total de puntos del partido (√2 × SCORE_STD, donde SCORE_STD=11.5)
TOTAL_STD = 16.27


def detect_value_bets(
    predictions_df: pd.DataFrame,
    odds_df: pd.DataFrame,
    value_threshold: float = VALUE_THRESHOLD,
) -> pd.DataFrame:
    """
    Compara las probabilidades del modelo contra las cuotas de las casas de apuestas
    y detecta oportunidades de valor.

    Args:
        predictions_df: Resultado de model.predictor.predict().
                        Columnas requeridas: game_id, home_win_prob, away_win_prob.
        odds_df:        Resultado de ingestion.odds_client.get_odds().
                        Columnas requeridas: game_id, bookmaker, home_odds, away_odds,
                                             home_team, away_team.
        value_threshold: Valor mínimo para marcar como value bet (por defecto 0).

    Returns:
        DataFrame con todas las combinaciones partido × casa × lado, incluyendo:
          - model_prob, odds, value, is_value_bet
          - Ordenado por value descendente
    """
    if predictions_df.empty:
        logger.warning("detect_value_bets: sin predicciones — no se puede calcular valor")
        return pd.DataFrame()

    if odds_df.empty:
        logger.warning("detect_value_bets: sin cuotas — no se puede calcular valor")
        return pd.DataFrame()

    today = date.today().isoformat()

    # ── Merge predicciones + cuotas por game_id ───────────────────────────────
    pred_cols = ["game_id", "game_date", "home_win_prob", "away_win_prob",
                 "home_team_id", "visitor_team_id"]
    if "mc_total" in predictions_df.columns:
        pred_cols.append("mc_total")

    merged = odds_df.merge(
        predictions_df[pred_cols],
        on="game_id",
        how="inner",
    )

    if merged.empty:
        logger.warning(
            "detect_value_bets: no se encontraron game_ids comunes entre predicciones y cuotas. "
            "Verifica que los game_ids coincidan."
        )
        return pd.DataFrame()

    # ── Calcular valor para lado local y visitante ────────────────────────────
    records = []

    for _, row in merged.iterrows():
        game_id  = str(row["game_id"])
        bk       = str(row["bookmaker"])
        gdate    = row.get("game_date")

        # Lado local
        if pd.notna(row.get("home_odds")) and pd.notna(row.get("home_win_prob")):
            home_value = (row["home_win_prob"] * row["home_odds"]) - 1
            records.append({
                "game_id":      game_id,
                "game_date":    gdate,
                "bookmaker":    bk,
                "side":         "home",
                "team_name":    str(row.get("home_team", "")),
                "model_prob":   round(float(row["home_win_prob"]), 4),
                "odds":         round(float(row["home_odds"]), 2),
                "value":        round(home_value, 4),
                "is_value_bet": home_value > value_threshold,
                "total_line":   None,
                "fetch_date":   today,
            })

        # Lado visitante
        if pd.notna(row.get("away_odds")) and pd.notna(row.get("away_win_prob")):
            away_value = (row["away_win_prob"] * row["away_odds"]) - 1
            records.append({
                "game_id":      game_id,
                "game_date":    gdate,
                "bookmaker":    bk,
                "side":         "away",
                "team_name":    str(row.get("away_team", "")),
                "model_prob":   round(float(row["away_win_prob"]), 4),
                "odds":         round(float(row["away_odds"]), 2),
                "value":        round(away_value, 4),
                "is_value_bet": away_value > value_threshold,
                "total_line":   None,
                "fetch_date":   today,
            })

        # Over / Under (solo si hay mc_total y cuotas O/U en la fila)
        mc_total = row.get("mc_total")
        over_line  = row.get("over_line")
        over_odds  = row.get("over_odds")
        under_odds = row.get("under_odds")

        if (
            pd.notna(mc_total)
            and pd.notna(over_line)
            and pd.notna(over_odds)
            and pd.notna(under_odds)
        ):
            line = float(over_line)
            mu   = float(mc_total)

            # P(total > line) usando distribución normal con media=mc_total, std=TOTAL_STD
            prob_over  = round(float(1 - norm.cdf(line, mu, TOTAL_STD)), 4)
            prob_under = round(float(norm.cdf(line, mu, TOTAL_STD)), 4)

            over_value  = (prob_over  * float(over_odds))  - 1
            under_value = (prob_under * float(under_odds)) - 1

            records.append({
                "game_id":      game_id,
                "game_date":    gdate,
                "bookmaker":    bk,
                "side":         "over",
                "team_name":    f"OVER {line}",
                "model_prob":   prob_over,
                "odds":         round(float(over_odds), 2),
                "value":        round(over_value, 4),
                "is_value_bet": over_value > value_threshold,
                "total_line":   line,
                "fetch_date":   today,
            })
            records.append({
                "game_id":      game_id,
                "game_date":    gdate,
                "bookmaker":    bk,
                "side":         "under",
                "team_name":    f"UNDER {line}",
                "model_prob":   prob_under,
                "odds":         round(float(under_odds), 2),
                "value":        round(under_value, 4),
                "is_value_bet": under_value > value_threshold,
                "total_line":   line,
                "fetch_date":   today,
            })

    if not records:
        logger.warning("detect_value_bets: ningún registro válido generado")
        return pd.DataFrame()

    result_df = pd.DataFrame(records).sort_values("value", ascending=False).reset_index(drop=True)

    n_total = len(result_df)
    n_value = result_df["is_value_bet"].sum()
    logger.info(
        "detect_value_bets: %d combinaciones analizadas | %d value bets (value > %.2f)",
        n_total, n_value, value_threshold,
    )
    return result_df
