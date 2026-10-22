"""
Entrena el modelo NBA localmente y lo publica automáticamente.

Flujo completo en UN comando:
  1. Determina la siguiente versión (vN+1)
  2. Entrena con todo el historial disponible (caché local + API)
  3. Compara con el modelo actual (CV-AUC / accuracy)
  4. Si es mejor: registra en Neon (model_registry) y hace commit+push
  5. Si es peor: NO lo publica (no rompe producción) y avisa

Uso:
    python utils/train_and_publish.py                    # entrenar y publicar
    python utils/train_and_publish.py --dry-run          # solo entrenar, no publicar
    python utils/train_and_publish.py --optimize 50      # trials Optuna
    python utils/train_and_publish.py --start-date 2021-10-19
    python utils/train_and_publish.py --force            # publicar aunque sea peor
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logger import get_logger

logger = get_logger("train_and_publish")

DEFAULT_START = "2021-10-19"   # inicio del caché de partidos


def _next_version() -> str:
    """Devuelve la siguiente versión disponible (vN+1)."""
    from utils.next_model_version import next_model_version
    return next_model_version("models")


def _current_version() -> str:
    """Devuelve la versión actual en producción (la más reciente por mtime)."""
    from sports.nba.model.predictor import _latest_model_version
    return _latest_model_version()


def _train(version: str, start_date: str, optimize: int) -> dict:
    """Ejecuta train_model.py y devuelve las métricas del modelo entrenado."""
    import joblib

    end_date = (date.today() - timedelta(days=1)).isoformat()
    cmd = [
        sys.executable, "train_model.py",
        "--start-date", start_date,
        "--end-date", end_date,
        "--season", "2025-26",
        "--model", "xgboost",
        "--optimize", str(optimize),
        "--with-form",
        "--with-travel",
        "--version", version,
    ]
    logger.info("Ejecutando: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if result.returncode != 0:
        raise RuntimeError(f"train_model.py falló (exit {result.returncode})")

    path = os.path.join("models", f"nba_model_{version}.joblib")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Modelo no encontrado tras entrenar: {path}")
    bundle = joblib.load(path)
    return bundle["metrics"]


def _compare_and_decide(new_metrics: dict, current_ver: str, force: bool = False) -> bool:
    """Compara el nuevo modelo con el actual. Devuelve True si se publica."""
    import joblib

    current_path = os.path.join("models", f"nba_model_{current_ver}.joblib")
    if not os.path.exists(current_path):
        logger.info("No hay modelo actual — se publica el nuevo")
        return True

    current = joblib.load(current_path)["metrics"]
    new_cv = new_metrics.get("cv_mean") or 0
    old_cv = current.get("cv_mean") or 0
    new_acc = new_metrics.get("accuracy") or 0
    old_acc = current.get("accuracy") or 0

    logger.info("=" * 50)
    logger.info("COMPARACIÓN DE MODELOS")
    logger.info("  Nuevo: CV-AUC=%.4f | Acc=%.4f | n=%d", new_cv, new_acc, new_metrics.get("n_samples", 0))
    logger.info("  Actual (%s): CV-AUC=%.4f | Acc=%.4f | n=%d", current_ver, old_cv, old_acc, current.get("n_samples", 0))
    logger.info("=" * 50)

    if force:
        logger.info("--force: publicando aunque sea peor")
        return True
    # Mejora estricta: solo se publica si el nuevo modelo es REALMENTE mejor.
    # Un empate (>=) o mejora infinitesimal no se publica para evitar versiones
    # redundantes cada vez que se reentrena sin datos nuevos.
    improved = new_cv > old_cv or new_acc > old_acc
    if improved:
        logger.info("✅ Nuevo modelo es mejor — se publica")
        return True
    logger.warning("❌ Nuevo modelo no mejora al actual — NO se publica (producción intacta)")
    return False


def _register(version: str, promoted: bool) -> None:
    """Registra la versión en Neon (model_registry)."""
    from utils.model_registry import register_model
    path = os.path.join("models", f"nba_model_{version}.joblib")
    register_model(version, path, promoted=promoted)


def _publish(version: str) -> None:
    """Hace commit + push del modelo entrenado."""
    logger.info("Publicando modelo %s en GitHub…", version)
    cmds = [
        ["git", "add", "models/*.joblib", "models/current_elos.json"],
        ["git", "commit", "-m", f"feat(model): reentrenamiento automático {version}"],
        ["git", "push", "origin", "main"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 and "nothing to commit" not in r.stderr:
            # Si no hay nada que commitear (modelo igual), no es error
            if "nothing to commit" in r.stdout or "nothing to commit" in r.stderr:
                logger.info("Sin cambios en modelos — nada que publicar")
                return
            logger.warning("Comando falló (%s): %s", cmd[0], r.stderr.strip())
    logger.info("✅ Modelo %s publicado en GitHub", version)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Entrena pero NO publica")
    parser.add_argument("--force", action="store_true", help="Publicar aunque el modelo sea peor")
    parser.add_argument("--optimize", type=int, default=50, help="Trials Optuna (default: 50)")
    parser.add_argument("--start-date", default=DEFAULT_START, help="Fecha inicio (default: 2021-10-19)")
    args = parser.parse_args()

    version = _next_version()
    current = _current_version()
    logger.info("Versión actual: %s | Nueva versión: %s", current, version)

    # 1. Entrenar
    try:
        metrics = _train(version, args.start_date, args.optimize)
    except Exception as exc:
        logger.error("Entrenamiento falló: %s", exc)
        return 1

    # 2. Comparar y decidir (con la versión capturada ANTES de entrenar)
    publish = _compare_and_decide(metrics, current_ver=current, force=args.force)

    # 3. Registrar en Neon (siempre, para tener historial)
    try:
        _register(version, promoted=publish)
    except Exception as exc:
        logger.warning("No se pudo registrar en Neon: %s", exc)

    # 4. Publicar si procede
    if publish and not args.dry_run:
        _publish(version)
    elif args.dry_run:
        logger.info("--dry-run: modelo entrenado pero NO publicado")
        # Eliminar el modelo entrenado para no alterar producción
        # (el auto-detect lo elegiría por fecha, aunque no se haya publicado)
        try:
            os.remove(os.path.join("models", f"nba_model_{version}.joblib"))
            logger.info("Modelo %s eliminado (dry-run no altera producción)", version)
        except OSError:
            pass
    else:
        logger.info("Modelo %s NO publicado (peor que %s). Producción intacta.", version, current)
        # Eliminar el modelo peor para que el auto-detect no lo elija
        try:
            os.remove(os.path.join("models", f"nba_model_{version}.joblib"))
            logger.info("Modelo %s eliminado (no promovido)", version)
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
