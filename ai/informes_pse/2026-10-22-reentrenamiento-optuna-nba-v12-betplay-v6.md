# Informe PSE → PKE — Reentrenamiento completo con Optuna (NBA v12 + BetPlay v6)

**Fecha:** 2026-10-22
**Tarea solicitada por el usuario:** Ejecutar el comando orquestador con Optuna para reentrenar todo (NBA + BetPlay) y verificar el resultado.

---

## 1. Objetivo del cambio

Ejecutar el ciclo MLOps completo con Optuna en ambos deportes mediante el orquestador, y verificar que el criterio de promoción estricto funciona correctamente.

## 2. Archivos modificados

- `data/stats_cache/team_stats_2025-26.csv` — stats NBA actualizadas durante el entrenamiento.
- `models/current_elos.json` — Elos NBA recalculados (62 equipos).

## 3. Archivos creados

- `models/betplay/betplay_yellow_model_v6.joblib` — acc=0.646, cv=0.528, n=2,537.
- `models/betplay/betplay_corners_model_v6.joblib` — acc=0.610, cv=0.519, n=2,537.

## 4. Archivos eliminados

Ninguno.

## 5. Funcionalidades agregadas

- **NBA v12 entrenado con Optuna 50 trials** (CV-AUC=0.6878, acc=0.6745, n=6,771) pero **NO publicado**: no superó al v11 (CV-AUC=0.7043, acc=0.7078). El criterio estricto (`>`) lo rechazó correctamente. Registrado en Neon como histórico, archivo eliminado, v11 intacto en producción.
- **BetPlay v6 con Optuna 50 trials** (ambos publicados):
  - Yellow: acc 64.4% → **64.6%** (cv 0.530 → 0.528)
  - Corners: acc 60.0% → **61.0%** (cv 0.503 → **0.519**)
- **El orquestador reportó éxito**: "Reentrenamiento completo: NBA + BetPlay finalizados sin errores".

## 6. Funcionalidades eliminadas

Ninguna.

## 7. Cambios arquitectónicos

- Confirmado el flujo completo: `train_and_publish_all.py --optimize 50 --optimize-betplay 50` ejecuta NBA → BetPlay en secuencia, cada uno con Optuna, comparación estricta, registro en Neon y publicación condicional.
- El criterio estricto (`>` en vez de `>=`) evitó publicar el NBA v12 (peor) y el BetPlay v6 solo se publicó por mejora real.

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- `modelos_ml.md` — modelos BetPlay v6 (yellow acc=0.646/cv=0.528, corners acc=0.610/cv=0.519, n=2,537).
- `database.md` — registros v6 en betplay_model_registry, v12 en model_registry (no promovido).

## 9. Riesgos detectados

- **NBA v12 no promovido**: el modelo con más datos (6,771 vs 4,086) salió peor en CV que v11. Posible causa: el v11 se entrenó con un dataset más limpio o con hiperparámetros mejores; el v12 con 50 trials de Optuna no logró superarlo. No es un error — es el sistema protegiendo producción.
- **Yellow v6 mejora marginal** (64.4 → 64.6): Optuna encontró params casi equivalentes. El techo del modelo yellow parece estar cerca con las features actuales.
- **Corners v6 mejoró claramente** (cv 0.503 → 0.519): Optuna encontró una configuración mejor (n_estimators=212, depth=3, lr=0.076).

## 10. Recomendaciones de actualización para la Base de Conocimiento

- Actualizar `modelos_ml.md` con métricas v6.
- Documentar el comando oficial: `python utils/train_and_publish_all.py --optimize 50 --optimize-betplay 50`.
- Nota: el NBA v11 sigue siendo el modelo activo (v12 rechazado); documentar que el sistema protege producción correctamente.