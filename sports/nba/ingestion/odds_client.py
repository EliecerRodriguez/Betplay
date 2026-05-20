"""
Cliente de cuotas deportivas – FASE 1.

Implementa tres modos de operación (en orden de prioridad):
  1. MODO REAL        → llama a The Odds API si ODDS_API_KEY está definida.
  2. MODO SCRAPING CO → extrae cuotas en tiempo real de casas colombianas/internacionales
                        (Betplay, Wplay, Rushbet, Bwin, Betsson) usando Playwright.
                        Se activa cuando CO_SCRAPING=true en .env o si playwright está instalado.
  3. MODO PLACEHOLDER → cuotas sintéticas para testing sin dependencias externas.

The Odds API (free tier):
  - 500 peticiones / mes gratis
  - Endpoint: GET /v4/sports/{sport}/odds
  - Docs: https://the-odds-api.com/liveapi/guides/v4/#get-odds
"""
import os
import random
from datetime import date
from typing import Optional

import pandas as pd
import requests

from config.settings import (
    ODDS_API_BASE_URL,
    ODDS_API_KEY,
    ODDS_MARKETS,
    ODDS_REGIONS,
    ODDS_SPORT,
)
from utils.logger import get_logger

logger = get_logger(__name__)

# Timeout para las peticiones HTTP
_HTTP_TIMEOUT = 15  # segundos

# Activa scraping de casas colombianas/internacionales (sin API key)
# Puedes forzarlo con CO_SCRAPING=true en .env
_CO_SCRAPING_ENABLED = os.getenv("CO_SCRAPING", "true").lower() in ("1", "true", "yes")


# ── Modo real: The Odds API ──────────────────────────────────────────────────

def _fetch_real_odds() -> pd.DataFrame:
    """
    Llama a The Odds API y devuelve las cuotas h2h para la NBA.

    Returns:
        DataFrame con columnas:
          game_id, home_team, away_team, bookmaker,
          home_odds, away_odds, commence_time, fetch_date
    """
    url = f"{ODDS_API_BASE_URL}/sports/{ODDS_SPORT}/odds"
    params = {
        "apiKey":  ODDS_API_KEY,
        "regions": ODDS_REGIONS,
        "markets": ODDS_MARKETS,
        "oddsFormat": "decimal",
    }

    logger.info("Consultando The Odds API: %s", url)

    try:
        response = requests.get(url, params=params, timeout=_HTTP_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        logger.error("HTTP error al consultar cuotas: %s", exc)
        return pd.DataFrame()
    except requests.exceptions.RequestException as exc:
        logger.error("Error de red al consultar cuotas: %s", exc)
        return pd.DataFrame()

    data = response.json()
    records = []

    for game in data:
        game_id       = game.get("id", "")
        home_team     = game.get("home_team", "")
        away_team     = game.get("away_team", "")
        commence_time = game.get("commence_time", "")

        for bookmaker in game.get("bookmakers", []):
            bk_name = bookmaker.get("title", "")

            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue

                outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
                records.append(
                    {
                        "game_id":       game_id,
                        "home_team":     home_team,
                        "away_team":     away_team,
                        "bookmaker":     bk_name,
                        "home_odds":     outcomes.get(home_team),
                        "away_odds":     outcomes.get(away_team),
                        "commence_time": commence_time,
                        "fetch_date":    date.today().isoformat(),
                    }
                )

    df = pd.DataFrame(records)
    logger.info("Cuotas reales obtenidas: %d registros de %d partidos", len(df), len(data))
    return df


# ── Modo scraping: casas colombianas/internacionales ─────────────────────────

def _fetch_co_odds(games_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    Extrae cuotas NBA en tiempo real de Betplay, Wplay, Rushbet, Bwin y Betsson
    usando el scraper de Playwright.

    Args:
        games_df: DataFrame de partidos del día (para asignar game_id).

    Returns:
        DataFrame con cuotas reales de las casas colombianas.
    """
    try:
        from ingestion.co_odds_scraper import get_co_odds
        logger.info("Modo SCRAPING CO: extrayendo cuotas de Betplay, Wplay, Rushbet, Bwin, Betsson")
        return get_co_odds(games_df=games_df)
    except ImportError as exc:
        logger.error("co_odds_scraper no disponible: %s", exc)
        return pd.DataFrame()
    except Exception as exc:
        logger.error("Error en scraping CO: %s", exc)
        return pd.DataFrame()




def _generate_placeholder_odds(games_df: pd.DataFrame) -> pd.DataFrame:
    """
    Genera cuotas sintéticas realistas a partir de un DataFrame de partidos.
    Útil para desarrollo sin clave de API.

    Args:
        games_df: DataFrame devuelto por nba_client.get_daily_games().
                  Debe tener columnas 'game_id', 'home_team_id', 'visitor_team_id'.

    Returns:
        DataFrame con la misma estructura que _fetch_real_odds().
    """
    if games_df.empty:
        logger.warning("No se proporcionaron partidos para generar cuotas placeholder")
        return pd.DataFrame()

    random.seed(42)
    records = []
    today = date.today().isoformat()

    # Casas de apuestas simuladas
    bookmakers = ["DraftKings", "FanDuel", "BetMGM"]

    for _, row in games_df.iterrows():
        game_id   = row.get("game_id", "")
        home_team = row.get("home_team_id", "Home")
        away_team = row.get("visitor_team_id", "Away")

        for bk in bookmakers:
            # Cuotas decimales entre 1.50 y 2.80 (rango realista NBA)
            home_odds = round(random.uniform(1.50, 2.80), 2)
            away_odds = round(random.uniform(1.50, 2.80), 2)

            records.append(
                {
                    "game_id":       game_id,
                    "home_team":     home_team,
                    "away_team":     away_team,
                    "bookmaker":     bk,
                    "home_odds":     home_odds,
                    "away_odds":     away_odds,
                    "commence_time": row.get("game_status_text", today),
                    "fetch_date":    today,
                    "is_placeholder": True,   # marcador para no usar en producción
                }
            )

    df = pd.DataFrame(records)
    logger.info(
        "Cuotas PLACEHOLDER generadas: %d registros para %d partidos",
        len(df),
        len(games_df),
    )
    return df


# ── Función pública principal ────────────────────────────────────────────────

def get_odds(games_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    Punto de entrada para obtener cuotas.

    Prioridad:
      1. ODDS_API_KEY configurada → The Odds API (cuotas internacionales)
      2. CO_SCRAPING=true (por defecto) y playwright instalado → scrapers CO
         (Betplay, Wplay, Rushbet, Bwin, Betsson)
      3. Fallback → cuotas placeholder sintéticas

    Args:
        games_df: DataFrame de partidos (necesario para modo placeholder y
                  para asignar game_id en modo scraping CO).

    Returns:
        DataFrame con cuotas listo para almacenar en la base de datos.
    """
    if ODDS_API_KEY:
        logger.info("Modo REAL: usando The Odds API")
        return _fetch_real_odds()

    if _CO_SCRAPING_ENABLED:
        logger.info("Modo SCRAPING CO: intentando cuotas de casas colombianas")
        df = _fetch_co_odds(games_df=games_df)
        if not df.empty:
            return df
        logger.warning(
            "Scraping CO no retornó datos (¿playwright instalado?). "
            "Ejecuta: pip install playwright && playwright install chromium"
        )

    logger.warning(
        "Usando cuotas PLACEHOLDER. Para cuotas reales:\n"
        "  Opción A: Agrega ODDS_API_KEY en .env\n"
        "  Opción B: pip install playwright && playwright install chromium"
    )
    return _generate_placeholder_odds(games_df if games_df is not None else pd.DataFrame())
