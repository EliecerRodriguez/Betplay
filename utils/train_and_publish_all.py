"""
Orquestador de reentrenamiento: NBA + Liga BetPlay en UN comando.

Ejecuta en secuencia los dos ciclos MLOps completos:
  1. NBA:      python utils/train_and_publish.py
  2. BetPlay:  python utils/train_and_publish_betplay.py

Cada uno: entrena → compara con el actual → registra en Neon → publica en
GitHub solo si es mejor (o --force).

Uso:
    python utils/train_and_publish_all.py                    # NBA + BetPlay
    python utils/train_and_publish_all.py --sport nba        # solo NBA
    python utils/train_and_publish_all.py --sport betplay    # solo BetPlay
    python utils/train_and_publish_all.py --dry-run          # entrena pero NO publica
    python utils/train_and_publish_all.py --force            # publica aunque sea peor
    python utils/train_and_publish_all.py --optimize 30      # trials Optuna NBA
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logger import get_logger

logger = get_logger("train_and_publish_all")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_script(script: str, args: list[str]) -> int:
    """Ejecuta un script Python y devuelve su exit code."""
    cmd = [sys.executable, script] + args
    logger.info("=" * 60)
    logger.info("EJECUTANDO: %s", " ".join(cmd))
    logger.info("=" * 60)
    result = subprocess.run(cmd, cwd=BASE_DIR)
    if result.returncode != 0:
        logger.error("❌ %s falló (exit %d)", script, result.returncode)
    else:
        logger.info("✅ %s completado", script)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sport", choices=["nba", "betplay", "both"], default="both",
                        help="Deporte(s) a reentrenar (default: both)")
    parser.add_argument("--dry-run", action="store_true", help="Entrena pero NO publica")
    parser.add_argument("--force", action="store_true", help="Publicar aunque el modelo sea peor")
    parser.add_argument("--optimize", type=int, default=50, help="Trials Optuna NBA (default: 50)")
    parser.add_argument("--optimize-betplay", type=int, default=0,
                        help="Trials Optuna BetPlay (default: 0 = fijos; ej. 30)")
    parser.add_argument("--start-date", default=None, help="Fecha inicio NBA (default: 2021-10-19)")
    args = parser.parse_args()

    failures: list[str] = []

    # ── 1. NBA ────────────────────────────────────────────────────────────────
    if args.sport in ("nba", "both"):
        nba_args = []
        if args.dry_run:
            nba_args.append("--dry-run")
        if args.force:
            nba_args.append("--force")
        nba_args += ["--optimize", str(args.optimize)]
        if args.start_date:
            nba_args += ["--start-date", args.start_date]
        if _run_script("utils/train_and_publish.py", nba_args) != 0:
            failures.append("NBA")

    # ── 2. BetPlay ────────────────────────────────────────────────────────────
    if args.sport in ("betplay", "both"):
        bp_args = []
        if args.dry_run:
            bp_args.append("--dry-run")
        if args.force:
            bp_args.append("--force")
        if args.optimize_betplay and args.optimize_betplay > 0:
            bp_args += ["--optimize", str(args.optimize_betplay)]
        if _run_script("utils/train_and_publish_betplay.py", bp_args) != 0:
            failures.append("BetPlay")

    # ── Resumen ───────────────────────────────────────────────────────────────
    logger.info("=" * 60)
    if failures:
        logger.error("Reentrenamiento con errores en: %s", ", ".join(failures))
        return 1
    logger.info("✅ Reentrenamiento completo: NBA + BetPlay finalizados sin errores")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())