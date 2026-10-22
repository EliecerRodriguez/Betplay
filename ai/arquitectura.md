# Arquitectura — Betplay Analytics

## Visión general

Sistema de predicción deportiva automatizado con arquitectura orientada a pipelines. Cada deporte (NBA, ATP) tiene su propio pipeline independiente que ejecuta diariamente mediante GitHub Actions.

## Stack tecnológico

| Componente | Tecnología | Versión/Detalle |
|------------|------------|-----------------|
| Lenguaje | Python | 3.x |
| ML Core | scikit-learn | Stacking, Random Forest, Logistic Regression |
| ML Boosting | XGBoost | Clasificador principal |
| Optimización | Optuna | TPE Sampler (Bayesian optimization) |
| Feature Engineering | pandas, numpy | Procesamiento de datos |
| Base de datos | PostgreSQL (Neon) | Puerto 5432 (sslmode=require) |
| ORM | SQLAlchemy | Modelos declarativos |
| API NBA | nba_api | Sin autenticación requerida |
| API Cuotas | The Odds API | Opcional (ODDS_API_KEY) |
| Scraper Cuotas | Requests + BeautifulSoup | Kambi (Betplay/Rushbet) |
| Web Framework | FastAPI | Dashboard API |
| Servidor Web | Uvicorn | Puerto 8000 |
| Config | python-dotenv | Variables de entorno desde .env |
| CI/CD | GitHub Actions | 3 workflows (cron) |
| Logging | logging (stdlib) | Nivel configurable |

## Arquitectura de capas

```
┌───────────────────────────────────────────────────────────────┐
│                   CAPA DE ORQUESTACIÓN                        │
│                                                               │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────┐  │
│  │ reconcile.py    │  │ run_pipeline.py │  │ run_atp_    │  │
│  │                 │  │ (NBA)           │  │ pipeline.py │  │
│  │ Actualiza       │  │                 │  │ (ATP)       │  │
│  │ resultados      │  │ Pipeline diario │  │             │  │
│  │ reales          │  │ completo NBA    │  │ Pipeline    │  │
│  │                 │  │                 │  │ diario ATP  │  │
│  └────────┬────────┘  └────────┬────────┘  └──────┬──────┘  │
└───────────┼────────────────────┼──────────────────┼─────────┘
            │                    │                  │
            ▼                    ▼                  ▼
┌───────────────────────────────────────────────────────────────┐
│                   CAPA DE INGESTION                           │
│                                                               │
│  NBA:                          ATP:                           │
│  • nba_client.py               • atp_client.py               │
│  • odds_client.py              • co_odds_scraper.py          │
│  • injuries_client.py          • historical_client.py        │
│  • elo.py                      • elo.py                      │
│  • recent_form.py              • h2h_client.py               │
│  • standings_client.py         • rankings_client.py          │
│  • travel_client.py            • odds_client.py              │
│                                • injuries_client.py          │
└───────────┬────────────────────┬──────────────────┬─────────┘
            │                    │                  │
            ▼                    ▼                  ▼
┌───────────────────────────────────────────────────────────────┐
│                 CAPA DE PROCESAMIENTO                         │
│                                                               │
│  NBA:                          ATP:                           │
│  • features.py                 • features.py                 │
│    - build_features()            - build_atp_features()      │
│    - clean_team_stats()          - rolling stats             │
│    - point-in-time rolling       - surface adjustments       │
│                                                               │
│  • recent_form.py              • recent_form.py              │
│    - enrich_with_form()          - surface-specific form     │
│    - get_season_wpct()                                       │
└───────────┬────────────────────┬──────────────────┬─────────┘
            │                    │                  │
            ▼                    ▼                  ▼
┌───────────────────────────────────────────────────────────────┐
│                    CAPA DE MODELO                             │
│                                                               │
│  NBA:                          ATP:                           │
│  • predictor.py                • predictor.py                │
│    - predict()                   - predict_single()          │
│    - adjust_predictions()        - load Elo + model         │
│  • monte_carlo.py              • value_detector.py           │
│    - enrich_predictions_       (sin Monte Carlo)             │
│      with_mc()                                               │
│  • value_detector.py                                         │
│    - detect_value_bets()                                     │
│                                                               │
│  Modelos guardados:                                          │
│  • models/nba_model_vN.joblib                                │
│  • sports/atp/models/atp_model_v2.joblib                     │
└───────────┬────────────────────┬──────────────────┬─────────┘
            │                    │                  │
            ▼                    ▼                  ▼
┌───────────────────────────────────────────────────────────────┐
│                   CAPA DE PERSISTENCIA                        │
│                                                               │
│  database/                                                    │
│  • models.py (SQLAlchemy ORM)                                │
│    - Team, Game, TeamStat, Odd, Prediction, ValueBet,       │
│      LineScore                                               │
│  • repository.py (upsert operations)                         │
│                                                               │
│  Tablas Neon:                                                │
│  ┌──────────┬──────────────┬────────────┬───────────────┐   │
│  │ teams    │ games        │ team_stats │ line_scores   │   │
│  ├──────────┼──────────────┼────────────┼───────────────┤   │
│  │ odds     │ predictions  │ value_bets │               │   │
│  └──────────┴──────────────┴────────────┴───────────────┘   │
└───────────┬────────────────────┬──────────────────┬─────────┘
            │                    │                  │
            ▼                    ▼                  ▼
┌───────────────────────────────────────────────────────────────┐
│                  CAPA DE PRESENTACIÓN                         │
│                                                               │
│  web/app.py (FastAPI)                                        │
│  • GET /                     → Dashboard HTML                │
│  • GET /api/predictions      → Predicciones del día          │
│  • GET /api/value_bets       → Value bets detectadas         │
│  • GET /api/accuracy         → Accuracy acumulado            │
│                                                               │
│  Servidor: uvicorn --reload --port 8000                      │
└───────────────────────────────────────────────────────────────┘
```

## Organización del proyecto

```
betplay/
├── config/                      # Configuración central
│   ├── settings.py              # Variables de entorno
│   └── __init__.py
│
├── database/                    # Capa de datos
│   ├── models.py                # Esquema SQLAlchemy
│   ├── repository.py            # Operaciones CRUD
│   └── __init__.py
│
├── ingestion/                   # Clientes legacy NBA
│   ├── nba_client.py
│   ├── odds_client.py
│   ├── injuries_client.py
│   ├── elo.py
│   ├── recent_form.py
│   ├── travel_client.py
│   └── co_odds_scraper.py
│
├── processing/                  # Feature engineering legacy NBA
│   ├── features.py
│   └── __init__.py
│
├── model/                       # Modelo legacy NBA
│   ├── predictor.py
│   ├── monte_carlo.py
│   ├── value_detector.py
│   └── __init__.py
│
├── sports/                      # Estructura modular por deporte
│   ├── nba/                     # Módulo NBA
│   │   ├── config/
│   │   │   └── settings.py
│   │   ├── database/
│   │   │   ├── models.py
│   │   │   └── repository.py
│   │   ├── ingestion/
│   │   │   ├── nba_client.py
│   │   │   ├── odds_client.py
│   │   │   ├── injuries_client.py
│   │   │   ├── elo.py
│   │   │   ├── recent_form.py
│   │   │   ├── standings_client.py
│   │   │   ├── travel_client.py
│   │   │   └── co_odds_scraper.py
│   │   ├── processing/
│   │   │   └── features.py
│   │   ├── model/
│   │   │   ├── predictor.py
│   │   │   ├── monte_carlo.py
│   │   │   └── value_detector.py
│   │   └── __init__.py
│   │
│   └── atp/                     # Módulo ATP
│       ├── config/
│       │   └── settings.py
│       ├── database/
│       │   └── models.py
│       ├── ingestion/
│       │   ├── atp_client.py
│       │   ├── co_odds_scraper.py
│       │   ├── elo.py
│       │   ├── h2h_client.py
│       │   ├── historical_client.py
│       │   ├── injuries_client.py
│       │   ├── odds_client.py
│       │   └── rankings_client.py
│       ├── processing/
│       │   ├── features.py
│       │   └── recent_form.py
│       ├── model/
│       │   ├── predictor.py
│       │   └── value_detector.py
│       └── models/
│           ├── atp_model_v2.joblib
│           └── current_elos.json
│
├── utils/                       # Utilidades compartidas
│   ├── logger.py
│   ├── backtest.py
│   └── __init__.py
│
├── web/                         # Dashboard web
│   ├── app.py                   # FastAPI application
│   └── __init__.py
│
├── models/                      # Modelos entrenados NBA
│   ├── nba_model_v9.joblib
│   ├── nba_model_v10.joblib
│   ├── nba_model_v11.joblib
│   └── ...
│
├── data/                        # Caché de datos crudos
│   ├── cache/                   # Partidos descargados
│   └── atp_cache/               # Partidos ATP históricos
│
├── output/                      # CSVs de salida
│   ├── bet_journal.csv          # Historial acumulado NBA
│   ├── atp_predictions.csv
│   ├── atp_value_bets.csv
│   └── atp_odds.csv
│
├── logs/                        # Logs de ejecución
│
├── .github/workflows/           # GitHub Actions
│   ├── daily_pipeline.yml       # NBA 07:00 COT
│   ├── reconcile.yml            # Reconcile 06:30 COT
│   ├── atp_pipeline.yml         # ATP 18:00 COT
│   └── catchup.yml              # Manual
│
├── run_pipeline.py              # Orquestador NBA
├── run_atp_pipeline.py          # Orquestador ATP
├── reconcile.py                 # Actualiza resultados
├── train_model.py               # Entrena modelo NBA
├── build_atp_model.py           # Entrena modelo ATP
├── build_elos.py                # Actualiza Elos ATP
├── calibrate_atp_model.py       # Calibra modelo ATP
├── requirements.txt
├── .env                         # Variables de entorno
└── README.md
```

## Patrones arquitectónicos detectados

### 1. Pipeline Pattern
Cada deporte sigue el patrón ETL:
- **Extract:** Ingestion (APIs, scrapers)
- **Transform:** Processing (features, Elo, form)
- **Load:** Repository (upserts a Neon)

### 2. Repository Pattern
`database/repository.py` abstrae las operaciones CRUD, permitiendo upserts idempotentes.

### 3. Modular por deporte
`sports/{nba,atp}/` encapsulan toda la lógica específica de cada deporte, permitiendo agregar nuevos deportes sin modificar el core.

### 4. Configuration as Code
`.env` + `config/settings.py` centralizan todas las variables de entorno (DB, APIs, modelo).

### 5. Point-in-Time Features
Features calculados con rolling windows para evitar look-ahead bias durante entrenamiento.

## Flujo de datos

### Pipeline NBA (diario 07:00 COT)

```
1. get_daily_games(date)
   ↓ [nba_client.py]
2. get_combined_team_stats(season)
   ↓ [nba_client.py]
3. enrich_with_form(games, season, n=5)
   ↓ [recent_form.py] — rolling W/L últimos 5 partidos
4. get_season_wpct() — override point-in-time
   ↓ [recent_form.py]
5. enrich_with_standings(games, season)
   ↓ [standings_client.py] — seed, games_back
6. enrich_with_travel(games)
   ↓ [travel_client.py] — jet lag, back-to-back
7. apply_elos_to_games(games, elos)
   ↓ [elo.py]
8. build_features(games, team_stats)
   ↓ [features.py] — 53 features
9. predict(features_df, model)
   ↓ [predictor.py] — XGBoost
10. get_team_injury_impact(home_id, visitor_id)
    ↓ [injuries_client.py]
11. adjust_predictions(predictions, injury_impacts)
    ↓ [predictor.py] — ajusta probabilidades post-modelo
12. enrich_predictions_with_mc(predictions, n_sims=10000)
    ↓ [monte_carlo.py] — simulación Monte Carlo
13. get_odds(date)
    ↓ [odds_client.py]
14. detect_value_bets(predictions, odds)
    ↓ [value_detector.py] — (prob * odds) - 1 > 0
15. upsert to Neon
    ↓ [repository.py]
16. append to CSV (output/)
```

### Pipeline ATP (diario 18:00 COT)

```
1. get_atp_co_odds(date)
   ↓ [co_odds_scraper.py] — Kambi (Betplay/Rushbet)
2. For each match:
   predict_single(p1, p2, surface, tourney)
   ↓ [predictor.py] — load Elo + model
3. detect_value_bets(predictions, odds)
   ↓ [value_detector.py]
4. append to CSV (output/)
```

### Reconciliación (diario 06:30 COT)

```
1. SELECT * FROM predictions WHERE home_win IS NULL
   ↓
2. get_line_scores(game_ids)
   ↓ [nba_client.py]
3. Determina ganador: home_win = 1 si local ganó, else 0
   ↓
4. UPDATE predictions SET home_win = ...
   ↓ [repository.py]
```

## Dependencias críticas

### Externas (pueden fallar)
- **nba_api** — sin rate limiting robusto, puede devolver 429
- **Kambi HTML** — scraper frágil, cambios en estructura rompen ATP pipeline
- **Neon** — conexión directa puerto 5432 con `sslmode=require`

### Internas
- **models/** — si falta modelo, pipeline falla sin fallback
- **data/cache/** — partidos cacheados aceleran re-entrenamientos
- **sports/atp/models/current_elos.json** — sin Elos, predicciones ATP degradan

## Fortalezas

1. **Automatización completa** — no requiere intervención manual
2. **Idempotencia** — correr pipeline 2x el mismo día no duplica registros
3. **Point-in-time features** — evita data leakage en entrenamiento
4. **Optimización Bayesiana** — Optuna mejora hiperparámetros automáticamente
5. **Modular** — agregar nuevo deporte es independiente del core
6. **Versionado de modelos** — permite rollback si nuevo modelo degrada

## Debilidades

1. **Sin retry logic** — fallo en API externa aborta pipeline completo
2. **Sin alertas** — no notifica si accuracy cae bajo umbral
3. **Scraper Kambi frágil** — ATP pipeline depende de estructura HTML
4. **Injury impact post-modelo** — no aprende de lesiones durante training
5. **CSV como respaldo** — puede divergir de Neon (source of truth ambigua)
6. **Sin validación de features** — puede guardar predicciones con NaN

## Próximas mejoras recomendadas

1. **Retry decorator** para llamadas a APIs externas
2. **Feature validation pipeline** — rechazar filas con NaN antes de predict()
3. **Monitoring dashboard** — alertar si accuracy < 60% en rolling 30 días
4. **ATP API alternativa** — reducir dependencia de scraper Kambi
5. **Injury features en training** — incorporar `injury_impact_diff` durante fit()
6. **Eliminar CSV** — usar Neon como única source of truth

## Última actualización

2026-10-21 — Migración Supabase → Neon (2026-10-20) y Sistema de Mejora Continua MLOps (2026-10-21) incorporados: flujo de entrenamiento local + CI verificador, monitoreo de drift, registro de versiones.
