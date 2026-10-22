# Informe PSE → PKE — Orquestador de reentrenamiento NBA + BetPlay

**Fecha:** 2026-10-21
**Tarea solicitada por el usuario:** Crear un comando orquestador único que reentrene los modelos de NBA y Liga BetPlay (entrenar, comparar, registrar y publicar) en una sola ejecución.

---

## 1. Objetivo del cambio

Proveer un único comando que ejecute el ciclo MLOps completo de ambos deportes (NBA y Liga BetPlay), evitando tener que correr dos comandos separados.

## 2. Archivos modificados

Ninguno (solo creación).

## 3. Archivos creados

- `utils/train_and_publish_all.py` — orquestador que ejecuta en secuencia:
  1. `utils/train_and_publish.py` (NBA)
  2. `utils/train_and_publish_betplay.py` (BetPlay)

## 4. Archivos eliminados

Ninguno.

## 5. Funcionalidades agregadas

- **Comando único**: `python utils/train_and_publish_all.py` reentrena NBA + BetPlay.
- **Opciones**:
  - `--sport nba|betplay|both` (default: both)
  - `--dry-run` (entrena pero NO publica)
  - `--force` (publica aunque el modelo sea peor)
  - `--optimize N` (trials Optuna NBA)
  - `--start-date YYYY-MM-DD` (inicio del dataset NBA)
- **Resumen final**: reporta éxito o lista los deportes que fallaron (exit code 1 si alguno falla).
- **Probado**: `--sport betplay --dry-run` ejecutó el ciclo completo (entrenó v4, comparó contra v3, registró en Neon, eliminó sin publicar) y reportó éxito.

## 6. Funcionalidades eliminadas

Ninguna.

## 7. Cambios arquitectónicos

- Nueva capa de orquestación MLOps: `train_and_publish_all.py` → { `train_and_publish.py` (NBA), `train_and_publish_betplay.py` (BetPlay) }.
- Cada script hijo conserva su lógica de comparación/registro/publicación; el orquestador solo los encadena y agrega el resumen.

## 8. Módulos de la Base de Conocimiento posiblemente afectados

- `modelos_ml.md` — documentar el comando orquestador.
- `github_actions.md` — si se desea, se puede añadir un workflow que lo ejecute periódicamente.

## 9. Riesgos detectados

- **Ejecución secuencial**: NBA primero, luego BetPlay. Si NBA tarda mucho (Optuna 50 trials + descarga), BetPlay espera. No hay paralelismo real (evita conflictos de git commit/push entre ambos).
- **El dry-run registra en Neon**: el dry-run de BetPlay registró v4 en `betplay_model_registry` aunque no publicó. Es un comportamiento heredado de `train_and_publish_betplay.py` (registra siempre). No rompe nada, pero el registro puede tener versiones "fantasma" no publicadas.
- **NBA requiere datos de red** (stats.nba.com, caché): si falla la descarga, el orquestador reporta NBA en failures y continúa con BetPlay.

## 10. Recomendaciones de actualización para la Base de Conocimiento

- Documentar en `modelos_ml.md` el comando `python utils/train_and_publish_all.py` como el flujo oficial de reentrenamiento de ambos deportes.
- Considerar un workflow de GitHub Actions `retrain_all.yml` (manual, `workflow_dispatch`) que ejecute el orquestador en CI.