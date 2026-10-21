# Informe PSE → PKE — Sistema de Mejora Continua (MLOps)

**Fecha:** 2026-10-21
**Tarea solicitada por el usuario:** Implementar un sistema de mejora continua para el modelo NBA: reentrenamiento automático, aprendizaje de errores, detección de drift con alerta en dashboard, y comparación de versiones. El usuario quiere ejecutar UN comando semanalmente y que el resto sea automático.

## 1. Objetivo del cambio

Convertir el pipeline estático de predicción NBA en un **ciclo de mejora continua (MLOps)**:
1. **Auto-detección de versión** — eliminar el cambio manual de `MODEL_VERSION` en 5 lugares.
2. **Reentrenamiento con un comando** — `python utils/train_and_publish.py` entrena, compara, registra y publica.
3. **Aprender de errores** — el reentrenamiento usa los resultados reales (`home_win`) como target.
4. **Detección de drift** — monitor que compara accuracy acumulado vs reciente (30 días) y alerta en el dashboard.
5. **Comparación de versiones** — registro de métricas de cada versión en Neon y tabla comparativa en el dashboard.

## 2. Archivos modificados

| Archivo | Cambio |
|---------|--------|
| `sports/nba/model/predictor.py` | `_latest_model_version()` ahora elige el joblib **más reciente por fecha de modificación** (no por número mayor, que fallaba con v9 > v10). |
| `.github/workflows/daily_pipeline.yml` | Eliminado `MODEL_VERSION: v11` (auto-detección). |
| `.github/workflows/reconcile.yml` | Eliminado `MODEL_VERSION: v11` + **añadido paso de monitor de salud** (`utils/model_monitor.py`) tras reconciliar. |
| `.github/workflows/catchup.yml` | Eliminado `MODEL_VERSION: v11`. |
| `.github/workflows/train_model.yml` | **Reescrito**: ya NO entrena en CI (no tiene caché de partidos y stats.nba.com bloquea IPs de nube). Ahora **verifica** que el modelo del repo es válido y lo **registra** en Neon. |
| `.env` | `MODEL_VERSION=v11` comentado (auto-detección). |
| `sports/nba/ingestion/nba_client.py` | **Retry con backoff** (`_retry()`) en `get_team_stats` y `get_team_advanced_stats` + **caché local** en `data/stats_cache/` como fallback cuando la API falla (stats.nba.com bloquea IPs de nube en CI). |
| `.gitignore` | Excepción para `data/stats_cache/*.csv` (necesarios en CI). |
| `web/app.py` | Endpoints `/api/model_health` y `/api/model_registry`. |
| `web/static/index.html` | Tarjeta de salud del modelo (drift + calibración) en el dashboard + tabla comparativa de versiones en modal Resultados. |

## 3. Archivos creados

| Archivo | Descripción |
|---------|-------------|
| `utils/next_model_version.py` | Auto-incrementa la versión del modelo (vN+1). |
| `utils/model_monitor.py` | Calcula accuracy acumulado, accuracy reciente (30d), drift y calibración por bucket de confianza; persiste en Neon (`model_health`). |
| `utils/model_registry.py` | Registra métricas de cada versión en Neon (`model_registry`) para comparación. |
| `utils/train_and_publish.py` | **Comando único semanal**: entrena localmente, compara con el actual, registra en Neon y hace push. |
| `data/stats_cache/team_stats_2025-26.csv` | Caché de stats básicas (30 equipos) para CI. |
| `data/stats_cache/team_stats_advanced_2025-26.csv` | Caché de stats avanzadas (30 equipos) para CI. |

## 4. Archivos eliminados

Ninguno.

## 5. Funcionalidades agregadas

- **Auto-detección de versión**: el modelo más reciente por fecha se usa automáticamente en todos los flujos (local + CI). Ya no hay que cambiar `MODEL_VERSION` a mano.
- **Comando único de reentrenamiento**: `python utils/train_and_publish.py` → entrena vN+1, compara con el actual, registra en Neon y hace push. Si el nuevo es peor, NO lo publica (producción intacta).
- **Monitor de salud del modelo**: accuracy acumulado, accuracy reciente (30d), drift (diferencia), calibración por bucket de confianza (50-60%, 60-70%, 70%+). Persiste en Neon y se muestra en el dashboard.
- **Detección de drift**: alerta en el dashboard si el accuracy reciente cae >10 puntos vs el acumulado.
- **Comparación de versiones**: tabla en el modal Resultados con métricas de cada versión (accuracy, CV-AUC, muestras, features, fecha).
- **Retry con backoff** en la ingesta de stats (mitiga timeouts de stats.nba.com).
- **Caché de stats** en `data/stats_cache/` para que CI funcione aunque stats.nba.com bloquee IPs de nube.

## 6. Funcionalidades eliminadas

- **Reentrenamiento en CI** (workflow `train_model.yml`): se eliminó porque CI no tiene el caché de partidos históricos (`data/cache/*.parquet`, ~GBs, ignorado por git) y stats.nba.com bloquea IPs de nube → entrenaría con datos incompletos. El entrenamiento real ahora es local (`train_and_publish.py`).

## 7. Cambios arquitectónicos

- **Flujo de reentrenamiento**: CI (entrenador) → **Local (entrenador) + CI (verificador/registrador)**.
- **Selección de versión**: hardcodeada (`MODEL_VERSION`) → **auto-detección por fecha de modificación**.
- **Monitoreo**: sin monitoreo → **monitor de salud con drift + calibración persistido en Neon**.
- **Registro de versiones**: sin registro → **`model_registry` en Neon con métricas comparables**.

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- `modelos_ml.md` — documentar el nuevo flujo de reentrenamiento local + verificación CI, el registro de versiones y el monitor de drift.
- `github_actions.md` — documentar el nuevo `train_model.yml` (verificador) y el paso de monitor en `reconcile.yml`.
- `database.md` — nuevas tablas `model_health` y `model_registry`.
- `web_dashboard.md` — nuevos endpoints `/api/model_health` y `/api/model_registry`, tarjeta de salud y tabla de versiones.
- `ingestion.md` — retry con backoff y caché de stats en `nba_client.py`.
- `config.md` — `MODEL_VERSION` ya no se fija (auto-detección).
- `arquitectura.md` — actualizar el flujo de entrenamiento (local + CI verificador).

## 9. Riesgos detectados

- 🔴 **stats.nba.com bloquea IPs de GitHub Actions**: el entrenamiento en CI no puede descargar datos. Mitigado con caché de stats (`data/stats_cache/`) y moviendo el entrenamiento a local.
- 🔴 **CI no tiene el caché de partidos históricos** (`data/cache/*.parquet`): si se intentara entrenar en CI, el modelo sería de baja calidad. Mitigado: el workflow de CI ya no entrena, solo verifica/registra.
- 🟡 **Caché de stats puede quedar desactualizada**: `data/stats_cache/*.csv` es un snapshot de hoy. Se actualiza cada vez que el pipeline corre localmente o cuando la API responde. En CI, es válido para la temporada actual.
- 🟡 **El monitor de drift usa datos limitados**: con pocas predicciones resueltas (35 actualmente), el drift puede ser ruidoso. Mejorará con más datos acumulados.
- 🟢 **`train_and_publish.py` depende de que el usuario lo ejecute semanalmente**: es el comando único que el usuario pidió, pero requiere acción manual (no es 100% automático). Alternativa futura: un cron local o un runner self-hosted.

## 10. Recomendaciones de actualización para la Base de Conocimiento

1. **`ai/modulos/modelos_ml.md`**: documentar el flujo de reentrenamiento (`utils/train_and_publish.py`), el registro de versiones (`model_registry`) y el monitor de drift (`model_monitor`).
2. **`ai/modulos/github_actions.md`**: actualizar `train_model.yml` (verificador/registrador, no entrenador) y el paso de monitor en `reconcile.yml`.
3. **`ai/modulos/database.md`**: documentar las tablas `model_health` y `model_registry`.
4. **`ai/modulos/web_dashboard.md`**: documentar `/api/model_health`, `/api/model_registry`, la tarjeta de salud y la tabla de versiones.
5. **`ai/modulos/ingestion.md`**: documentar el retry con backoff y la caché de stats.
6. **`ai/arquitectura.md`**: actualizar el flujo de entrenamiento (local + CI verificador) y el monitoreo de drift.

---
**Nota de verificación (PSE):** 
- Auto-detección verificada: `_latest_model_version()` → v11 (el más reciente por fecha).
- Monitor verificado: detecta drift real (acc 51.4% vs reciente 33.3% → drift -18.1 ⚠️).
- Registry verificado: 11 versiones registradas en Neon con métricas.
- Endpoints verificados: `/api/model_health` y `/api/model_registry` responden desde Neon.
- Retry verificado: simulación de 3 fallos → éxito en el 3er intento.
- Caché de stats verificada: CSVs legibles (30 equipos, 56+12 columnas).
- `train_and_publish.py` en prueba (dry-run) al momento de escribir este informe.
