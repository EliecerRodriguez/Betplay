# Base de Conocimiento — Betplay Analytics

## Resumen del proyecto

Sistema automatizado de predicción deportiva multi-deporte (NBA + ATP Tenis) con detección de value bets usando Machine Learning. Opera 24/7 mediante GitHub Actions, generando predicciones diarias antes de cada partido y almacenándolas en Supabase (PostgreSQL).

- **Nombre:** Betplay Analytics
- **Deportes:** NBA (baloncesto) y ATP (tenis masculino)
- **Tecnología:** Python 3.x + Machine Learning (XGBoost, Stacking, Optuna)
- **Base de datos:** Supabase (PostgreSQL) — puerto 6543 (Transaction Pooler)
- **Automatización:** GitHub Actions — 3 workflows diarios
- **Dashboard:** FastAPI + Uvicorn (puerto 8000)

## Propósito

Identificar oportunidades de valor en apuestas deportivas comparando probabilidades del modelo ML contra cuotas de casas de apuestas (Betplay/Rushbet vía Kambi, The Odds API).

## Tecnologías detectadas

| Capa | Tecnología |
|------|------------|
| Lenguaje | Python 3.x |
| ML Framework | scikit-learn, XGBoost |
| Optimización | Optuna (TPE Bayesian) |
| Base de datos | PostgreSQL (Supabase) |
| ORM | SQLAlchemy |
| API NBA | nba_api (sin clave) |
| API Cuotas | The Odds API (opcional) + Kambi scraper |
| Web Framework | FastAPI |
| Servidor | Uvicorn |
| CI/CD | GitHub Actions |
| Env Management | python-dotenv |
| Data Processing | pandas, numpy |
| Logging | logging (stdlib) |

## Arquitectura general

```
┌─────────────────────────────────────────────────────────┐
│           GitHub Actions (Automatización)               │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐   │
│  │ Reconcile    │ │ NBA Pipeline │ │ ATP Pipeline │   │
│  │ 06:30 COT    │ │ 07:00 COT    │ │ 18:00 COT    │   │
│  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘   │
└─────────┼─────────────────┼─────────────────┼───────────┘
          │                 │                 │
          ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────┐
│              Pipelines (Orquestación)                   │
│  reconcile.py  run_pipeline.py  run_atp_pipeline.py    │
└────────┬──────────────────┬──────────────────┬──────────┘
         │                  │                  │
         ▼                  ▼                  ▼
┌─────────────────────────────────────────────────────────┐
│           Ingestion (Clientes de datos)                 │
│  NBA API  │  Odds API  │  Kambi  │  Injuries  │  Elo   │
└────────┬──────────────────┬──────────────────┬──────────┘
         │                  │                  │
         ▼                  ▼                  ▼
┌─────────────────────────────────────────────────────────┐
│         Processing (Feature Engineering)                │
│  features.py  │  recent_form.py  │  standings          │
└────────┬──────────────────┬──────────────────┬──────────┘
         │                  │                  │
         ▼                  ▼                  ▼
┌─────────────────────────────────────────────────────────┐
│           Model (Predicción + Value Detection)          │
│  XGBoost  │  Stacking  │  Monte Carlo  │  Calibración  │
└────────┬──────────────────┬──────────────────┬──────────┘
         │                  │                  │
         ▼                  ▼                  ▼
┌─────────────────────────────────────────────────────────┐
│         Database (Supabase PostgreSQL)                  │
│  games  predictions  odds  value_bets  teams  stats    │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│          Web Dashboard (Visualización)                  │
│              FastAPI + Uvicorn:8000                     │
└─────────────────────────────────────────────────────────┘
```

## Estado actual de la documentación

| Indicador | Valor |
|-----------|-------|
| Módulos documentados | 12 |
| Archivos Python | ~75 (sin contar .venv) |
| Cobertura de tests | No detectada |
| Última actualización | 2026-10-02 |

## Índice de módulos detectados

| # | Módulo | Archivo | Estado |
|---|--------|---------|--------|
| 1 | Pipeline NBA | `modulos/pipeline_nba.md` | ✅ Documentado |
| 2 | Pipeline ATP | `modulos/pipeline_atp.md` | ✅ Documentado |
| 3 | Modelos ML | `modulos/modelos_ml.md` | ✅ Documentado |
| 4 | Ingestion NBA | `modulos/ingestion_nba.md` | ✅ Documentado |
| 5 | Ingestion ATP | `modulos/ingestion_atp.md` | ✅ Documentado |
| 6 | Database | `modulos/database.md` | ✅ Documentado |
| 7 | Processing | `modulos/processing.md` | ✅ Documentado |
| 8 | Value Detection | `modulos/value_detection.md` | ✅ Documentado |
| 9 | Web Dashboard | `modulos/web_dashboard.md` | ✅ Documentado |
| 10 | GitHub Actions | `modulos/github_actions.md` | ✅ Documentado |
| 11 | Config | `modulos/config.md` | ✅ Documentado |
| 12 | Utils | `modulos/utils.md` | ✅ Documentado |

## Flujo operacional diario (automatizado)

| Hora COT | Acción | Script | Salida |
|----------|--------|--------|--------|
| 06:30 AM | Reconciliación de resultados | `reconcile.py` | Actualiza `predictions.home_win` en Supabase |
| 07:00 AM | Pipeline NBA | `run_pipeline.py` | Genera predicciones NBA del día |
| 06:00 PM | Pipeline ATP | `run_atp_pipeline.py` | Genera predicciones ATP del día |

> **Nota:** El sistema no requiere intervención manual. Los workflows de GitHub Actions ejecutan los scripts automáticamente aunque el equipo local esté apagado.

## Versiones de modelos

### NBA
| Versión | Tipo | Accuracy | ROC-AUC | Notas |
|---------|------|----------|---------|-------|
| v9 | Stacking (XGB+RF+LR) | 66.2% | — | Baseline |
| v10 | Stacking (XGB+RF+LR) | 68.5% | 0.806 | +standings, sin injury_impact_diff |
| v11 | XGBoost + Optuna (50 trials) | 70.8% | 0.752 | CV-AUC 0.704±0.041, Brier 0.201 |
| v12+ | Pendiente | — | — | Post-Finales NBA (Jun 2026) |

### ATP
| Versión | Tipo | Accuracy | ROC-AUC | Notas |
|---------|------|----------|---------|-------|
| v1 | XGBoost + calibración isotónica | 63.6% | 0.698 | Train 2013-2023, val 2024 |
| v2 | Stacking (XGB+RF+LR) | 64.0% | 0.6935 | Train 2013-2025, val honesta 2025 |

## Riesgos detectados

### Críticos
- **Dependencia de APIs externas sin rate limiting robusto** — nba_api puede fallar sin retry logic
- **Scraper Kambi frágil** — cambios en HTML rompen ingestion ATP
- **Sin validación de integridad de datos** — predicciones pueden guardarse con features NaN
- **Modelo NBA sin inyección de lesiones en training** — `injury_impact_diff` solo post-modelo

### Medios
- **Task Scheduler local obsoleto** — reemplazado por GitHub Actions pero aún configurado
- **Sin monitoreo de accuracy en producción** — reconciliación no alerta sobre caída de performance
- **CSV como respaldo vs. Supabase como source of truth** — puede causar inconsistencias

## Última actualización

2026-10-02 — Base de Conocimiento construida desde cero. Proyecto Betplay Analytics correctamente documentado (reemplaza documentación obsoleta de proyecto TCM/Trasteoya que fue respaldada en `README_OLD_TCM.md` y `arquitectura_OLD_TCM.md`).
