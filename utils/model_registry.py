"""
Registro de versiones del modelo NBA.

Persiste en Neon (tabla model_registry) las métricas de cada versión entrenada,
para poder comparar versiones y decidir cuál promover.

Uso:
    python utils/model_registry.py --version v12 --model models/nba_model_v12.joblib
    python utils/model_registry.py --list          # lista versiones registradas
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
        CREATE TABLE IF NOT EXISTS model_registry (
            id            SERIAL PRIMARY KEY,
            version       VARCHAR(20) NOT NULL UNIQUE,
            model_type    VARCHAR(30),
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
            created_at    TIMESTAMPTZ DEFAULT now()
        )
    """))


def register_model(version: str, model_path: str, promoted: bool = False) -> dict:
    """Registra las métricas de un modelo entrenado en Neon."""
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

    record = {
        "version": version,
        "model_type": _native(metrics.get("model_type", "?")),
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

    if not DATABASE_URL:
        logger.warning("model_registry: DATABASE_URL no configurado — no se persiste")
        return record

    from sqlalchemy import text
    engine = _get_engine()
    with engine.begin() as conn:
        _ensure_table(conn)
        conn.execute(text("""
            INSERT INTO model_registry
                (version, model_type, accuracy, roc_auc, log_loss, brier,
                 cv_mean, cv_std, n_samples, n_features, trained_at, promoted)
            VALUES
                (:version, :model_type, :accuracy, :roc_auc, :log_loss, :brier,
                 :cv_mean, :cv_std, :n_samples, :n_features, :trained_at, :promoted)
            ON CONFLICT (version) DO UPDATE SET
                model_type = EXCLUDED.model_type,
                accuracy   = EXCLUDED.accuracy,
                roc_auc    = EXCLUDED.roc_auc,
                log_loss   = EXCLUDED.log_loss,
                brier      = EXCLUDED.brier,
                cv_mean    = EXCLUDED.cv_mean,
                cv_std     = EXCLUDED.cv_std,
                n_samples  = EXCLUDED.n_samples,
                n_features = EXCLUDED.n_features,
                trained_at = EXCLUDED.trained_at,
                promoted   = EXCLUDED.promoted
        """), record)
    logger.info("model_registry: versión %s registrada en Neon", version)
    return record


def list_models() -> list[dict]:
    """Lista las versiones registradas en Neon."""
    if not DATABASE_URL:
        return []
    from sqlalchemy import text
    engine = _get_engine()
    with engine.connect() as conn:
        _ensure_table(conn)
        rows = conn.execute(text("""
            SELECT version, model_type, accuracy, roc_auc, log_loss, brier,
                   cv_mean, cv_std, n_samples, n_features, trained_at, promoted
            FROM model_registry
            ORDER BY version
        """)).mappings().all()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="Versión a registrar (ej. v12)")
    parser.add_argument("--model", help="Ruta al joblib (ej. models/nba_model_v12.joblib)")
    parser.add_argument("--promoted", action="store_true", help="Marcar como promovido")
    parser.add_argument("--list", action="store_true", help="Listar versiones registradas")
    args = parser.parse_args()

    if args.list:
        for m in list_models():
            print(f"  {m['version']} | acc={m['accuracy']} | cv={m['cv_mean']} | "
                  f"n={m['n_samples']} | promovido={m['promoted']}")
    elif args.version and args.model:
        register_model(args.version, args.model, promoted=args.promoted)
        print(f"Versión {args.version} registrada")
    else:
        parser.print_help()
