"""
Configuración específica del módulo NBA.
Re-exporta desde config.settings para que los módulos internos puedan importar
tanto desde aquí como desde config.settings — evita duplicar valores.
"""
from config.settings import (  # noqa: F401  (re-exports)
    NBA_SEASON,
    NBA_API_TIMEOUT,
    NBA_API_DELAY,
    ODDS_API_KEY,
    ODDS_API_BASE_URL,
    ODDS_SPORT,
    ODDS_REGIONS,
    ODDS_MARKETS,
    DATABASE_URL,
    DB_HOST,
    DB_PORT,
    DB_NAME,
    DB_USER,
    DB_PASSWORD,
    LOG_LEVEL,
    LOG_DIR,
)
