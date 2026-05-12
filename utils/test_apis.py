import requests, json

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

def kambi_nba_events(operator, market="CO", lang="es_CO"):
    """Obtiene eventos NBA de la API de Kambi para un operador dado."""
    base = f"https://us.offering-api.kambicdn.com/offering/v2018/{operator}"
    # Paso 1: encontrar el grupo NBA
    r = requests.get(f"{base}/group.json", params={"lang": lang, "market": market, "client_id": "200", "channel_id": "1"}, headers=headers, timeout=15)
    if not r.ok:
        return None, f"group.json {r.status_code}"

    # Buscar NBA en el árbol de grupos
    nba_group_id = None
    def search(obj):
        nonlocal nba_group_id
        if isinstance(obj, dict):
            if obj.get("termKey") == "nba" or (obj.get("sport") == "BASKETBALL" and "NBA" in str(obj.get("englishName",""))):
                nba_group_id = obj.get("id")
            for v in obj.values():
                search(v)
        elif isinstance(obj, list):
            for item in obj:
                search(item)
    search(r.json())

    if not nba_group_id:
        return None, "NBA group not found"

    # Paso 2: obtener eventos del grupo NBA
    r2 = requests.get(f"{base}/listView/basketball/nba/all/matches.json", params={"lang": lang, "market": market, "client_id": "200", "channel_id": "1", "ncid": "1"}, headers=headers, timeout=15)
    if r2.ok:
        return r2.json(), None

    # Fallback: evento/grupo directo
    r3 = requests.get(f"{base}/event/group/{nba_group_id}.json", params={"lang": lang, "market": market, "client_id": "200", "channel_id": "1", "ncid": "1"}, headers=headers, timeout=15)
    if r3.ok:
        return r3.json(), None

    return None, f"events {r3.status_code}"


print("=== BETPLAY ===")
data, err = kambi_nba_events("betplay", market="CO", lang="es_CO")
if err:
    print("ERROR:", err)
else:
    print("Keys:", list(data.keys())[:10])
    # Kambi listView devuelve 'liveEvents' y 'events' o 'eventGroups'
    all_evs = data.get("events", []) + data.get("liveEvents", [])
    evgroups = data.get("eventGroups", [])
    for g in evgroups:
        all_evs += g.get("events", [])
    print(f"Total eventos: {len(all_evs)}")
    for ev in all_evs[:3]:
        home = ev.get("homeName", ev.get("home", "?"))
        away = ev.get("awayName", ev.get("away", "?"))
        start = ev.get("start", "")
        print(f"  {home} vs {away} | {start}")

print()
print("=== RUSHBET ===")
data2, err2 = kambi_nba_events("rsico", market="CO", lang="es_ES")
if err2:
    print("ERROR:", err2)
    # Intentar directamente el listView
    r = requests.get("https://us.offering-api.kambicdn.com/offering/v2018/rsico/event/live/open.json", params={"lang": "es_ES", "market": "CO", "client_id": "200", "channel_id": "1"}, headers=headers, timeout=15)
    print(f"live/open.json: {r.status_code}")
    if r.ok:
        d = r.json()
        print("Keys:", list(d.keys()))
        print(str(d)[:300])
else:
    all_evs2 = data2.get("events", []) + data2.get("liveEvents", [])
    for g in data2.get("eventGroups", []):
        all_evs2 += g.get("events", [])
    print(f"Total eventos: {len(all_evs2)}")
    for ev in all_evs2[:3]:
        print(f"  {ev.get('homeName','?')} vs {ev.get('awayName','?')} | {ev.get('start','')}")

