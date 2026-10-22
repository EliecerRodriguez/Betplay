"""
Monitor de salud de los modelos Liga BetPlay (yellow/corners).

Calcula métricas de rendimiento en producción y detecta drift:
  - Accuracy acumulado por versión de modelo
  - Accuracy reciente (últimos 30 días) — para detectar degradación
  - Drift = accuracy_reciente - accuracy_acumulado (negativo = degradación)
  - Calibración por bucket de confianza (50-60%, 60-70%, 70%+)

Fuente de datos: output/betplay_bet_journal.csv (apuestas registradas con
resultado real reconciliado por _reconcile_betplay_results en web/app.py).

Persiste los resultados en Neon (tabla betplay_model_health) para que el
dashboard pueda mostrarlos. Es read-only sobre las tablas existentes.

Uso:
    python utils/betplay_model_monitor.py              # calcula y persiste
    python utils/betplay_model_monitor.py --dry-run    # solo imprime, no persiste
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


def _load_journal() -> pd.DataFrame:
    """Carga el journal de apuestas BetPlay con resultados reconciliados."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "output", "betplay_bet_journal.csv")
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path, dtype=str)
    # Solo apuestas con resultado real (correct = 0/1)
    df = df[df["correct"].notna() & (~df["correct"].astype(str).str.strip().isin(["", "nan"]))]
    return df


def compute_health(dry_run: bool = False) -> dict:
    """Calcula métricas de salud de los modelos BetPlay y las persiste en Neon."""
    df = _load_journal()
    if df.empty:
        logger.warning("Monitor BetPlay: sin apuestas con resultado en el journal")
        return {"error": "sin datos"}

    # ── Normalizar ────────────────────────────────────────────────────────────
    df["correct"] = pd.to_numeric(df["correct"], errors="coerce")
    df["model_prob_pct"] = pd.to_numeric(df["model_prob_pct"], errors="coerce")
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce").dt.date

    # Probabilidad del lado predicho (la que el modelo "creyó")
    df["pred_prob"] = df["model_prob_pct"] / 100.0

    today = date.today()
    cutoff = today - timedelta(days=RECENT_DAYS)

    # ── Métricas por versión de modelo ────────────────────────────────────────
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
        "Monitor BetPlay: acc=%s%% | reciente=%s%% | drift=%s | drift_detected=%s",
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
                    CREATE TABLE IF NOT EXISTS betplay_model_health (
                        id            SERIAL PRIMARY KEY,
                        generated_at  DATE NOT NULL,
                        model_version VARCHAR(50),
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
                        INSERT INTO betplay_model_health
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
            logger.info("Monitor BetPlay: salud persistida en Neon (betplay_model_health)")
        except Exception as exc:
            logger.warning("Monitor BetPlay: no se pudo persistir en Neon: %s", exc)

    return health


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Solo imprime, no persiste")
    args = parser.parse_args()
    result = compute_health(dry_run=args.dry_run)
    if "error" not in result:
        print(f"Accuracy: {result['accuracy_pct']}% | Reciente: {result['recent_accuracy_pct']}% | "
              f"Drift: {result['drift_pct']} | Detectado: {result['drift_detected']}")