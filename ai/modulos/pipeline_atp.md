# Pipeline ATP

## Objetivo

Generar predicciones diarias de tenis ATP obteniendo cuotas en tiempo real desde Kambi (Betplay/Rushbet), prediciendo con modelo basado en Elo y detectando value bets.

## Responsabilidad

- Scrapear cuotas Kambi del día
- Para cada partido: predecir con modelo ATP + Elo
- Detectar value bets
- Guardar en CSV (no usa base de datos; ATP no persiste en Neon)

## Flujo funcional

```
1. get_atp_co_odds(date)                     → DataFrame cuotas Kambi
2. Deduplicar por (p1, p2, tourney)          → partidos únicos
3. Para cada partido:
   predict_single(p1, p2, surface, tourney)  → probabilidades
4. detect_value_bets(preds, odds)            → value > 0
5. _upsert_csv()                             → guardar acumulativo
```

## Archivos involucrados

### Script principal
- `run_atp_pipeline.py` (206 líneas)

### Dependencias
- `sports/atp/ingestion/co_odds_scraper.py`
- `sports/atp/model/predictor.py`
- `sports/atp/model/value_detector.py`
- `sports/atp/ingestion/elo.py`

## Entrada

- `--date YYYY-MM-DD` (opcional, default: hoy)
- `--no-save` (opcional, solo consola sin guardar)

## Salida

### CSV (output/)
- `atp_predictions.csv` — predicciones acumulativas
- `atp_value_bets.csv` — value bets detectadas
- `atp_odds.csv` — snapshot de cuotas

## Modelo utilizado

- **Path:** `sports/atp/models/atp_model_v2.joblib`
- **Tipo:** StackingClassifier (XGB+RF+LR)
- **Accuracy:** 64.0% (val 2025)
- **ROC-AUC:** 0.6935
- **Features:** 17 (incluye rolling saque, Elo, superficie)
- **Elos:** `sports/atp/models/current_elos.json`

## Características críticas

### Scraper Kambi
```python
# HTML parsing frágil
soup = BeautifulSoup(response.text, 'html.parser')
# Cambios en estructura rompen pipeline
```

### Sin base de datos (Neon)
Pipeline ATP solo usa CSV (no integrado con base de datos).

### Deduplicación por casa
```python
matchups = odds_df.drop_duplicates(
    subset=["player1_name", "player2_name", "tourney_name"]
)
```
Evita predecir mismo partido N veces (una por bookmaker).

## Dependencias externas

| Fuente | Propósito | Fallo → |
|--------|-----------|---------|
| Kambi HTML | Cuotas en tiempo real | Pipeline aborta (no hay datos) |
| Elos JSON | Ratings actualizados | Predicciones degradan |

## Frecuencia de ejecución

- **Automatizado:** Diario 18:00 COT (GitHub Actions)
- **Manual:** `python run_atp_pipeline.py --date YYYY-MM-DD`

## Riesgos encontrados

### Críticos
- **🔴 Scraper Kambi frágil** — cambios en HTML rompen pipeline completo
- **🔴 Sin base de datos** — ATP no persiste en Neon (solo CSV); decisión de diseño previa, no modificada en la migración
- **🔴 Sin reconciliación ATP** — no se actualizan resultados reales

### Medios
- **🟡 Elos manuales** — requiere `build_elos.py` periódicamente

## Relaciones con otros módulos

- **Ingestion ATP** → scraper Kambi
- **Modelos ML** → modelo ATP v2
- **Value Detection** → detecta oportunidades
- **GitHub Actions** → ejecuta automáticamente

## Pendientes de documentación

### ATP sin base de datos
**¿Por qué ATP no usa Neon?**

**Respuesta documentada en:** [database.md](database.md) — sección "ATP sin base de datos"

**Resumen:** ATP usa solo CSV por diseño legacy, menor volumen y fuentes diferentes (Kambi scraper + Sackmann). No hay integración con `DatabaseRepository`.

### Actualización de resultados reales
**¿Cómo se actualizan resultados ATP?**

**Evidencia (web/app.py:894-1120):** Existe reconciliación ATP separada que usa 3 fuentes:

```python
# web/app.py líneas 898-901
# Fuentes (en orden de prioridad):
#   1. ESPN scoreboard API — gratuita, sin clave
#   2. The Odds API /scores — si ODDS_API_KEY configurada
#   3. Dataset Sackmann — fallback histórico
```

**Endpoint manual:** `/api/atp/reconcile` (línea 1945-1953)

**No hay cron:** La reconciliación ATP no se ejecuta automáticamente en GitHub Actions, solo manualmente desde el dashboard.

### Threshold de value bet
**¿Cuál es el threshold ATP?**

**Evidencia (sports/atp/model/value_detector.py):** Mismo threshold que NBA:

```python
# web/app.py línea 1857: valor hardcodeado en respuesta
"is_value_bet": val >= 0.05  # 5% mínimo
```

**Diferencia con NBA:** ATP filtra value bets con `val >= 0.05` (5%), mientras NBA usa `threshold = 0.0`.

## Historial de actualizaciones

| Fecha | Cambio |
|-------|--------|
| 2026-10-02 | Documentación inicial |
| 2026-10-20 | Referencias Supabase → Neon (ATP sigue sin persistir en BD; solo CSV). |
