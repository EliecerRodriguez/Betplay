"""
Prueba Betsson con client_id específico de betsson.co
y espera más larga entre peticiones.
"""
import time
import requests

# Betsson usa Kambi con un client_id diferente al de betplay
# Los clientes Kambi colombianos pueden tener diferentes IDs
BETSSON_CLIENT_IDS = ["2", "1", "10", "100", "1000", "200", "2000", "9999"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "es-CO,es;q=0.9",
    "Referer": "https://www.betsson.co/",
    "Origin": "https://www.betsson.co",
}

NBA_GROUP = 1000093652

print("Esperando 15s para limpiar rate limit...")
time.sleep(15)

for cid in BETSSON_CLIENT_IDS:
    url = f"https://eu.offering-api.kambicdn.com/offering/v2018/betssonco/event/group/{NBA_GROUP}.json"
    params = {"client_id": cid, "channel_id": "1", "ncid": "1", "lang": "es_ES", "market": "CO"}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        print(f"  client_id={cid}: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            events = [e for e in data.get("events", []) if e.get("homeName")]
            print(f"  => {len(events)} eventos!")
            for ev in events[:3]:
                print(f"     {ev.get('homeName')} vs {ev.get('awayName')}")
            break
        elif r.status_code == 429:
            print(f"  => 429 (rate limited)")
    except Exception as e:
        print(f"  ERROR: {e}")
    time.sleep(5)

print("\nTambién probando con us.offering-api:")
time.sleep(10)
url2 = f"https://us.offering-api.kambicdn.com/offering/v2018/betssonco/event/group/{NBA_GROUP}.json"
params2 = {"client_id": "200", "channel_id": "1", "ncid": "1", "lang": "es_ES", "market": "CO"}
try:
    r2 = requests.get(url2, params=params2, headers=HEADERS, timeout=15)
    print(f"  us-kambi betssonco: {r2.status_code}")
    if r2.status_code == 200:
        data2 = r2.json()
        print(f"  => {len(data2.get('events', []))} eventos")
except Exception as e:
    print(f"  ERROR: {e}")
