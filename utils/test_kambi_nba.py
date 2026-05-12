"""Encuentra los eventos NBA reales con sus cuotas h2h."""
import requests, json

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}
BASE = "https://us.offering-api.kambicdn.com/offering/v2018/betplay"
P = {"lang": "es_CO", "market": "CO", "client_id": "200", "channel_id": "1", "ncid": "1"}

# Ruta correcta NBA hombres (USA)
paths_to_try = [
    "listView/basketball/usa/nba/all/matches.json",
    "listView/basketball/nba/all/matches.json",
]

print("=== Buscando ruta correcta para NBA ===")
for path in paths_to_try:
    r = requests.get(f"{BASE}/{path}", params=P, headers=headers, timeout=15)
    events = r.json().get("events", []) if r.ok else []
    if events:
        print(f"Path: {path} → {len(events)} eventos")
        for ev in events[:4]:
            home = ev.get("homeName", "?")
            away = ev.get("awayName", "?")
            start = ev.get("start", "")[:10]
            print(f"  {home} vs {away} | {start}")
        break
    else:
        print(f"Path: {path} → {r.status_code} / 0 eventos")

# Obtener eventos por el grupo NBA (id conocido: 1000093652)
print("\n=== Eventos via group ID 1000093652 ===")
r2 = requests.get(f"{BASE}/event/group/1000093652.json", params={**P, "includeParticipants": "true"}, headers=headers, timeout=15)
print(f"Status: {r2.status_code}")
if r2.ok:
    data = r2.json()
    print("Keys:", list(data.keys()))
    events2 = data.get("events", [])
    print(f"Eventos: {len(events2)}")
    for ev in events2[:5]:
        home = ev.get("homeName", "?")
        away = ev.get("awayName", "?")
        start = ev.get("start", "")[:10]
        eid = ev.get("id")
        print(f"  [{eid}] {home} vs {away} | {start}")

# Obtener cuotas h2h del primer evento NBA
print("\n=== Cuotas h2h primer evento NBA ===")
if r2.ok and events2:
    ev_id = events2[0].get("id")
    r3 = requests.get(f"{BASE}/betoffer/event/{ev_id}.json", params=P, headers=headers, timeout=15)
    print(f"BetOffer status: {r3.status_code}")
    if r3.ok:
        offers = r3.json().get("betOffers", [])
        print(f"Total ofertas: {len(offers)}")
        for offer in offers:
            tipo = offer.get("betOfferType", {}).get("englishLabel", "?")
            outcomes = offer.get("outcomes", [])
            if tipo in ("Match", "Head to Head", "Match Betting", "1x2", "Money Line"):
                print(f"  TIPO: {tipo}")
                for oc in outcomes:
                    label = oc.get("label", oc.get("englishLabel", "?"))
                    odds_raw = oc.get("odds", 0)
                    odds_dec = odds_raw / 1000 if odds_raw > 100 else odds_raw
                    print(f"    {label}: {odds_dec:.3f}")
                break
        # Si no encontré h2h explícito, mostrar los primeros 2
        if offers:
            print("\n  Todas las ofertas disponibles:")
            for offer in offers[:5]:
                tipo = offer.get("betOfferType", {}).get("englishLabel", "?")
                outcomes = offer.get("outcomes", [])
                print(f"  [{tipo}]:", [(oc.get("label","?"), oc.get("odds",0)/1000) for oc in outcomes[:3]])
