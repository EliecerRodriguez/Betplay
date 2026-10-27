# Informe PSE → PKE — Eliminación del presupuesto fijo del 25% (montos por ½ Kelly)

**Fecha:** 2026-10-26
**Tarea solicitada por el usuario:** Quitar también la parte del presupuesto fijo del 25% (MAX_BANKROLL_EXPOSURE).

---

## 1. Objetivo del cambio

Eliminar el presupuesto global fijo (25% del bankroll repartido entre las apuestas del día) y dimensionar cada apuesta individualmente por su propio edge usando **½ Kelly**.

## 2. Archivos modificados

- `web/app.py` — funciones `_log_bets_to_journal` (NBA) y `_log_betplay_bets_to_journal` (BetPlay).

## 3. Archivos creados

Ninguno.

## 4. Archivos eliminados

Ninguno.

## 5. Funcionalidades agregadas

- **NBA**: `bet_amount = bankroll × kelly_pct / 200` (½ Kelly) — consistente con lo que el dashboard ya mostraba como "Apostar (½ Kelly)".
- **BetPlay**: ½ Kelly calculado desde la probabilidad del modelo y la cuota: `raw_kelly = (p − 1/odds) / (odds − 1)`, se usa la mitad. BetPlay no calcula kelly_pct real en el value_detector, así que se deriva del edge.
- Cada apuesta se dimensiona por su propio valor esperado, sin presupuesto global repartido.

## 6. Funcionalidades eliminadas

- `MAX_BANKROLL_EXPOSURE` (25% del bankroll por día) como mecanismo de dimensionamiento.
- Variables `max_exposure` sin uso en ambos journals.

## 7. Cambios arquitectónicos

- **Antes**: presupuesto diario = bankroll × 25%, repartido entre todas las apuestas (más apuestas = montos menores).
- **Ahora**: cada apuesta = ½ Kelly individual. Una apuesta con más edge recibe más capital; una con edge pequeño recibe poco. No hay tope global de exposición diaria.

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- `value_detection.md` — el journal ya no usa presupuesto fijo; usa ½ Kelly.
- `web_dashboard.md` — montos del journal ahora coinciden con lo que muestra la UI (½ Kelly).

## 9. Riesgos detectados

- **Sin tope de exposición diaria**: si hay muchas value bets con edge alto el mismo día, la exposición total puede superar el 25% anterior. El usuario lo pidió explícitamente (quiere apostar sin límites artificiales).
- **BetPlay usa ½ Kelly aproximado** (derivado de prob/cuota) porque su value_detector no calcula kelly_pct real. Si se quiere exactitud, habría que añadir kelly_pct al value_detector de BetPlay.
- **Kelly ½ puede dar montos muy pequeños** para edges marginales (value apenas > 0) → apuestas de $1 mínimo.

## 10. Recomendaciones de actualización para la Base de Conocimiento

- Actualizar `value_detection.md`: el dimensionamiento es ½ Kelly individual, no presupuesto global.
- Documentar que `MAX_BANKROLL_EXPOSURE` ya no se usa en el journal (queda en config como referencia histórica o se elimina).
- Considerar añadir `kelly_pct` real al value_detector de BetPlay para consistencia total.