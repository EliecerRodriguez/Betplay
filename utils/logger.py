"""
Módulo de logging centralizado.
Crea un logger con salida a consola y a archivo rotativo.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

from config.settings import LOG_LEVEL, LOG_DIR


def get_logger(name: str) -> logging.Logger:
    """
    Devuelve un logger configurado con:
    - Handler de consola (StreamHandler)
    - Handler de archivo rotativo (máx 5 MB × 3 archivos)

    Args:
        name: nombre del módulo (usar __name__ en cada módulo).

    Returns:
        logging.Logger listo para usar.
    """
    logger = logging.getLogger(name)

    # Evitar agregar handlers duplicados si el logger ya fue configurado
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Consola ──────────────────────────────────────────────────────────────
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # ── Archivo rotativo ─────────────────────────────────────────────────────
    os.makedirs(LOG_DIR, exist_ok=True)
    file_handler = RotatingFileHandler(
        filename=os.path.join(LOG_DIR, "betplay.log"),
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
