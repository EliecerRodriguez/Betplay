# Informe PSE → PKE — Eliminación total del módulo ATP

**Fecha:** 2026-10-21
**Tarea solicitada por el usuario:** Confirmar la eliminación total del sistema ATP del repositorio ("si" a la eliminación propuesta al finalizar la Fase 5).

---

## 1. Objetivo del cambio

Eliminar completamente el módulo ATP (código muerto sin referencia desde el dashboard, pipelines o MLOps) del repositorio, cumpliendo el pedido del usuario de que desaparezca cualquier registro de ATP.

## 2. Archivos modificados

- `.env.example` — eliminada variable `ATP_MODEL_VERSION=atp_v4`.
- `.env` (local, no versionado) — eliminada la misma variable.
- `database/cron_local.bat` — eliminada tarea "Betplay ATP" (19:00); añadida tarea "Betplay Liga" (07:00) con `run_betplay_pipeline.py`.
- `sports/__init__.py` — comentario actualizado (sports.nba, sports.betplay).
- `.gitignore` — agregado `data/betplay_cache/` (caché regenerable) y excepción `!models/betplay/*.joblib`.

## 3. Archivos creados

Ninguno.

## 4. Archivos eliminados

**Módulo completo `sports/atp/`** (27 archivos):
- `config/settings.py`, `ingestion/` (atp_client, co_odds_scraper, elo, h2h_client, historical_client, injuries_client, odds_client, rankings_client), `model/` (predictor, value_detector), `processing/` (features, recent_form), `database/models.py`, `models/` (current_elos.json, training_metrics*, .joblib).

**Scripts raíz ATP:**
- `run_atp_pipeline.py`, `build_atp_model.py`, `build_atp_model_v4.py`, `calibrate_atp_model.py`, `build_elos.py`, `espn_may21.py`.

**Scripts temporales de exploración (nunca versionados):**
- `_explore_kambi.py`, `_explore_live.py`, `_scan_kambi_ids.py`, `_inspect_corners.py`, `_build_betplay_dataset.py`.

## 5. Funcionalidades agregadas

- Modelos BetPlay v2 versionados por primera vez (`models/betplay/betplay_yellow_model_v2.joblib`, `betplay_corners_model_v2.joblib`).
- Tarea de cron local para el pipeline Liga BetPlay (07:00).

## 6. Funcionalidades eliminadas

- Todo el pipeline ATP (diario, modelos, Elo, cuotas, value bets, journal, dashboard, CI).
- Variable de entorno `ATP_MODEL_VERSION`.
- Tarea de cron "Betplay ATP".

## 7. Cambios arquitectónicos

- El proyecto queda con **2 deportes activos: NBA y Liga BetPlay**.
- `sports/` solo contiene `nba/` y `betplay/`.
- Verificado: **0 referencias** a `sports.atp`/`run_atp`/`atp_pipeline`/`atp_model` en todo el código ejecutable (`.py`, `.yml`, `.yaml`, `.bat`, `.sql`).
- El dashboard y los endpoints `/api/betplay/*` siguen funcionando (verificado HTTP 200 para NBA y BetPlay).

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- **`pipeline_atp.md`** — TODO el módulo quedó obsoleto. **Recomendación: eliminar**.
- `ingestion.md` — referencias a `build_elos.py` y Elos ATP ya no existen.
- `modelos_ml.md` — secciones ATP (build_atp_model*, calibrate, atp_model_v2) obsoletas.
- `database.md` — referencias a atp_odds/atp journal obsoletas.
- `github_actions.md` — sección "ATP Pipeline" obsoleta.
- `arquitectura.md` — diagrama de capas con ATP; líneas de run_atp_pipeline, build_atp_model, build_elos.
- `ai/README.md` y `README.md` raíz — líneas de horario ATP, comandos run_atp_pipeline/build_atp_model, tabla de archivos ATP.

## 9. Riesgos detectados

- **Documentación desactualizada**: la Base de Conocimiento (`ai/modulos/*`, `ai/arquitectura.md`, `ai/README.md`) y el `README.md` raíz aún describen el sistema ATP. El PKE debe actualizarla (no es responsabilidad del PSE).
- **Historial git**: el código ATP eliminado permanece en el historial (consultable si se necesita recuperar).
- **Parquets `data/betplay_cache/`** no versionados: se regeneran con `build_historical_dataset()`.

## 10. Recomendaciones de actualización para la Base de Conocimiento

- **Eliminar** `ai/modulos/pipeline_atp.md`.
- **Actualizar** `ai/modulos/database.md`, `ingestion.md`, `modelos_ml.md`, `github_actions.md` quitando secciones ATP.
- **Actualizar** `ai/arquitectura.md` y `ai/README.md` con la arquitectura nueva (NBA + BetPlay, sin ATP).
- **Actualizar** `README.md` raíz: quitar comandos ATP y actualizar horarios.
- **Documentar** que los modelos BetPlay v2 se versionan en `models/betplay/` y que `data/betplay_cache/` es caché regenerable.