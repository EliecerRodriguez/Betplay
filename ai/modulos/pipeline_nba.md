# Pipeline NBA

## Objetivo

Orquestar la generación diaria de predicciones NBA mediante la integración de múltiples fuentes de datos, feature engineering punto-en-el-tiempo, predicción con modelo ML y detección de value bets.

## Responsabilidad

- Obtener partidos del día desde nba_api
- Enriquecer con stats de equipo, forma reciente, standings, Elo, travel, lesiones
- Construir features sin data leakage (rolling windows)
- Predecir con modelo XGBoost
- Ajustar probabilidades por impacto de lesiones
- Simular resultados con Monte Carlo
- Detectar value bets vs. cuotas
- Persistir en Supabase y CSV

## Flujo funcional

```
1. get_daily_games(date)                     → DataFrame de partidos
2. get_combined_team_stats(season)           → Stats por equipo
3. enrich_with_form(games, season, n=5)      → W/L últimos 5 partidos
4. get_season_wpct() override                → W% acumulado punto-en-el-tiempo
5. enrich_with_standings(games, season)      → seed, games_back
6. enrich_with_travel(games)                 → jet lag, rest days
7. apply_elos_to_games(games, elos)          → Elo rating
8. build_features(games, team_stats)         → 53 features
9. predict(features_df, model)               → probabilidades raw
10. get_team_injury_impact()                 → impacto lesiones por equipo
11. adjust_predictions(preds, injuries)      → ajuste post-modelo
12. enrich_predictions_with_mc(preds)        → simulación Monte Carlo
13. get_odds(date)                           → cuotas de casas
14. detect_value_bets(preds, odds)           → value > 0
15. upsert_to_supabase()                     → persistencia
16. append_to_csv()                          → respaldo local
```

## Archivos involucrados

### Script principal
- `run_pipeline.py` (312 líneas) — orquestador

### Dependencias
- `sports/nba/ingestion/nba_client.py`
- `sports/nba/ingestion/odds_client.py`
- `sports/nba/ingestion/injuries_client.py`
- `sports/nba/ingestion/elo.py`
- `sports/nba/ingestion/recent_form.py`
- `sports/nba/ingestion/standings_client.py`
- `sports/nba/ingestion/travel_client.py`
- `sports/nba/processing/features.py`
- `sports/nba/model/predictor.py`
- `sports/nba/model/monte_carlo.py`
- `sports/nba/model/value_detector.py`
- `sports/nba/database/repository.py`

## Entrada

- `--date YYYY-MM-DD` (opcional, default: hoy)
- `--no-append` (opcional, reemplaza CSV en lugar de acumular)

## Salida

### Supabase
- `predictions` — predicciones con probabilidades
- `odds` — cuotas por casa
- `value_bets` — oportunidades detectadas
- `games` — partidos del día

### CSV (output/)
- `bet_journal.csv` — historial acumulado (nunca borrar)

## Configuración

Variables de entorno (`.env`):
```
NBA_SEASON=2024-25
MODEL_VERSION=v11
ODDS_API_KEY=...
DATABASE_URL=postgresql://...
```

## Modelo utilizado

- **Path:** `models/nba_model_{MODEL_VERSION}.joblib`
- **Tipo:** XGBoost + Optuna (v11: 70.8% accuracy)
- **Features:** 53 (ver `processing/features.py`)
- **Calibración:** Isotónica post-entrenamiento

## Características críticas

### Point-in-time override
```python
# Ajusta w_pct en team_stats_df con el acumulado real hasta hoy
wpct = get_season_wpct(tid, pit_date, NBA_SEASON)
team_stats_df.loc[mask, "w_pct"] = wpct
```
Elimina asimetría entre training (rolling) y producción (stats completas).

### Ajuste por lesiones (post-modelo)
```python
# Impacto de lesiones NO está en el training
injury_impact = get_team_injury_impact(home_id, visitor_id)
home_win_prob_adj = adjust_predictions(home_win_prob, injury_impact)
```
**Riesgo:** Modelo no aprende patrones de lesiones.

### Monte Carlo opcional
```python
enrich_predictions_with_mc(predictions, n_sims=10000)
# Añade: mc_home_win_prob, mc_spread, mc_total
```
Simula 10K partidos por predicción.

## Dependencias externas

| API | Propósito | Fallo → |
|-----|-----------|---------|
| nba_api | Partidos, stats, scores | Pipeline aborta |
| The Odds API | Cuotas | Usa placeholders (no value bets) |
| Lesiones API | Impacto por equipo | Salta ajuste |

## Frecuencia de ejecución

- **Automatizado:** Diario 07:00 COT (GitHub Actions)
- **Manual:** `python run_pipeline.py --date YYYY-MM-DD`

## Riesgos encontrados

### Críticos
- **🔴 Sin retry logic** — fallo en nba_api aborta pipeline completo
- **🔴 Injury impact post-modelo** — modelo no aprende de lesiones durante training
- **🔴 Sin validación de features** — puede guardar predicciones con NaN

### Medios
- **🟡 CSV como respaldo ambiguo** — puede divergir de Supabase
- **🟡 Rate limiting nba_api** — delay hardcodeado a 0.6s, puede devolver 429

## Relaciones con otros módulos

- **Ingestion NBA** → provee datos crudos
- **Processing** → construye features
- **Modelos ML** → carga modelo entrenado
- **Value Detection** → detecta oportunidades
- **Database** → persiste resultados
- **GitHub Actions** → ejecuta automáticamente

## Pendientes de documentación

✅ **Resueltos (2026-10-02):**

**1. Lógica de fallback cuando `get_combined_team_stats()` falla:**
```python
# run_pipeline.py líneas 68-73
try:
    team_stats_df = clean_team_stats(get_combined_team_stats(NBA_SEASON))
except Exception as exc:
    logger.warning("get_combined_team_stats falló (%s) — stats básicas", exc)
    from sports.nba.ingestion.nba_client import get_team_stats
    team_stats_df = clean_team_stats(get_team_stats(NBA_SEASON))
```
**Respuesta:** Usa `get_team_stats()` como fallback (stats básicas en lugar de stats avanzadas).

**2. Criterio de threshold para value bets:**
```python
# sports/nba/model/value_detector.py línea 30
VALUE_THRESHOLD = float(0)   # value > 0 es suficiente
```
**Respuesta:** `threshold = 0.0` (cualquier valor esperado positivo). Comentario indica que se puede subir a 0.05 para filtrar ruido.

**3. Qué hace el pipeline si hay 0 partidos del día:**
```python
# run_pipeline.py líneas 57-61
if games_df.empty:
    logger.warning("No hay partidos NBA para %s.", date_str)
    return {"games": pd.DataFrame(), "features": pd.DataFrame(),
            "predictions": pd.DataFrame(), "odds": pd.DataFrame(),
            "value_bets": pd.DataFrame()}
```
**Respuesta:** Retorna DataFrames vacíos y termina limpiamente con advertencia en log.

## Historial de actualizaciones

| Fecha | Cambio |
|-------|--------|
| 2026-10-02 | Documentación inicial |
