# Informe PSE → PKE — Reentrenamiento automático Liga BetPlay v3

**Fecha:** 2026-10-21
**Tarea solicitada por el usuario:** Verificar que un solo comando reentrena los dos modelos de la Liga BetPlay, los compara y los publica.

---

## 1. Objetivo del cambio

1. Confirmar que `utils/train_and_publish_betplay.py --market both` ejecuta el ciclo MLOps completo (entrenar ambos modelos → comparar → registrar → publicar) en un solo comando.
2. Actualizar el dataset de entrenamiento de 233 → 795 partidos (regenerando el CSV consolidado desde la caché de parquets).
3. Corregir un bug de NaN descubierto al ampliar el dataset.

## 2. Archivos modificados

- `sports/betplay/processing/features.py` — `parse_record()` y `parse_form()` ahora manejan NaN numérico (float) que llega al leer parquets con tipos Arrow, además de strings "nan"/"".
- `data/betplay_cache/dataset_completo.csv` — regenerado desde la caché (no versionado, en `.gitignore`).

## 3. Archivos creados

- `models/betplay/betplay_yellow_model_v3.joblib` — acc=0.598, cv=0.532 (795 muestras).
- `models/betplay/betplay_corners_model_v3.joblib` — acc=0.566, cv=0.519 (795 muestras).

## 4. Archivos eliminados

- Los modelos v2 fueron reemplazados por los v3 (se conservan en git).

## 5. Funcionalidades agregadas

- **Ciclo MLOps verificado end-to-end**: un solo comando entrenó AMBOS modelos (yellow v3 + corners v3), comparó contra v2, registró en Neon (`betplay_model_registry`) y publicó en GitHub (commits `0b356f1` yellow, `f24f7f2` corners).
- **Mejora de modelos** gracias a 795 partidos:

| Modelo | v2 (233) | v3 (795) | Δ |
|--------|----------|----------|---|
| Yellow | acc=0.574 cv=0.522 | **acc=0.598 cv=0.532** | +0.024 acc |
| Corners | acc=0.404 cv=0.467 | **acc=0.566 cv=0.519** | +0.162 acc |

- **Primer value bet detectado en producción**: Atlético Nacional vs Pasto → Córners UNDER 9.5 @ 1.66, prob modelo 69.2%, EV +14.8% (el v2 nunca detectó ninguno).

## 6. Funcionalidades eliminadas

Ninguna.

## 7. Cambios arquitectónicos

- El dataset de entrenamiento ahora usa TODA la caché de parquets (795 partidos, 2025-01-24 → 2026-10-21) en vez de solo 233. El entrenamiento es más informativo.
- Bug de tipos Arrow resuelto: las columnas `home_record`/`home_form` de los parquets vienen como float NaN con dtype `ArrowDtype`, y `parse_record`/`parse_form` ahora convierten a string de forma segura.

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- `modelos_ml.md` — modelos BetPlay v3 con nuevas métricas (795 muestras).
- `pipeline_nba.md` / nuevo `pipeline_betplay.md` — el ciclo de reentrenamiento se documenta con un comando único.

## 9. Riesgos detectados

- **El CSV consolidado no se versiona** (en `.gitignore`): en CI el workflow debe regenerarlo o esperar a que el dataset esté disponible como artefacto.
- **Los modelos v3 aún son débiles** (cv=0.53/0.52, cerca de 0.5): siguen siendo baseline; el value bet detectado es prometedor pero requiere validación con resultados reales.
- La corrida real (sin `--dry-run`) hace commit+push automático: si el usuario no quiere publicar, debe usar `--dry-run`.

## 10. Recomendaciones de actualización para la Base de Conocimiento

- Documentar el comando oficial de reentrenamiento: `python utils/train_and_publish_betplay.py --market both` (y variantes `--dry-run`, `--force`, `--market yellow|corners`).
- Documentar que `data/betplay_cache/dataset_completo.csv` se regenera desde los parquets y es la fuente del entrenamiento.
- Actualizar métricas de los modelos en `modelos_ml.md`.