"""
Monitor de salud del modelo NBA.

Calcula métricas de rendimiento en producción y detecta drift:
  - Accuracy acumulado por versión de modelo
  - Accuracy reciente (últimos 30 días) — para detectar degradación
  - Drift = accuracy_reciente - accuracy_acumulado (negativo = degradación)
  - Calibración por bucket de confianza (50-60%, 60-70%, 70%+)

Persiste los resultados en Neon (tabla model_health) para que el dashboard
pueda mostrarlos. Es read-only sobre las tablas existentes.

Uso:
    python utils/model_monitor.py              # calcula y persiste
    python utils/model_monitor.py --dry-run    # solo imprime, no persiste
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import DATABASE_URL
from utils.logger import get_logger

logger = get_logger(__name__)

# Umbral de drift: si el accuracy reciente cae más de X puntos porcentuales
# respecto al acumulado, se considera drift significativo.
DRIFT_THRESHOLD_PCT = 10.0

# Ventana de "reciente" para detectar degradación
RECENT_DAYS = 30


def _load_predictions_from_db() -> pd.DataFrame:
    """Carga predicciones con resultado desde Neon."""
    from sqlalchemy import create_engine, text

    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT game_id, game_date, model_version, home_win_prob,
                       away_win_prob, predicted_winner, home_win
                FROM predictions
                WHERE home_win IS NOT NULL
            """)
        ).mappings().all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


def _load_predictions_from_csv() -> pd.DataFrame:
    """Fallback: carga predicciones desde predictions.csv local."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "output", "predictions.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path, dtype=str)
    df = df[df["home_win"].notna() & (~df["home_win"].astype(str).str.strip().isin(["", "nan"]))]
    return df


def compute_health(dry_run: bool = False) -> dict:
    """Calcula métricas de salud del modelo y las persiste en Neon."""
    # ── Cargar datos ──────────────────────────────────────────────────────────
    df = pd.DataFrame()
    if DATABASE_URL:
        try:
            df = _load_predictions_from_db()
            logger.info("Monitor: %d predicciones con resultado desde Neon", len(df))
        except Exception as exc:
            logger.warning("Monitor: Neon no disponible (%s) — usando CSV", exc)
    if df.empty:
        df = _load_predictions_from_csv()
        logger.info("Monitor: %d predicciones con resultado desde CSV", len(df))

    if df.empty:
        logger.warning("Monitor: sin datos de predicciones con resultado")
        return {"error": "sin datos"}

    # ── Normalizar ────────────────────────────────────────────────────────────
    df["home_win"] = pd.to_numeric(df["home_win"], errors="coerce")
    df["home_win_prob"] = pd.to_numeric(df["home_win_prob"], errors="coerce")
    df["away_win_prob"] = pd.to_numeric(df["away_win_prob"], errors="coerce")
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce").dt.date

    # Probabilidad del lado predicho (la que el modelo "creyó")
    df["pred_prob"] = df.apply(
        lambda r: r["home_win_prob"] if r["predicted_winner"] == "home" else r["away_win_prob"],
        axis=1,
    )
    # Acierto: 1 si el modelo acertó
    df["correct"] = df.apply(
        lambda r: 1 if (
            (r["predicted_winner"] == "home" and r["home_win"] == 1)
            or (r["predicted_winner"] == "away" and r["home_win"] == 0)
        ) else 0,
        axis=1,
    )

    today = date.today()
    cutoff = today - timedelta(days=RECENT_DAYS)

    # ── Métricas por versión ──────────────────────────────────────────────────
    by_model = {}
    for mv, grp in df.groupby("model_version"):
        total = len(grp)
        correct = int(grp["correct"].sum())
        acc = round(correct / total * 100, 1) if total else 0

        recent = grp[grp["game_date"] >= cutoff]
        recent_total = len(recent)
        recent_correct = int(recent["correct"].sum())
        recent_acc = round(recent_correct / recent_total * 100, 1) if recent_total else None

        drift = round(recent_acc - acc, 1) if recent_acc is not None else None

        # Calibración por bucket de confianza
        buckets = {}
        for lo, hi, label in [(0.5, 0.6, "50-60%"), (0.6, 0.7, "60-70%"), (0.7, 1.01, "70%+")]:
            b = grp[(grp["pred_prob"] >= lo) & (grp["pred_prob"] < hi)]
            if len(b) == 0:
                buckets[label] = {"bets": 0, "correct": 0, "accuracy": None}
            else:
                b_correct = int(b["correct"].sum())
                buckets[label] = {
                    "bets": len(b),
                    "correct": b_correct,
                    "accuracy": round(b_correct / len(b) * 100, 1),
                }

        by_model[mv] = {
            "model_version": mv,
            "total": total,
            "correct": correct,
            "accuracy_pct": acc,
            "recent_total": recent_total,
            "recent_correct": recent_correct,
            "recent_accuracy_pct": recent_acc,
            "drift_pct": drift,
            "drift_detected": drift is not None and drift < -DRIFT_THRESHOLD_PCT,
            "buckets": buckets,
        }

    # ── Métricas globales ─────────────────────────────────────────────────────
    total = len(df)
    correct = int(df["correct"].sum())
    acc = round(correct / total * 100, 1) if total else 0
    recent = df[df["game_date"] >= cutoff]
    recent_total = len(recent)
    recent_correct = int(recent["correct"].sum())
    recent_acc = round(recent_correct / recent_total * 100, 1) if recent_total else None
    drift = round(recent_acc - acc, 1) if recent_acc is not None else None

    health = {
        "generated_at": today.isoformat(),
        "total_predictions": total,
        "accuracy_pct": acc,
        "recent_accuracy_pct": recent_acc,
        "recent_window_days": RECENT_DAYS,
        "drift_pct": drift,
        "drift_detected": drift is not None and drift < -DRIFT_THRESHOLD_PCT,
        "drift_threshold_pct": DRIFT_THRESHOLD_PCT,
        "by_model": by_model,
    }

    logger.info(
        "Monitor: acc=%s%% | reciente=%s%% | drift=%s | drift_detected=%s",
        acc, recent_acc, drift, health["drift_detected"],
    )

    # ── Persistir en Neon ─────────────────────────────────────────────────────
    if not dry_run and DATABASE_URL:
        try:
            import json
            from sqlalchemy import create_engine, text
            engine = create_engine(DATABASE_URL, pool_pre_ping=True)
            with engine.begin() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS model_health (
                        id            SERIAL PRIMARY KEY,
                        generated_at  DATE NOT NULL,
                        model_version VARCHAR(20),
                        total         INTEGER,
                        correct       INTEGER,
                        accuracy_pct  FLOAT,
                        recent_total  INTEGER,
                        recent_correct INTEGER,
                        recent_accuracy_pct FLOAT,
                        drift_pct     FLOAT,
                        drift_detected BOOLEAN,
                        buckets       JSONB,
                        created_at    TIMESTAMPTZ DEFAULT now()
                    )
                """))
                for mv, m in by_model.items():
                    conn.execute(text("""
                        INSERT INTO model_health
                            (generated_at, model_version, total, correct, accuracy_pct,
                             recent_total, recent_correct, recent_accuracy_pct,
                             drift_pct, drift_detected, buckets)
                        VALUES
                            (:generated_at, :model_version, :total, :correct, :accuracy_pct,
                             :recent_total, :recent_correct, :recent_accuracy_pct,
                             :drift_pct, :drift_detected, CAST(:buckets AS JSONB))
                    """), {
                        "generated_at": today.isoformat(),
                        "model_version": mv,
                        "total": m["total"],
                        "correct": m["correct"],
                        "accuracy_pct": m["accuracy_pct"],
                        "recent_total": m["recent_total"],
                        "recent_correct": m["recent_correct"],
                        "recent_accuracy_pct": m["recent_accuracy_pct"],
                        "drift_pct": m["drift_pct"],
                        "drift_detected": m["drift_detected"],
                        "buckets": json.dumps(m["buckets"]),
                    })
            logger.info("Monitor: salud persistida en Neon (model_health)")
        except Exception as exc:
            logger.warning("Monitor: no se pudo persistir en Neon: %s", exc)

    return health


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Solo imprime, no persiste")
    args = parser.parse_args()
    result = compute_health(dry_run=args.dry_run)
    if "error" not in result:
        print(f"Accuracy: {result['accuracy_pct']}% | Reciente: {result['recent_accuracy_pct']}% | "
              f"Drift: {result['drift_pct']} | Detectado: {result['drift_detected']}")
