"""
Busca el operador Kambi real de Betsson Colombia leyendo su JavaScript.
"""
import re
import time
import requests

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CO,es;q=0.9",
})

print("Cargando betsson.co para encontrar operador Kambi...")
try:
    r = S.get("https://www.betsson.co/deportes/baloncesto/nba", timeout=15)
    print(f"  Status: {r.status_code}")
    html = r.text
    
    # Buscar referencias a Kambi
    kambi_refs = re.findall(r'kambi[^\s\'"<>]*', html, re.IGNORECASE)
    print(f"\nRefs a Kambi en HTML: {list(set(kambi_refs))[:15]}")
    
    # Buscar operador
    op_match = re.findall(r'(?:operator|operatorId|clientId)["\s]*[:=]["\s]*["\']?([a-zA-Z0-9_-]+)["\']?', html, re.IGNORECASE)
    print(f"\nOperadores encontrados: {list(set(op_match))[:10]}")
    
    # Buscar scripts de sportsbook
    scripts = re.findall(r'<script[^>]*src=["\']([^"\']+)["\']', html)
    sb_scripts = [s for s in scripts if any(kw in s.lower() for kw in ['sport', 'kambi', 'betting', 'sportsbook'])]
    print(f"\nScripts de sportsbook: {sb_scripts[:5]}")
    
    # Buscar URLs de API en el JS inline
    api_patterns = re.findall(r'https?://[^\s\'"<>]*kambi[^\s\'"<>]{0,100}', html, re.IGNORECASE)
    print(f"\nURLs Kambi en HTML: {list(set(api_patterns))[:10]}")
    
except Exception as e:
    print(f"  ERROR: {e}")

print("\n" + "="*60)
print("Intentando encontrar config en páginas de Betsson CO")
print("="*60)

betsson_pages = [
    "https://www.betsson.co/",
    "https://www.betsson.co/deportes",
    "https://www.betsson.co/es/sports",
]

for url in betsson_pages:
    try:
        r2 = S.get(url, timeout=10)
        html2 = r2.text
        # Buscar patrones de operador
        ops = re.findall(r'(?:operator|bsOperator|kambiOperator)["\s]*[:=]["\s]*["\']([a-zA-Z0-9_-]+)["\']', html2, re.IGNORECASE)
        kambi_urls = re.findall(r'offering-api\.kambicdn\.com/offering/v[0-9]+/([a-zA-Z0-9]+)/', html2)
        print(f"\n{url}")
        print(f"  Status: {r2.status_code}")
        if ops:
            print(f"  Operadores: {list(set(ops))}")
        if kambi_urls:
            print(f"  Operadores Kambi en URL: {list(set(kambi_urls))}")
    except Exception as e:
        print(f"  ERROR: {e}")
    time.sleep(1)

print("\n" + "="*60)
print("Probando Betsson con espera larga (60s)")
print("="*60)
print("Esperando 60s...")
time.sleep(60)

# Probar con el operador más probable después del rate limit
for op in ["betssonco", "betssonce"]:
    url = f"https://eu.offering-api.kambicdn.com/offering/v2018/{op}/event/group/1000093652.json"
    params = {"client_id": "200", "channel_id": "1", "ncid": "1", "lang": "es_ES", "market": "CO"}
    h = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": "https://www.betsson.co/",
    }
    try:
        r3 = requests.get(url, params=params, headers=h, timeout=15)
        print(f"\n  {op}: {r3.status_code}")
        if r3.status_code == 200:
            data = r3.json()
            events = [e for e in data.get("events", []) if e.get("homeName")]
            print(f"  => {len(events)} eventos h2h")
            for ev in events[:3]:
                print(f"     {ev.get('homeName')} vs {ev.get('awayName')}")
        elif r3.status_code == 429:
            print(f"  => Todavía rate limited")
    except Exception as e:
        print(f"  ERROR: {e}")
    time.sleep(5)
