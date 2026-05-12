"""Inspecciona el formato exacto de las cuotas en Kambi."""
import requests, json

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}
BASE = "https://us.offering-api.kambicdn.com/offering/v2018/betplay"
P = {"lang": "es_CO", "market": "CO", "client_id": "200", "channel_id": "1", "ncid": "1"}

# Obtener eventos NBA
r = requests.get(f"{BASE}/event/group/1000093652.json", params=P, headers=headers, timeout=15)
events = r.json().get("events", [])
print(f"Eventos NBA: {len(events)}")

for ev in events[:4]:
    eid = ev.get("id")
    home = ev.get("homeName", "?")
    away = ev.get("awayName", "?")
    print(f"\n{'='*50}")
    print(f"Partido: {home} vs {away} | ID={eid}")
    print(f"Start: {ev.get('start', '')}")

    # Obtener bet offers
    r2 = requests.get(f"{BASE}/betoffer/event/{eid}.json", params={**P, "include_player_props": "false"}, headers=headers, timeout=15)
    if not r2.ok:
        print(f"  BetOffer status: {r2.status_code}")
        continue

    offers = r2.json().get("betOffers", [])
    print(f"  Bet offers: {len(offers)}")

    for offer in offers[:3]:
        offer_type = offer.get("betOfferType", {})
        print(f"  OFERTA: {json.dumps(offer_type, ensure_ascii=False)}")
        outcomes = offer.get("outcomes", [])
        print(f"  OUTCOMES ({len(outcomes)}):")
        for oc in outcomes:
            # Mostrar TODOS los campos relevantes
            label = oc.get("label", oc.get("englishLabel", "?"))
            odds_val = oc.get("odds")
            odds_euro = oc.get("oddsEuro")
            odds_american = oc.get("oddsAmerican")
            odds_decimal = oc.get("oddsDecimal")
            status = oc.get("status")
            print(f"    {label}: odds={odds_val} | euro={odds_euro} | american={odds_american} | decimal={odds_decimal} | status={status}")
        break  # solo primera oferta por partido
