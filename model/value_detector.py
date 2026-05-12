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
  - format_value_bets_report(value_bets_df)    → string para logging/output
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

# Umbral mínimo de valor para considerar una oportunidad
VALUE_THRESHOLD = float(0)   # value > 0 es suficiente; se puede subir a 0.05 para filtrar ruido


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
    merged = odds_df.merge(
        predictions_df[["game_id", "game_date", "home_win_prob", "away_win_prob",
                         "home_team_id", "visitor_team_id"]],
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


def format_value_bets_report(df: pd.DataFrame) -> str:
    """
    Genera un resumen legible de las value bets detectadas para logging o consola.

    Args:
        df: Resultado de detect_value_bets().

    Returns:
        String con el reporte formateado.
    """
    if df.empty:
        return "Sin value bets disponibles para reportar."

    value_bets = df[df["is_value_bet"]].copy()

    if value_bets.empty:
        return (
            f"Se analizaron {len(df)} combinaciones.\n"
            "No se encontraron value bets (value > 0) para los partidos de hoy."
        )

    lines = [
        "=" * 60,
        f"  VALUE BETS DETECTADAS ({len(value_bets)} de {len(df)} combinaciones)",
        "=" * 60,
    ]

    for _, row in value_bets.iterrows():
        lines.append(
            f"  {row['game_id'][:10]:<12} | {row['side'].upper():<5} | "
            f"{str(row.get('team_name','')):<20} | "
            f"Bookmaker: {str(row['bookmaker']):<12} | "
            f"Prob: {row['model_prob']:.1%}  Cuota: {row['odds']:.2f}  "
            f"VALUE: +{row['value']:.3f}"
        )

    lines.append("=" * 60)
    return "\n".join(lines)
