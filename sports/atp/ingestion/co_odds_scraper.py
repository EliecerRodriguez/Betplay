"""
Cuotas ATP desde casas colombianas vía Kambi API.

Casas implementadas (sin Playwright, API directa):
  - Betplay  (betplay.com.co)  → Kambi operator: betplay
  - Rushbet  (rushbet.co)      → Kambi operator: rsico

Cubre todos los torneos ATP masculinos disponibles en Betplay/Rushbet:
  - Torneos ATP regulares   (group_id: 1000093324)
  - Grand Slams             (group_id: 1000093528)

Estructura Kambi para tenis:
  - event.homeName / awayName  = nombres de jugadores
  - event.group                = nombre del torneo (ej. "Hamburgo", "Roland Garros")
  - event.path[2]              = categoría (ATP / Grand Slam)
  - betoffer tipo 2 = "Match"  → cuotas ganador del partido (2-way)
  - odds / 1000 = cuota decimal europea

Funciones públicas:
  - get_atp_co_odds(date_str)  → DataFrame con todas las cuotas disponibles
  - get_best_odds(p1, p2)      → mejores cuotas consolidadas para un partido
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
import requests

from sports.atp.config.settings import ATP_KAMBI_GROUP_BETPLAY, ATP_KAMBI_GROUP_RUSHBET
from sports.atp.ingestion.atp_client import get_tournament_surface, get_tournament_level
from sports.atp.ingestion.rankings_client import get_player_id_by_name
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Configuración Kambi ────────────────────────────────────────────────────────
_KAMBI_BASE = "https://us.offering-api.kambicdn.com/offering/v2018"

# Grupo ATP general (torneos regulares 250/500/1000)
_ATP_GROUP_ID      = 1000093324
# Grand Slams (Australian Open, Roland Garros, Wimbledon, US Open)
_GRAND_SLAM_ID     = 1000093528

# betOfferType.id == 2 → Ganador del partido (Match/H2H)
_MATCH_OFFER_ID    = 2

_HTTP_TIMEOUT = 15

_KAMBI_OPERATORS: dict[str, dict] = {
    "Betplay": {"operator": "betplay", "lang": "es_CO", "market": "CO"},
    "Rushbet": {"operator": "rsico",   "lang": "es_ES", "market": "CO"},
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
}


# ── Kambi HTTP helper ──────────────────────────────────────────────────────────

def _kambi_get(
    operator: str,
    path: str,
    extra: Optional[dict] = None,
) -> Optional[dict]:
    """GET a Kambi API. Devuelve JSON o None si falla."""
    url = f"{_KAMBI_BASE}/{operator}/{path}"
    params: dict = {"client_id": "200", "channel_id": "1", "ncid": "1"}
    if extra:
        params.update(extra)
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("Kambi %s/%s → %s", operator, path, exc)
        return None


# ── Scraping por operador ──────────────────────────────────────────────────────

def _fetch_kambi_atp(
    bookmaker_name: str,
    operator: str,
    lang: str,
    market: str,
    date_str: Optional[str] = None,
) -> list[dict]:
    """
    Obtiene cuotas de ganador de partido ATP desde Kambi.

    Recorre los grupos:
      1000093324 (ATP regular)
      1000093528 (Grand Slams)

    Returns:
        Lista de dicts con: player1_name, player2_name, player1_odds,
        player2_odds, tourney_name, surface, tourney_level,
        event_id, match_datetime, bookmaker.
    """
    extra = {"lang": lang, "market": market}
    target_date = date_str or date.today().isoformat()
    records: list[dict] = []

    group_ids = [_ATP_GROUP_ID, _GRAND_SLAM_ID]
    # Añadir grupos custom si el usuario los configuró
    if ATP_KAMBI_GROUP_BETPLAY and operator == "betplay":
        group_ids.append(ATP_KAMBI_GROUP_BETPLAY)
    if ATP_KAMBI_GROUP_RUSHBET and operator == "rsico":
        group_ids.append(ATP_KAMBI_GROUP_RUSHBET)

    all_events = []
    for gid in group_ids:
        data = _kambi_get(operator, f"event/group/{gid}.json", extra)
        if not data:
            continue
        evs = data.get("events", [])
        logger.debug("%s grupo %d: %d eventos", bookmaker_name, gid, len(evs))
        all_events.extend(evs)

    # Deduplicar por event_id (puede aparecer en ambos grupos)
    seen_ids: set[int] = set()
    unique_events = []
    for ev in all_events:
        eid = ev.get("id")
        if eid not in seen_ids:
            seen_ids.add(eid)
            unique_events.append(ev)

    logger.info("%s: %d eventos ATP únicos encontrados", bookmaker_name, len(unique_events))

    for ev in unique_events:
        event_id  = ev.get("id")
        p1_name   = ev.get("homeName", "")
        p2_name   = ev.get("awayName", "")
        start_str = ev.get("start", "")
        tourney   = ev.get("group", "ATP")
        state     = ev.get("state", "")   # "NOT_STARTED", "STARTED", "FINISHED"

        # Excluir partidos femeninos / WTA (el grupo Grand Slam mezcla géneros)
        _tourney_lower = tourney.lower()
        if any(kw in _tourney_lower for kw in ("femenin", "women", "wta", "ladies", "feminine")):
            logger.debug("%s: omitiendo evento femenino '%s'", bookmaker_name, tourney)
            continue

        # Filtrar por fecha si se especificó
        try:
            dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            event_date = dt.astimezone(timezone.utc).date().isoformat()
        except Exception:
            event_date = ""

        if date_str and event_date and event_date != date_str:
            continue

        # Inferir superficie y nivel desde el nombre del torneo
        surface      = get_tournament_surface(tourney)
        tourney_level = get_tournament_level(tourney)

        # Intentar obtener superficie más precisa desde el path del evento
        path = ev.get("path", [])
        for node in path:
            if node.get("id") == _GRAND_SLAM_ID:
                tourney_level = "G"
            tourney_name_from_path = node.get("englishName", "")
            if tourney_name_from_path and tourney_name_from_path.lower() != "atp":
                surface = get_tournament_surface(tourney_name_from_path) or surface

        # Obtener cuotas del partido
        offers_data = _kambi_get(operator, f"betoffer/event/{event_id}.json", extra)
        if not offers_data:
            continue

        offers = offers_data.get("betOffers", [])

        # Kambi puede devolver múltiples ofertas tipo 2 (Match) por evento:
        #   - Algunas con labels de nombres ("Andrea Pellegrino" / "Lloyd Harris")
        #   - Otras con labels europeos ("1" / "X" / "2", 3 outcomes)
        # Preferimos la oferta con 2 outcomes donde los labels contienen apellidos.
        match_offer = None
        fallback_offer = None
        p1_last = p1_name.lower().split()[-1] if p1_name else ""
        p2_last = p2_name.lower().split()[-1] if p2_name else ""

        for offer in offers:
            otype = offer.get("betOfferType", {})
            if not (
                otype.get("id") == _MATCH_OFFER_ID
                or otype.get("englishName", "").lower() in ("match", "winner")
            ):
                continue
            outcomes = offer.get("outcomes", [])
            if len(outcomes) != 2:      # 3 = europeo 1/X/2; omitir
                continue
            labels = [oc.get("label", "").lower() for oc in outcomes]
            # Verificar que al menos un apellido aparece en los labels
            if (
                any(p1_last and p1_last in lbl for lbl in labels)
                and any(p2_last and p2_last in lbl for lbl in labels)
            ):
                match_offer = offer
                break
            # Guardar como fallback el primer offer de 2 outcomes
            if fallback_offer is None:
                fallback_offer = offer

        if match_offer is None:
            match_offer = fallback_offer   # acepta posicional si no hay nombre

        if match_offer is None:
            logger.debug("%s: sin oferta Match para %s vs %s", bookmaker_name, p1_name, p2_name)
            continue

        outcomes = match_offer.get("outcomes", [])
        p1_odds: Optional[float] = None
        p2_odds: Optional[float] = None

        for oc in outcomes:
            raw_odds = oc.get("odds")
            status   = oc.get("status", "")
            label    = oc.get("label", "")

            if status == "SUSPENDED" or raw_odds is None:
                continue

            decimal_odds = raw_odds / 1000.0
            if not (1.01 <= decimal_odds <= 50.0):
                continue

            # En tenis Kambi el label del outcome = nombre del jugador
            label_norm = label.lower().strip()
            p1_norm    = p1_name.lower().strip()
            p2_norm    = p2_name.lower().strip()

            if label_norm == p1_norm or p1_norm in label_norm or label_norm in p1_norm:
                p1_odds = round(decimal_odds, 3)
            elif label_norm == p2_norm or p2_norm in label_norm or label_norm in p2_norm:
                p2_odds = round(decimal_odds, 3)

        if p1_odds is None and p2_odds is None:
            # Fallback: asignar por posición
            for idx, oc in enumerate(outcomes):
                raw = oc.get("odds")
                if raw is None:
                    continue
                dec = raw / 1000.0
                if not (1.01 <= dec <= 50.0):
                    continue
                if idx == 0:
                    p1_odds = round(dec, 3)
                elif idx == 1:
                    p2_odds = round(dec, 3)

        if p1_odds is None or p2_odds is None:
            logger.debug(
                "%s: cuotas incompletas para %s vs %s (p1=%s, p2=%s)",
                bookmaker_name, p1_name, p2_name, p1_odds, p2_odds,
            )
            continue

        records.append({
            "event_id":      event_id,
            "player1_name":  p1_name,
            "player2_name":  p2_name,
            "player1_odds":  p1_odds,
            "player2_odds":  p2_odds,
            "tourney_name":  tourney,
            "surface":       surface,
            "tourney_level": tourney_level,
            "state":         state,
            "match_datetime": start_str,
            "game_date":     event_date or target_date,
            "bookmaker":     bookmaker_name,
        })

    logger.info(
        "%s ATP: %d partidos con cuotas obtenidas",
        bookmaker_name, len(records),
    )
    return records


# ── Probabilidades implícitas ─────────────────────────────────────────────────

def _implied_prob(odds: float) -> float:
    """Convierte cuota decimal a probabilidad implícita (sin margen)."""
    if odds <= 1.0:
        return 1.0
    return round(1.0 / odds, 6)


def _remove_vig(p1_raw: float, p2_raw: float) -> tuple[float, float]:
    """
    Elimina el vig (margen) de dos probabilidades implícitas.
    Devuelve probabilidades normalizadas que suman 1.0.
    """
    total = p1_raw + p2_raw
    if total <= 0:
        return 0.5, 0.5
    return round(p1_raw / total, 6), round(p2_raw / total, 6)


# ── API pública ───────────────────────────────────────────────────────────────

def get_atp_co_odds(date_str: Optional[str] = None) -> pd.DataFrame:
    """
    Obtiene todas las cuotas ATP de Betplay y Rushbet para una fecha.

    Cada fila del DataFrame es una línea (partido × casa de apuestas).
    Incluye probabilidad implícita sin vig para comparar con el modelo.

    Args:
        date_str: Fecha 'YYYY-MM-DD'. Si es None, usa hoy.

    Returns:
        DataFrame con columnas:
          event_id, player1_name, player2_name, player1_id, player2_id,
          player1_odds, player2_odds, player1_implied_prob, player2_implied_prob,
          vig, tourney_name, surface, tourney_level, state,
          match_datetime, game_date, bookmaker
    """
    target = date_str or date.today().isoformat()
    all_records: list[dict] = []

    for bk_name, bk_cfg in _KAMBI_OPERATORS.items():
        records = _fetch_kambi_atp(
            bookmaker_name=bk_name,
            operator=bk_cfg["operator"],
            lang=bk_cfg["lang"],
            market=bk_cfg["market"],
            date_str=target,
        )
        all_records.extend(records)

    if not all_records:
        logger.warning("No se encontraron cuotas ATP para %s", target)
        return pd.DataFrame()

    df = pd.DataFrame(all_records)

    # Resolver player_ids desde Sackmann
    df["player1_id"] = df["player1_name"].apply(get_player_id_by_name)
    df["player2_id"] = df["player2_name"].apply(get_player_id_by_name)

    # Calcular probabilidades implícitas (sin vig)
    df["p1_raw"]  = df["player1_odds"].apply(_implied_prob)
    df["p2_raw"]  = df["player2_odds"].apply(_implied_prob)
    df["vig"]     = (df["p1_raw"] + df["p2_raw"] - 1.0).round(4)

    probs = df.apply(lambda r: _remove_vig(r["p1_raw"], r["p2_raw"]), axis=1)
    df["player1_implied_prob"] = [p[0] for p in probs]
    df["player2_implied_prob"] = [p[1] for p in probs]

    # Limpiar columnas temporales
    df = df.drop(columns=["p1_raw", "p2_raw"])

    col_order = [
        "event_id", "player1_name", "player2_name",
        "player1_id", "player2_id",
        "player1_odds", "player2_odds",
        "player1_implied_prob", "player2_implied_prob", "vig",
        "tourney_name", "surface", "tourney_level", "state",
        "match_datetime", "game_date", "bookmaker",
    ]
    for c in col_order:
        if c not in df.columns:
            df[c] = None

    df = df[col_order].reset_index(drop=True)

    resolved = df["player1_id"].notna().sum()
    logger.info(
        "ATP cuotas Betplay/Rushbet: %d líneas | %d/%d jugadores resueltos",
        len(df), resolved, len(df),
    )
    return df


def get_best_odds(
    player1_name: str,
    player2_name: str,
    date_str: Optional[str] = None,
) -> dict:
    """
    Devuelve las mejores cuotas disponibles para un partido específico,
    consolidando todas las casas.

    Returns:
        {
          'player1_best_odds': float,
          'player2_best_odds': float,
          'player1_best_book': str,
          'player2_best_book': str,
          'player1_implied_prob': float,  # sin vig, mejor cuota
          'player2_implied_prob': float,
          'found': bool,
        }
    """
    df = get_atp_co_odds(date_str)
    if df.empty:
        return {"found": False}

    p1_norm = player1_name.lower().strip()
    p2_norm = player2_name.lower().strip()

    mask = (
        df["player1_name"].str.lower().str.contains(p1_norm, na=False)
        | df["player2_name"].str.lower().str.contains(p1_norm, na=False)
    ) & (
        df["player1_name"].str.lower().str.contains(p2_norm, na=False)
        | df["player2_name"].str.lower().str.contains(p2_norm, na=False)
    )
    subset = df[mask]

    if subset.empty:
        return {"found": False}

    # Si los jugadores están en orden invertido, intercambiar odds
    result: dict = {"found": True}
    best_p1 = best_p2 = 0.0
    best_bk_p1 = best_bk_p2 = ""

    for _, row in subset.iterrows():
        is_inverted = p1_norm in str(row["player2_name"]).lower()
        p1_o = row["player2_odds"] if is_inverted else row["player1_odds"]
        p2_o = row["player1_odds"] if is_inverted else row["player2_odds"]
        bk   = row["bookmaker"]

        if p1_o > best_p1:
            best_p1 = p1_o
            best_bk_p1 = bk
        if p2_o > best_p2:
            best_p2 = p2_o
            best_bk_p2 = bk

    p1_imp, p2_imp = _remove_vig(_implied_prob(best_p1), _implied_prob(best_p2))

    result.update({
        "player1_best_odds": best_p1,
        "player2_best_odds": best_p2,
        "player1_best_book": best_bk_p1,
        "player2_best_book": best_bk_p2,
        "player1_implied_prob": p1_imp,
        "player2_implied_prob": p2_imp,
    })
    return result
