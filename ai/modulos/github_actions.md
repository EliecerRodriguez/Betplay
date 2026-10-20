# GitHub Actions

## Objetivo

Automatizar la ejecución diaria de pipelines NBA, ATP y reconciliación sin intervención manual mediante workflows de GitHub Actions.

## Responsabilidad

- Ejecutar pipelines en horarios específicos (cron)
- Proveer ambiente Python con dependencias
- Conectar a Supabase con secretos
- Permitir ejecución manual (workflow_dispatch)

## Workflows configurados

### 1. Reconcile — `reconcile.yml`
**Horario:** 06:30 COT (11:30 UTC)  
**Script:** `reconcile.py`  
**Propósito:** Actualiza `predictions.home_win` con resultados reales

```yaml
name: Daily Reconcile
on:
  schedule:
    - cron: '30 11 * * *'  # 06:30 COT
  workflow_dispatch:

jobs:
  reconcile:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.x'
      - run: pip install -r requirements.txt
      - run: python reconcile.py
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
```

### 2. NBA Pipeline — `daily_pipeline.yml`
**Horario:** 07:00 COT (12:00 UTC)  
**Script:** `run_pipeline.py`  
**Propósito:** Genera predicciones NBA del día

```yaml
name: Daily NBA Pipeline
on:
  schedule:
    - cron: '0 12 * * *'  # 07:00 COT
  workflow_dispatch:

jobs:
  nba-pipeline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
      - run: pip install -r requirements.txt
      - run: python run_pipeline.py
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          ODDS_API_KEY: ${{ secrets.ODDS_API_KEY }}
```

### 3. ATP Pipeline — `atp_pipeline.yml`
**Horario:** 18:00 COT (23:00 UTC)  
**Script:** `run_atp_pipeline.py`  
**Propósito:** Genera predicciones ATP del día

```yaml
name: Daily ATP Pipeline
on:
  schedule:
    - cron: '0 23 * * *'  # 18:00 COT
  workflow_dispatch:

jobs:
  atp-pipeline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
      - run: pip install -r requirements.txt
      - run: python run_atp_pipeline.py
```

### 4. Catch-up — `catchup.yml`
**Horario:** Manual (workflow_dispatch)  
**Script:** `reconcile.py` (procesa backlog)  
**Propósito:** Reconcilia predicciones pendientes de días pasados

## Secretos configurados

**Ubicación:** `github.com/[usuario]/Betplay/settings/secrets/actions`

| Secret | Uso |
|--------|-----|
| `DATABASE_URL` | Conexión Supabase (puerto 6543) |
| `ODDS_API_KEY` | The Odds API (opcional) |

**Formato DATABASE_URL:**
```
postgresql://user:password@host:6543/database
```

## Ejecución manual

**URL workflows:**
- NBA: `github.com/[usuario]/Betplay/actions/workflows/daily_pipeline.yml`
- Reconcile: `github.com/[usuario]/Betplay/actions/workflows/reconcile.yml`
- ATP: `github.com/[usuario]/Betplay/actions/workflows/atp_pipeline.yml`
- Catch-up: `github.com/[usuario]/Betplay/actions/workflows/catchup.yml`

**Botón:** "Run workflow" en cada página

## Archivos involucrados

- `.github/workflows/reconcile.yml`
- `.github/workflows/daily_pipeline.yml`
- `.github/workflows/atp_pipeline.yml`
- `.github/workflows/catchup.yml`

## Flujo operacional

```
06:30 COT → Reconcile
   ↓ Actualiza home_win de predicciones de ayer
   
07:00 COT → NBA Pipeline
   ↓ Genera predicciones de hoy
   ↓ Guarda en Supabase
   
18:00 COT → ATP Pipeline
   ↓ Scrapea cuotas Kambi
   ↓ Genera predicciones ATP
   ↓ Guarda en CSV
```

## Ventajas vs Task Scheduler local

| Aspecto | GitHub Actions | Task Scheduler |
|---------|----------------|----------------|
| Disponibilidad | 24/7 cloud | Requiere PC encendida |
| Configuración | YAML versionado | GUI Windows |
| Logs | Interfaz web | Event Viewer |
| Notificaciones | Email automático | Manual |
| Costo | Gratis (2000 min/mes) | Electricidad local |

## Limitaciones GitHub Actions

- **2000 minutos/mes** en plan gratuito
- **Timeout:** 6 horas por job (más que suficiente)
- **Concurrencia:** 20 jobs paralelos (no aplica aquí)

## Riesgos encontrados

### Críticos
- **🔴 Sin notificación de fallos** — workflow falla silenciosamente
- **🔴 Sin retry** — fallo de API externa aborta workflow completo

### Medios
- **🟡 Task Scheduler local obsoleto** — aún configurado pero no usado
- **🟡 Dependencia de GitHub** — si GitHub cae, pipelines no corren

## Relaciones con otros módulos

- **Pipeline NBA** → ejecutado por `daily_pipeline.yml`
- **Pipeline ATP** → ejecutado por `atp_pipeline.yml`
- **Reconciliación** → ejecutado por `reconcile.yml`

## Pendientes de documentación

### Política de reintentos
**¿Existe retry en workflows?**

**Evidencia (.github/workflows/*.yml):** NO existe política de reintentos configurada.

```yaml
# Ningún workflow tiene configurado:
# retry_on_error: true
# max_attempts: N
```

**Consecuencia:** Si nba_api falla temporalmente, el workflow falla completamente sin reintento.

### Alertas de fallos
**¿Hay notificaciones?**

**Evidencia:** NO existen alertas configuradas.

- Sin integración con Slack
- Sin emails de notificación
- Sin webhooks de error

**Única monitorización:** El usuario debe revisar manualmente la pestaña "Actions" en GitHub.

### Monitoring de ejecuciones
**¿Existe monitoring?**

**Evidencia:** Solo el historial de GitHub Actions. No hay:

- Dashboard de monitoreo
- Métricas de éxito/fallo exportadas
- Alertas de degradación
- Tracking de duración de jobs

**Limitación:** El plan gratuito de GitHub Actions no incluye monitoring avanzado.

## Historial de actualizaciones

| Fecha | Cambio |
|-------|--------|
| 2026-10-02 | Documentación inicial |
