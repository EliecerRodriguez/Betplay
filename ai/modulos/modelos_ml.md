# Modelos ML

## Objetivo

Proveer modelos de Machine Learning entrenados para predecir resultados de partidos NBA y ATP con calibración de probabilidades.

## Responsabilidad

- Entrenar modelos con datos históricos
- Optimizar hiperparámetros (Optuna)
- Calibrar probabilidades
- Versionar modelos
- Cargar modelos en pipelines de predicción

## Modelos NBA

### Versiones

| Versión | Tipo | Accuracy | ROC-AUC | Samples | Features | Notas |
|---------|------|----------|---------|---------|----------|-------|
| v9 | Stacking (XGB+RF+LR) | 66.2% | — | ~3500 | 50 | Baseline |
| v10 | Stacking (XGB+RF+LR) | 68.5% | 0.806 | ~3800 | 52 | +standings, sin injury_impact_diff |
| v11 | XGBoost + Optuna | 70.8% | 0.752 | 4086 | 53 | CV-AUC 0.704±0.041, Brier 0.201, best trial #21 |

### Sistema de Mejora Continua (MLOps) — 2026-10-21

El pipeline estático se convirtió en un **ciclo de mejora continua**:

1. **Auto-detección de versión** — `sports/nba/model/predictor.py:_latest_model_version()` elige el joblib **más reciente por fecha de modificación** (no por número mayor, que fallaba con v9 > v10). Ya no hay que cambiar `MODEL_VERSION` a mano (eliminado de `.env` y workflows).
2. **Reentrenamiento con un comando** — `python utils/train_and_publish.py`:
   - Determina la siguiente versión (vN+1) con `utils/next_model_version.py`
   - Entrena localmente con todo el historial (caché local + API)
   - Compara con el modelo actual (CV-AUC / accuracy)
   - Si es mejor: registra en Neon (`model_registry`) y hace commit+push
   - Si es peor: **NO lo publica** (producción intacta) y avisa
   - Opciones: `--dry-run`, `--optimize N`, `--start-date`, `--force`
3. **Aprender de errores** — el reentrenamiento usa los resultados reales (`home_win`) como target.
4. **Detección de drift** — `utils/model_monitor.py` compara accuracy acumulado vs reciente (30 días); alerta en el dashboard si cae >10 puntos. Persiste en Neon (`model_health`).
5. **Comparación de versiones** — `utils/model_registry.py` registra métricas de cada versión en Neon (`model_registry`); tabla comparativa en el dashboard.

**Flujo de reentrenamiento:** CI (entrenador) → **Local (entrenador) + CI (verificador/registrador)**. El workflow `train_model.yml` ya NO entrena en CI (no tiene caché de partidos y stats.nba.com bloquea IPs de nube); solo verifica que el modelo del repo es válido y lo registra en Neon.

**Riesgos MLOps:**
- 🟡 Caché de stats (`data/stats_cache/*.csv`) puede quedar desactualizada (snapshot de hoy; se actualiza en cada corrida local o cuando la API responde)
- 🟡 El monitor de drift usa datos limitados (35 predicciones resueltas actualmente) — el drift puede ser ruidoso
- 🟢 `train_and_publish.py` requiere ejecución manual semanal (no es 100% automático; alternativa futura: cron local o runner self-hosted)

### Script de entrenamiento

**Archivo:** `train_model.py`

**Comando:**
```bash
python train_model.py \
  --version v12 \
  --start-date 2023-10-24 \
  --season 2024-25 --season 2025-26 \
  --model xgboost \
  --optimize 50 \
  --with-form
```

**Opciones:**
- `--start-date` / `--days N` — rango histórico (caché en `data/cache/`)
- `--season YYYY-YY` — temporada(s) para stats (repetible)
- `--model` — `xgboost` | `stacking` | `random_forest` | `logistic`
- `--optimize N` — trials Optuna (0=desactivado)
- `--with-form` — incluir forma reciente y rest days
- `--with-travel` — incluir jet lag (requiere `--with-form`)
- `--version vN` — tag del archivo de salida

**Salida:** `models/nba_model_vN.joblib`

### Arquitectura v11 (actual)

```python
XGBClassifier(
    n_estimators=...,      # optimizado por Optuna
    max_depth=...,
    learning_rate=...,
    subsample=...,
    colsample_bytree=...,
    gamma=...,
    min_child_weight=...,
    scale_pos_weight=...,
    objective='binary:logistic',
    eval_metric='logloss',
    use_label_encoder=False
)
```

**Post-procesamiento:**
- Calibración isotónica sobre probabilidades raw
- Validación cruzada: `TimeSeriesSplit(n_splits=3)`

### Features utilizadas (53)

Ver `sports/nba/processing/features.py:build_features()`

**Categorías:**
1. **Elo** — `elo_diff`
2. **Stats de equipo** — `pts_diff`, `ast_diff`, `reb_diff`, `fg_pct_diff`, etc.
3. **Forma reciente** — `home_last_5_wins`, `visitor_last_5_wins`
4. **Standings** — `seed_diff`, `games_back_diff`
5. **Travel** — `home_jet_lag`, `visitor_jet_lag`, `home_rest_days`, `visitor_rest_days`
6. **Contexto** — `is_home`, `season`

**Nota crítica:** `injury_impact_diff` NO está en features de entrenamiento, solo se aplica post-modelo en producción.

## Modelos ATP

### Versiones

| Versión | Tipo | Accuracy | ROC-AUC | Train | Val | Notas |
|---------|------|----------|---------|-------|-----|-------|
| v1 | XGBoost + calibración | 63.6% | 0.698 | 2013-2023 | 2024 | 13 features |
| v2 | Stacking (XGB+RF+LR) | 64.0% | 0.6935 | 2013-2025 | 2025 | 17 features (+saque rolling) |

### Script de entrenamiento

**Archivo:** `build_atp_model.py`

**Comando:**
```bash
python build_atp_model.py --force
```

**Salida:** `sports/atp/models/atp_model_v2.joblib`

### Arquitectura v2 (actual)

```python
StackingClassifier(
    estimators=[
        ('xgb', XGBClassifier(...)),
        ('rf', RandomForestClassifier(...)),
        ('lr', LogisticRegression(...))
    ],
    final_estimator=LogisticRegression(),
    cv=5
)
```

### Features ATP (17)

Ver `sports/atp/processing/features.py:build_atp_features()`

1. **Elo** — `elo_diff`
2. **Rankings** — `rank_diff`
3. **H2H** — `h2h_wins`, `h2h_losses`
4. **Forma superficie** — `surface_win_pct_diff`
5. **Saque rolling** — `ace_rate_diff`, `first_serve_pct_diff`
6. **Superficie** — `surface` (categorical)

### Dependencia: Elos

**Archivo:** `sports/atp/models/current_elos.json`

**Actualización:**
```bash
python build_elos.py --start-year 2010
```

Descarga partidos desde Jeff Sackmann GitHub y calcula Elos acumulados.

## Archivos involucrados

### NBA
- `train_model.py` — script de entrenamiento
- `sports/nba/model/predictor.py` — carga modelo y predice (auto-detección de versión por mtime)
- `sports/nba/model/monte_carlo.py` — simulaciones
- `models/nba_model_vN.joblib` — modelos versionados
- `utils/train_and_publish.py` — comando único de reentrenamiento + publicación (MLOps)
- `utils/next_model_version.py` — auto-incrementa versión (vN+1)
- `utils/model_monitor.py` — monitor de salud (drift + calibración) → `model_health`
- `utils/model_registry.py` — registro de métricas por versión → `model_registry`

### ATP
- `build_atp_model.py` — script de entrenamiento v2
- `build_atp_model_v4.py` — versión experimental
- `calibrate_atp_model.py` — calibración isotónica
- `build_elos.py` — actualiza Elos
- `sports/atp/model/predictor.py` — carga y predice
- `sports/atp/models/atp_model_v2.joblib` — modelo activo
- `sports/atp/models/current_elos.json` — ratings por jugador

## Optimización Bayesiana (Optuna)

**Método:** TPESampler (Tree-structured Parzen Estimator)

**Hiperparámetros optimizados (XGBoost):**
- `n_estimators` — [50, 500]
- `max_depth` — [3, 12]
- `learning_rate` — [0.01, 0.3]
- `subsample` — [0.6, 1.0]
- `colsample_bytree` — [0.6, 1.0]
- `gamma` — [0, 5]
- `min_child_weight` — [1, 10]
- `scale_pos_weight` — [0.5, 2.0]

**Validación:** `TimeSeriesSplit(n_splits=3)` para evitar data leakage

**Trials:** 50 (configurable con `--optimize N`)

## Calibración de probabilidades

Ambos modelos usan calibración isotónica:

```python
from sklearn.calibration import CalibratedClassifierCV

calibrated = CalibratedClassifierCV(
    base_estimator=model,
    method='isotonic',
    cv=5
)
```

Mapea probabilidades raw → probabilidades calibradas (mejor alineadas con frecuencia real).

## Métricas evaluadas

- **Accuracy** — % aciertos
- **ROC-AUC** — área bajo curva ROC
- **Brier Score** — (prob - real)² promedio
- **CV-AUC** — cross-validation AUC ± desviación

## Riesgos encontrados

### Críticos
- **🔴 Injury impact NO en training** — se aplica post-modelo, modelo no aprende patrones de lesiones
- **🟡 Reentrenamiento semanal manual** — `train_and_publish.py` requiere que el usuario lo ejecute (2026-10-21); el flujo automático de CI solo verifica/registra
- **🔴 Feature importance no documentada** — no hay análisis de qué features aportan más

### Medios
- **🟡 Elos ATP manuales** — requiere ejecutar `build_elos.py` periódicamente
- **🟡 Sin A/B testing** — no hay comparación online v11 vs v12

## Relaciones con otros módulos

- **Processing** → genera features para training
- **Ingestion** → provee datos históricos
- **Pipeline NBA/ATP** → cargan modelos para predicción
- **Config** → `MODEL_VERSION` en `.env`

## Pendientes de documentación

### Criterio de reentrenamiento
**¿Cuándo reentrenar?**

**Evidencia (2026-10-21):** Se implementó el **Sistema de Mejora Continua (MLOps)**. El reentrenamiento ahora es:

```bash
python utils/train_and_publish.py   # comando único semanal
```

**Qué resuelve:**
- ✅ Monitoreo de accuracy en producción — `utils/model_monitor.py` (drift + calibración, persiste en `model_health`)
- ✅ Alerta cuando accuracy reciente cae >10 puntos vs acumulado — visible en el dashboard
- ✅ Comparación de versiones — `model_registry` en Neon con métricas por versión
- ⚠️ Scheduler de reentrenamiento periódico — sigue siendo manual (el usuario ejecuta el comando semanalmente); alternativa futura: cron local o runner self-hosted

### Feature importance v11
**¿Cuál es el ranking de features?**

**Evidencia:** NO existe análisis de feature importance documentado. El modelo v11 usa 53 features pero no hay archivo o log que muestre qué features aportan más.

**Archivos revisados:**
- `train_model.py` — no genera feature importance
- `sports/nba/model/predictor.py` — solo carga y predice
- No existe archivo `feature_importance_v11.json` o similar

### Optuna en ATP
**¿Por qué ATP no usa Optuna?**

**Evidencia (build_atp_model.py):** El script ATP NO tiene integración con Optuna. Solo NBA usa optimización bayesiana.

**Diferencia arquitectónica:**
- NBA: Script moderno (`train_model.py` v2024+) con `--optimize N`
- ATP: Scripts legacy (`build_atp_model.py`, `build_atp_model_v4.py`) sin optimización hiperparámetrica automática

**Razón probable:** ATP tiene menos features (17 vs 53) y menos datos históricos, por lo que la optimización no se consideró prioritaria.

## Historial de actualizaciones

| Fecha | Cambio |
|-------|--------|
| 2026-10-02 | Documentación inicial |
| 2026-10-21 | Documentado Sistema de Mejora Continua (MLOps): auto-detección de versión, `train_and_publish.py`, monitor de drift (`model_monitor.py`), registro de versiones (`model_registry.py`). Flujo de reentrenamiento: local (entrenador) + CI (verificador/registrador). |
