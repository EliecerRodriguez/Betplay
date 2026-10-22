"""
Detección de value bets para Liga BetPlay.

Fórmula (idéntica a NBA): value = (prob_modelo × cuota_decimal) - 1
  - Prob del modelo Over línea (P(over) del predictor BetPlay)
  - Cuota Over de la casa (Kambi/Betplay)
  - value > 0 → value bet (el modelo ve más probabilidad que la casa)

Mercados: Over/Under Córners (Total Tiros de Esquina) y Tarjetas.
"""
from __future__ import annotations

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

VALUE_THRESHOLD = float(0)   # > 0 suficiente; subir a 0.05 para filtrar ruido


def detect_value_bets(
    predictions_df: pd.DataFrame,
    odds_df: pd.DataFrame,
    value_threshold: float = VALUE_THRESHOLD,
) -> pd.DataFrame:
    """
    Compara probabilidades del modelo vs cuotas Over/Under de córners y tarjetas.

    Args:
        predictions_df: con game_id, over_corners_prob, over_yellow_prob
                        (del predictor BetPlay).
        odds_df: con event_id (o game_id), corners_line, corners_over_odds,
                 corners_under_odds, cards_line, cards_over_odds, cards_under_odds.

    Returns:
        DataFrame con value bets: partido, mercado, lado (over/under),
        prob_modelo, cuota, value, is_value_bet.
    """
    if predictions_df.empty or odds_df.empty:
        logger.warning("detect_value_bets: sin datos")
        return pd.DataFrame()

    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    records = []

    def _norm(name: str) -> str:
        """Normaliza nombre de equipo para unir ESPN ↔ Kambi."""
        if not isinstance(name, str):
            return ""
        return (name.lower()
                .replace("s.a", "").replace("s.a.", "").replace("club deportivo", "")
                .replace("club", "").replace("deportivo", "").replace("deportes", "")
                .replace("atl", "atletico").replace("á", "a").replace("é", "e")
                .replace("í", "i").replace("ó", "o").replace("ú", "u")
                .replace("aguilas", "aguilas").replace("rionegro", "")
                .replace("doradas", "").strip())

    def _match_prediction(o_row) -> pd.DataFrame:
        """Busca la predicción del partido por nombre de equipo normalizado."""
        if predictions_df.empty:
            return pd.DataFrame()
        o_home = _norm(o_row.get("home_team", ""))
        o_away = _norm(o_row.get("away_team", ""))
        if not o_home or not o_away:
            return pd.DataFrame()
        mask = (
            predictions_df["home_team"].map(_norm).eq(o_home)
            & predictions_df["away_team"].map(_norm).eq(o_away)
        )
        return predictions_df[mask]

    # ── Córners ───────────────────────────────────────────────────────────────
    corners_odds = odds_df[odds_df["corners_line"].notna()].copy()
    for _, o in corners_odds.iterrows():
        pred_row = _match_prediction(o)
        if pred_row.empty:
            continue
        gid = str(pred_row["game_id"].iloc[0])
        prob_over = float(pred_row["over_corners_prob"].iloc[0])
        prob_under = 1 - prob_over

        # Over
        over_odds = o.get("corners_over_odds")
        if over_odds and pd.notna(over_odds):
            v_over = (prob_over * float(over_odds)) - 1
            records.append({
                "game_id": gid,
                "game_date": pred_row["game_date"].iloc[0] if "game_date" in pred_row else today,
                "home_team": pred_row["home_team"].iloc[0] if "home_team" in pred_row else "",
                "away_team": pred_row["away_team"].iloc[0] if "away_team" in pred_row else "",
                "market": "corners",
                "side": "over",
                "line": float(o["corners_line"]),
                "bookmaker": "Betplay",
                "model_prob": round(prob_over, 4),
                "odds": round(float(over_odds), 2),
                "value": round(v_over, 4),
                "is_value_bet": bool(v_over > value_threshold),
            })

        # Under
        under_odds = o.get("corners_under_odds")
        if under_odds and pd.notna(under_odds):
            v_under = (prob_under * float(under_odds)) - 1
            records.append({
                "game_id": gid,
                "game_date": pred_row["game_date"].iloc[0] if "game_date" in pred_row else today,
                "home_team": pred_row["home_team"].iloc[0] if "home_team" in pred_row else "",
                "away_team": pred_row["away_team"].iloc[0] if "away_team" in pred_row else "",
                "market": "corners",
                "side": "under",
                "line": float(o["corners_line"]),
                "bookmaker": "Betplay",
                "model_prob": round(prob_under, 4),
                "odds": round(float(under_odds), 2),
                "value": round(v_under, 4),
                "is_value_bet": bool(v_under > value_threshold),
            })

    # ── Tarjetas (si hay cuotas y predicciones) ──────────────────────────────
    if "cards_line" in odds_df.columns:
        cards_odds = odds_df[odds_df["cards_line"].notna()].copy()
        for _, o in cards_odds.iterrows():
            pred_row = _match_prediction(o)
            if pred_row.empty or "over_yellow_prob" not in pred_row.columns:
                continue
            gid = str(pred_row["game_id"].iloc[0])
            prob_over = float(pred_row["over_yellow_prob"].iloc[0])
            prob_under = 1 - prob_over

            over_odds = o.get("cards_over_odds")
            if over_odds and pd.notna(over_odds):
                v_over = (prob_over * float(over_odds)) - 1
                records.append({
                    "game_id": gid,
                    "game_date": pred_row["game_date"].iloc[0] if "game_date" in pred_row else today,
                    "home_team": pred_row["home_team"].iloc[0] if "home_team" in pred_row else "",
                    "away_team": pred_row["away_team"].iloc[0] if "away_team" in pred_row else "",
                    "market": "cards",
                    "side": "over",
                    "line": float(o["cards_line"]),
                    "bookmaker": "Betplay",
                    "model_prob": round(prob_over, 4),
                    "odds": round(float(over_odds), 2),
                    "value": round(v_over, 4),
                    "is_value_bet": bool(v_over > value_threshold),
                })

            under_odds = o.get("cards_under_odds")
            if under_odds and pd.notna(under_odds):
                v_under = (prob_under * float(under_odds)) - 1
                records.append({
                    "game_id": gid,
                    "game_date": pred_row["game_date"].iloc[0] if "game_date" in pred_row else today,
                    "home_team": pred_row["home_team"].iloc[0] if "home_team" in pred_row else "",
                    "away_team": pred_row["away_team"].iloc[0] if "away_team" in pred_row else "",
                    "market": "cards",
                    "side": "under",
                    "line": float(o["cards_line"]),
                    "bookmaker": "Betplay",
                    "model_prob": round(prob_under, 4),
                    "odds": round(float(under_odds), 2),
                    "value": round(v_under, 4),
                    "is_value_bet": bool(v_under > value_threshold),
                })

    if not records:
        logger.warning("detect_value_bets betplay: sin value bets calculables")
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df = df.sort_values("value", ascending=False)
    n_vb = int(df["is_value_bet"].sum())
    logger.info("detect_value_bets betplay: %d oportunidades (%d value bets)", len(df), n_vb)
    return df
