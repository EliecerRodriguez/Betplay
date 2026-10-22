# Database

## Objetivo

Proveer capa de persistencia con esquema SQL (SQLAlchemy ORM) y operaciones CRUD idempotentes para almacenar predicciones, cuotas, partidos y resultados en Neon (PostgreSQL serverless).

## Responsabilidad

- Definir esquema de tablas (ORM)
- Operaciones upsert (INSERT ... ON CONFLICT DO UPDATE)
- Conexión a Neon vía `DATABASE_URL` (puerto 5432, `sslmode=require`)
- Evitar duplicados con constraints únicos

> **Migración Supabase → Neon (2026-10-20):** La instancia gratuita de Supabase se eliminó sin previo aviso. El backend de persistencia ahora es **Neon PostgreSQL serverless** (puerto 5432, `sslmode=require`). La arquitectura por capas, los modelos ORM, los upserts y las vistas no cambiaron (SQL estándar). Se omitió `channel_binding=require` por compatibilidad con `psycopg2-binary`.

## Archivos involucrados

- `database/models.py` (205 líneas) — esquema SQLAlchemy
- `database/repository.py` — operaciones CRUD
- `database/neon_setup.sql` — script de setup para Neon (esquema + índices + vistas)
- `database/supabase_setup.sql` — **obsoleto** (referencia histórica, contiene sección pg_cron inaplicable; recomendado eliminar en limpieza futura)
- `sports/nba/database/models.py` — modelos específicos NBA
- `sports/nba/database/repository.py` — repo NBA

## Esquema de tablas (Neon)

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

## Conexión a Neon

**Configuración (`.env`):**
```
DATABASE_URL=postgresql://user:password@host.neon.tech:5432/db?sslmode=require
```

**Puerto 5432:** conexión directa Neon (no Transaction Pooler). `sslmode=require` es obligatorio en Neon.

**Engine:**
```python
from sqlalchemy import create_engine

engine = create_engine(DATABASE_URL)
```

## Tablas de monitoreo MLOps (creadas por utils/)

### `model_health`
Persistida por `utils/model_monitor.py` (se ejecuta tras reconciliar en CI). Guarda el snapshot diario de salud del modelo.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | SERIAL PK | |
| generated_at | DATE | Fecha del snapshot |
| model_version | VARCHAR(20) | Versión del modelo |
| total / correct | INTEGER | Predicciones resueltas acumuladas |
| accuracy_pct | FLOAT | Accuracy acumulado |
| recent_total / recent_correct | INTEGER | Predicciones resueltas últimos 30 días |
| recent_accuracy_pct | FLOAT | Accuracy reciente (30d) |
| drift_pct | FLOAT | Diferencia reciente vs acumulado |
| drift_detected | BOOLEAN | True si drift > 10 puntos |
| buckets | JSONB | Calibración por bucket de confianza (50-60%, 60-70%, 70%+) |
| created_at | TIMESTAMPTZ | |

### `model_registry`
Persistida por `utils/model_registry.py` y `utils/train_and_publish.py`. Registro de métricas por versión para comparación.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| id | SERIAL PK | |
| version | VARCHAR(20) UNIQUE NOT NULL | vN |
| model_type | VARCHAR(30) | xgboost, stacking... |
| accuracy / roc_auc / log_loss / brier | FLOAT | Métricas |
| cv_mean / cv_std | FLOAT | CV-AUC ± desviación |
| n_samples / n_features | INTEGER | |
| trained_at | DATE | |
| promoted | BOOLEAN | True si es la versión en producción |
| created_at | TIMESTAMPTZ | |

## Vistas (definidas en `_SETUP_SQL` del repositorio)

> **Nota crítica (2026-10-20):** Las vistas en Neon son las del `_SETUP_SQL` del repositorio (se recrean en cada arranque con `CREATE OR REPLACE VIEW`), **no** las de `database/neon_setup.sql`. Cualquier consulta a vistas debe usar las columnas del repo:
> - `v_backtest_history`: `game_id, game_date, home_team_id, visitor_team_id, predicted_winner, home_win_prob, away_win_prob, home_win, model_version, result`
> - `v_model_accuracy`: `model_version, total_games, total_with_result, accuracy_pct, correct`
>
> Se recomienda **unificar** `database/neon_setup.sql` con el `_SETUP_SQL` del repositorio para evitar divergencias futuras (o eliminar el SQL duplicado).

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
- **Neon (PostgreSQL serverless)** — conexión directa puerto 5432, sslmode=require

### Internas
- **SQLAlchemy** — ORM
- **psycopg2** — driver PostgreSQL

## Riesgos encontrados

### Críticos
- **🔴 Sin migraciones (Alembic)** — cambios de esquema requieren DROP TABLE manual
- **🔴 ATP no usa Database** — solo CSV (inconsistencia)
- **🔴 Sin índices explícitos** — queries por `game_date` pueden ser lentos

### Medios
- **🟡 channel_binding=require omitido** — se descartó por compatibilidad con psycopg2-binary; si Neon lo requiere en el futuro, habrá que habilitarlo
- **🟡 Sin retry en conexión** — fallo de red aborta pipeline
- **🟡 .env no versionado** — la URL de Neon vive solo en .env local y en el secreto de GitHub (mitigado por .env.example poblado)
- **🟡 supabase_setup.sql obsoleto** — sigue en el repo con sección pg_cron inaplicable; recomendado eliminar en limpieza futura

## Relaciones con otros módulos

- **Pipeline NBA** → escribe predictions, odds, value_bets
- **Reconciliación** → actualiza predictions.home_win
- **Web Dashboard** → lee predictions, value_bets

## Pendientes de documentación

### ATP sin base de datos
**¿Por qué ATP no usa Neon?**

**Evidencia (run_atp_pipeline.py, web/app.py):** ATP usa solo CSV por diseño:

```python
# run_atp_pipeline.py líneas 43-46
## Salida CSV (output/)
- atp_predictions.csv
- atp_value_bets.csv
# Sin conexión a DatabaseRepository
```

**Razones identificadas:**
1. **Legacy:** Pipeline ATP fue desarrollado antes de la integración con la base de datos
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

### Índices en Neon
**¿Existen índices?**

**Evidencia (database/neon_setup.sql):** ✅ **SÍ existen índices explícitos** (2026-10-21):

```sql
-- database/neon_setup.sql líneas 32-35
CREATE INDEX IF NOT EXISTS idx_line_scores_game_date ON line_scores(game_date);
CREATE INDEX IF NOT EXISTS idx_predictions_game_date ON predictions(game_date);
CREATE INDEX IF NOT EXISTS idx_predictions_home_win  ON predictions(home_win)
    WHERE home_win IS NULL;
```

**Además:** Los `UniqueConstraint` del ORM (`database/models.py`) generan índices únicos automáticos en Neon.

**Pendiente:** No hay índices explícitos en `home_team_id`/`visitor_team_id` para joins (Neon crea índices automáticos para FK, pero no están documentados).

## Historial de actualizaciones

| Fecha | Cambio |
|-------|--------|
| 2026-10-02 | Documentación inicial |
| 2026-10-20 | Migración Supabase → Neon (puerto 5432, sslmode=require). Documentadas tablas model_health y model_registry. Documentado bug corregido de la vista v_backtest_history (columnas home_team_id/visitor_team_id). Documentado neon_setup.sql y supabase_setup.sql obsoleto. |
| 2026-10-21 | Documentadas columnas reales de las vistas del repo (v_backtest_history, v_model_accuracy) y recomendación de unificar neon_setup.sql con _SETUP_SQL. |
| 2026-10-21 | Pendiente "Índices en Neon" resuelto: `neon_setup.sql` define índices explícitos en `game_date` y `home_win`. |
