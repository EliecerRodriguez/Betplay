# Informe PSE → PKE — Bankroll editable y persistente desde el modal de resultados

**Fecha:** 2026-10-26
**Tarea solicitada por el usuario:** Poder escribir el bankroll manualmente en la pestaña de resultados (con botón Actualizar), que las sugerencias de apuesta se recalculen con ese bankroll, se guarden y persistan hasta que el usuario lo cambie.

---

## 1. Objetivo del cambio

Permitir al usuario definir su propio bankroll desde el modal de resultados. Al cambiarlo, los montos de las apuestas (½ Kelly) se recalculan, se guardan en el journal y el sistema trabaja con ese bankroll hasta que el usuario lo modifique.

## 2. Archivos modificados

- `web/app.py` — endpoints de bankroll, recálculo de montos en backtests.
- `web/static/index.html` — campo de bankroll + botón Actualizar en el modal.

## 3. Archivos creados

- `data/bankroll.json` — persistencia del bankroll elegido por el usuario (creado en runtime).

## 4. Archivos eliminados

Ninguno.

## 5. Funcionalidades agregadas

- **Backend**:
  - `POST /api/bankroll?bankroll=N` — guarda el bankroll en `data/bankroll.json`.
  - `GET /api/bankroll` — devuelve el guardado (o el de config).
  - `_get_weekly_bankroll()` — prioriza el bankroll guardado por el usuario.
  - `get_backtest()` y `get_betplay_backtest()` — usan el bankroll guardado y **recalculan los montos** de las apuestas (½ Kelly) si el `bankroll_snapshot` del journal difiere del actual, persistiendo `bet_amount`, `potential_gain` y `bankroll_snapshot`.
- **Frontend**:
  - Modal de resultados: campo "Bankroll $" editable + botón "⟳ Actualizar".
  - `saveBankrollAndReload()` — guarda el bankroll (POST) y recarga el backtest.
  - `loadSavedBankroll()` — carga el bankroll persistente al iniciar la app.
  - El campo de bankroll del header se sincroniza con el del modal.

## 6. Funcionalidades eliminadas

Ninguna.

## 7. Cambios arquitectónicos

- El bankroll ya no es solo un parámetro del frontend: ahora hay una **fuente de verdad persistente** (`data/bankroll.json`) que tiene prioridad sobre `INITIAL_BANKROLL` y sobre el parámetro del frontend.
- Los montos de las apuestas ya no son inmutables: se recalculan con ½ Kelly cada vez que el bankroll cambia, y el journal se actualiza.

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- `web_dashboard.md` — nuevo campo de bankroll en el modal, persistencia.
- `value_detection.md` — montos recalculados dinámicamente con el bankroll del usuario.

## 9. Riesgos detectados

- **Recálculo retroactivo**: al cambiar el bankroll, TODAS las apuestas del journal (incluidas las ya resueltas) se recalculan con el nuevo bankroll. Esto cambia el P&L histórico. Es el comportamiento pedido por el usuario ("se modifiquen mis apuestas y se guarden"), pero significa que el historial refleja el bankroll actual, no el que había en cada momento.
- **`data/bankroll.json` no está versionado** (en `data/`, que está en `.gitignore` parcialmente): si se borra, el sistema vuelve a `INITIAL_BANKROLL`.
- **BetPlay usa ½ Kelly derivado** (prob/cuota) porque su journal no guarda `kelly_pct` real.

## 10. Recomendaciones de actualización para la Base de Conocimiento

- Documentar en `web_dashboard.md` el nuevo flujo de bankroll (campo en modal → POST → persistencia → recálculo de montos).
- Documentar que `data/bankroll.json` es la fuente de verdad del bankroll del usuario.
- Nota: el recálculo retroactivo de montos es intencional (pedido por el usuario).