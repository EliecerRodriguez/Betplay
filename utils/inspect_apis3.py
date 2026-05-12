"""
Verificación de Betsson en Kambi API y Wplay con sesión.
"""
import json
import time
import requests

KAMBI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "es-CO,es;q=0.9",
}
KAMBI_BASE = "https://eu.offering-api.kambicdn.com/offering/v2018"
KAMBI_BASE_US = "https://us.offering-api.kambicdn.com/offering/v2018"
NBA_GROUP = 1000093652

def kambi_probe(base, operator, lang="es_ES", market="CO"):
    url = f"{base}/{operator}/event/group/{NBA_GROUP}.json"
    params = {"client_id": "200", "channel_id": "1", "ncid": "1", "lang": lang, "market": market}
    try:
        r = requests.get(url, params=params, headers=KAMBI_HEADERS, timeout=15)
        ct = r.headers.get("Content-Type", "")
        print(f"  {r.status_code} | {operator} | {base.split('//')[1].split('/')[0]}")
        if r.status_code == 200 and "json" in ct:
            data = r.json()
            events = data.get("events", [])
            nba_events = [e for e in events if e.get("homeName") and e.get("awayName")]
            print(f"  => {len(nba_events)} eventos h2h encontrados")
            for ev in nba_events[:4]:
                print(f"     {ev.get('homeName')} vs {ev.get('awayName')}")
        elif r.status_code == 429:
            print(f"  => 429 Rate limited (operador existe!)")
        elif r.status_code == 404:
            print(f"  => 404 Operador no existe")
        else:
            print(f"  => {r.text[:100]}")
    except Exception as e:
        print(f"  ERROR: {e}")

print("="*60)
print("BETSSON - Probando operadores Kambi con delay")
print("="*60)

# Betsson Colombia usa Kambi. Posibles operadores:
operators = ["betssonco", "betssonce", "betssonmx", "betssonpe", "betsson", "betssonec"]
for op in operators:
    for base in [KAMBI_BASE_US, KAMBI_BASE]:
        kambi_probe(base, op)
        time.sleep(2)  # Respetar rate limit

print("\n" + "="*60)
print("WPLAY - Probando con sesión (cookies)")
print("="*60)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-CO,es;q=0.9",
})

# 1. Cargar la página principal para obtener cookies de sesión
try:
    r = session.get("https://www.wplay.co/deportes/baloncesto/nba", timeout=10)
    print(f"  Página principal: {r.status_code}")
    cookies_list = [(k, v[:20]) for k, v in session.cookies.items()]
    print(f"  Cookies: {cookies_list[:5]}")
except Exception as e:
    print(f"  Error cargando página: {e}")

time.sleep(1)

# 2. Probar endpoints de la API Playtech IMS conocidos
# La plataforma IMS de Playtech suele tener estas rutas:
wplay_api_probes = [
    "https://openapi.wplay.co/api/v1.0/sports",
    "https://openapi.wplay.co/api/IMS/sports",
    "https://openapi.wplay.co/api/IMS/events",
    "https://openapi.wplay.co/IMS/events",
    "https://openapi.wplay.co/IMS/sports",
    "https://openapi.wplay.co/live/json/getLiveFixtures.aspx",
    "https://openapi.wplay.co/pre/json/getFixtures.aspx",
    "https://openapi.wplay.co/pre/json/getFixtures.aspx?SportId=4",  # Basketball=4 en Playtech?
]

for url in wplay_api_probes:
    try:
        r = session.get(url, timeout=8)
        ct = r.headers.get("Content-Type", "")
        print(f"\n  {r.status_code} | {url.split('openapi.wplay.co')[1][:60]}")
        if r.status_code == 200 and r.content:
            if "json" in ct:
                print(f"  JSON: {json.dumps(r.json(), ensure_ascii=False)[:300]}")
            else:
                print(f"  Body: {r.text[:200]}")
        elif r.status_code in (400, 401, 403):
            print(f"  Body[{r.status_code}]: {r.text[:200]}")
    except Exception as e:
        print(f"\n  ERROR: {url.split('openapi.wplay.co')[1][:60]}")
        print(f"  {e}")
    time.sleep(0.5)

print("\n" + "="*60)
print("BWIN - Headers de respuesta del endpoint que devuelve 200")
print("="*60)
try:
    r = requests.get(
        "https://sports.bwin.co/api/v1/sports/7/competitions/35/events",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=10
    )
    print(f"  Status: {r.status_code}")
    print(f"  Headers: {dict(r.headers)}")
    print(f"  Body len: {len(r.content)}")
    print(f"  Body: {r.content[:300]}")
except Exception as e:
    print(f"  ERROR: {e}")
