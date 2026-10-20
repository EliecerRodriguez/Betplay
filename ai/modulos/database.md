# Database

## Objetivo

Proveer capa de persistencia con esquema SQL (SQLAlchemy ORM) y operaciones CRUD idempotentes para almacenar predicciones, cuotas, partidos y resultados en Supabase (PostgreSQL).

## Responsabilidad

- Definir esquema de tablas (ORM)
- Operaciones upsert (INSERT ... ON CONFLICT DO UPDATE)
- Conexión a Supabase vía Transaction Pooler (puerto 6543)
- Evitar duplicados con constraints únicos

## Archivos involucrados

- `database/models.py` (205 líneas) — esquema SQLAlchemy
- `database/repository.py` — operaciones CRUD
- `sports/nba/database/models.py` — modelos específicos NBA
- `sports/nba/database/repository.py` — repo NBA

## Esquema de tablas (Supabase)

### `teams`
Catálogo de equipos NBA.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | Integer PK | nba_api team id |
| full_name | String(100) | Ej: "Los Angeles Lakers" |
| abbreviation | String(10) UNIQUE | Ej: "LAL" |
| nickname | String(50) | Ej: "Lakers" |
| city | String(50) | |
| state | String(50) | |
| year_founded | Integer | |
| created_at | DateTime | |

### `games`
Partidos (cabecera del partido).

| Campo | Tipo | Constraint |
|-------|------|------------|
| id | Integer PK AUTOINCREMENT | |
| game_id | String(20) | UNIQUE |
| game_date | Date | |
| home_team_id | Integer | FK a teams |
| visitor_team_id | Integer | FK a teams |
| home_team_score | Integer | NULL hasta finalizado |
| visitor_team_score | Integer | NULL hasta finalizado |
| game_status | String(50) | Final, Live, Scheduled |
| season | String(10) | Ej: "2024-25" |
| fetch_date | Date | |
| created_at | DateTime | |

**Constraint:** `UniqueConstraint("game_id")`

### `team_stats`
Estadísticas por equipo y temporada.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | Integer PK AUTOINCREMENT | |
| team_id | Integer | |
| team_name | String(100) | |
| season | String(10) | |
| pts | Float | Puntos por partido |
| ast | Float | Asistencias |
| reb | Float | Rebotes totales |
| oreb | Float | Rebotes ofensivos |
| dreb | Float | Rebotes defensivos |
| stl | Float | Robos |
| blk | Float | Tapones |
| tov | Float | Pérdidas |
| fg_pct | Float | % campo |
| fg3_pct | Float | % triples |
| ft_pct | Float | % tiros libres |
| w | Integer | Victorias |
| l | Integer | Derrotas |
| w_pct | Float | % victorias |
| fetch_date | Date | |
| created_at | DateTime | |

**Constraint:** `UniqueConstraint("team_id", "season")`

### `odds`
Cuotas por partido y casa de apuestas.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | Integer PK AUTOINCREMENT | |
| game_id | String(20) | |
| home_team | String(100) | |
| away_team | String(100) | |
| bookmaker | String(50) | Ej: "fanduel", "draftkings" |
| home_odds | Float | Cuota decimal local |
| away_odds | Float | Cuota decimal visitante |
| commence_time | String(50) | |
| is_placeholder | Boolean | True si no hay API key |
| fetch_date | Date | |
| created_at | DateTime | |

**Constraint:** `UniqueConstraint("game_id", "bookmaker")`

### `predictions`
Predicciones del modelo.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | Integer PK AUTOINCREMENT | |
| game_id | String(20) | |
| game_date | Date | |
| home_team_id | Integer | |
| visitor_team_id | Integer | |
| home_win_prob | Float | [0-1] probabilidad local |
| away_win_prob | Float | [0-1] probabilidad visitante |
| predicted_winner | String(10) | 'home' \| 'away' |
| home_win | Integer | 1=local ganó, 0=visitante ganó, NULL=pendiente |
| model_version | String(20) | Ej: "v11" |
| fetch_date | Date | |
| created_at | DateTime | |

**Constraint:** `UniqueConstraint("game_id", "model_version")`

**Nota crítica:** `home_win` se llena en reconciliación (06:30 COT).

### `line_scores`
Puntos anotados por equipo (para reconciliación).

| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | Integer PK AUTOINCREMENT | |
| game_id | String(20) | |
| game_date | Date | |
| team_id | Integer | |
| pts | Integer | Puntos anotados |
| fetch_date | Date | |
| created_at | DateTime | |

**Constraint:** `UniqueConstraint("game_id", "team_id")`

### `value_bets`
Oportunidades de valor detectadas.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | Integer PK AUTOINCREMENT | |
| game_id | String(20) | |
| game_date | Date | |
| bookmaker | String(50) | |
| side | String(10) | 'home' \| 'away' |
| team_name | String(100) | |
| model_prob | Float | Probabilidad del modelo |
| odds | Float | Cuota decimal |
| value | Float | (prob * odds) - 1 |
| is_value_bet | Boolean | value > 0 |
| fetch_date | Date | |
| created_at | DateTime | |

**Constraint:** `UniqueConstraint("game_id", "bookmaker", "side")`

## Operaciones Repository

### Upsert pattern
```python
# INSERT ... ON CONFLICT DO UPDATE
stmt = insert(Model).values(data)
stmt = stmt.on_conflict_do_update(
    index_elements=['game_id'],
    set_=data
)
session.execute(stmt)
```

Permite correr pipeline 2x el mismo día sin duplicados.

## Conexión a Supabase

**Configuración (`.env`):**
```
DATABASE_URL=postgresql://user:pass@host:6543/db
```

**Puerto 6543:** Transaction Pooler (no 5432 directo)

**Engine:**
```python
from sqlalchemy import create_engine

engine = create_engine(DATABASE_URL)
```

## Creación automática de tablas

```python
Base.metadata.create_all(engine)
```

Se ejecuta en primer arranque del pipeline.

## Flujo de datos

### Pipeline NBA → Database
1. Pipeline genera DataFrames (games, predictions, odds, value_bets)
2. `repository.upsert_*()` convierte DataFrame → dict → INSERT
3. Conflict en constraint → UPDATE
4. Commit

### Reconciliación → Database
1. Lee `predictions WHERE home_win IS NULL`
2. Descarga `line_scores` desde nba_api
3. Calcula ganador: `home_win = 1 si home_pts > away_pts else 0`
4. UPDATE `predictions SET home_win = ...`

## Dependencias

### Externas
- **Supabase (PostgreSQL)** — timeout en Transaction Pooler bajo carga

### Internas
- **SQLAlchemy** — ORM
- **psycopg2** — driver PostgreSQL

## Riesgos encontrados

### Críticos
- **🔴 Sin migraciones (Alembic)** — cambios de esquema requieren DROP TABLE manual
- **🔴 ATP no usa Database** — solo CSV (inconsistencia)
- **🔴 Sin índices explícitos** — queries por `game_date` pueden ser lentos

### Medios
- **🟡 Transaction Pooler timeout** — conexiones largas pueden cerrarse
- **🟡 Sin retry en conexión** — fallo de red aborta pipeline

## Relaciones con otros módulos

- **Pipeline NBA** → escribe predictions, odds, value_bets
- **Reconciliación** → actualiza predictions.home_win
- **Web Dashboard** → lee predictions, value_bets

## Pendientes de documentación

### ATP sin Supabase
**¿Por qué ATP no usa Supabase?**

**Evidencia (run_atp_pipeline.py, web/app.py):** ATP usa solo CSV por diseño:

```python
# run_atp_pipeline.py líneas 43-46
## Salida CSV (output/)
- atp_predictions.csv
- atp_value_bets.csv
# Sin conexión a DatabaseRepository
```

**Razones identificadas:**
1. **Legacy:** Pipeline ATP fue desarrollado antes de la integración con Supabase
2. **Menor volumen:** ATP tiene menos partidos diarios que NBA
3. **Fuentes diferentes:** ATP usa scraper Kambi + Jeff Sackmann, no APIs con IDs normalizados

**Impacto:** No hay reconciliación automática de resultados ATP (ver `web/app.py:894-1120` que implementa reconciliación ATP separada usando ESPN/Sackmann).

### Política de retención
**¿Existe política de limpieza de datos?**

**Evidencia:** NO existe política de retención documentada ni implementada.

- `predictions` y `value_bets` crecen indefinidamente
- No hay `DELETE WHERE game_date < ...` en el código
- No hay jobs de limpieza en GitHub Actions

**Riesgo:** Tablas pueden crecer indefinidamente afectando rendimiento.

### Índices en Supabase
**¿Existen índices?**

**Evidencia (database/models.py):** Los índices están definidos **solo en ORM** como `UniqueConstraint`:

```python
# Líneas 55-57, 79-81, etc.
__table_args__ = (
    UniqueConstraint("game_id", name="uq_game_id"),
)
```

**No existen:**
- Índices en `game_date` para queries por fecha
- Índices en `home_team_id`, `visitor_team_id` para joins
- `CREATE INDEX` explícito en migraciones

**Dependencia:** Supabase puede crear índices automáticos para FK, pero no están documentados.

## Historial de actualizaciones

| Fecha | Cambio |
|-------|--------|
| 2026-10-02 | Documentación inicial |
