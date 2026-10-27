# Informe PSE → PKE — Eliminación del límite de 5 apuestas diarias

**Fecha:** 2026-10-26
**Tarea solicitada por el usuario:** Quitar el límite de 5 apuestas máximas por día.

---

## 1. Objetivo del cambio

Eliminar el tope de `MAX_BETS_PER_DAY=5` para que el sistema registre TODAS las value bets detectadas, sin descartar ninguna por límite diario.

## 2. Archivos modificados

- `web/app.py` — funciones `_log_bets_to_journal` (NBA) y `_log_betplay_bets_to_journal` (BetPlay).
- `config/settings.py` — eliminada variable `MAX_BETS_PER_DAY`.
- `sports/betplay/config/settings.py` — eliminado re-export de `MAX_BETS_PER_DAY`.
- `.env` (local, no versionado) — eliminada la variable.

## 3. Archivos creados

Ninguno.

## 4. Archivos eliminados

Ninguno.

## 5. Funcionalidades agregadas

- Todas las value bets detectadas se registran en el journal (antes solo las 5 mejores por día).
- Se mantiene el orden por confianza (mayor prob primero) para el cálculo de montos.

## 6. Funcionalidades eliminadas

- Límite de 5 apuestas diarias (`MAX_BETS_PER_DAY`).
- Lógica de `slots_used`/`max_new`/`candidates[:max_new]` en ambos journals.

## 7. Cambios arquitectónicos

- La gestión de riesgo ahora depende SOLO de `MAX_BANKROLL_EXPOSURE` (25% del bankroll por día, repartido entre todas las apuestas). Sin tope de cantidad, el presupuesto se divide entre más apuestas → montos individuales menores.
- **Nota de riesgo**: sin tope de cantidad, si hay muchas value bets en un día, el monto por apuesta baja (presupuesto fijo repartido). El bankroll semanal sigue protegiendo contra pérdidas grandes.

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- `web_dashboard.md` — comportamiento del journal (ya no hay límite diario).
- `database.md` — journal de apuestas sin tope.

## 9. Riesgos detectados

- **Más apuestas = más exposición total**: aunque el presupuesto diario es fijo (25% del bankroll), más apuestas significa más comisiones/ruido. El usuario lo pidió explícitamente.
- **Montos más pequeños por apuesta**: con el presupuesto repartido entre más apuestas, cada una recibe menos capital. El P&L por apuesta será menor pero la diversificación mayor.

## 10. Recomendaciones de actualización para la Base de Conocimiento

- Actualizar `web_dashboard.md` y `database.md`: el journal ya no tiene límite diario; la protección es solo `MAX_BANKROLL_EXPOSURE` (25%).
- Documentar que el presupuesto diario se reparte entre todas las value bets detectadas.