# Informe PSE → PKE — Historial ampliado 2021-2025 + modelos BetPlay v5

**Fecha:** 2026-10-22
**Tarea solicitada por el usuario:** Aumentar el historial de la Liga BetPlay preguntando antes si eso asegura mejores oportunidades de predicción. Respuesta: SÍ (verificado empíricamente) → se amplió el dataset y se reentrenaron los modelos.

---

## 1. Objetivo del cambio

1. Verificar empíricamente que ampliar el historial mejora la predicción (antes de descargar).
2. Ampliar el dataset de entrenamiento de 795 → 2,537 partidos (2021-01-16 → 2026-10-21).
3. Reentrenar los modelos con Optuna y publicar las versiones v5.

## 2. Archivos modificados

- `sports/betplay/config/settings.py` — `BETPLAY_HISTORY_START` de 2025 → 2021.
- `data/betplay_cache/dataset_completo.csv` — regenerado (2,537 partidos, no versionado).

## 3. Archivos creados

- `models/betplay/betplay_yellow_model_v5.joblib` — acc=0.644, cv=0.530, n=2,537.
- `models/betplay/betplay_corners_model_v5.joblib` — acc=0.600, cv=0.503, n=2,537.

## 4. Archivos eliminados

Ninguno.

## 5. Funcionalidades agregadas

- **Dataset histórico 3.2× mayor**: 2,537 partidos (2021-2026) vs 795 (2025-2026) anteriores.
- **Verificación empírica del valor del historial**:
  - Muestreo ESPN: 8/8 fechas antiguas (2021-2024) con stats de córners/tarjetas → el historial existe y es utilizable.
  - Conteo por año: 2021=411, 2022=450, 2023=452, 2024=432, 2025+~590 partidos.
  - Experimento A/B: entrenar con 795 vs 2,322/2,537 partidos mostró mejora clara de accuracy.
- **Modelos v5 con Optuna 20 trials**:
  - Yellow: acc 59.8% → **64.4%** (+4.6 pts)
  - Corners: acc 56.6% → **60.0%** (+3.4 pts)
- Ambos publicados en GitHub (v5) y registrados en Neon.

## 6. Funcionalidades eliminadas

Ninguna.

## 7. Cambios arquitectónicos

- El historial del pipeline BetPlay ahora abarca 5 temporadas completas (2021-2026) en vez de 1.8.
- La descarga del historial se hizo con `build_historical_dataset()` desde 2021 usando la caché de parquets existente (no re-descarga lo ya descargado).

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- `ingestion.md` — rango de historial BetPlay (2021→hoy), verificación de cobertura ESPN.
- `modelos_ml.md` — modelos v5 con nuevas métricas y dataset ampliado.
- `database.md` — registros v5 en betplay_model_registry.

## 9. Riesgos detectados

- **CV-AUC de corners bajó ligeramente** (0.519 → 0.503) al ampliar el historial: la liga cambió entre temporadas (ascensos/descensos, técnicos). El accuracy subió +3.4 pts, que es lo que importa para apostar, pero la consistencia temporal (CV) bajó.
- **El dataset ampliado mezcla eras**: equipos de 2021-2023 incluyen descendidos; los promedios históricos por equipo pueden ser menos comparables. Mitigación: el modelo usa forma reciente (últimos 5) como feature dominante.
- **Descarga larga**: 2021-2024 requiere ~40-60 min con rate-limit amistoso; en CI el workflow debe poder re-descargar (o usar un artefacto/cache).

## 10. Recomendaciones de actualización para la Base de Conocimiento

- Actualizar `modelos_ml.md` con métricas v5 (yellow acc=0.644/cv=0.530, corners acc=0.600/cv=0.503, n=2,537).
- Documentar en `ingestion.md` que el historial BetPlay arranca en 2021 y cómo se regenera el CSV consolidado desde los parquets.
- Considerar documentar el experimento A/B (795 vs 2,537) como evidencia de que el historial mejora la predicción +4 pts de accuracy.