"""
Entrena los modelos Liga BetPlay localmente y los publica automáticamente.

Flujo completo en UN comando (clon del patrón NBA train_and_publish.py):
  1. Determina la siguiente versión (vN+1) para yellow y corners
  2. Entrena con el dataset consolidado (CSV) o re-descarga si falta
  3. Compara con el modelo actual (CV-AUC / accuracy)
  4. Si es mejor: registra en Neon (betplay_model_registry) y hace commit+push
  5. Si es peor: NO lo publica (no rompe producción) y avisa

Uso:
    python utils/train_and_publish_betplay.py                    # entrenar y publicar
    python utils/train_and_publish_betplay.py --dry-run          # solo entrenar, no publicar
    python utils/train_and_publish_betplay.py --force            # publicar aunque sea peor
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logger import get_logger

logger = get_logger("train_and_publish_betplay")

MODEL_DIR = "models/betplay"


def _next_version(model_type: str) -> str:
    """Devuelve la siguiente versión disponible (vN+1) para el tipo de modelo."""
    from sports.betplay.model.predictor import _latest_model_version
    cur = _latest_model_version(model_type)
    cur_num = int(cur[1:]) if cur.startswith("v") and cur[1:].isdigit() else 1
    return f"v{cur_num + 1}"


def _current_version(model_type: str) -> str:
    """Devuelve la versión actual en producción (la más reciente por mtime)."""
    from sports.betplay.model.predictor import _latest_model_version
    return _latest_model_version(model_type)


def _train(version: str, market: str, optimize: int = 0) -> dict:
    """Ejecuta train_betplay_model.py y devuelve las métricas del modelo entrenado."""
    import joblib

    cmd = [
        sys.executable, "train_betplay_model.py",
        "--market", market,
    ]
    if optimize and optimize > 0:
        cmd += ["--optimize", str(optimize)]
    logger.info("Ejecutando: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if result.returncode != 0:
        raise RuntimeError(f"train_betplay_model.py falló (exit {result.returncode})")

    # El entrenamiento auto-incrementa la versión; cargar métricas del modelo nuevo
    path = os.path.join(MODEL_DIR, f"betplay_{market}_model_{version}.joblib")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Modelo no encontrado tras entrenar: {path}")
    bundle = joblib.load(path)
    return bundle["metrics"]


def _compare_and_decide(new_metrics: dict, current_ver: str, market: str, force: bool = False) -> bool:
    """Compara el nuevo modelo con el actual. Devuelve True si se publica."""
    import joblib

    current_path = os.path.join(MODEL_DIR, f"betplay_{market}_model_{current_ver}.joblib")
    if not os.path.exists(current_path):
        logger.info("No hay modelo actual de %s — se publica el nuevo", market)
        return True

    current = joblib.load(current_path)["metrics"]
    new_cv = new_metrics.get("cv_mean") or 0
    old_cv = current.get("cv_mean") or 0
    new_acc = new_metrics.get("accuracy") or 0
    old_acc = current.get("accuracy") or 0

    logger.info("=" * 50)
    logger.info("COMPARACIÓN DE MODELOS %s", market)
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
        logger.info("✅ Nuevo modelo %s es mejor — se publica", market)
        return True
    logger.warning("❌ Nuevo modelo %s no mejora al actual — NO se publica (producción intacta)", market)
    return False


def _register(version: str, market: str, promoted: bool) -> None:
    """Registra la versión en Neon (betplay_model_registry)."""
    from utils.betplay_model_registry import register_model
    path = os.path.join(MODEL_DIR, f"betplay_{market}_model_{version}.joblib")
    register_model(version, path, promoted=promoted)


def _publish(version: str, market: str) -> None:
    """Hace commit + push del modelo entrenado."""
    logger.info("Publicando modelo %s (%s) en GitHub…", version, market)
    cmds = [
        ["git", "add", "models/betplay/*.joblib"],
        ["git", "commit", "-m", f"feat(betplay): reentrenamiento automático {market} {version}"],
        ["git", "push", "origin", "main"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            if "nothing to commit" in r.stdout or "nothing to commit" in r.stderr:
                logger.info("Sin cambios en modelos — nada que publicar")
                return
            logger.warning("Comando falló (%s): %s", cmd[0], r.stderr.strip())
    logger.info("✅ Modelo %s (%s) publicado en GitHub", version, market)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Entrena pero NO publica")
    parser.add_argument("--force", action="store_true", help="Publicar aunque el modelo sea peor")
    parser.add_argument("--market", choices=["yellow", "corners", "both"], default="both",
                        help="Mercado a entrenar (default: both)")
    parser.add_argument("--optimize", type=int, default=0,
                        help="Trials Optuna de hiperparámetros (default: 0 = fijos)")
    args = parser.parse_args()

    markets = ["yellow", "corners"] if args.market == "both" else [args.market]

    for market in markets:
        version = _next_version(market)
        current = _current_version(market)
        logger.info("[%s] Versión actual: %s | Nueva versión: %s", market, current, version)

        # 1. Entrenar
        try:
            metrics = _train(version, market, optimize=args.optimize)
        except Exception as exc:
            logger.error("[%s] Entrenamiento falló: %s", market, exc)
            return 1

        # 2. Comparar y decidir
        publish = _compare_and_decide(metrics, current_ver=current, market=market, force=args.force)

        # 3. Registrar en Neon (siempre, para tener historial)
        try:
            _register(version, market, promoted=publish)
        except Exception as exc:
            logger.warning("[%s] No se pudo registrar en Neon: %s", market, exc)

        # 4. Publicar si procede
        if publish and not args.dry_run:
            _publish(version, market)
        elif args.dry_run:
            logger.info("[%s] --dry-run: modelo entrenado pero NO publicado", market)
            try:
                os.remove(os.path.join(MODEL_DIR, f"betplay_{market}_model_{version}.joblib"))
                logger.info("[%s] Modelo %s eliminado (dry-run no altera producción)", market, version)
            except OSError:
                pass
        else:
            logger.info("[%s] Modelo %s NO publicado (peor que %s). Producción intacta.", market, version, current)
            try:
                os.remove(os.path.join(MODEL_DIR, f"betplay_{market}_model_{version}.joblib"))
                logger.info("[%s] Modelo %s eliminado (no promovido)", market, version)
            except OSError:
                pass

    return 0


if __name__ == "__main__":
    sys.exit(main())