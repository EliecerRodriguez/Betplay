"""
Cuotas NBA desde casas colombianas e internacionales.

Casas implementadas con API directa (sin Playwright):
  - Betplay  (betplay.com.co)  -> Kambi API (operator: betplay)
  - Rushbet  (rushbet.co)      -> Kambi API (operator: rsico)

Casas pendientes (Wplay, Betsson, Bwin) se devuelven vacias hasta
que se identifique su API backend.

Uso:
  from ingestion.co_odds_scraper import get_co_odds
  df = get_co_odds()
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

import pandas as pd
import requests

from utils.logger import get_logger

logger = get_logger(__name__)

# -- Configuracion Kambi API --------------------------------------------------
_KAMBI_BASE     = "https://us.offering-api.kambicdn.com/offering/v2018"
_NBA_GROUP_ID   = 1000093652   # mismo ID en betplay y rsico
_MATCH_OFFER_ID      = 2   # betOfferType.id == 2 -> "Match" (h2h)
_OVER_UNDER_OFFER_ID = 6   # betOfferType.id == 6 -> "Over/Under" (totales de puntos)
_HTTP_TIMEOUT   = 15           # segundos

_KAMBI_OPERATORS: dict[str, dict] = {
    "Betplay": {"operator": "betplay", "lang": "es_CO", "market": "CO"},
    "Rushbet": {"operator": "rsico",   "lang": "es_ES", "market": "CO"},
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
}


# -- Mapeo de nombres de equipos ----------------------------------------------
_TEAM_ALIASES: dict[str, list[str]] = {
    "Atlanta Hawks":          ["atlanta", "hawks"],
    "Boston Celtics":         ["boston", "celtics"],
    "Brooklyn Nets":          ["brooklyn", "nets"],
    "Charlotte Hornets":      ["charlotte", "hornets"],
    "Chicago Bulls":          ["chicago", "bulls"],
    "Cleveland Cavaliers":    ["cleveland", "cavaliers", "cavs"],
    "Dallas Mavericks":       ["dallas", "mavericks", "mavs"],
    "Denver Nuggets":         ["denver", "nuggets"],
    "Detroit Pistons":        ["detroit", "pistons"],
    "Golden State Warriors":  ["golden state", "warriors", "g. state", "golden st"],
    "Houston Rockets":        ["houston", "rockets"],
    "Indiana Pacers":         ["indiana", "pacers"],
    "LA Clippers":            ["clippers", "la clippers", "los angeles clippers"],
    "LA Lakers":              ["lakers", "la lakers", "los angeles lakers",
                               "los angeles lakers"],
    "Memphis Grizzlies":      ["memphis", "grizzlies"],
    "Miami Heat":             ["miami", "heat"],
    "Milwaukee Bucks":        ["milwaukee", "bucks"],
    "Minnesota Timberwolves": ["minnesota", "timberwolves", "t-wolves", "timber"],
    "New Orleans Pelicans":   ["new orleans", "pelicans", "nueva orleans"],
    "New York Knicks":        ["new york", "knicks", "nueva york"],
    "Oklahoma City Thunder":  ["oklahoma", "thunder", "okc", "oklahoma city"],
    "Orlando Magic":          ["orlando", "magic"],
    "Philadelphia 76ers":     ["philadelphia", "76ers", "sixers", "filadelfia"],
    "Phoenix Suns":           ["phoenix", "suns"],
    "Portland Trail Blazers": ["portland", "trail blazers", "blazers"],
    "Sacramento Kings":       ["sacramento", "kings"],
    "San Antonio Spurs":      ["san antonio", "spurs"],
    "Toronto Raptors":        ["toronto", "raptors"],
    "Utah Jazz":              ["utah", "jazz"],
    "Washington Wizards":     ["washington", "wizards"],
}

_ALIAS_INDEX: dict[str, str] = {}
for _canonical, _aliases in _TEAM_ALIASES.items():
    _ALIAS_INDEX[_canonical.lower()] = _canonical
    for _alias in _aliases:
        _ALIAS_INDEX[_alias.lower()] = _canonical


def normalize_team(raw: str) -> Optional[str]:
    """Convierte nombre de equipo (cualquier formato) al nombre canonico NBA."""
    if not raw:
        return None
    cleaned = raw.strip().lower()
    if cleaned in _ALIAS_INDEX:
        return _ALIAS_INDEX[cleaned]
    for alias, canonical in _ALIAS_INDEX.items():
        if len(alias) >= 4 and alias in cleaned:
            return canonical
    return None


def _safe_float(text: str) -> Optional[float]:
    """Convierte texto de cuota a float. Retorna None si invalido."""
    try:
        val = float(re.sub(r"[^\d.,]", "", text).replace(",", "."))
        return val if 1.01 <= val <= 50.0 else None
    except (ValueError, AttributeError):
        return None


# -- Kambi API client ---------------------------------------------------------

def _kambi_get(operator: str, path: str, extra_params: dict | None = None) -> Optional[dict]:
    """GET a la API Kambi. Retorna JSON o None si falla."""
    url = f"{_KAMBI_BASE}/{operator}/{path}"
    params: dict = {"client_id": "200", "channel_id": "1", "ncid": "1"}
    if extra_params:
        params.update(extra_params)
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=_HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("Kambi %s/%s error: %s", operator, path, exc)
        return None


def _fetch_kambi_nba(bookmaker_name: str, operator: str, lang: str, market: str) -> list[dict]:
    """
    Obtiene cuotas h2h NBA desde Kambi API para un operador dado.

    Formato de cuotas Kambi: campo 'odds' / 1000 = cuota decimal europea.
    Ejemplo: odds=1360 -> 1.360 decimal.

    Returns:
        Lista de dicts: home_team, away_team, home_odds, away_odds, bookmaker.
    """
    extra = {"lang": lang, "market": market}
    today = date.today().isoformat()
    records: list[dict] = []

    # 1. Obtener eventos del grupo NBA
    data = _kambi_get(operator, f"event/group/{_NBA_GROUP_ID}.json", extra)
    if not data:
        logger.warning("%s: no se pudo obtener eventos del grupo NBA", bookmaker_name)
        return records

    events = data.get("events", [])
    logger.info("%s: %d eventos NBA encontrados via Kambi", bookmaker_name, len(events))

    for ev in events:
        event_id = ev.get("id")
        home_raw = ev.get("homeName", "")
        away_raw = ev.get("awayName", "")

        home_team = normalize_team(home_raw)
        away_team = normalize_team(away_raw)

        if not home_team or not away_team:
            logger.debug(
                "%s: equipos no reconocidos: '%s' vs '%s'",
                bookmaker_name, home_raw, away_raw,
            )
            continue

        # 2. Obtener bet offers del evento
        offers_data = _kambi_get(operator, f"betoffer/event/{event_id}.json", extra)
        if not offers_data:
            continue

        offers = offers_data.get("betOffers", [])

        # Buscar oferta "Match" (ganador del partido, h2h)
        match_offer = None
        for offer in offers:
            offer_type = offer.get("betOfferType", {})
            if (
                offer_type.get("id") == _MATCH_OFFER_ID
                or offer_type.get("englishName", "").lower() == "match"
            ):
                match_offer = offer
                break

        if match_offer is None:
            logger.debug(
                "%s: sin oferta Match para %s vs %s (event %s)",
                bookmaker_name, home_team, away_team, event_id,
            )
            continue

        # 3. Parsear outcomes
        outcomes = match_offer.get("outcomes", [])
        home_odds: Optional[float] = None
        away_odds: Optional[float] = None

        for oc in outcomes:
            raw_odds = oc.get("odds")
            status   = oc.get("status", "")
            label    = oc.get("label", "")

            if status == "SUSPENDED" or raw_odds is None:
                continue

            decimal_odds = raw_odds / 1000.0
            if not (1.01 <= decimal_odds <= 50.0):
                continue

            team = normalize_team(label)
            if team == home_team:
                home_odds = round(decimal_odds, 2)
            elif team == away_team:
                away_odds = round(decimal_odds, 2)

        # ── 4. Oferta Over/Under (totales de puntos) ─────────────────────────
        over_line:  Optional[float] = None
        over_odds:  Optional[float] = None
        under_odds: Optional[float] = None

        ou_offers = [
            o for o in offers
            if o.get("betOfferType", {}).get("id") == _OVER_UNDER_OFFER_ID
            and o.get("criterion", {}).get("label", "").lower().startswith("total de puntos")
            and "pr\u00f3rroga" in o.get("criterion", {}).get("label", "").lower()  # solo partido completo (incluye prórroga)
            and "del " not in o.get("criterion", {}).get("label", "").lower()        # excluye totales individuales por equipo
            and "mitad" not in o.get("criterion", {}).get("label", "").lower()       # excluye segunda mitad
            and "parte" not in o.get("criterion", {}).get("label", "").lower()       # excluye primera/segunda parte
        ]

        if ou_offers:
            # Seleccionar la línea más equilibrada (odds más cercanas entre sí)
            best_ou: Optional[dict] = None
            best_balance = float("inf")
            for offer in ou_offers:
                o_raw = u_raw = None
                for oc in offer.get("outcomes", []):
                    if oc.get("status") == "SUSPENDED" or oc.get("odds") is None:
                        continue
                    if oc.get("type") == "OT_OVER":
                        o_raw = oc["odds"]
                    elif oc.get("type") == "OT_UNDER":
                        u_raw = oc["odds"]
                if o_raw and u_raw:
                    balance = abs(o_raw - u_raw)
                    if balance < best_balance:
                        best_balance = balance
                        best_ou = {"offer": offer, "o_raw": o_raw, "u_raw": u_raw}

            if best_ou:
                for oc in best_ou["offer"].get("outcomes", []):
                    if oc.get("type") == "OT_OVER":
                        raw_line = oc.get("line", 0)
                        over_line = round(raw_line / 1000.0, 1)
                        break
                over_odds  = round(best_ou["o_raw"] / 1000.0, 2)
                under_odds = round(best_ou["u_raw"] / 1000.0, 2)

        if home_odds is not None and away_odds is not None:
            records.append({
                "home_team":   home_team,
                "away_team":   away_team,
                "bookmaker":   bookmaker_name,
                "home_odds":   home_odds,
                "away_odds":   away_odds,
                "over_line":   over_line,
                "over_odds":   over_odds,
                "under_odds":  under_odds,
                "fetch_date":  today,
                "event_start": ev.get("start", ""),
            })
            logger.debug(
                "%s: %s (%.2f) vs %s (%.2f) | O/U %.1f (%.2f/%.2f)",
                bookmaker_name, home_team, home_odds, away_team, away_odds,
                over_line or 0.0, over_odds or 0.0, under_odds or 0.0,
            )

    logger.info("%s: %d partidos con cuotas h2h validas", bookmaker_name, len(records))
    return records


# -- Scrapers publicos --------------------------------------------------------

def scrape_betplay() -> list[dict]:
    """Cuotas NBA de Betplay via Kambi API."""
    cfg = _KAMBI_OPERATORS["Betplay"]
    return _fetch_kambi_nba("Betplay", cfg["operator"], cfg["lang"], cfg["market"])


def scrape_rushbet() -> list[dict]:
    """Cuotas NBA de Rushbet via Kambi API."""
    cfg = _KAMBI_OPERATORS["Rushbet"]
    return _fetch_kambi_nba("Rushbet", cfg["operator"], cfg["lang"], cfg["market"])


def scrape_wplay() -> list[dict]:
    """Wplay: pendiente (plataforma Playtech, API no identificada)."""
    logger.info("Wplay: scraper aun no implementado (Playtech)")
    return []


def scrape_betsson() -> list[dict]:
    """Betsson: pendiente (API no identificada)."""
    logger.info("Betsson: scraper aun no implementado")
    return []


def scrape_bwin() -> list[dict]:
    """Bwin: pendiente (API no identificada)."""
    logger.info("Bwin: scraper aun no implementado")
    return []


# -- Orquestador --------------------------------------------------------------

def _attach_game_ids(
    odds_df: pd.DataFrame,
    games_df: pd.DataFrame,
) -> pd.DataFrame:
    """Une game_id de nba_api con partidos scrapeados via nombres de equipo."""
    try:
        from nba_api.stats.static import teams as nba_teams_static

        nba_teams = pd.DataFrame(nba_teams_static.get_teams())
        id_to_name = dict(zip(nba_teams["id"], nba_teams["full_name"]))

        def _get_name(tid):
            return id_to_name.get(int(tid), "") if tid else ""

        needed = {"game_id", "home_team_id", "visitor_team_id"}
        if not needed.issubset(set(games_df.columns)):
            odds_df["game_id"] = ""
            return odds_df

        match_rows = []
        for _, g in games_df.iterrows():
            home_name = _get_name(g["home_team_id"])
            away_name = _get_name(g["visitor_team_id"])
            match_rows.append({
                "game_id":       g["game_id"],
                "nba_home_team": home_name,
                "nba_away_team": away_name,
            })

        match_df = pd.DataFrame(match_rows)
        match_df["home_key"] = match_df["nba_home_team"].apply(normalize_team)
        match_df["away_key"] = match_df["nba_away_team"].apply(normalize_team)

        # Construir lookup bidireccional: (teamA, teamB) → game_id
        # para manejar inversiones home/away entre Kambi y nba_api
        game_lookup: dict[tuple, str] = {}
        for _, mrow in match_df.iterrows():
            gid = mrow["game_id"]
            hk  = mrow["home_key"]
            ak  = mrow["away_key"]
            if hk and ak:
                game_lookup[(hk, ak)] = gid
                game_lookup[(ak, hk)] = gid  # también en orden inverso

        def _lookup_gid(r):
            return game_lookup.get((r["home_team"], r["away_team"]), "")

        odds_df = odds_df.copy()
        odds_df["game_id"] = odds_df.apply(_lookup_gid, axis=1)

        assigned = (odds_df["game_id"] != "").sum()
        logger.info(
            "_attach_game_ids: %d/%d cuotas con game_id asignado",
            assigned,
            len(odds_df),
        )
        return odds_df

    except Exception as exc:
        logger.warning("_attach_game_ids fallo: %s - cuotas sin game_id", exc)
        odds_df["game_id"] = ""
        return odds_df


def get_co_odds(games_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    Obtiene cuotas NBA reales de todas las casas colombianas implementadas.

    Args:
        games_df: DataFrame de partidos del dia (para asignar game_id).
                  Columnas esperadas: game_id, home_team_id, visitor_team_id.

    Returns:
        DataFrame con columnas:
          game_id, home_team, away_team, bookmaker, home_odds, away_odds, fetch_date
    """
    all_records: list[dict] = []

    scrapers = [
        ("Betplay", scrape_betplay),
        ("Rushbet", scrape_rushbet),
        ("Wplay",   scrape_wplay),
        ("Betsson", scrape_betsson),
        ("Bwin",    scrape_bwin),
    ]

    for name, scraper_fn in scrapers:
        try:
            records = scraper_fn()
            all_records.extend(records)
        except Exception as exc:
            logger.error("%s: error inesperado: %s", name, exc)

    if not all_records:
        logger.warning("co_odds_scraper: ninguna casa devolvio cuotas NBA")
        return pd.DataFrame()

    df = pd.DataFrame(all_records)
    df = df.drop_duplicates(subset=["home_team", "away_team", "bookmaker"])

    # Auto-descubrir fechas de Kambi que no estan en games_df y consultarlas
    if "event_start" in df.columns:
        from datetime import datetime as _dt, timedelta as _td
        from ingestion.nba_client import get_daily_games

        existing_dates: set[str] = set()
        if games_df is not None and not games_df.empty and "game_date" in games_df.columns:
            existing_dates = set(games_df["game_date"].astype(str).unique())

        extra_games: list = []
        seen_dates: set[str] = set()
        for es in df["event_start"].dropna().unique():
            if not es:
                continue
            try:
                # Kambi usa UTC; NBA API usa Eastern Time (UTC-5)
                dt_utc = _dt.fromisoformat(str(es).replace("Z", "+00:00"))
                qdate = (dt_utc - _td(hours=5)).strftime("%Y-%m-%d")
                if qdate not in existing_dates and qdate not in seen_dates:
                    seen_dates.add(qdate)
                    logger.info("Auto-fetch games para fecha Kambi: %s", qdate)
                    extra = get_daily_games(qdate)
                    if not extra.empty:
                        extra_games.append(extra)
                        existing_dates.add(qdate)
            except Exception as _e:
                logger.debug("No se pudo parsear event_start '%s': %s", es, _e)

        if extra_games:
            if games_df is not None and not games_df.empty:
                games_df = pd.concat([games_df] + extra_games, ignore_index=True)
            else:
                games_df = pd.concat(extra_games, ignore_index=True)

    if games_df is not None and not games_df.empty:
        df = _attach_game_ids(df, games_df)

    cols_order = ["game_id", "home_team", "away_team", "bookmaker",
                  "home_odds", "away_odds", "over_line", "over_odds", "under_odds", "fetch_date"]
    existing = [c for c in cols_order if c in df.columns]
    df = df[existing]

    logger.info(
        "co_odds_scraper: total %d cuotas de %d casas",
        len(df),
        df["bookmaker"].nunique() if "bookmaker" in df.columns else 0,
    )
    return df
