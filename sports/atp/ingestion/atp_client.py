"""
Cliente de partidos ATP del día.

Fuentes de datos (en orden de prioridad):
  1. The Odds API  — tennis_atp events (si ODDS_API_KEY está configurada)
                     Endpoint: GET /v4/sports/tennis_atp/events
                     Ventaja: no consume cuota de odds, solo lista eventos
  2. Modo placeholder — genera estructura vacía para testing sin red

La superficie del partido se deriva del nombre del torneo usando el mapa
TOURNAMENT_SURFACE_MAP construido a partir del calendario ATP oficial.
El nivel del torneo (G/M/A) se deriva de forma similar con TOURNAMENT_LEVEL_MAP.

Los player_id de Sackmann se resuelven automáticamente desde los nombres
usando rankings_client.get_player_id_by_name() con búsqueda difusa.

Funciones públicas:
  - get_daily_matches(date_str)   → DataFrame con partidos del día
  - get_tournament_surface(name)  → 'Hard' | 'Clay' | 'Grass' | 'Hard' (default)
  - get_tournament_level(name)    → 'G' | 'M' | 'A'
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
import requests

from config.settings import ODDS_API_BASE_URL, ODDS_API_KEY
from sports.atp.config.settings import ATP_ODDS_SPORT, ATP_ODDS_REGIONS, ATP_ODDS_MARKETS
from sports.atp.ingestion.rankings_client import resolve_players
from utils.logger import get_logger

logger = get_logger(__name__)

_HTTP_TIMEOUT = 15


# ── Mapa torneo → superficie ──────────────────────────────────────────────────
# Cubre los torneos ATP del calendario 2025-26 (se extiende con .env si se necesita)

TOURNAMENT_SURFACE_MAP: dict[str, str] = {
    # ── Grand Slams ────────────────────────────────────────────────────────
    "australian open":        "Hard",
    "roland garros":          "Clay",
    "french open":            "Clay",
    "wimbledon":              "Grass",
    "us open":                "Hard",

    # ── Masters 1000 ───────────────────────────────────────────────────────
    "indian wells":           "Hard",
    "bnp paribas open":       "Hard",
    "miami open":             "Hard",
    "monte-carlo":            "Clay",
    "monte carlo":            "Clay",
    "rolex monte-carlo":      "Clay",
    "madrid":                 "Clay",
    "mutua madrid":           "Clay",
    "rome":                   "Clay",
    "internazionali":         "Clay",
    "canada":                 "Hard",
    "canadian open":          "Hard",
    "national bank open":     "Hard",
    "cincinnati":             "Hard",
    "western & southern":     "Hard",
    "us open series":         "Hard",
    "shanghai":               "Hard",
    "rolex shanghai":         "Hard",
    "paris":                  "Hard",
    "rolex paris masters":    "Hard",

    # ── ATP 500 ────────────────────────────────────────────────────────────
    "rotterdam":              "Hard",
    "abn amro":               "Hard",
    "dubai":                  "Hard",
    "qatar open":             "Hard",
    "doha":                   "Hard",
    "acapulco":               "Hard",
    "abierto mexicano":       "Hard",
    "barcelona":              "Clay",
    "barcelona open":         "Clay",
    "estoril":                "Clay",
    "hambourg":               "Clay",
    "hamburg":                "Clay",
    "halle":                  "Grass",
    "terra wortmann open":    "Grass",
    "queen's club":           "Grass",
    "cinch championships":    "Grass",
    "tokyo":                  "Hard",
    "rakuten japan open":     "Hard",
    "beijing":                "Hard",
    "china open":             "Hard",
    "vienna":                 "Hard",
    "erste bank open":        "Hard",
    "basel":                  "Hard",
    "swiss indoors":          "Hard",
    "washington":             "Hard",
    "citi open":              "Hard",

    # ── ATP 250 ────────────────────────────────────────────────────────────
    "brisbane":               "Hard",
    "adelaide":               "Hard",
    "auckland":               "Hard",
    "pune":                   "Hard",
    "tata open":              "Hard",
    "dallas":                 "Hard",
    "delray beach":           "Hard",
    "san jose":               "Hard",
    "memphis":                "Hard",
    "murray river open":      "Hard",
    "montpellier":            "Hard",
    "marseille":              "Hard",
    "open 13":                "Hard",
    "rio":                    "Clay",
    "rio open":               "Clay",
    "buenos aires":           "Clay",
    "cordoba":                "Clay",
    "santiago":               "Clay",
    "marrakech":              "Clay",
    "cagliari":               "Clay",
    "geneva":                 "Clay",
    "lyon":                   "Clay",
    "munich":                 "Clay",
    "bmw open":               "Clay",
    "bucharest":              "Clay",
    "eastbourne":             "Grass",
    "surbiton":               "Grass",
    "newport":                "Grass",
    "stuttgart":              "Grass",
    "mallorca":               "Grass",
    "metz":                   "Hard",
    "chengdu":                "Hard",
    "hangzhou":               "Hard",
    "astana":                 "Hard",
    "antwerp":                "Hard",
    "european open":          "Hard",
    "stockholm":              "Hard",
    "moscow":                 "Hard",
    "sofia":                  "Hard",
}

# ── Mapa torneo → nivel ───────────────────────────────────────────────────────

TOURNAMENT_LEVEL_MAP: dict[str, str] = {
    "australian open": "G", "roland garros": "G", "french open": "G",
    "wimbledon": "G", "us open": "G",
    "indian wells": "M", "miami open": "M", "monte-carlo": "M",
    "monte carlo": "M", "madrid": "M", "rome": "M", "internazionali": "M",
    "canada": "M", "canadian open": "M", "national bank open": "M",
    "cincinnati": "M", "western & southern": "M",
    "shanghai": "M", "paris": "M", "rolex paris masters": "M",
    "rotterdam": "A", "dubai": "A", "acapulco": "A", "barcelona": "A",
    "hambourg": "A", "hamburg": "A", "halle": "A", "queen's club": "A",
    "tokyo": "A", "beijing": "A", "vienna": "A", "basel": "A",
    "washington": "A",
}


def get_tournament_surface(tourney_name: str) -> str:
    """Devuelve la superficie de un torneo por su nombre (default: 'Hard')."""
    key = tourney_name.lower().strip()
    for pattern, surface in TOURNAMENT_SURFACE_MAP.items():
        if pattern in key:
            return surface
    return "Hard"   # fallback — la mayoría de torneos son hard court


def get_tournament_level(tourney_name: str) -> str:
    """Devuelve el nivel ATP (G/M/A) de un torneo (default: 'A')."""
    key = tourney_name.lower().strip()
    for pattern, level in TOURNAMENT_LEVEL_MAP.items():
        if pattern in key:
            return level
    return "A"


# ── Fuente 1: The Odds API ────────────────────────────────────────────────────

def _fetch_from_odds_api(date_str: str) -> pd.DataFrame:
    """
    Obtiene los partidos ATP del día desde The Odds API (endpoint /events).
    Solo consume cuota si ODDS_API_KEY está configurada.
    """
    if not ODDS_API_KEY:
        logger.debug("ODDS_API_KEY no configurada — omitiendo The Odds API")
        return pd.DataFrame()

    url = f"{ODDS_API_BASE_URL}/sports/{ATP_ODDS_SPORT}/events"
    params = {"apiKey": ODDS_API_KEY, "dateFormat": "iso"}

    try:
        resp = requests.get(url, params=params, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("The Odds API (ATP events) falló: %s", exc)
        return pd.DataFrame()

    data = resp.json()
    if not data:
        return pd.DataFrame()

    # Filtrar por fecha
    records = []
    for event in data:
        commence = event.get("commence_time", "")
        try:
            dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            event_date = dt.astimezone(timezone.utc).date().isoformat()
        except Exception:
            event_date = ""

        if event_date != date_str:
            continue

        p1_name = event.get("home_team", "")
        p2_name = event.get("away_team", "")
        tourney = event.get("sport_title", "ATP")

        # Inferir torneo del nombre del evento si está disponible
        event_name = event.get("event_name", event.get("name", ""))
        if event_name and " vs " in event_name.lower():
            # "Alcaraz vs Sinner — French Open" → extraer torneo
            parts = event_name.split("—")
            if len(parts) > 1:
                tourney = parts[-1].strip()

        records.append({
            "match_id":     event.get("id", ""),
            "player1_name": p1_name,
            "player2_name": p2_name,
            "tourney_name": tourney,
            "surface":      get_tournament_surface(tourney),
            "tourney_level": get_tournament_level(tourney),
            "round":        "",
            "commence_time": commence,
            "game_date":    date_str,
            "source":       "odds_api",
        })

    logger.info("The Odds API ATP: %d partidos para %s", len(records), date_str)
    return pd.DataFrame(records) if records else pd.DataFrame()


# ── Construcción del DataFrame final ─────────────────────────────────────────

def _enrich_with_player_ids(df: pd.DataFrame) -> pd.DataFrame:
    """Añade player1_id y player2_id resolviendo los nombres con rankings_client."""
    if df.empty:
        return df

    ids = [
        resolve_players(row["player1_name"], row["player2_name"])
        for _, row in df.iterrows()
    ]
    df["player1_id"] = [p[0] for p in ids]
    df["player2_id"] = [p[1] for p in ids]
    return df


def _build_match_id(row: dict) -> str:
    """Genera un match_id reproducible desde los datos del partido."""
    if row.get("match_id"):
        return str(row["match_id"])
    d  = str(row.get("game_date", "")).replace("-", "")
    p1 = str(row.get("player1_name", "")).split()[-1].lower()
    p2 = str(row.get("player2_name", "")).split()[-1].lower()
    t  = str(row.get("tourney_name", "atp")).split()[0].lower()
    return f"atp_{d}_{t}_{p1}_{p2}"


# ── API pública ───────────────────────────────────────────────────────────────

def get_daily_matches(date_str: Optional[str] = None) -> pd.DataFrame:
    """
    Obtiene los partidos ATP programados para una fecha.

    Args:
        date_str: Fecha 'YYYY-MM-DD'. Si es None, usa hoy.

    Returns:
        DataFrame con columnas:
          match_id, player1_name, player2_name, player1_id, player2_id,
          tourney_name, surface, tourney_level, round,
          commence_time, game_date, source

        Vacío si no hay partidos o si ninguna fuente está disponible.
    """
    if date_str is None:
        date_str = date.today().isoformat()

    logger.info("Buscando partidos ATP para %s…", date_str)

    # Intentar fuentes en orden de prioridad
    df = _fetch_from_odds_api(date_str)

    if df.empty:
        logger.warning(
            "No se encontraron partidos ATP para %s. "
            "Configura ODDS_API_KEY en .env para acceder a The Odds API. "
            "La Fase 3 añadirá Betplay/Rushbet como fuente alternativa.",
            date_str,
        )
        return pd.DataFrame()

    # Enriquecer con player_ids de Sackmann
    df = _enrich_with_player_ids(df)

    # Generar match_id consistente
    df["match_id"] = df.apply(lambda r: _build_match_id(r.to_dict()), axis=1)
    df["fetch_date"] = date_str

    resolved = df["player1_id"].notna().sum()
    logger.info(
        "  → %d partidos ATP | %d/%d jugadores resueltos a ID Sackmann",
        len(df), resolved, len(df) * 2,
    )
    return df
