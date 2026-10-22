# Informe PSE → PKE — Fix: apuestas NBA quedaban pendientes para siempre

**Fecha:** 2026-10-22
**Tarea solicitada por el usuario:** Investigar por qué partidos NBA que ya terminaron seguían mostrando resultados en "pendiente" en el dashboard.

---

## 1. Objetivo del cambio

Corregir el bug por el cual las apuestas NBA quedaban en estado "pendiente" indefinidamente aunque el partido ya hubiera terminado.

## 2. Archivos modificados

- `web/app.py` — función `_fetch_and_reconcile_results()`.

## 3. Archivos creados

Ninguno.

## 4. Archivos eliminados

Ninguno.

## 5. Funcionalidades agregadas

- El reconcile ahora **re-descarga line_scores** para fechas que ya estaban en `line_scores.csv` pero cuyos game_ids pendientes no tienen datos.

## 6. Funcionalidades eliminadas

- Variable `existing_ls_dates` (ya no se usa).

## 7. Cambios arquitectónicos

**Bug corregido (raíz):**
- Antes: `if date_str in existing_ls_dates: continue` — si una fecha ya estaba en `line_scores.csv`, se saltaba para siempre. Si el backtest se consultaba mientras un partido estaba en curso, `get_line_scores` devolvía vacío/parcial, la fecha quedaba registrada, y cuando el partido terminaba el reconcile nunca re-descargaba → apuestas pendientes eternas.
- Ahora: se comparan los **game_ids pendientes** de cada fecha contra los que ya tienen line_scores. Si faltan, se re-descarga la fecha completa. Solo se salta si todos los pendientes ya tienen datos.

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- `database.md` — flujo de reconciliación predicciones → resultados.
- `web_dashboard.md` — comportamiento del backtest (resultados en vivo).

## 9. Riesgos detectados

- **Re-descarga repetida**: si un partido de hoy no termina, el reconcile intentará descargar su fecha en cada request del backtest (hasta que termine). Es aceptable (1 request extra por fecha pendiente) pero podría optimizarse con un TTL.
- **Las apuestas de hoy (2026-10-22) siguen pendientes** hasta que los partidos terminen — comportamiento correcto.

## 10. Recomendaciones de actualización para la Base de Conocimiento

- Documentar en `database.md` que el reconcile ahora verifica game_ids faltantes (no solo fechas) para decidir re-descarga.
- Nota: el backtest actual muestra 42 apuestas resueltas (26W/16L, 61.9% win rate, ROI +30.6%) y 4 pendientes (partidos de hoy en curso).