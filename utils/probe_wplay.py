"""
Prueba Wplay con el cliente IMS de Playtech usando el canal de datos JSON.
La plataforma Playtech IMS suele exponer sus datos en /api/sports/ o /sports-data/
"""
import json
import time
import requests

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-CO,es;q=0.9",
})

# Primero cargar la página para obtener cookies y tokens
print("Cargando página principal de Wplay...")
r = S.get("https://www.wplay.co/deportes/baloncesto/nba", timeout=15)
print(f"  Status: {r.status_code}")

# Buscar en el HTML tokens o config de API
html = r.text
import re
# Buscar URLs de API en el JavaScript
api_refs = re.findall(r'https?://[^\s\'"<>]+(?:api|sports|events|odds)[^\s\'"<>]{0,80}', html)
unique_refs = list(set(api_refs))
print(f"\nURLs de API encontradas en HTML ({len(unique_refs)}):")
for u in unique_refs[:20]:
    print(f"  {u}")

# Buscar configuración inline
config_match = re.findall(r'(?:apiUrl|apiBase|sportsUrl|dataUrl|baseUrl)["\s]*[:=]["\s]*["\']([^"\']+)["\']', html)
print(f"\nConfig URLs: {config_match[:10]}")

print("\n" + "="*60)
print("Probando patrones alternativos de Wplay / Playtech IMS")
print("="*60)

# Wplay utiliza openapi.wplay.co como subdomain de API
# El 400 consistente sugiere que necesita un path de API específico 
# con autenticación/token en headers o path
endpoints = [
    # Playtech IMS típicos:
    ("https://openapi.wplay.co/api/IMS/v1/sports/", {}),
    ("https://openapi.wplay.co/api/IMS/v2/sports/", {}),
    ("https://openapi.wplay.co/sportsbook/api/v1/sports/", {}),
    ("https://openapi.wplay.co/sportsbook/api/v2/events/live/", {}),
    ("https://openapi.wplay.co/sportsbook/api/v2/events/upcoming/", {}),
    # Con language param:
    ("https://openapi.wplay.co/api/sports/", {"lang": "es_CO"}),
    ("https://openapi.wplay.co/api/sports/live/", {"lang": "es_CO"}),
    ("https://openapi.wplay.co/api/basketball/", {"lang": "es_CO"}),
]

for url, params in endpoints:
    try:
        r = S.get(url, params=params, timeout=8)
        ct = r.headers.get("Content-Type", "")
        print(f"\n  {r.status_code} | {url.split('openapi.wplay.co')[1][:60]}")
        if r.status_code == 200 and r.content:
            if "json" in ct:
                print(f"  JSON: {json.dumps(r.json(), ensure_ascii=False)[:400]}")
            else:
                print(f"  Body: {r.text[:200]}")
        elif r.status_code in (400, 401, 403, 404):
            body = r.text[:150]
            if body:
                print(f"  Body[{r.status_code}]: {body}")
    except Exception as e:
        print(f"\n  ERROR: {url.split('openapi.wplay.co')[1][:60]} → {str(e)[:80]}")
    time.sleep(0.3)
