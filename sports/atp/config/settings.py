"""
Configuración específica del módulo ATP.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Fuente de datos históricos (Jeff Sackmann) ────────────────────────────────
ATP_DATA_BASE_URL = os.getenv(
    "ATP_DATA_BASE_URL",
    "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master",
)

# Año desde el cual se procesan datos para el Elo (cuanto más atrás, más preciso)
ATP_ELO_START_YEAR = int(os.getenv("ATP_ELO_START_YEAR", 2010))

# Año hasta el que se descargan datos históricos
ATP_DATA_END_YEAR = int(os.getenv("ATP_DATA_END_YEAR", 2026))

# Directorio de caché local para los CSVs de Sackmann
ATP_CACHE_DIR = os.getenv("ATP_CACHE_DIR", "data/atp_cache")

# Ruta del archivo de Elo actuales
ATP_ELO_PATH = os.getenv("ATP_ELO_PATH", "sports/atp/models/current_elos.json")

# ── API de Cuotas ─────────────────────────────────────────────────────────────
# Reutiliza The Odds API con deporte tennis_atp
ATP_ODDS_SPORT   = os.getenv("ATP_ODDS_SPORT", "tennis_atp")
ATP_ODDS_REGIONS = os.getenv("ATP_ODDS_REGIONS", "eu,us")
ATP_ODDS_MARKETS = os.getenv("ATP_ODDS_MARKETS", "h2h")

# Reutiliza la misma ODDS_API_KEY que NBA
from config.settings import (  # noqa: F401  (re-exports compartidos)
    ODDS_API_KEY,
    ODDS_API_BASE_URL,
    DATABASE_URL,
    LOG_LEVEL,
    LOG_DIR,
)

# ── Kambi (Betplay / Rushbet) — tennis ────────────────────────────────────────
# IDs de grupo en Kambi para torneos ATP (se completan en Fase 3)
ATP_KAMBI_GROUP_BETPLAY = int(os.getenv("ATP_KAMBI_GROUP_BETPLAY", 0))
ATP_KAMBI_GROUP_RUSHBET = int(os.getenv("ATP_KAMBI_GROUP_RUSHBET", 0))

# ── Torneos mínimos para incluir un jugador en predicciones ──────────────────
ATP_MIN_MATCHES_FOR_PREDICTION = int(os.getenv("ATP_MIN_MATCHES_FOR_PREDICTION", 20))

# Superficies reconocidas en el sistema
SURFACES = ['Hard', 'Clay', 'Grass', 'Carpet']
