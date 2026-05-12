"""
Usa Playwright para interceptar las peticiones de red en Betsson, Wplay y Bwin
y encontrar sus operadores/APIs de cuotas.
"""
import json
import time
from playwright.sync_api import sync_playwright

CAPTURED = {
    "betsson": [],
    "wplay": [],
    "bwin": [],
}

def capture_site(site_name, url, page_wait=6000):
    found_requests = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="es-CO",
            extra_http_headers={"Accept-Language": "es-CO,es;q=0.9"}
        )
        page = context.new_page()

        def on_request(req):
            ru = req.url
            # Capturar peticiones a APIs de cuotas / sportsbook
            keywords = ["kambi", "offering-api", "betoffer", "sportsbook", "sports/api",
                       "openapi", "odds", "fixtures", "events", "betslip"]
            if any(kw in ru.lower() for kw in keywords):
                found_requests.append({"method": req.method, "url": ru})

        def on_response(resp):
            ru = resp.url
            keywords = ["kambi", "offering-api", "betoffer", "sportsbook", "sports/api",
                       "openapi", "odds", "fixtures", "events"]
            if any(kw in ru.lower() for kw in keywords):
                try:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct and resp.status == 200:
                        body = resp.json()
                        print(f"\n  ✓ JSON {resp.status}: {ru[:100]}")
                        print(f"    Keys: {list(body.keys())[:6] if isinstance(body, dict) else type(body).__name__}")
                        # Si tiene eventos NBA, mostrar algunos
                        events = body.get("events", body.get("data", []))
                        if isinstance(events, list) and len(events) > 0:
                            for ev in events[:2]:
                                if isinstance(ev, dict):
                                    h = ev.get("homeName", ev.get("home", ev.get("homeTeam", "")))
                                    a = ev.get("awayName", ev.get("away", ev.get("awayTeam", "")))
                                    if h and a:
                                        print(f"    Evento: {h} vs {a}")
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        print(f"\nCargando {site_name}: {url}")
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(page_wait)
        except Exception as e:
            print(f"  Timeout/Error: {e}")

        browser.close()

    return found_requests

print("="*70)
print("BETSSON - Capturando peticiones de red")
print("="*70)
betsson_reqs = capture_site("Betsson", "https://www.betsson.co/", 8000)
print(f"\nPeticiones capturadas ({len(betsson_reqs)}):")
for req in betsson_reqs[:20]:
    print(f"  {req['method']} {req['url'][:120]}")

print("\n" + "="*70)
print("WPLAY - Capturando peticiones de red")
print("="*70)
wplay_reqs = capture_site("Wplay", "https://www.wplay.co/deportes/baloncesto/nba", 8000)
print(f"\nPeticiones capturadas ({len(wplay_reqs)}):")
for req in wplay_reqs[:20]:
    print(f"  {req['method']} {req['url'][:120]}")

print("\n" + "="*70)
print("BWIN - Capturando peticiones de red")
print("="*70)
bwin_reqs = capture_site("Bwin", "https://www.bwin.co/es/sports/baloncesto-7/nba-35", 8000)
print(f"\nPeticiones capturadas ({len(bwin_reqs)}):")
for req in bwin_reqs[:20]:
    print(f"  {req['method']} {req['url'][:120]}")
