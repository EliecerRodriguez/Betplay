# Informe PSE → PKE — Fase 3: Pipeline diario Liga BetPlay

**Fecha:** 2026-10-21
**Tarea solicitada por el usuario:** Continuar con la Fase 3 del módulo Liga BetPlay (pipeline diario de predicción y detección de value bets).

---

## 1. Objetivo del cambio

Completar la Fase 3 del módulo `sports/betplay/`: orquestar el flujo diario completo (partidos ESPN → features → predicción Over/Under → cuotas Kambi → value bets) y dejarlo operativo con datos reales del 2026-10-22.

## 2. Archivos modificados

- `sports/betplay/ingestion/kambi_client.py` — propagación de nombres de equipos desde prepacks al consolidado de cuotas (clave para unir con predicciones ESPN).
- `sports/betplay/model/value_detector.py` — matching por **nombre de equipo normalizado** (ESPN ↔ Kambi) en lugar de por ID (los IDs de ambas fuentes son incompatibles); guardas para columnas ausentes (`cards_line`).

## 3. Archivos creados

- `run_betplay_pipeline.py` — orquestador diario (clon del patrón NBA): partidos del día → promedios por equipo → features → predicción → cuotas → value bets → CSV.
- `sports/betplay/ingestion/kambi_client.py` (creado en esta fase, commit anterior) — cuotas de córners/tarjetas vía prepacks + betoffer.
- `sports/betplay/model/value_detector.py` (creado en esta fase) — fórmula `value = prob_modelo × cuota − 1` (idéntica a NBA).

## 4. Archivos eliminados

Ninguno.

## 5. Funcionalidades agregadas

- **Pipeline diario ejecutable**: `python run_betplay_pipeline.py [--date YYYY-MM-DD] [--no-save]`.
- **Ruta rápida de promedios**: usa `data/betplay_cache/dataset_completo.csv` si existe (evita re-descargar ~280 fechas de ESPN en cada ejecución; el histórico completo tarda >5 min).
- **Matching ESPN ↔ Kambi por nombre normalizado**: resuelve "Atlético Nacional S.A" (Kambi) ↔ "Atlético Nacional" (ESPN).
- **Outputs**: `output/betplay_predictions.csv`, `output/betplay_odds.csv`, `output/betplay_value_bets.csv` (upsert por clave).

## 6. Funcionalidades eliminadas

Ninguna.

## 7. Cambios arquitectónicos

- El módulo BetPlay replica el patrón del módulo NBA (ingestion → processing → model → value_detector → orquestador), sin tocar el código NBA.
- El pipeline usa el CSV consolidado como fuente de promedios por equipo (misma estrategia que `train_betplay_model.py` con `--from-csv`).

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- `ingestion.md` — nueva fuente Kambi (prepacks + betoffer) y ESPN (scoreboard/summary) para fútbol colombiano.
- `modelos_ml.md` — modelos BetPlay yellow/corners v2 y su uso en pipeline.
- `pipeline_nba.md` — el patrón NBA se clonó para BetPlay (`run_betplay_pipeline.py`).
- `value_detection.md` — fórmula de value bet reutilizada para BetPlay.

## 9. Riesgos detectados

- **Cuotas de tarjetas ausentes**: Kambi no expone mercado de tarjetas amarillas para los partidos actuales (solo córners). El value_detector lo maneja con guardas, pero el mercado de tarjetas queda sin cuotas hasta que Kambi lo ofrezca.
- **IDs incompatibles ESPN/Kambi**: resuelto por nombre normalizado; si ESPN cambia nombres de equipos, el matching puede fallar silenciosamente.
- **Modelo aún sin edge**: 0 value bets detectados (prob modelo 0.43 vs cuota 2.05 → value −0.12). Esperado en Fase 3; mejorará con más datos.
- **Línea de córners**: Kambi mueve la línea (6.5/8.5/9.5 según el momento); el cliente elige la más cercana a 9.5, pero el modelo predice sobre 9.5 fija.

## 10. Recomendaciones de actualización para la Base de Conocimiento

- Documentar en `ai/modulos/ingestion.md` los endpoints Kambi descubiertos (prepacks shapegamescloud + betoffer) y el flujo de cuotas de córners.
- Documentar en `ai/modulos/pipeline_nba.md` (o nuevo `pipeline_betplay.md`) el orquestador diario y su dependencia del CSV consolidado.
- Actualizar `ai/README.md` con el estado de Fase 3 completada (pendiente Fase 4: dashboard, Fase 5: MLOps).