"""
Configuración específica del módulo Liga BetPlay.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Liga ────────────────────────────────────────────────────────────────────
# ESPN league slug: col.1 = Colombian Primera A (Liga BetPlay)
BETPLAY_LEAGUE = os.getenv("BETPLAY_LEAGUE", "col.1")
BETPLAY_SEASON = os.getenv("BETPLAY_SEASON", "2026")

# ── ESPN API ────────────────────────────────────────────────────────────────
ESPN_BASE_URL = os.getenv(
    "ESPN_BASE_URL",
    "https://site.api.espn.com/apis/site/v2/sports/soccer",
)
ESPN_HEADERS = {"User-Agent": "Mozilla/5.0"}
ESPN_TIMEOUT = int(os.getenv("ESPN_TIMEOUT", "30"))
ESPN_DELAY = float(os.getenv("ESPN_DELAY", "0.5"))  # rate-limit amistoso

# ── Caché local ────────────────────────────────────────────────────────────
BETPLAY_CACHE_DIR = os.getenv("BETPLAY_CACHE_DIR", "data/betplay_cache")
BETPLAY_STATS_CACHE = os.getenv("BETPLAY_STATS_CACHE", "data/betplay_stats")

# Año desde el cual se construye el dataset histórico.
# ESPN tiene stats de córners/tarjetas desde 2021 (verificado 2026-10-21).
BETPLAY_HISTORY_START = int(os.getenv("BETPLAY_HISTORY_START", "2021"))

# ── Kambi (Betplay / Rushbet) — fútbol ──────────────────────────────────────
# Reutiliza el patrón del scraper ATP, adaptado a fútbol.
ATP_KAMBI_GROUP_BETPLAY = int(os.getenv("ATP_KAMBI_GROUP_BETPLAY", 0))
ATP_KAMBI_GROUP_RUSHBET = int(os.getenv("ATP_KAMBI_GROUP_RUSHBET", 0))

# ── Modelo ─────────────────────────────────────────────────────────────────
BETPLAY_MODEL_VERSION = os.getenv("BETPLAY_MODEL_VERSION", "")  # auto-detect si vacío
BETPLAY_MODEL_TYPE = os.getenv("BETPLAY_MODEL_TYPE", "xgboost")

# Re-exports compartidos
from config.settings import (  # noqa: F401  (re-exports de config global)
    DATABASE_URL,
    LOG_LEVEL,
    LOG_DIR,
    ODDS_API_KEY,
    ODDS_API_BASE_URL,
    MAX_BETS_PER_DAY,
    MAX_BANKROLL_EXPOSURE,
    INITIAL_BANKROLL,
)
