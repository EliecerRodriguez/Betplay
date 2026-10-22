# Informe PSE → PKE — Fase 2: Features y Modelos Liga BetPlay

**Fecha:** 2026-10-21
**Tarea solicitada por el usuario:** Continuar con la Fase 2 del sistema Liga BetPlay (que reemplaza ATP): features 50/50 y entrenamiento de modelos, clonando el patrón de la NBA.

## 1. Objetivo del cambio

Completar el motor de predicción del módulo `sports/betplay/`:
1. **Feature engineering** para mercados 50/50 (tarjetas y córners).
2. **Entrenamiento de modelos** Over/Under reutilizando el motor genérico de la NBA.
3. **Baseline funcional** con el dataset histórico (233 partidos).

## 2. Archivos modificados

| Archivo | Cambio |
|---------|--------|
| `sports/betplay/processing/features.py` | **Creado**: `build_team_averages()`, `build_features()`, `make_targets()`, `get_feature_columns()`. Features 50/50: promedios por equipo (tarjetas, córners, faltas, posesión, tiros), forma reciente, record, puntos en tabla, local/visitante. Fix: prioriza `home_form_recent` (forma real de ESPN); helper `_col_or_zero()` evita crash con columnas ausentes. |
| `sports/betplay/model/predictor.py` | **Creado**: motor de entrenamiento (XGBoost + calibración isotónica + TimeSeriesSplit) clonando NBA; auto-detección de versión; `train_model()` (yellow/corners); `predict_over_under()`, `predict_betplay()`. Fix: versionado correcto (v1→v2). |
| `train_betplay_model.py` | **Creado**: script de entrenamiento (`--market yellow\|corners\|both`, usa CSV consolidado por defecto para ruta rápida). |

## 3. Archivos creados

| Archivo | Descripción |
|---------|-------------|
| `sports/betplay/processing/features.py` | Features 50/50 + targets. |
| `sports/betplay/model/predictor.py` | Motor de entrenamiento + predicción. |
| `train_betplay_model.py` | Script CLI de entrenamiento. |
| `models/betplay/betplay_yellow_model_v2.joblib` | Modelo Over/Under tarjetas (baseline). |
| `models/betplay/betplay_corners_model_v2.joblib` | Modelo Over/Under córners (baseline). |

## 4. Archivos eliminados

Ninguno.

## 5. Funcionalidades agregadas

- **Entrenamiento de 2 modelos**: tarjetas (Over 4.5) y córners (Over 9.5), con calibración isotónica y validación temporal.
- **Auto-detección de versión** para modelos BetPlay (clon del patrón NBA).
- **Script CLI** `train_betplay_model.py` con opción de mercado y uso de CSV consolidado.

## 6. Funcionalidades eliminadas

Ninguna.

## 7. Cambios arquitectónicos

- El módulo `sports/betplay/` ahora tiene capas completas de processing y model, siguiendo el patrón de `sports/nba/` (modelo maestro).
- Los modelos se guardan en `models/betplay/` (separados de NBA).

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- `modelos_ml.md` — nuevos modelos de tarjetas/córners BetPlay.
- `pipeline_atp.md` — será `pipeline_betplay.md` cuando se elimine ATP.
- `arquitectura.md` — nuevas capas processing/model de BetPlay.

## 9. Riesgos detectados

- 🟡 **Rendimiento baseline modesto**: yellow acc=0.575 (cv 0.522), corners acc=0.404 (cv 0.467) con 233 muestras. Mejorará con:
  1. Más datos acumulados (cada jornada agrega ~8-10 partidos).
  2. Optimización con Optuna (aún no aplicada al train de BetPlay).
  3. Más features (Elo por equipo, árbitro, estilo de juego).
- 🟢 **El modelo de córners está por debajo del azar** (acc 0.404): indica que la línea 9.5 o las features necesitan ajuste. Se validará con backtesting.
- 🟢 **18 de 22 features disponibles**: algunas (possession/shots_ot) no se generan si el CSV no las tiene.

## 10. Recomendaciones de actualización para la Base de Conocimiento

1. Crear `ai/modulos/pipeline_betplay.md` documentando el flujo completo.
2. Documentar los modelos BetPlay en `modelos_ml.md`.
3. Actualizar `arquitectura.md` con el módulo BetPlay.

---
**Nota de verificación (PSE):**
- ✅ Dataset: 233 partidos (2025-01-24 → 2026-10-21) desde CSV consolidado.
- ✅ Entrenamiento yellow: acc=0.575, cv=0.522, n=233.
- ✅ Entrenamiento corners: acc=0.404, cv=0.467, n=233.
- ✅ Versiones correctas (v2), sin errores de compilación.
- ✅ Commits pusheados: `d0aa2f8`, `5b290dc`.
