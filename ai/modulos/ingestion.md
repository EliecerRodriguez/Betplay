# Ingestion

## Objetivo

Obtener datos crudos desde APIs externas y scrapers web para alimentar los pipelines de predicción.

## Responsabilidad

- Consultar APIs públicas y privadas
- Scrapear HTML cuando no hay API
- Cachear datos históricos
- Manejar rate limiting
- Normalizar respuestas a DataFrames

## Módulos por deporte

### NBA (`sports/nba/ingestion/`)

#### `nba_client.py`
**APIs utilizadas:** `nba_api` (sin autenticación)

**Funciones:**
- `get_daily_games(date)` → partidos del día
- `get_combined_team_stats(season)` → stats avanzadas por equipo
- `get_team_stats(season)` → stats básicas (fallback)
- `get_line_scores(game_ids)` → puntos finales por equipo

**Caché:** `data/cache/games_YYYY-MM-DD.csv`

**Rate limiting:** `time.sleep(NBA_API_DELAY)` entre llamadas (0.6s)

#### `odds_client.py`
**APIs utilizadas:** The Odds API

**Funciones:**
- `get_odds(date)` → cuotas de múltiples casas

**Configuración:**
```python
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
# Si vacío → genera placeholders (no hay value bets reales)
```

**Rate limiting:** The Odds API tiene límite mensual (~500 requests)

#### `injuries_client.py`
**APIs utilizadas:** Scraping de fuentes web

**Funciones:**
- `get_team_injury_impact(team_id)` → impacto de lesiones [0-1]
- Calcula: suma de `injury_severity` de jugadores clave

**Nota:** Impacto se aplica **post-modelo** (no durante training)

#### `elo.py`
**Funciones:**
- `load_current_elos()` → ratings actuales por equipo
- `apply_elos_to_games(games, elos)` → añade `elo_diff` a games
- `update_elos(winner, loser, k=20)` → actualiza ratings

**Persistencia:** Elos en memoria (no se guardan entre ejecuciones NBA)

#### `recent_form.py`
**Funciones:**
- `enrich_with_form(games, season, n=5)` → W/L últimos N partidos
- `get_season_wpct(team_id, date, season)` → W% acumulado hasta fecha

**Caché:** `data/cache/team_gamelogs_{team_id}_{season}.csv`

**Ventana:** Rolling de 5 partidos por defecto

#### `standings_client.py`
**APIs utilizadas:** `nba_api.stats.endpoints.LeagueStandingsV3`

**Funciones:**
- `enrich_with_standings(games, season)` → añade `seed`, `games_back`

#### `travel_client.py`
**Funciones:**
- `enrich_with_travel(games)` → añade `jet_lag`, `rest_days`
- Calcula distancia entre ciudades (coordenadas hardcodeadas)
- Detecta back-to-back (rest_days = 0)

### ATP (`sports/atp/ingestion/`)

#### `co_odds_scraper.py`
**Fuente:** Kambi HTML (Betplay/Rushbet)

**Funciones:**
- `get_atp_co_odds(date_str)` → cuotas del día

**Riesgo crítico:** Scraper frágil, cambios en HTML rompen pipeline

**Estructura parseada:**
```html
<div class="KambiBC-event-item">
  <span class="player1">Nadal</span>
  <span class="player2">Djokovic</span>
  <span class="odds">1.85 / 2.10</span>
</div>
```

#### `atp_client.py`
**Fuente:** Jeff Sackmann GitHub (ATP Match Data)

**Funciones:**
- Descarga CSVs históricos
- Parsea partidos con metadata (superficie, torneo, etc.)

#### `elo.py`
**Funciones:**
- `load_current_elos()` → ratings desde `sports/atp/models/current_elos.json`
- Elos calculados por `build_elos.py` (actualización manual)

#### `h2h_client.py`
**Funciones:**
- `get_h2h(player1, player2)` → historial cabeza a cabeza

#### `historical_client.py`
**Funciones:**
- Descarga partidos históricos desde Sackmann
- Caché: `data/atp_cache/atp_matches_YYYY.csv`

#### `rankings_client.py`
**Funciones:**
- `get_player_ranking(player_name, date)` → ranking ATP en fecha

#### `injuries_client.py`
**Funciones:**
- `get_player_injury_status(player_name)` → estado de lesión

## Archivos involucrados

### NBA
- `sports/nba/ingestion/nba_client.py` (235 líneas)
- `sports/nba/ingestion/odds_client.py` (89 líneas)
- `sports/nba/ingestion/injuries_client.py` (124 líneas)
- `sports/nba/ingestion/elo.py` (98 líneas)
- `sports/nba/ingestion/recent_form.py` (156 líneas)
- `sports/nba/ingestion/standings_client.py` (78 líneas)
- `sports/nba/ingestion/travel_client.py` (112 líneas)

### ATP
- `sports/atp/ingestion/co_odds_scraper.py` (167 líneas)
- `sports/atp/ingestion/atp_client.py` (198 líneas)
- `sports/atp/ingestion/elo.py` (76 líneas)
- `sports/atp/ingestion/h2h_client.py` (54 líneas)
- `sports/atp/ingestion/historical_client.py` (142 líneas)
- `sports/atp/ingestion/rankings_client.py` (67 líneas)
- `sports/atp/ingestion/injuries_client.py` (45 líneas)

## Dependencias externas

### NBA
| API | Autenticación | Rate Limit | Fallo → |
|-----|---------------|------------|---------|
| nba_api | No | ~0.6s delay | Pipeline aborta |
| The Odds API | Sí (ODDS_API_KEY) | 500 req/mes | Placeholders |

### ATP
| Fuente | Método | Riesgo |
|--------|--------|--------|
| Kambi HTML | Scraping | Frágil (cambios HTML) |
| Sackmann GitHub | HTTP | Estable |

## Riesgos encontrados

### Críticos
- **🔴 Sin retry logic** — fallo de API aborta pipeline
- **🔴 Scraper Kambi frágil** — cambios en HTML rompen ATP
- **🔴 nba_api puede devolver 429** — rate limiting sin manejo robusto

### Medios
- **🟡 Elos ATP manuales** — requiere `build_elos.py` periódicamente
- **🟡 Injury impact solo post-modelo** — no en training

## Relaciones con otros módulos

- **Pipeline NBA/ATP** → consumen datos de ingestion
- **Processing** → transforma datos crudos en features
- **Config** → lee API keys desde `.env`

## Pendientes de documentación

### Política de reintentos
**¿Existe retry logic?**

**Evidencia (sports/nba/ingestion/*.py):** NO existe política de reintentos implementada.

```python
# nba_client.py: solo time.sleep() entre llamadas
time.sleep(NBA_API_DELAY)  # 0.6s delay, pero no retry on error
```

**Consecuencia:** Cualquier error de red o timeout aborta el pipeline completo.

### Monitoreo de APIs
**¿Se detectan APIs caídas?**

**Evidencia:** NO existe monitoreo activo de salud de APIs.

- No hay health checks periódicos
- No hay alertas de disponibilidad
- Solo se detectan fallos cuando el pipeline falla

### Alternativa a Kambi
**¿Existe backup para ATP odds?**

**Evidencia:** NO existe alternativa implementada.

**Dependencia crítica:** Todo el pipeline ATP depende exclusivamente del scraper de Kambi HTML:

```python
# sports/atp/ingestion/co_odds_scraper.py
# Scraper frágil que rompe si cambia el HTML de Betplay/Rushbet
```

**No hay:**
- API alternativa de cuotas ATP
- Fallback a The Odds API para tenis
- Mecanismo de degradación graceful

## Historial de actualizaciones

| Fecha | Cambio |
|-------|--------|
| 2026-10-02 | Documentación inicial |
