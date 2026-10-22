# Config

## Objetivo

Centralizar configuración del proyecto mediante variables de entorno y constantes globales.

## Responsabilidad

- Leer variables de entorno desde `.env`
- Proveer constantes a todos los módulos
- Configurar conexión a base de datos
- Configurar APIs externas

## Archivos involucrados

- `config/settings.py` — configuración global
- `sports/nba/config/settings.py` — configuración específica NBA
- `sports/atp/config/settings.py` — configuración específica ATP
- `.env` — variables de entorno (no versionado)
- `.env.example` — template de variables

## Variables de entorno (`.env`)

### Base de datos
\DB_HOST=ep-xxxxx.us-east-1.aws.neon.tech
DB_PORT=5432
DB_NAME=neondb
DB_USER=neondb_owner
DB_PASSWORD=xxxxx
DATABASE_URL=postgresql://user:password@host.neon.tech:5432/db?sslmode=require
\
**Nota:** Puerto 5432 = conexión directa Neon. \sslmode=require\ es obligatorio. Se omitió \channel_binding=require\ por compatibilidad con \psycopg2-binary\.

### NBA API
```
NBA_API_TIMEOUT=30
NBA_API_DELAY=0.6
NBA_SEASON=2024-25
```

### Cuotas
```
ODDS_API_KEY=xxxxx
ODDS_API_BASE_URL=https://api.the-odds-api.com/v4
ODDS_SPORT=basketball_nba
ODDS_REGIONS=us
ODDS_MARKETS=h2h
```

### Modelo
```
# MODEL_VERSION=v11  # comentado: auto-detección por fecha de modificación (MLOps)
```

**Nota (2026-10-21):** `MODEL_VERSION` ya no se fija manualmente. `sports/nba/model/predictor.py:_latest_model_version()` elige el joblib más reciente por fecha de modificación (no por número mayor, que fallaba con v9 > v10).

### Logging
```
LOG_LEVEL=INFO
LOG_DIR=logs
```

## Constantes globales

### `config/settings.py`
```python
import os
from dotenv import load_dotenv

load_dotenv()

# Database
DATABASE_URL = os.getenv("DATABASE_URL")

# NBA API
NBA_API_TIMEOUT = int(os.getenv("NBA_API_TIMEOUT", 30))
NBA_API_DELAY = float(os.getenv("NBA_API_DELAY", 0.6))
NBA_SEASON = os.getenv("NBA_SEASON", "2024-25")

# Odds API
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
ODDS_API_BASE_URL = os.getenv("ODDS_API_BASE_URL")
ODDS_SPORT = os.getenv("ODDS_SPORT", "basketball_nba")
ODDS_REGIONS = os.getenv("ODDS_REGIONS", "us")
ODDS_MARKETS = os.getenv("ODDS_MARKETS", "h2h")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR = os.getenv("LOG_DIR", "logs")
```

## Uso en módulos

```python
from config.settings import NBA_SEASON, DATABASE_URL

# Usar en código
games = get_daily_games(NBA_SEASON)
engine = create_engine(DATABASE_URL)
```

## Riesgos encontrados

### Críticos
- **🔴 Sin validación de variables** — fallo silencioso si falta variable crítica
- **🔴 `.env` puede no existir** — mitigado: `.env.example` poblado (2026-10-20) con plantilla Neon

### Medios
- **🟡 Defaults hardcodeados** — algunos valores default pueden ser incorrectos

## Relaciones con otros módulos

- **Todos los módulos** → leen config

## Pendientes de documentación

### Validación de variables
**¿Existe validación de variables al inicio?**

**Evidencia (config/settings.py):** NO existe validación. Las variables se leen con `os.getenv()` y valores default, pero no hay validación de que las variables críticas existan.

```python
# Líneas 10-20: Lectura sin validación
DB_HOST = os.getenv("DB_HOST", "localhost")
DATABASE_URL = os.getenv("DATABASE_URL", f"postgresql://...")
```

**Riesgo confirmado:** Si falta `DATABASE_URL` o `DB_PASSWORD`, el código usa defaults incorrectos en lugar de fallar explícitamente.

### Template .env.example
**¿Existe `.env.example` completo?**

**Evidencia (2026-10-20):** El archivo `.env.example` fue **poblado** por el PSE con plantilla Neon (host, puerto 5432, URL de ejemplo, resto de config). Antes estaba vacío (0 bytes).

**Estado:** ✅ Resuelto.

## Historial de actualizaciones

| Fecha | Cambio |
|-------|--------|
| 2026-10-02 | Documentación inicial |
| 2026-10-20 | Actualizado a Neon (host, puerto 5432, URL con sslmode=require). `.env.example` poblado (antes vacío). |
| 2026-10-21 | `MODEL_VERSION` ya no se fija manualmente (auto-detección por fecha). |
