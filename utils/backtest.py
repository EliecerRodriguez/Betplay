"""
Módulo de backtesting para el modelo NBA.

Simula apuestas históricas usando la predicción del modelo + estrategia Kelly,
calculando métricas de rendimiento reales:

  - Hit rate: % de predicciones correctas
  - ROI:      Retorno sobre inversión total
  - Max drawdown: peor racha negativa sobre bankroll
  - Sharpe ratio: retorno / volatilidad de apuestas
  - Calibración: expected vs actual win rate por bucket

Uso desde línea de comandos:
    python -m utils.backtest --version v3 --start-date 2025-10-22 --end-date 2026-05-10

Uso programático:
    from utils.backtest import run_backtest
    results = run_backtest(feature_df, model_version="v3")
    print(results.summary())
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional

import numpy as np
import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Tipos de datos
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    """Resultados completos de una simulación de backtesting."""
    n_games:         int
    n_bets:          int            # partidos donde Kelly > umbral mínimo
    hit_rate:        float          # predicciones correctas / total predicciones
    bet_hit_rate:    float          # hits en partidos con apuesta Kelly
    roi:             float          # retorno neto / apuestas totales
    profit_units:    float          # unidades de ganancia (bankroll=1)
    max_drawdown:    float          # peor caída de bankroll pico a valle
    sharpe:          float          # media retorno / std retorno
    avg_kelly_frac:  float          # fracción Kelly promedio
    log_loss:        float          # log loss calibración
    brier_score:     float          # brier score calibración
    bets_df:         pd.DataFrame   # detalle por apuesta
    bucket_df:       pd.DataFrame   # calibración por bucket de probabilidad

    def summary(self) -> str:
        lines = [
            "=" * 55,
            f"  BACKTEST RESULTADOS",
            "=" * 55,
            f"  Partidos analizados : {self.n_games}",
            f"  Apuestas realizadas : {self.n_bets}",
            f"  Hit rate total      : {self.hit_rate:.1%}",
            f"  Hit rate apostado   : {self.bet_hit_rate:.1%}",
            f"  ROI                 : {self.roi:+.2%}",
            f"  Profit (unidades)   : {self.profit_units:+.4f}",
            f"  Max Drawdown        : {self.max_drawdown:.2%}",
            f"  Sharpe Ratio        : {self.sharpe:.3f}",
            f"  Avg Kelly Fraction  : {self.avg_kelly_frac:.2%}",
            f"  Log Loss            : {self.log_loss:.4f}",
            f"  Brier Score         : {self.brier_score:.4f}",
            "=" * 55,
        ]
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Función principal
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(
    feature_df: pd.DataFrame,
    model_version: Optional[str] = None,
    min_kelly: float = 0.01,
    kelly_fraction: float = 0.25,   # Kelly fraccionado (1=full Kelly, 0.25=quarter Kelly)
    flat_odds: float = 1.90,        # cuota implícita para apuestas sin cuotas reales
) -> BacktestResult:
    """
    Simula apuestas históricas sobre feature_df con el modelo cargado.

    Args:
        feature_df:     DataFrame con features + columna 'home_win' (resultado real).
        model_version:  Versión del modelo a cargar (usa MODEL_VERSION de env si None).
        min_kelly:      Fracción Kelly mínima para realizar una apuesta.
        kelly_fraction: Multiplicador del Kelly (0.25 = quarter Kelly, más conservador).
        flat_odds:      Cuota decimal asumida si no hay cuotas reales disponibles.

    Returns:
        BacktestResult con todas las métricas.
    """
    from model.predictor import predict as model_predict, load_model

    if "home_win" not in feature_df.columns:
        raise ValueError("feature_df debe tener columna 'home_win' con resultados reales")

    df = feature_df.copy()
    df = df[df["home_win"].notna()].reset_index(drop=True)
    n_games = len(df)
    logger.info("Backtesting sobre %d partidos con resultado conocido", n_games)

    # ── Predicciones del modelo ───────────────────────────────────────────────
    try:
        preds = model_predict(df, version=model_version)
    except Exception as exc:
        raise RuntimeError(f"Error cargando modelo para backtesting: {exc}") from exc

    df["home_win_prob"] = preds["home_win_prob"].values
    df["away_win_prob"] = preds["away_win_prob"].values

    # ── Kelly + P&L por partido ───────────────────────────────────────────────
    records = []
    bankroll = 1.0
    peak_bankroll = 1.0
    max_dd = 0.0

    for _, row in df.iterrows():
        home_prob  = float(row["home_win_prob"])
        away_prob  = float(row["away_win_prob"])
        actual_win = int(row["home_win"])

        # Elegir el lado con mayor valor esperado
        # Kelly = (p * b - (1-p)) / b,  donde b = odds - 1
        b = flat_odds - 1.0
        kelly_home = (home_prob * b - (1 - home_prob)) / b
        kelly_away = (away_prob * b - (1 - away_prob)) / b

        if kelly_home >= kelly_away and kelly_home > min_kelly:
            side      = "home"
            raw_kelly = kelly_home
            prob_used = home_prob
            bet_won   = (actual_win == 1)
        elif kelly_away > kelly_home and kelly_away > min_kelly:
            side      = "away"
            raw_kelly = kelly_away
            prob_used = away_prob
            bet_won   = (actual_win == 0)
        else:
            # Sin apuesta suficientemente favorable
            records.append({
                "side": None, "kelly_frac": 0.0, "bet_size": 0.0,
                "pnl": 0.0, "bankroll": bankroll, "bet": False,
                "home_prob": home_prob, "actual_win": actual_win,
            })
            continue

        # Kelly fraccionado — apuesta sobre bankroll INICIAL fijo (1.0) para evitar overflow
        frac     = min(raw_kelly * kelly_fraction, 0.20)   # cap en 20%
        bet_size = frac   # fracción del bankroll inicial = 1.0 (flat-Kelly)
        pnl      = bet_size * b if bet_won else -bet_size
        bankroll += pnl

        # Drawdown
        if bankroll > peak_bankroll:
            peak_bankroll = bankroll
        dd = (peak_bankroll - bankroll) / peak_bankroll
        if dd > max_dd:
            max_dd = dd

        records.append({
            "side":       side,
            "kelly_frac": frac,
            "bet_size":   bet_size,
            "pnl":        pnl,
            "bankroll":   bankroll,
            "bet":        True,
            "bet_won":    bet_won,
            "home_prob":  home_prob,
            "actual_win": actual_win,
        })

    bets_df  = pd.DataFrame(records)
    n_bets   = int(bets_df["bet"].sum())
    bets_only = bets_df[bets_df["bet"] == True]

    # ── Métricas ──────────────────────────────────────────────────────────────
    hit_rate = (df["home_win_prob"] > 0.5).astype(int).eq(df["home_win"].astype(int)).mean()
    bet_hit_rate = bets_only["bet_won"].mean() if not bets_only.empty else 0.0

    total_staked = bets_only["bet_size"].sum() if not bets_only.empty else 0.0
    net_profit   = bets_only["pnl"].sum() if not bets_only.empty else 0.0
    roi          = net_profit / total_staked if total_staked > 0 else 0.0

    # Profit en unidades: retorno aritmetico total (sin compounding)
    if not bets_only.empty:
        profit_units = float(bets_only["pnl"].sum())   # en unidades de bankroll inicial = 1
    else:
        profit_units = 0.0

    # Sharpe (por apuesta)
    if not bets_only.empty and len(bets_only) > 1:
        returns_pct = bets_only["pnl"] / bets_only["bet_size"]
        sharpe = returns_pct.mean() / returns_pct.std() if returns_pct.std() > 0 else 0.0
    else:
        sharpe = 0.0

    avg_kelly = bets_only["kelly_frac"].mean() if not bets_only.empty else 0.0

    # ── Calibración ───────────────────────────────────────────────────────────
    y_pred = df["home_win_prob"].values
    y_true = df["home_win"].values.astype(float)
    log_loss  = _log_loss(y_true, y_pred)
    brier     = float(np.mean((y_pred - y_true) ** 2))

    # Bucketing de calibración (10 buckets)
    bucket_df = _calibration_buckets(y_true, y_pred)

    return BacktestResult(
        n_games=n_games,
        n_bets=n_bets,
        hit_rate=hit_rate,
        bet_hit_rate=float(bet_hit_rate),
        roi=roi,
        profit_units=profit_units,
        max_drawdown=max_dd,
        sharpe=float(sharpe),
        avg_kelly_frac=float(avg_kelly),
        log_loss=log_loss,
        brier_score=brier,
        bets_df=bets_df,
        bucket_df=bucket_df,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────────────────

def _log_loss(y_true: np.ndarray, y_pred: np.ndarray, eps: float = 1e-15) -> float:
    """Log loss manual (no requiere sklearn en este módulo)."""
    y_pred  = np.clip(y_pred, eps, 1 - eps)
    return float(-np.mean(y_true * np.log(y_pred) + (1 - y_true) * np.log(1 - y_pred)))


def _calibration_buckets(y_true: np.ndarray, y_pred: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    """Devuelve DataFrame de calibración por decil de probabilidad predicha."""
    bins = np.linspace(0, 1, n_bins + 1)
    rows = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (y_pred >= lo) & (y_pred < hi)
        if mask.sum() == 0:
            continue
        rows.append({
            "bin_low":     round(lo, 2),
            "bin_high":    round(hi, 2),
            "n":           int(mask.sum()),
            "mean_pred":   round(float(y_pred[mask].mean()), 4),
            "actual_rate": round(float(y_true[mask].mean()), 4),
        })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _cli() -> int:
    parser = argparse.ArgumentParser(description="Backtesting del modelo NBA")
    parser.add_argument("--version",    default=None, help="Versión del modelo (ej: v3)")
    parser.add_argument("--start-date", required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--end-date",   default=None,  metavar="YYYY-MM-DD")
    parser.add_argument("--season",     default=None,  help="Temporada NBA (ej: 2025-26)")
    parser.add_argument("--kelly",      type=float, default=0.25, help="Fracción Kelly (default: 0.25)")
    parser.add_argument("--min-kelly",  type=float, default=0.01, help="Kelly mínimo para apostar")
    parser.add_argument("--cache-dir",  default="data/cache")
    parser.add_argument("--with-form",  action="store_true", help="Incluir forma reciente")
    args = parser.parse_args()

    from config.settings import NBA_SEASON
    from ingestion.nba_client import get_team_stats
    from processing.features import build_features, clean_team_stats

    season = args.season or NBA_SEASON
    start  = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end    = (datetime.strptime(args.end_date, "%Y-%m-%d").date()
              if args.end_date else date.today() - timedelta(days=1))

    from train_model import _date_range, _fetch_completed_games, _merge_scores
    dates    = _date_range(start, end)
    games_df = _fetch_completed_games(dates, cache_dir=args.cache_dir)

    if games_df.empty:
        logger.error("Sin partidos para el rango especificado")
        return 1

    stats_df = get_team_stats(season)
    stats_df = clean_team_stats(stats_df)

    if args.with_form:
        from ingestion.recent_form import enrich_with_form
        if "game_date" not in games_df.columns and "game_date_est" in games_df.columns:
            games_df["game_date"] = pd.to_datetime(games_df["game_date_est"]).dt.date
        logger.info("Enriqueciendo con forma reciente…")
        games_df = enrich_with_form(games_df, season=season, n=5)

    feature_df = build_features(games_df, stats_df)

    if "home_win" not in feature_df.columns:
        if "home_win" in games_df.columns:
            hw = games_df[["game_id", "home_win"]].copy()
            feature_df = feature_df.drop(columns=["home_win"], errors="ignore")
            feature_df = feature_df.merge(hw, on="game_id", how="left")

    result = run_backtest(feature_df, model_version=args.version,
                          min_kelly=args.min_kelly, kelly_fraction=args.kelly)
    print(result.summary())
    print()
    print("NOTA: Esta evaluacion usa todos los datos historicos incluyendo")
    print("partidos del set de entrenamiento. Use un rango post-entrenamiento")
    print("para obtener metricas fuera de muestra mas realistas.")
    print("Ejemplo: --start-date 2026-02-01 para evaluar solo en datos recientes.")


    # Calibración
    print("\nCalibración por bucket:")
    print(result.bucket_df.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
