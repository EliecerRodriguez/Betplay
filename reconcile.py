"""
Script de reconciliación standalone.
Ejecuta la misma lógica que /api/reconcile pero sin necesitar el servidor FastAPI.

Uso:
    python reconcile.py
"""
import sys
import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(os.path.dirname(__file__), "logs", "reconcile.log"), encoding="utf-8"),
    ],
)

# Asegurar que el directorio raíz esté en el path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Importar la función desde web.app — el servidor NO arranca al importar el módulo
from web.app import _fetch_and_reconcile_results

if __name__ == "__main__":
    try:
        updated = _fetch_and_reconcile_results()
        print(f"Reconciliación completada: {updated} predicciones actualizadas.")
        sys.exit(0)
    except Exception as exc:
        logging.error("Error en reconciliación: %s", exc, exc_info=True)
        sys.exit(1)
