# Web Dashboard

## Objetivo

Proveer interfaz web para visualizar predicciones, value bets, accuracy acumulado y estadísticas del sistema.

## Responsabilidad

- API REST con FastAPI
- Servir dashboard HTML
- Consultar Neon
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

**Nota (2026-10-20):** Lee primero desde Neon (vista `v_model_accuracy`); si no está disponible, cae al CSV. El campo `source` de la respuesta es `"neon"` (antes `"supabase"`). Columnas reales de la vista: `model_version, total_games, total_with_result, accuracy_pct, correct`.

### `GET /api/model_health`
**Descripción (2026-10-21):** Salud del modelo (drift + calibración)

**Fuente:** Neon (tabla `model_health`, generada por `utils/model_monitor.py` tras reconciliar)

**Respuesta:**
```json
{
  "source": "neon",
  "models": [
    {
      "model_version": "v11",
      "total": 35,
      "correct": 18,
      "accuracy_pct": 51.4,
      "recent_total": 6,
      "recent_correct": 2,
      "recent_accuracy_pct": 33.3,
      "drift_pct": -18.1,
      "drift_detected": true,
      "buckets": { "50-60": {...}, "60-70": {...}, "70+": {...} }
    }
  ]
}
```

**Alerta de drift:** si el accuracy reciente cae >10 puntos vs el acumulado → `drift_detected: true` → tarjeta de salud en el dashboard muestra ⚠️.

### `GET /api/model_registry`
**Descripción (2026-10-21):** Comparación de versiones del modelo

**Fuente:** Neon (tabla `model_registry`, generada por `utils/model_registry.py`)

**Respuesta:**
```json
{
  "source": "neon",
  "models": [
    {
      "version": "v11",
      "model_type": "xgboost",
      "accuracy": 0.708,
      "roc_auc": 0.752,
      "cv_mean": 0.704,
      "cv_std": 0.041,
      "n_samples": 4086,
      "n_features": 53,
      "trained_at": "2026-10-01",
      "promoted": true
    }
  ]
}
```

**Frontend:** tabla comparativa en el modal Resultados del dashboard (accuracy, CV-AUC, muestras, features, fecha por versión).

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
| Base datos | SQLAlchemy (Neon) |
| Frontend | HTML + CSS + JavaScript |

## Dependencias

- `fastapi`
- `uvicorn[standard]`
- `sqlalchemy`
- `psycopg2-binary`

## Riesgos encontrados

### Críticos
- **🔴 Sin autenticación** — dashboard público (cualquiera puede ver predicciones)
- **🔴 Sin caché** — cada request consulta Neon (lento con muchas predicciones)

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
| 2026-10-20 | Referencias Supabase → Neon. Documentados bugs corregidos: `/api/backtest` usaba columna inexistente `actual_result` (ahora `home_win`), `/api/accuracy` usaba columnas inexistentes (ahora las de la vista del repo), reconciliación excluía partidos de HOY (`<` → `<=`). |
| 2026-10-21 | Documentados endpoints `/api/model_health` y `/api/model_registry`, tarjeta de salud del modelo (drift + calibración) y tabla comparativa de versiones en el modal Resultados. |
