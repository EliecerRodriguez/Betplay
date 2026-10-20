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
```
DB_HOST=aws-0-us-west-1.pooler.supabase.com
DB_PORT=6543
DB_NAME=postgres
DB_USER=postgres.xxxxx
DB_PASSWORD=xxxxx
DATABASE_URL=postgresql://user:pass@host:6543/db
```

**Nota:** Puerto 6543 = Transaction Pooler (no 5432 directo)

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
MODEL_VERSION=v11
```

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
- **🔴 `.env` puede no existir** — no hay `.env.example` completo

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

**Evidencia:** El archivo `.env.example` existe pero está **vacío** (0 bytes). No contiene template de variables.

**Recomendación:** Crear template con todas las variables documentadas en este módulo.

## Historial de actualizaciones

| Fecha | Cambio |
|-------|--------|
| 2026-10-02 | Documentación inicial |
