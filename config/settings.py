"""
Configuración central del proyecto.
Lee variables de entorno desde .env usando python-dotenv.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Base de datos (Supabase / PostgreSQL) ───────────────────────────────────
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "5432") or "5432")
DB_NAME     = os.getenv("DB_NAME", "betplay")
DB_USER     = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}",
)

# ── NBA API ─────────────────────────────────────────────────────────────────
# nba_api no requiere clave; estas opciones controlan la sesión HTTP
NBA_API_TIMEOUT = int(os.getenv("NBA_API_TIMEOUT", 30))
NBA_API_DELAY   = float(os.getenv("NBA_API_DELAY", 0.6))  # segundos entre llamadas (rate‑limit)

# Temporada activa por defecto (formato: "2024-25")
NBA_SEASON = os.getenv("NBA_SEASON", "2024-25")

# ── API de Cuotas (The Odds API) ────────────────────────────────────────────
ODDS_API_KEY      = os.getenv("ODDS_API_KEY", "")          # vacío → modo placeholder
ODDS_API_BASE_URL = os.getenv("ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4")
ODDS_SPORT        = os.getenv("ODDS_SPORT", "basketball_nba")
ODDS_REGIONS      = os.getenv("ODDS_REGIONS", "us")
ODDS_MARKETS      = os.getenv("ODDS_MARKETS", "h2h")

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR   = os.getenv("LOG_DIR", "logs")
