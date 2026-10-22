# Informe PSE → PKE — Fase 4: Dashboard Liga BetPlay + eliminación de ATP

**Fecha:** 2026-10-21
**Tarea solicitada por el usuario:** Continuar con la Fase 4 del módulo Liga BetPlay (integrar módulo en el dashboard web) y eliminar por completo el sistema ATP ("que desaparezca cualquier registro de ATP").

---

## 1. Objetivo del cambio

Reemplazar la pestaña ATP del dashboard por la nueva pestaña ⚽ Liga BetPlay con predicciones Over/Under de córners y tarjetas, cuotas de Betplay (Kambi), value bets, registro de apuestas y resultados — clonando el patrón NBA/ATP. Eliminar todo el código ATP del backend y frontend.

## 2. Archivos modificados

- `web/app.py` — añadidos endpoints BetPlay; eliminadas todas las secciones ATP.
- `web/static/index.html` — pestaña BetPlay; eliminados estilos/funciones ATP.

## 3. Archivos creados

Ninguno (todo es modificación).

## 4. Archivos eliminados (código)

Código ATP eliminado de `web/app.py`:
- `_ATP_LEVEL_LABELS`, `_run_atp()` (pipeline ATP → JSON dashboard)
- `_log_atp_bets_to_journal()`, `_fetch_atp_completed_from_espn()`, `_fetch_and_reconcile_atp_results()`
- Endpoints `/api/atp/analysis`, `/api/atp/cache`, `/api/atp/reconcile`, `/api/atp/backtest`, `/api/atp/journal/bet`
- Caché `_ATP_CACHE`/`_ATP_CACHE_TTL`
- En `index.html`: `loadATP/renderATP/renderAtpStats/renderAtpMatches/atpCard/setLoading(ATP)/loadAtpBacktest/renderAtpBacktest/deleteAtpBet` y todas las clases CSS `atp-*`.

## 5. Funcionalidades agregadas

- **Endpoints nuevos**:
  - `GET /api/betplay/analysis` — ejecuta `run_betplay_pipeline` y devuelve matches con predicciones (over_yellow_prob, over_corners_prob), cuotas y value bets.
  - `DELETE /api/betplay/cache` — limpia caché.
  - `GET /api/betplay/reconcile` — cruza apuestas pendientes con resultados reales ESPN.
  - `GET /api/betplay/backtest` — historial con P&L, ROI, win_rate, por modelo y por día.
  - `DELETE /api/betplay/journal/bet` — eliminar apuesta.
- **Journal BetPlay**: `output/betplay_bet_journal.csv` con límite `MAX_BETS_PER_DAY` y exposición `MAX_BANKROLL_EXPOSURE` (mismos límites NBA).
- **Reconciliación** `_reconcile_betplay_results()`: usa `get_match_summary()` de ESPN para obtener `total_corners`/`total_yellow_cards` reales del partido (game_id de ESPN) y determina over/under vs línea.
- **Frontend**: pestaña ⚽ BetPlay con cards (equipos, % Over córners/tarjetas, cuotas Kambi línea real, value bets), y modal Resultados con historial completo (gráfico, resumen, por modelo, diario, chips de mes).

## 6. Funcionalidades eliminadas

- Todo el sistema ATP del dashboard (pestaña, endpoints, pipeline, journal, reconcile, backtest, eliminación de apuestas).

## 7. Cambios arquitectónicos

- El dashboard ahora soporta NBA + Liga BetPlay (2 deportes).
- El patrón de la NBA (pipeline → journal → reconcile → backtest) se clonó para BetPlay sin tocar el código NBA.
- Se eliminó el import y uso de `sports.atp.*` en `web/app.py`; el módulo `sports/atp/` queda intacto en el repo pero sin referencias desde el dashboard.

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- `web_dashboard.md` — nueva pestaña Liga BetPlay, endpoints nuevos, ATP retirado.
- `pipeline_atp.md` — obsoleto (módulo ATP fuera del dashboard). Decidir si se conserva o archiva.
- `value_detection.md` — value bets BetPlay (matching por nombre de equipo).
- `database.md` / `ingestion.md` — journal BetPlay y reconcile desde ESPN summary.

## 9. Riesgos detectados

- **Reconciliación BetPlay depende de ESPN summary**: si ESPN no tiene stats completas (partido sin boxscore), la apuesta queda pendiente hasta que las publique.
- **Matching por nombre de equipo**: ESPN ("Atlético Nacional") vs Kambi ("Atlético Nacional S.A") unido por normalización; si ESPN cambia nombres, puede fallar silenciosamente.
- **Cuotas de tarjetas ausentes en Kambi**: solo córners disponibles actualmente; el mercado de tarjetas esperará cuotas.
- **El módulo `sports/atp/` aún existe en el repo** (código muerto sin referencia). Se recomienda su eliminación total en una fase posterior (archivos `run_atp_pipeline.py`, `build_atp_model*`, `sports/atp/`, `calibrate_atp_model.py`, etc.) con confirmación del usuario.
- **`web/app.py`**: al trabajar en un solo commit hubo restauración intermedia; el estado final fue verificado con `get_errors` (sin errores) y pruebas en navegador.

## 10. Recomendaciones de actualización para la Base de Conocimiento

- Actualizar `ai/modulos/web_dashboard.md`: documentar endpoints BetPlay, estructura de respuesta JSON, y nota de ATP retirado.
- Actualizar `ai/arquitectura.md`: diagrama de capas con BetPlay (ingestion ESPN+Kambi, model XGBoost, dashboard 2 deportes).
- Decidir destino del módulo `pipeline_atp.md` (archivar o eliminar).
- Documentar `output/betplay_bet_journal.csv` como nuevo artefacto del pipeline diario.