"""
Intercepta las llamadas de red (XHR/fetch) de cada casa de apuestas
para descubrir sus APIs internas de cuotas NBA.
"""
import json
import re
from playwright.sync_api import sync_playwright

log = open("utils/inspect_sites_output.txt", "w", encoding="utf-8")

def p(*args):
    msg = " ".join(str(a) for a in args)
    print(msg)
    log.write(msg + "\n")
    log.flush()

def p(*args):
    msg = " ".join(str(a) for a in args)
    print(msg)
    log.write(msg + "\n")
    log.flush()


def is_odds_url(url: str) -> bool:
    """Detecta si una URL de red parece ser una API de cuotas/eventos."""
    low = url.lower()
    keywords = [
        "basket", "nba", "sport", "odds", "event", "offer",
        "market", "match", "fixture", "coupon", "api"
    ]
    return any(k in low for k in keywords)


sites = {
    "Betplay": {
        "start_url":  "https://betplay.com.co/apuestas",
        "nba_nav":    "#sport_basketball",     # link a hacer click para ir a NBA
        "wait_extra": 8000,
    },
    "Wplay": {
        "start_url":  "https://www.wplay.co/deportes/baloncesto/nba",
        "nba_nav":    None,
        "wait_extra": 8000,
    },
    "Rushbet": {
        "start_url":  "https://www.rushbet.co/?page=sportsbook#filter/all",
        "nba_nav":    "#sport_basketball",
        "wait_extra": 8000,
    },
    "Betsson": {
        "start_url":  "https://www.betsson.co/",
        "nba_nav":    None,
        "wait_extra": 8000,
    },
    "Bwin": {
        "start_url":  "https://www.bwin.co/es/sports/baloncesto-7/nba-35",
        "nba_nav":    None,
        "wait_extra": 8000,
    },
}

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)

    for name, cfg in sites.items():
        p(f"\n{'='*55}")
        p(f"CASA: {name}  |  {cfg['start_url']}")

        captured_urls = []

        def handle_request(request):
            if request.resource_type in ("xhr", "fetch"):
                url = request.url
                if is_odds_url(url):
                    captured_urls.append({"method": request.method, "url": url})

        try:
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page.on("request", handle_request)

            page.goto(cfg["start_url"], wait_until="commit", timeout=30000)
            page.wait_for_timeout(cfg["wait_extra"])

            # Intentar navegar al link de basketball si existe
            if cfg.get("nba_nav"):
                try:
                    page.click(f'a[href="{cfg["nba_nav"]}"]', timeout=5000)
                    page.wait_for_timeout(5000)
                except Exception:
                    # Intentar con selector más amplio
                    try:
                        page.click(
                            'a[href*="basketball"], a[href*="basket"], '
                            'a[href*="baloncesto"], a[href*="nba"]',
                            timeout=4000
                        )
                        page.wait_for_timeout(5000)
                    except Exception:
                        pass

            p(f"  URL final  : {page.url[:80]}")
            p(f"  Titulo     : {page.title()[:60]}")
            p(f"  API calls relevantes ({len(captured_urls)}):")
            seen = set()
            for req in captured_urls:
                url = req["url"]
                # Mostrar solo URLs únicas, truncadas
                key = url[:120]
                if key not in seen:
                    seen.add(key)
                    p(f"    [{req['method']}] {url[:120]}")

            page.close()
        except Exception as e:
            p(f"  ERROR: {e}")

    browser.close()

log.close()
print("\nDone -> utils/inspect_sites_output.txt")

