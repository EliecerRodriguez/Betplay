# Web Dashboard

## Objetivo

Proveer interfaz web para visualizar predicciones, value bets, accuracy acumulado y estadísticas del sistema.

## Responsabilidad

- API REST con FastAPI
- Servir dashboard HTML
- Consultar Supabase
- Mostrar predicciones del día
- Calcular accuracy histórico

## Archivos involucrados

- `web/app.py` — FastAPI application

## Endpoints

### `GET /`
**Descripción:** Dashboard HTML principal

**Respuesta:** HTML renderizado

### `GET /api/predictions`
**Descripción:** Predicciones del día

**Query params:**
- `date` (opcional) — YYYY-MM-DD (default: hoy)

**Respuesta:**
```json
[
  {
    "game_id": "0022400123",
    "game_date": "2026-10-02",
    "home_team": "Lakers",
    "away_team": "Warriors",
    "home_win_prob": 0.68,
    "away_win_prob": 0.32,
    "predicted_winner": "home",
    "model_version": "v11"
  }
]
```

### `GET /api/value_bets`
**Descripción:** Value bets detectadas

**Query params:**
- `date` (opcional)

**Respuesta:**
```json
[
  {
    "game_id": "0022400123",
    "bookmaker": "fanduel",
    "side": "home",
    "team_name": "Lakers",
    "model_prob": 0.68,
    "odds": 1.75,
    "value": 0.19
  }
]
```

### `GET /api/accuracy`
**Descripción:** Accuracy acumulado del modelo

**Query params:**
- `model_version` (opcional) — default: último

**Respuesta:**
```json
{
  "model_version": "v11",
  "total_predictions": 1523,
  "correct": 1078,
  "accuracy": 0.708,
  "date_range": {
    "start": "2024-10-01",
    "end": "2026-10-02"
  }
}
```

## Ejecución

### Desarrollo
```bash
cd C:\Betplay
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "C:\Betplay"
uvicorn web.app:app --reload --port 8000
```

### Producción
```bash
uvicorn web.app:app --host 0.0.0.0 --port 8000
```

**URL:** http://localhost:8000

## Tecnología

| Componente | Versión |
|------------|---------|
| Framework | FastAPI |
| Servidor | Uvicorn |
| Base datos | SQLAlchemy (Supabase) |
| Frontend | HTML + CSS + JavaScript |

## Dependencias

- `fastapi`
- `uvicorn[standard]`
- `sqlalchemy`
- `psycopg2-binary`

## Riesgos encontrados

### Críticos
- **🔴 Sin autenticación** — dashboard público (cualquiera puede ver predicciones)
- **🔴 Sin caché** — cada request consulta Supabase (lento con muchas predicciones)

### Medios
- **🟡 Sin paginación** — `/api/predictions` retorna todo el día (puede ser >50 partidos)
- **🟡 Frontend básico** — HTML estático, sin framework moderno

## Relaciones con otros módulos

- **Database** → consulta predictions, value_bets
- **Pipeline NBA** → genera datos visualizados

## Pendientes de documentación

### HTML del dashboard
**¿Existe HTML o solo API?**

**Evidencia (web/static/index.html):** Sí existe dashboard HTML completo en `web/static/index.html`. El endpoint `GET /` lo sirve:

```python
# web/app.py líneas 1332-1334
@app.get("/", include_in_schema=False)
def root():
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
```

**Arquitectura:** Frontend HTML estático + JavaScript que consume la API REST (`/api/analysis`, `/api/atp/analysis`, etc.).

### Gráficos de accuracy
**¿Hay visualizaciones de accuracy histórico?**

**Evidencia (web/app.py:1412-1512):** El endpoint `/api/accuracy` devuelve datos de accuracy por modelo:

```python
# Líneas 1505-1512: Retorna accuracy_pct, by_model, detail
return {
    "total_with_result": total,
    "correct": correct,
    "accuracy_pct": accuracy,
    "by_model": by_model,
    "detail": results,  # Lista de cada predicción
}
```

**Frontend:** El HTML debe renderizar estos datos, pero no hay backend de gráficos (matplotlib, plotly, etc.). Los gráficos son generados por JavaScript en el cliente.

### Visualización Monte Carlo
**¿Se muestran simulaciones Monte Carlo?**

**Evidencia (web/app.py:429-435, 486-487):** El pipeline enriquece predicciones con `mc_total` y otros datos Monte Carlo:

```python
# Líneas 429-435: Enriquecimiento con Monte Carlo
from sports.nba.model.monte_carlo import enrich_predictions_with_mc
predictions_df = enrich_predictions_with_mc(predictions_df, feature_df)

# Línea 487: Se incluye en la respuesta
mc_total = round(float(p["mc_total"].iloc[0]), 1)
```

**Disponibilidad:** `mc_total` está expuesto en `/api/analysis` → `games[].mc_total`. El frontend puede mostrarlo pero no hay visualización de distribución completa.

## Historial de actualizaciones

| Fecha | Cambio |
|-------|--------|
| 2026-10-02 | Documentación inicial |
