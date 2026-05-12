"""
Investigación específica de APIs Wplay, Betsson, Bwin.
"""
import json
import time
import requests

HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
    "Referer": "https://www.wplay.co/",
    "Origin": "https://www.wplay.co",
}

def get(url, params=None, headers=None):
    h = headers or HEADERS_BROWSER
    try:
        r = requests.get(url, params=params, headers=h, timeout=10)
        ct = r.headers.get("Content-Type", "")
        print(f"  {r.status_code} | {ct[:50]} | {url[:100]}")
        if r.status_code == 200 and r.content:
            if "json" in ct:
                d = r.json()
                print(f"  JSON keys: {list(d.keys())[:8] if isinstance(d, dict) else type(d).__name__ + '[' + str(len(d)) + ']'}")
                print(f"  Sample: {json.dumps(d, ensure_ascii=False)[:400]}")
            elif r.text.strip():
                print(f"  Body[200]: {r.text[:200]}")
        elif r.status_code == 400:
            print(f"  Body[400]: {r.text[:300]}")
        elif r.status_code == 401 or r.status_code == 403:
            print(f"  Body[{r.status_code}]: {r.text[:200]}")
        return r
    except Exception as e:
        print(f"  ERROR | {url[:100]}")
        print(f"  {e}")
        return None

print("\n" + "="*70)
print("WPLAY - Explorando openapi.wplay.co")
print("="*70)

# Wplay / Playtech IMS API patterns
wplay_urls = [
    ("https://openapi.wplay.co/api/1.0/sports/", None),
    ("https://openapi.wplay.co/api/1.0/sport/", None),
    ("https://openapi.wplay.co/api/1.0/events/", {"sportId": "4", "lang": "es"}),
    ("https://openapi.wplay.co/api/1.0/events/", {"sport": "basketball", "lang": "es"}),
    ("https://openapi.wplay.co/api/v1/events/", {"sport": "BASKETBALL"}),
    # Playtech IMS typical paths:
    ("https://openapi.wplay.co/ims-api/", None),
    ("https://openapi.wplay.co/sports-api/v1/basketball/", None),
    ("https://openapi.wplay.co/sports-api/v1/events/", {"sport": "Basketball", "league": "NBA"}),
    # Some Playtech setups use /sportsbook/
    ("https://openapi.wplay.co/sportsbook/api/v1/fixtures/", {"sport": "basketball"}),
]

for url, params in wplay_urls:
    get(url, params)
    time.sleep(0.3)

print("\n" + "="*70)
print("BETSSON - Explorando endpoints conocidos")
print("="*70)

betsson_urls = [
    # Betsson mobile API pattern
    ("https://www.betsson.co/api/v2/sportsbook/sports/", None),
    ("https://www.betsson.co/api/v3/sportsbook/sports/basketball/events/", None),
    # Betsson usa Kambi! Verificar:
    ("https://eu.offering-api.kambicdn.com/offering/v2018/betssonce/event/group/1000093652.json",
     {"client_id": "2", "channel_id": "1", "lang": "es_ES", "market": "CO"}),
    ("https://eu.offering-api.kambicdn.com/offering/v2018/betssonco/event/group/1000093652.json",
     {"client_id": "2", "channel_id": "1", "lang": "es_ES", "market": "CO"}),
    ("https://us.offering-api.kambicdn.com/offering/v2018/betssonce/event/group/1000093652.json",
     {"client_id": "2", "channel_id": "1", "lang": "es_ES", "market": "CO"}),
]

betsson_h = {
    **HEADERS_BROWSER,
    "Referer": "https://www.betsson.co/",
    "Origin": "https://www.betsson.co",
}

for url, params in betsson_urls:
    get(url, params, betsson_h)
    time.sleep(0.3)

print("\n" + "="*70)
print("BWIN - Explorando CDS API con host sports.bwin.co")
print("="*70)

# sports.bwin.co returns 200 (but empty), try with proper headers
bwin_h = {
    **HEADERS_BROWSER,
    "Referer": "https://www.bwin.co/",
    "Origin": "https://www.bwin.co",
    "X-Widget": "1",
}

# bwin.co uses MGM/Entain CDS API
# accessid might be needed - let's probe base domains first
bwin_urls = [
    ("https://sports.bwin.co/api/v1/sports/7/competitions/35/events", None),
    ("https://sports.bwin.co/api/v2/sports/7/leagues/35/events", None),
    # Entain/GVC CDS API
    ("https://cds-api.bwin.co/bettingoffer/fixtures", {
        "lang": "es", "country": "CO", "userCountry": "CO",
        "fixtureTypes": "Standard", "state": "Latest",
        "skip": "0", "take": "50",
        "offerMapping": "Filtered",
        "filterType": "League", "regionId": "5", "leagueId": "35"
    }),
    ("https://ms.bwin.co/ms/api/sports/7/competitions/35/events", None),
]

for url, params in bwin_urls:
    get(url, params, bwin_h)
    time.sleep(0.3)
