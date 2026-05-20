"""
Cliente de cuotas ATP — The Odds API + Betplay/Rushbet (Kambi).

Prioridad de fuentes:
  1. The Odds API (tennis_atp) → si ODDS_API_KEY está configurada
  2. Betplay / Rushbet (Kambi) → siempre disponible, sin costo

Devuelve cuotas h2h (ganador del partido) consolidadas por partido,
con probabilidad implícita sin vig para alimentar el detector de valor.

Funciones públicas:
  - get_atp_odds(date_str)       → DataFrame con todas las cuotas
  - get_best_market_odds(p1, p2) → mejores cuotas para un partido específico
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
import requests

from config.settings import ODDS_API_BASE_URL, ODDS_API_KEY
from sports.atp.config.settings import ATP_ODDS_SPORT, ATP_ODDS_REGIONS, ATP_ODDS_MARKETS
from sports.atp.ingestion.atp_client import get_tournament_surface, get_tournament_level
from sports.atp.ingestion.co_odds_scraper import get_atp_co_odds, _implied_prob, _remove_vig
from sports.atp.ingestion.rankings_client import get_player_id_by_name
from utils.logger import get_logger

logger = get_logger(__name__)

_HTTP_TIMEOUT = 15


# ── Fuente 1: The Odds API ────────────────────────────────────────────────────

def _fetch_odds_api(date_str: Optional[str] = None) -> pd.DataFrame:
    """
    Obtiene cuotas ATP de The Odds API (usa cuota de requests mensual).
    Solo se llama si ODDS_API_KEY está configurada.
    """
    if not ODDS_API_KEY:
        return pd.DataFrame()

    target = date_str or date.today().isoformat()
    url    = f"{ODDS_API_BASE_URL}/sports/{ATP_ODDS_SPORT}/odds"
    params = {
        "apiKey":     ODDS_API_KEY,
        "regions":    ATP_ODDS_REGIONS,
        "markets":    ATP_ODDS_MARKETS,
        "oddsFormat": "decimal",
    }

    try:
        r = requests.get(url, params=params, timeout=_HTTP_TIMEOUT)
        r.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("The Odds API (ATP) falló: %s", exc)
        return pd.DataFrame()

    records = []
    for game in r.json():
        home_name  = game.get("home_team", "")
        away_name  = game.get("away_team", "")
        commence   = game.get("commence_time", "")

        # Filtrar por fecha si se especificó
        try:
            dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            event_date = dt.astimezone(timezone.utc).date().isoformat()
        except Exception:
            event_date = ""

        if date_str and event_date and event_date != date_str:
            continue

        # Intentar extraer nombre del torneo del sport_title o similar
        tourney = game.get("sport_title", "ATP")

        for bk in game.get("bookmakers", []):
            bk_name = bk.get("title", "")
            for mkt in bk.get("markets", []):
                if mkt.get("key") != "h2h":
                    continue
                outcomes = mkt.get("outcomes", [])
                p1_odds = p2_odds = None
                for oc in outcomes:
                    if oc.get("name", "").lower() == home_name.lower():
                        p1_odds = oc.get("price")
                    elif oc.get("name", "").lower() == away_name.lower():
                        p2_odds = oc.get("price")
                if p1_odds and p2_odds:
                    p1_imp, p2_imp = _remove_vig(
                        _implied_prob(p1_odds),
                        _implied_prob(p2_odds),
                    )
                    records.append({
                        "event_id":            game.get("id", ""),
                        "player1_name":        home_name,
                        "player2_name":        away_name,
                        "player1_id":          get_player_id_by_name(home_name),
                        "player2_id":          get_player_id_by_name(away_name),
                        "player1_odds":        round(p1_odds, 3),
                        "player2_odds":        round(p2_odds, 3),
                        "player1_implied_prob": p1_imp,
                        "player2_implied_prob": p2_imp,
                        "vig":                 round(_implied_prob(p1_odds) + _implied_prob(p2_odds) - 1, 4),
                        "tourney_name":        tourney,
                        "surface":             get_tournament_surface(tourney),
                        "tourney_level":       get_tournament_level(tourney),
                        "state":               "NOT_STARTED",
                        "match_datetime":      commence,
                        "game_date":           event_date or target,
                        "bookmaker":           bk_name,
                    })

    if records:
        logger.info("The Odds API ATP: %d líneas de cuotas para %s", len(records), target)
    return pd.DataFrame(records) if records else pd.DataFrame()


# ── Consolidación de fuentes ──────────────────────────────────────────────────

def get_atp_odds(date_str: Optional[str] = None) -> pd.DataFrame:
    """
    Devuelve todas las cuotas ATP disponibles para una fecha, consolidando
    The Odds API y Betplay/Rushbet (Kambi).

    Cada fila = (partido × casa de apuestas).  Las dos fuentes se fusionan
    sin deduplicar — el detector de valor usa TODAS las líneas para encontrar
    discrepancias entre casas.

    Args:
        date_str: Fecha 'YYYY-MM-DD'. None = hoy.

    Returns:
        DataFrame con columnas estándar (ver co_odds_scraper.get_atp_co_odds).
    """
    target = date_str or date.today().isoformat()
    frames = []

    # Fuente 1: The Odds API (si tiene key)
    df_odds_api = _fetch_odds_api(target)
    if not df_odds_api.empty:
        frames.append(df_odds_api)

    # Fuente 2: Betplay/Rushbet (siempre)
    df_co = get_atp_co_odds(target)
    if not df_co.empty:
        frames.append(df_co)

    if not frames:
        logger.warning("No hay cuotas ATP disponibles para %s", target)
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    logger.info("ATP cuotas consolidadas: %d líneas para %s", len(df), target)
    return df


def get_best_market_odds(
    player1_name: str,
    player2_name: str,
    date_str: Optional[str] = None,
) -> dict:
    """
    Devuelve las mejores cuotas de mercado para un partido específico,
    consolidando todas las casas disponibles.

    Útil para el detector de valor: compara la probabilidad del modelo
    vs la mejor cuota disponible en el mercado.

    Returns:
        {
          'player1_best_odds':   float,
          'player2_best_odds':   float,
          'player1_best_book':   str,
          'player2_best_book':   str,
          'player1_market_prob': float,  # prob implícita sin vig
          'player2_market_prob': float,
          'found':               bool,
        }
    """
    df = get_atp_odds(date_str)
    if df.empty:
        return {"found": False}

    p1_n = player1_name.lower()
    p2_n = player2_name.lower()

    mask = (
        (df["player1_name"].str.lower().str.contains(p1_n, na=False)) |
        (df["player2_name"].str.lower().str.contains(p1_n, na=False))
    ) & (
        (df["player1_name"].str.lower().str.contains(p2_n, na=False)) |
        (df["player2_name"].str.lower().str.contains(p2_n, na=False))
    )
    subset = df[mask]
    if subset.empty:
        return {"found": False}

    best_p1 = best_p2 = 0.0
    best_bk_p1 = best_bk_p2 = ""

    for _, row in subset.iterrows():
        inverted = p1_n in str(row["player2_name"]).lower()
        p1_o = float(row["player2_odds"]) if inverted else float(row["player1_odds"])
        p2_o = float(row["player1_odds"]) if inverted else float(row["player2_odds"])
        bk   = str(row["bookmaker"])

        if p1_o > best_p1:
            best_p1 = p1_o
            best_bk_p1 = bk
        if p2_o > best_p2:
            best_p2 = p2_o
            best_bk_p2 = bk

    p1_imp, p2_imp = _remove_vig(_implied_prob(best_p1), _implied_prob(best_p2))

    return {
        "player1_best_odds":   best_p1,
        "player2_best_odds":   best_p2,
        "player1_best_book":   best_bk_p1,
        "player2_best_book":   best_bk_p2,
        "player1_market_prob": p1_imp,
        "player2_market_prob": p2_imp,
        "found":               True,
    }
