"""
Inspección de APIs de cuotas NBA en Wplay, Betsson y Bwin (Colombia).
Ejecutar con: python utils/inspect_apis.py
"""
import time
import json
import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
}

# ── WPLAY (Playtech / openapi) ────────────────────────────────────────────────
# Wplay usa la plataforma Playtech con dominio openapi.wplay.co

WPLAY_ENDPOINTS = [
    # Posibles endpoints Playtech
    "https://api-ptech.wplay.co/sports/api/sportsbook/events?sport=basketball&region=US&league=NBA",
    "https://openapi.wplay.co/api/sports/basketball/NBA",
    "https://openapi.wplay.co/sports/api/basketball/events",
    "https://www.wplay.co/api/sports/v1/events?sport=BASKETBALL&league=NBA",
    "https://www.wplay.co/pageInfo/deportes/baloncesto/nba",
]

# ── BETSSON ────────────────────────────────────────────────────────────────────
# Betsson Colombia usa su propia API en betsson.co

BETSSON_ENDPOINTS = [
    "https://www.betsson.co/api/v3/content/sports/events?sport=basketball&competition=nba",
    "https://www.betsson.co/api/v2/sports/basketball/competitions/nba/events",
    "https://sbapi.betsson.co/api/v1/sports/basketball/competitions?jurisdiction=COGA",
    "https://sbapi.betsson.co/api/v1/sports/events?sport=basketball&competition=nba&jurisdiction=COGA",
    "https://www.betsson.co/api/v3/sports/basketball/nba/events",
    "https://api.betsson.co/sportsbook/v1/events?category=basketball&league=nba",
]

# ── BWIN ──────────────────────────────────────────────────────────────────────
# Bwin Colombia

BWIN_ENDPOINTS = [
    "https://sports.bwin.co/api/v1/sports/7/competitions/35/events",
    "https://sports.bwin.co/api/v2/events?sportId=7&leagueId=35",
    "https://www.bwin.co/es/api/sports/basketball/nba/events",
    "https://cds-api.bwin.co/bettingoffer/fixtures?x-bwin-accessid=YjRmMTJhZTktNTg4My00MGE0LThlNmItY2E0MzE4NmIwY2Mw&lang=es&country=CO&userCountry=CO&subdivision=&fixtureTypes=Standard&state=Latest&skip=0&take=50&offerMapping=Filtered&offerCategories=Gridable&scoreboardMode=Full&filterType=League&regionId=5&leagueId=35",
]

def probe_endpoints(name, endpoints):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print('='*60)
    for url in endpoints:
        try:
            r = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
            ct = r.headers.get("Content-Type", "")
            print(f"\n  URL: {url}")
            print(f"  Status: {r.status_code} | Content-Type: {ct[:60]}")
            if r.status_code == 200:
                if "json" in ct:
                    data = r.json()
                    snippet = json.dumps(data, ensure_ascii=False)[:300]
                    print(f"  JSON: {snippet}...")
                else:
                    print(f"  Body (200 chars): {r.text[:200]}")
        except Exception as e:
            print(f"\n  URL: {url}")
            print(f"  ERROR: {e}")
        time.sleep(0.5)

if __name__ == "__main__":
    probe_endpoints("WPLAY", WPLAY_ENDPOINTS)
    probe_endpoints("BETSSON", BETSSON_ENDPOINTS)
    probe_endpoints("BWIN", BWIN_ENDPOINTS)
