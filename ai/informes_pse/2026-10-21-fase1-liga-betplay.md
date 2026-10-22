# Informe PSE → PKE — Fase 1: Sistema Liga BetPlay (reemplaza ATP)

**Fecha:** 2026-10-21
**Tarea solicitada por el usuario:** Comenzar la Fase 1 del sistema de predicción de fútbol colombiano "Liga BetPlay", reemplazando completamente el módulo ATP. El modelo maestro es el de la NBA. Se predicen mercados 50/50 (tarjetas amarillas, córners), NO resultados.

## 1. Objetivo del cambio

Crear la base del módulo `sports/betplay/` (fútbol Liga BetPlay) clonando el patrón arquitectónico de la NBA, con foco en:
1. **Fuente de datos**: ESPN API (gratuita, sin clave) — confirmada y probada.
2. **Mercados 50/50**: Over/Under tarjetas amarillas y córners (no resultados).
3. **Reemplazo de ATP**: el módulo ATP será sustituido por BetPlay en fases sucesivas.

## 2. Archivos modificados

Ninguno de producción. Solo creación de archivos nuevos (ver sección 3).

## 3. Archivos creados

| Archivo | Descripción |
|---------|-------------|
| `sports/betplay/__init__.py` | Paquete del módulo BetPlay. |
| `sports/betplay/config/settings.py` | Config: league `col.1`, ESPN base URL, caché, Kambi, modelo. Re-exporta config global. |
| `sports/betplay/config/__init__.py` | Paquete config. |
| `sports/betplay/ingestion/__init__.py` | Paquete ingesta. |
| `sports/betplay/ingestion/espn_client.py` | Cliente ESPN: `get_daily_games(date)` (scoreboard) y `get_match_summary(event_id)` (tarjetas, córners, faltas, posesión, tiros, pases, centros, entradas + forma reciente). Confirma los campos: `yellowCards`, `redCards`, `wonCorners`, `foulsCommitted`, `possessionPct`, `totalShots`, `shotsOnTarget`, `totalPasses`, `passPct`, `totalCrosses`, `totalTackles`. |
| `sports/betplay/ingestion/historical_client.py` | `build_historical_dataset()` — itera calendario (scoreboard + summary), cachea por fecha en `data/betplay_cache/*.parquet`, devuelve dataset con targets `total_yellow_cards` y `total_corners`. |
| `sports/betplay/processing/__init__.py` | Paquete processing (Fase 2). |
| `sports/betplay/model/__init__.py` | Paquete model (Fase 2). |
| `sports/betplay/database/__init__.py` | Paquete database (Fase 2). |
| `_build_betplay_dataset.py` | Script temporal para generar dataset completo (se eliminará). |

## 4. Archivos eliminados

Ninguno (ATP se eliminará en fases posteriores, cuando BetPlay lo sustituya por completo).

## 5. Funcionalidades agregadas

- **Ingesta desde ESPN**: partidos del día + stats completas por partido de la Liga BetPlay (gateway `col.1`), con retry y rate-limit.
- **Dataset histórico**: construcción automática de `(2025 → hoy)` con tarjetas y córners por partido, cacheado en parquet.
- **Targets 50/50 confirmados**: `total_yellow_cards` (Over 4.5 en 57.1% de una muestra de 14 partidos) y `total_corners` (Over 9.5 en 35.7%).

## 6. Funcionalidades eliminadas

Ninguna todavía (eliminación de ATP planificada para Fases 3-4).

## 7. Cambios arquitectónicos

- Nuevo módulo `sports/betplay/` con la misma estructura por capas que `sports/nba/` (modelo maestro):
  - `config/` → settings
  - `ingestion/` → ESPN client + historical client (+ futuros: Kambi, Elo)
  - `processing/` → features (Fase 2)
  - `model/` → predictor (reutilizará `train()` de NBA, Fase 2)
  - `database/` → repositorio (Fase 2)

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- `ingestion.md` — nueva fuente de datos ESPN para Liga BetPlay.
- `pipeline_atp.md` — será reemplazado por `pipeline_betplay.md` (cuando se elimine ATP).
- `arquitectura.md` — el diagrama de capas cambiará (ATP → BetPlay).
- `modelos_ml.md` — se añadirán modelos de tarjetas/córners.
- `value_detection.md` — mismo patrón que NBA, aplicado a O/U tarjetas/córners.
- `web_dashboard.md` — la pestaña ATP será reemplazada por BetPlay (Fase 4).

## 9. Riesgos detectados

- 🟡 **Forma reciente**: ESPN no expone `form` en scoreboard; se extrae de `lastFiveGames` en el summary (verificado a medias — puede requerir ajuste fino del parseo).
- 🟡 **Línea de córners**: Over 9.5 en 35.7% de la muestra — la línea óptima podría ser 8.5. Se validará con el dataset completo.
- 🟢 **Volumen de datos**: ~2-4 partidos/día de Liga BetPlay (vs 8-12 NBA). El dataset 2025-2026 tendrá ~300-400 partidos — suficiente para un primer modelo, madura con el tiempo.
- 🟢 **Kambi**: el scraper de cuotas (adaptar de ATP) requiere verificar los `betOfferType` de tarjetas/córners en Betplay (Fase de exploración).

## 10. Recomendaciones de actualización para la Base de Conocimiento

1. Crear `ai/modulos/pipeline_betplay.md` documentando la arquitectura del nuevo módulo.
2. Actualizar `ai/arquitectura.md` para reflejar el módulo BetPlay.
3. Documentar en `ingestion.md` la fuente ESPN (`col.1`) y sus campos.
4. Cuando se elimine ATP: mover `pipeline_atp.md` a histórico y actualizar el diagrama de capas.

---
**Nota de verificación (PSE):**
- ✅ `get_daily_games('2026-10-21')` devuelve 2 partidos correctamente.
- ✅ `get_match_summary('401878012')` devuelve tarjetas totales (3), córners (9) y todas las stats de contexto.
- ✅ `build_historical_dataset(7 días)` → 14 partidos con targets correctos.
- ✅ Sin errores de compilación en los archivos nuevos.
- 🔄 Generación del dataset completo (2025→hoy) en progreso al momento de escribir.
