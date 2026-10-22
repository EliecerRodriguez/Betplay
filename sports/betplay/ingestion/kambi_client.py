"""
Cliente de cuotas Kambi para Liga BetPlay (fútbol).

Obtiene los mercados de Over/Under de TARJETAS y CÓRNERS de Betplay.co (Kambi).

Flujo:
  1. get_league_events()  → eventos de la liga con event_id Kambi (vía prepacks)
  2. get_match_odds(event_id) → mercados del partido (vía betoffer/event/{id}.json)
  3. get_betplay_odds(date) → consolida todos los partidos con cuotas de córners
     y tarjetas (líneas Over/Under).

Endpoints descubiertos (2026-10-21):
  - betoffer: us.offering-api.kambicdn.com/offering/v2018/betplay/betoffer/event/{id}.json
  - prepacks: offering.sbo.fra-hub.../betplay/api/prepacks?prepack_id=...
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
import requests

from utils.logger import get_logger

logger = get_logger(__name__)

# ── Configuración Kambi ────────────────────────────────────────────────────────
_KAMBI_BASE = "https://us.offering-api.kambicdn.com/offering/v2018"
# Endpoint nuevo de betplay para prepacks (lista eventos de la liga)
_PREPACK_BASE = "https://offering.sbo.fra-hub.workload.shapegamescloud.com/betplay/api"
_SHAPE_BASE = "https://offering.sbo.fra-hub.workload.shapegamescloud.com/betplay/api"

_HTTP_TIMEOUT = 15
_KAMBI_OPERATORS: dict[str, dict] = {
    "Betplay": {"operator": "betplay", "lang": "es_CO", "market": "CO"},
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
}

# Prepacks que corresponden a la Liga BetPlay (descubiertos en 2026-10-21)
_BETPLAY_LEAGUE_PREPACKS = [
    "8239501", "8239503", "8239504", "8239508", "8239528", "8239540",
]


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _kambi_get(operator: str, path: str, extra: Optional[dict] = None) -> Optional[dict]:
    """GET a la API de ofertas Kambi. Devuelve JSON o None."""
    url = f"{_KAMBI_BASE}/{operator}/{path}"
    params: dict = {"client_id": "200", "channel_id": "1", "ncid": "1",
                    "lang": "es_CO", "market": "CO"}
    if extra:
        params.update(extra)
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("Kambi %s/%s → %s", operator, path, exc)
        return None


def _shape_get(path: str, params: Optional[dict] = None) -> Optional[dict]:
    """GET a la API nueva de Betplay (shapegamescloud)."""
    try:
        r = requests.get(f"{_SHAPE_BASE}/{path}", params=params, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("Shape %s → %s", path, exc)
        return None


# ── Eventos de la liga (vía prepacks) ────────────────────────────────────────

def get_league_events(prepack_ids: Optional[list[str]] = None) -> pd.DataFrame:
    """
    Obtiene los eventos de la Liga BetPlay con sus event_ids Kambi.

    Usa el endpoint prepacks de Betplay (shapegamescloud) que lista los
    partidos con home/away y event_id.

    Returns:
        DataFrame: event_id, home_team, away_team.
    """
    prepack_ids = prepack_ids or _BETPLAY_LEAGUE_PREPACKS
    params = {"lang": "es-CO", "market": "CO"}
    for pid in prepack_ids:
        params[f"prepack_id"] = pid  # se repite el param (requests lo maneja)

    all_events = {}
    for pid in prepack_ids:
        p = {"lang": "es-CO", "market": "CO", "prepack_id": pid}
        data = _shape_get("prepacks", p)
        if not data:
            continue
        for prepack in data.get("prepacks", []):
            for ev in prepack.get("prepack_events", []):
                eid = str(ev.get("event_id", ""))
                if not eid:
                    continue
                parts = ev.get("participants") or {}
                home = (parts.get("home") or {}).get("name", "")
                away = (parts.get("away") or {}).get("name", "")
                all_events[eid] = {"event_id": eid, "home_team": home, "away_team": away}

    df = pd.DataFrame(list(all_events.values()))
    logger.info("Betplay: %d eventos de la Liga BetPlay via prepacks", len(df))
    return df


# ── Mercados de un partido ────────────────────────────────────────────────────

def get_match_odds(event_id: str) -> Optional[dict]:
    """
    Obtiene los mercados de córners y tarjetas de un partido de la Liga BetPlay.

    Args:
        event_id: ID del evento Kambi (ej. 1029420496).

    Returns:
        dict con:
          event_id, home_team, away_team
          corners_line, corners_over_odds, corners_under_odds  (Total tiros esquina)
          home_corners_line, home_corners_over, home_corners_under (córners equipo local)
          away_corners_line, away_corners_over, away_corners_under (córners visitante)
          cards_line, cards_over_odds, cards_under_odds (tarjetas si disponible)
        None si no hay mercados.
    """
    data = _kambi_get("betplay", f"betoffer/event/{event_id}.json")
    if not data:
        return None

    offers = data.get("betOffers", [])
    result = {"event_id": str(event_id)}

    # Nombre de equipos desde el header si existe
    header = data.get("header") or {}
    competitors = header.get("competitors") or []
    if len(competitors) >= 2:
        result["home_team"] = competitors[0].get("name", "")
        result["away_team"] = competitors[1].get("name", "")

    import re

    def _collect_lines(offer) -> dict:
        """Agrupa outcomes por línea: {line: {'over': odds, 'under': odds}}."""
        lines = {}
        for out in offer.get("outcomes", []):
            odds = (out.get("odds") or 0) / 1000.0
            label_out = out.get("label", "").lower()
            is_over = "más" in label_out
            is_under = "menos" in label_out
            line = None
            # Kambi pone la línea en 'line' (en milésimas: 9500 = 9.5)
            line_raw = out.get("line")
            if line_raw is not None:
                line = float(line_raw) / 1000.0
            if line is None:
                m = re.search(r"([\d.]+)", label_out)
                if m:
                    line = float(m.group(1))
            if line is None:
                continue
            if line not in lines:
                lines[line] = {}
            if is_over:
                lines[line]["over"] = round(odds, 2)
            elif is_under:
                lines[line]["under"] = round(odds, 2)
        return lines

    # Línea objetivo para córners (la que predice el modelo)
    TARGET_CORNERS_LINE = 9.5

    def _pick_line(lines: dict, target: float) -> dict:
        """Elige la línea deseada; si no existe, la más cercana."""
        if not lines:
            return {}
        if target in lines:
            return lines[target]
        best_line = min(lines.keys(), key=lambda l: abs(l - target))
        return {**lines.get(best_line, {}), "line_used": best_line}

    for offer in offers:
        label_lower = ((offer.get("criterion") or {}).get("label", "")).lower()

        if "total de tiros de esquina" in label_lower and "intervalo" not in label_lower \
                and "a favor" not in label_lower:
            lines = _collect_lines(offer)
            chosen = _pick_line(lines, TARGET_CORNERS_LINE)
            if "over" in chosen:
                # Conservar la línea MÁS CERCANA a la objetivo entre todas las ofertas
                cur_line = result.get("corners_line")
                new_line = chosen.get("line_used", TARGET_CORNERS_LINE)
                if cur_line is None or abs(cur_line - TARGET_CORNERS_LINE) > abs(new_line - TARGET_CORNERS_LINE):
                    result["corners_line"] = new_line
                    result["corners_over_odds"] = chosen["over"]
                    result["corners_under_odds"] = chosen.get("under")
        elif "a favor de" in label_lower and "tiros de esquina" in label_lower:
            if "intervalo" in label_lower:
                continue
            # Distinguir córners del local vs visitante:
            # el label contiene el nombre del equipo ("a favor de Atlético Nacional")
            label_team = label_lower.replace("total de tiros de esquina a favor de", "").strip()
            home_name = result.get("home_team", "").lower()
            prefix = "home_corners" if home_name and home_name in label_team else "away_corners"
            lines = _collect_lines(offer)
            chosen = _pick_line(lines, 4.5)  # línea por equipo típica
            prefix_key = f"{prefix}_line"
            if "over" in chosen and result.get(prefix_key) is None:
                result[prefix_key] = chosen.get("line_used", 4.5)
                result[f"{prefix}_over_odds"] = chosen["over"]
                result[f"{prefix}_under_odds"] = chosen.get("under")
        # Tarjetas: buscar "tarjeta" o "card" en el label
        elif "tarjeta" in label_lower or "card" in label_lower:
            if "intervalo" not in label_lower and "siguiente" not in label_lower:
                lines = _collect_lines(offer)
                chosen = _pick_line(lines, 4.5)
                if "over" in chosen and result.get("cards_line") is None:
                    result["cards_line"] = chosen.get("line_used", 4.5)
                    result["cards_over_odds"] = chosen["over"]
                    result["cards_under_odds"] = chosen.get("under")

    logger.info(
        "Betplay evento %s: córners %s (O %s/U %s) | tarjetas %s | local %s",
        event_id,
        result.get("corners_line"), result.get("corners_over_odds"),
        result.get("corners_under_odds"), result.get("cards_line"),
        result.get("home_corners_line"),
    )
    return result if result.get("corners_line") is not None else None


# ── Consolidado del día ──────────────────────────────────────────────────────

def get_betplay_odds(date_str: Optional[str] = None) -> pd.DataFrame:
    """
    Obtiene cuotas de córners/tarjetas de todos los partidos de la Liga BetPlay.

    Args:
        date_str: Fecha (no se usa en la API — Kambi da todos los abiertos).

    Returns:
        DataFrame: event_id, home_team, away_team, corners_line,
                   corners_over_odds, corners_under_odds,
                   cards_line, cards_over_odds, cards_under_odds, fetch_date.
    """
    events = get_league_events()
    if events.empty:
        return pd.DataFrame()

    rows = []
    for _, ev in events.iterrows():
        odds = get_match_odds(ev["event_id"])
        if not odds:
            continue
        # Propagar nombres de equipos desde el prepack (el header de betoffer
        # no siempre los trae) — clave para unir con predicciones ESPN.
        if not odds.get("home_team"):
            odds["home_team"] = ev.get("home_team", "")
        if not odds.get("away_team"):
            odds["away_team"] = ev.get("away_team", "")
        odds["fetch_date"] = date.today().isoformat()
        rows.append(odds)

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["event_id"])
    logger.info("Betplay: %d partidos con cuotas de córners/tarjetas", len(df))
    return df
