# Informe PSE → PKE — Bankroll editable: solo las apuestas pendientes se recalculan

**Fecha:** 2026-10-26
**Tarea solicitada por el usuario:** Poder cambiar el bankroll desde el modal de resultados, pero que SOLO las apuestas pendientes se recalculen con el nuevo bankroll. Las ya cerradas son historial y nunca deben cambiar.

---

## 1. Objetivo del cambio

Permitir al usuario definir su bankroll desde el modal de resultados y que:
- Las apuestas **pendientes** (sin resultado) se recalculen con el nuevo bankroll (½ Kelly) y se guarden.
- Las apuestas **resueltas** (historial) queden **inmutables** — el dinero ganado/perdido ya pasó.

## 2. Archivos modificados

- `web/app.py` — endpoints de bankroll, recálculo selectivo en backtests (`6426c88`).
- `web/static/index.html` — campo de bankroll + botón Actualizar en el modal (`a85c8f7`).

## 3. Archivos creados

- `data/bankroll.json` — persistencia del bankroll del usuario.

## 4. Archivos eliminados

Ninguno.

## 5. Funcionalidades agregadas

- `POST /api/bankroll` — guarda el bankroll del usuario.
- `GET /api/bankroll` — devuelve el guardado.
- **Recálculo selectivo de montos**:
  - **NBA**: se calculan los resultados reales (line_scores + Neon) ANTES del recálculo. Solo las apuestas sin resultado (pendientes) se recalculan con ½ Kelly. Las resueltas quedan intactas.
  - **BetPlay**: solo las apuestas sin `correct` (pendientes) se recalculan.
  - Normalización de game_ids (ceros iniciales) para comparar con line_scores/Neon.
- Frontend: campo de bankroll + botón ⟳ Actualizar en el modal (la UI sincroniza el bankroll del header).

## 6. Funcionalidades eliminadas

- El recálculo masivo (que alteraba el historial) del commit `a85c8f7`.

## 7. Cambios arquitectónicos

- El bankroll es persistente (`data/bankroll.json`) y tiene prioridad sobre `INITIAL_BANKROLL`.
- El journal distingue implícitamente resueltas vs pendientes: las resueltas conservan su `bankroll_snapshot` original; las pendientes se actualizan con el bankroll actual.
- **Inmutabilidad del historial**: los montos y el P&L de apuestas cerradas nunca cambian al modificar el bankroll.

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- `web_dashboard.md` — campo bankroll en modal, recálculo solo de pendientes.
- `value_detection.md` — montos ½ Kelly dinámicos solo para pendientes.
- `database.md` — journal con resueltas inmutables.

## 9. Riesgos detectados

- **Primera versión (`a85c8f7`) alteró el historial**: recalcularon todas las apuestas al cambiar el bankroll. Se corrigió con `6426c88` y el journal se restauró (resueltas a 500000). El usuario lo detectó y se corrigió a tiempo.
- **Detección de "resuelta" depende de line_scores/Neon**: si un partido pasó pero sus datos no llegaron, la apuesta se trata como pendiente y se recalcula — al llegar el resultado se resolverá con el monto actualizado.
- **BetPlay usa ½ Kelly derivado** (prob/cuota) porque su journal no guarda kelly_pct real.

## 10. Recomendaciones de actualización para la Base de Conocimiento

- Documentar el flujo de bankroll en `web_dashboard.md` (campo en modal → POST → persistencia → recálculo solo de pendientes).
- Documentar la inmutabilidad del historial: las apuestas resueltas conservan su bankroll_snapshot original.
- Verificar que el valor por defecto sea 500000 (INITIAL_BANKROLL) si `data/bankroll.json` no existe.