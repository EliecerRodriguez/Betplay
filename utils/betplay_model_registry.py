"""
Registro de versiones de los modelos Liga BetPlay.

Persiste en Neon (tabla betplay_model_registry) las métricas de cada versión
entrenada (yellow y corners), para poder comparar versiones y decidir cuál
promover.

Uso:
    python utils/betplay_model_registry.py --version v3 --model models/betplay/betplay_yellow_model_v3.joblib
    python utils/betplay_model_registry.py --list          # lista versiones registradas
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import DATABASE_URL
from utils.logger import get_logger

logger = get_logger(__name__)


def _get_engine():
    from sqlalchemy import create_engine
    return create_engine(DATABASE_URL, pool_pre_ping=True)


def _ensure_table(conn) -> None:
    from sqlalchemy import text
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS betplay_model_registry (
            id            SERIAL PRIMARY KEY,
            version       VARCHAR(20) NOT NULL,
            model_type    VARCHAR(30) NOT NULL,
            accuracy      FLOAT,
            roc_auc       FLOAT,
            log_loss      FLOAT,
            brier         FLOAT,
            cv_mean       FLOAT,
            cv_std        FLOAT,
            n_samples     INTEGER,
            n_features    INTEGER,
            trained_at    DATE,
            promoted      BOOLEAN DEFAULT FALSE,
            created_at    TIMESTAMPTZ DEFAULT now(),
            UNIQUE (version, model_type)
        )
    """))


def register_model(version: str, model_path: str, promoted: bool = False) -> dict:
    """Registra las métricas de un modelo BetPlay entrenado en Neon."""
    import joblib

    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Modelo no encontrado: {model_path}")

    bundle = joblib.load(model_path)
    metrics = bundle.get("metrics", {})

    # Convertir numpy types a Python nativos (evita errores de serialización)
    def _native(v):
        if v is None:
            return None
        try:
            import numpy as np
            if isinstance(v, (np.integer,)):
                return int(v)
            if isinstance(v, (np.floating,)):
                return float(v)
        except ImportError:
            pass
        return v

    model_type = _native(metrics.get("model_type", "?"))
    record = {
        "version": version,
        "model_type": model_type,
        "accuracy": _native(metrics.get("accuracy")),
        "roc_auc": _native(metrics.get("roc_auc")),
        "log_loss": _native(metrics.get("log_loss")),
        "brier": _native(metrics.get("brier")),
        "cv_mean": _native(metrics.get("cv_mean")),
        "cv_std": _native(metrics.get("cv_std")),
        "n_samples": _native(metrics.get("n_samples")),
        "n_features": _native(metrics.get("n_features")),
        "trained_at": date.today().isoformat(),
        "promoted": promoted,
    }

    engine = _get_engine()
    with engine.begin() as conn:
        _ensure_table(conn)
        from sqlalchemy import text
        conn.execute(text("""
            INSERT INTO betplay_model_registry
                (version, model_type, accuracy, roc_auc, log_loss, brier,
                 cv_mean, cv_std, n_samples, n_features, trained_at, promoted)
            VALUES
                (:version, :model_type, :accuracy, :roc_auc, :log_loss, :brier,
                 :cv_mean, :cv_std, :n_samples, :n_features, :trained_at, :promoted)
            ON CONFLICT (version, model_type) DO UPDATE SET
                accuracy = EXCLUDED.accuracy,
                roc_auc = EXCLUDED.roc_auc,
                log_loss = EXCLUDED.log_loss,
                brier = EXCLUDED.brier,
                cv_mean = EXCLUDED.cv_mean,
                cv_std = EXCLUDED.cv_std,
                n_samples = EXCLUDED.n_samples,
                n_features = EXCLUDED.n_features,
                trained_at = EXCLUDED.trained_at,
                promoted = EXCLUDED.promoted
        """), record)
    logger.info("Modelo BetPlay %s (%s) registrado en Neon", version, model_type)
    return record


def list_models() -> list[dict]:
    """Lista las versiones registradas de modelos BetPlay."""
    engine = _get_engine()
    with engine.connect() as conn:
        _ensure_table(conn)
        from sqlalchemy import text
        rows = conn.execute(text("""
            SELECT version, model_type, accuracy, roc_auc, log_loss, brier,
                   cv_mean, cv_std, n_samples, n_features, trained_at, promoted
            FROM betplay_model_registry
            ORDER BY trained_at DESC, version DESC
        """)).mappings().all()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=None, help="Versión (ej. v3)")
    parser.add_argument("--model", default=None, help="Ruta al .joblib")
    parser.add_argument("--promoted", action="store_true", help="Marcar como promovido")
    parser.add_argument("--list", action="store_true", help="Listar versiones")
    args = parser.parse_args()

    if args.list:
        for m in list_models():
            print(f"{m['version']} | {m['model_type']} | acc={m['accuracy']} | "
                  f"cv={m['cv_mean']} | n={m['n_samples']} | promovido={m['promoted']}")
    elif args.version and args.model:
        register_model(args.version, args.model, promoted=args.promoted)
    else:
        parser.print_help()