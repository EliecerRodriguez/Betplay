"""Explora la estructura de eventos y cuotas de la API Kambi."""
import requests, json

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

BASE = "https://us.offering-api.kambicdn.com/offering/v2018/betplay"
PARAMS = {"lang": "es_CO", "market": "CO", "client_id": "200", "channel_id": "1", "ncid": "1"}

# Paso 1: ver el grupo de basketball completo (incluye NBA Playoffs)
print("=== group.json - estructura basketball ===")
r = requests.get(f"{BASE}/group.json", params=PARAMS, headers=headers, timeout=15)
data = r.json()

def show_basketball(obj, depth=0):
    if isinstance(obj, dict):
        sport = obj.get("sport", "")
        if sport == "BASKETBALL" or "basketball" in str(obj.get("termKey","")).lower() or "nba" in str(obj.get("termKey","")).lower():
            print("  " * depth + f"[{obj.get('id')}] {obj.get('name')} | termKey={obj.get('termKey')} | events={obj.get('eventCount',0)}")
        for v in obj.values():
            show_basketball(v, depth+1)
    elif isinstance(obj, list):
        for item in obj[:20]:
            show_basketball(item, depth)

show_basketball(data)

# Paso 2: ver un evento con sus bet offers (para obtener cuotas h2h)
print("\n=== Primer evento - estructura completa ===")
r2 = requests.get(f"{BASE}/listView/basketball/nba/all/matches.json", params=PARAMS, headers=headers, timeout=15)
events = r2.json().get("events", [])
if events:
    ev = events[0]
    print("Evento keys:", list(ev.keys()))
    print("Nombre:", ev.get("name", ""))
    print("Home:", ev.get("homeName", ""))
    print("Away:", ev.get("awayName", ""))
    print("Event ID:", ev.get("id", ""))

    # Obtener bet offers para el primer evento
    ev_id = ev.get("id")
    if ev_id:
        r3 = requests.get(f"{BASE}/betoffer/event/{ev_id}.json", params=PARAMS, headers=headers, timeout=15)
        print(f"\n=== BetOffer evento {ev_id}: status={r3.status_code} ===")
        if r3.ok:
            offers = r3.json().get("betOffers", [])
            print(f"Ofertas: {len(offers)}")
            for offer in offers[:3]:
                print(f"  Tipo: {offer.get('betOfferType',{}).get('name','?')}")
                for outcome in offer.get("outcomes", []):
                    print(f"    {outcome.get('label','?')}: {outcome.get('odds',0)/1000:.2f}")

# Paso 3: buscar NBA Playoffs en el grupo
print("\n=== Buscando NBA Playoffs ===")
r4 = requests.get(f"{BASE}/group.json", params=PARAMS, headers=headers, timeout=15)
def find_nba_groups(obj, path=""):
    results = []
    if isinstance(obj, dict):
        name = obj.get("name", "")
        termKey = obj.get("termKey", "")
        sport = obj.get("sport", "")
        if sport == "BASKETBALL" or "nba" in termKey.lower() or "basket" in str(name).lower():
            results.append({
                "id": obj.get("id"),
                "name": name,
                "termKey": termKey,
                "eventCount": obj.get("eventCount", 0),
                "path": path
            })
        for k, v in obj.items():
            results += find_nba_groups(v, path + f"/{k}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            results += find_nba_groups(item, path + f"[{i}]")
    return results

found = find_nba_groups(r4.json())
for f in found[:20]:
    print(f"  [{f['id']}] {f['name']} (termKey={f['termKey']}, events={f['eventCount']})")
