# Informe PSE → PKE — Fase 5: MLOps Liga BetPlay

**Fecha:** 2026-10-21
**Tarea solicitada por el usuario:** Continuar con la Fase 5 del módulo Liga BetPlay (sistema de mejora continua: monitoreo de drift, registro de versiones y publicación automática).

---

## 1. Objetivo del cambio

Clonar el sistema MLOps de la NBA para la Liga BetPlay: monitor de salud (drift + calibración), registro de versiones en Neon, y comando único de entrenamiento+publicación. Integrar la salud del modelo en el dashboard y en CI.

## 2. Archivos modificados

- `web/app.py` — endpoints `/api/betplay/model_health` y `/api/betplay/model_registry`.
- `web/static/index.html` — card de salud del modelo BetPlay (drift + buckets de calibración).
- `.github/workflows/atp_pipeline.yml` — **eliminado** (ATP fuera del proyecto).

## 3. Archivos creados

- `utils/betplay_model_monitor.py` — monitor de salud (clon de `model_monitor.py` NBA).
- `utils/betplay_model_registry.py` — registro de versiones en Neon (clon de `model_registry.py`).
- `utils/train_and_publish_betplay.py` — entrenar+comparar+registrar+publicar (clon de `train_and_publish.py`).
- `.github/workflows/betplay_pipeline.yml` — pipeline diario BetPlay + monitor en CI.

## 4. Archivos eliminados

- `.github/workflows/atp_pipeline.yml` (workflow ATP obsoleto).

## 5. Funcionalidades agregadas

- **Monitor de salud BetPlay** (`betplay_model_monitor.py`):
  - Lee `output/betplay_bet_journal.csv` (apuestas con resultado reconciliado).
  - Accuracy acumulado y reciente (30 días) por versión de modelo.
  - Drift = accuracy_reciente − accuracy_acumulado (umbral −10 pts).
  - Calibración por bucket de confianza (50-60%, 60-70%, 70%+).
  - Persiste en Neon tabla `betplay_model_health`.
- **Registro de versiones** (`betplay_model_registry.py`):
  - Tabla Neon `betplay_model_registry` con UNIQUE (version, model_type).
  - `ON CONFLICT DO UPDATE` para re-registrar sin duplicar.
  - Modelos v2 actuales registrados: yellow acc=0.5745 cv=0.5225, corners acc=0.4043 cv=0.4674 (n=233).
- **Publicación automática** (`train_and_publish_betplay.py`):
  - Entrena yellow/corners, compara CV-AUC/accuracy con el actual, registra en Neon y publica en GitHub solo si es mejor (o `--force`).
- **Dashboard**: card "Modelo BetPlay activo" con accuracy, drift y buckets.
- **CI**: workflow `betplay_pipeline.yml` (cron 09:00 UTC) que construye el dataset histórico si falta, ejecuta el pipeline diario y corre el monitor.

## 6. Funcionalidades eliminadas

- Workflow ATP de CI (`atp_pipeline.yml`).

## 7. Cambios arquitectónicos

- El sistema MLOps de la NBA se clonó con prefijo `betplay_` (monitor, registry, train_and_publish) sin tocar el código NBA.
- El monitor BetPlay usa el **journal de apuestas** como fuente de verdad (a diferencia del NBA que usa `predictions.csv`/Neon), porque el pipeline BetPlay aún no persiste predicciones en Neon.
- CI: nuevo workflow independiente para BetPlay (no encadenado al reconcile NBA).

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- `modelos_ml.md` — modelos BetPlay yellow/corners v2 y su ciclo MLOps.
- `github_actions.md` — nuevo workflow `betplay_pipeline.yml`, eliminado `atp_pipeline.yml`.
- `database.md` — nuevas tablas Neon `betplay_model_health` y `betplay_model_registry`.
- `pipeline_nba.md` — patrón MLOps clonado para BetPlay.

## 9. Riesgos detectados

- **Monitor sin datos hasta que haya apuestas con resultado**: el journal BetPlay está vacío (0 value bets hasta ahora), así que `model_health` devuelve "sin datos". La card del dashboard se oculta correctamente. Se poblará cuando el modelo empiece a detectar value bets y se reconcilien resultados.
- **CI construye el dataset desde ESPN**: `data/betplay_cache/dataset_completo.csv` no está en git; el workflow lo construye (puede tardar varios minutos). Alternativa futura: subir el CSV como artefacto o a un bucket.
- **train_and_publish_betplay.py** entrena con el CSV consolidado local; en CI no hay CSV, por lo que el reentrenamiento automático debe ejecutarse localmente (como el NBA).
- **El módulo `sports/atp/` y scripts ATP siguen en el repo** (código muerto). Pendiente de eliminación total con confirmación del usuario.

## 10. Recomendaciones de actualización para la Base de Conocimiento

- Documentar en `ai/modulos/modelos_ml.md` el ciclo MLOps BetPlay (monitor, registry, train_and_publish).
- Actualizar `ai/modulos/github_actions.md` con el workflow BetPlay y la eliminación del ATP.
- Documentar las tablas `betplay_model_health` y `betplay_model_registry` en `ai/modulos/database.md`.
- Decidir el destino del módulo `sports/atp/` (eliminación total pendiente).