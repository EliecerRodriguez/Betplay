# Betplay Analytics — NBA + ATP

Sistema de predicción multi-deporte (NBA y tenis ATP) con detección de value bets usando modelos de Machine Learning.

---

## Cómo funciona ahora (automatización completa vía GitHub Actions)

**El sistema corre solo todos los días en los servidores de GitHub aunque tu PC esté apagado.**

### Qué pasa cada día sin que hagas nada

| Hora Colombia | Qué ocurre | Script |
|---|---|---|
| 06:30 AM | Reconciliación: busca predicciones sin resultado y llena el score real de ayer en Supabase | `reconcile.py` |
| 07:00 AM | Pipeline NBA: genera predicciones del día de hoy (partidos que aún no ocurrieron) y las guarda en Supabase | `run_pipeline.py` |
| 06:00 PM | Pipeline ATP: obtiene cuotas Kambi en tiempo real y genera predicciones ATP del día | `run_atp_pipeline.py` |

> Las predicciones se generan **antes del partido**. Eso es lo que les da valor.
> La reconciliación actualiza al día siguiente si el modelo acertó o no.

---

## Qué ves cuando abres el dashboard (aunque hayas estado días sin entrar)

```powershell
cd C:\Betplay
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "C:\Betplay"
uvicorn web.app:app --reload --port 8000
```

Abrir en el navegador: **http://localhost:8000**

Al abrir el dashboard encontrarás en Supabase:
- Las **predicciones de cada día** que corrió el pipeline automático (con fecha correcta)
- El **resultado real** de cada partido ya reconciliado (ganó/perdió el modelo)
- Las **value bets detectadas** por día
- El **accuracy acumulado** del modelo

> El dashboard es solo un visualizador — los datos ya están en Supabase independientemente de si lo abres o no.

---

## Estado de la infraestructura

| Componente | Estado |
|---|---|
| Supabase (PostgreSQL) | ✅ Conectado — puerto 6543 (Transaction Pooler) |
| GitHub Actions | ✅ Activo — 3 workflows automáticos diarios |
| Tablas y vistas | ✅ Se crean automáticamente en el primer arranque |
| Task Scheduler local | ⛔ Ya no es necesario — reemplazado por GitHub Actions |

> Todas las escrituras son **upserts** (INSERT … ON CONFLICT DO UPDATE).
> Correr los scripts más de una vez el mismo día no genera duplicados.

---

## Lo único que debes hacer tú

### 1. Nada en el día a día
Los pipelines corren solos. Solo abre el dashboard cuando quieras revisar los resultados.

### 2. Forzar manualmente un pipeline (si necesitas)
Ve a GitHub → **Actions** → selecciona el workflow → **Run workflow**:

| Workflow | URL |
|---|---|
| NBA Pipeline | `github.com/EliecerRodriguez/Betplay/actions/workflows/daily_pipeline.yml` |
| Reconciliar resultados | `github.com/EliecerRodriguez/Betplay/actions/workflows/reconcile.yml` |
| ATP Pipeline | `github.com/EliecerRodriguez/Betplay/actions/workflows/atp_pipeline.yml` |
| Catch-up (reconciliar resultados pendientes) | `github.com/EliecerRodriguez/Betplay/actions/workflows/catchup.yml` |

### 3. Si hubo un fallo y los datos de Supabase están desactualizados
Ejecuta el workflow **"Catch-up – Reconciliar Resultados Pasados"** manualmente desde GitHub Actions.
Esto busca todas las predicciones sin resultado en Supabase y las actualiza, sin importar de cuándo sean.

### 4. Actualizar el modelo (cuando quieras reentrenar)
Esto sí requiere correrlo localmente:
```powershell
cd C:\Betplay
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "C:\Betplay"
python train_model.py          # reentrenar modelo NBA
python build_atp_model.py --force  # reentrenar modelo ATP
git add models/
git commit -m "feat: retrain model"
git push
```

---

## Secretos de GitHub (ya configurados)

| Secret | Para qué se usa |
|---|---|
| `DATABASE_URL` | Conexión a Supabase desde los workflows. Puerto 6543 (Transaction Pooler) |
| `ODDS_API_KEY` | The Odds API (opcional — sin clave usa cuotas Kambi scrapeadas) |

> Si cambias la contraseña de Supabase debes actualizar `DATABASE_URL` en:
> `github.com/EliecerRodriguez/Betplay/settings/secrets/actions`

---

## Pipeline ATP (tennis)

### Ejecución diaria

```powershell
cd C:\Betplay
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "C:\Betplay"
python run_atp_pipeline.py              # predicciones de hoy
python run_atp_pipeline.py --date 2026-05-20  # fecha específica
python run_atp_pipeline.py --no-save   # solo consola, sin guardar CSV
```

Salida en `output/`:
- `atp_predictions.csv` — predicciones del día
- `atp_value_bets.csv`  — oportunidades de valor detectadas
- `atp_odds.csv`        — snapshot de cuotas Kambi (Betplay/Rushbet)

### Reentrenar el modelo ATP (cada temporada)

```powershell
python build_elos.py --start-year 2010              # actualizar Elos (descarga hasta 2026 por defecto)
python build_atp_model.py --force                   # re-entrenar v2 (train 2013-2025, eval honesta 2024/2025)
```

> Para incluir datos del año más reciente, borra el caché del año y re-entrena:
> ```powershell
> Remove-Item data\atp_cache\atp_matches_2026.csv   # fuerza re-descarga desde Sackmann
> python build_atp_model.py --force
> ```

Modelo guardado en `sports/atp/models/atp_model_v2.joblib`.

---

## Reentrenamiento del modelo NBA — cuándo y cómo

### Frecuencia recomendada

| Momento | Acción |
|---|---|
| **Resto de playoffs 2025-26** (hasta ~Jun 15) | ❌ No reentrenar — solo ~15-20 partidos más, ganancia mínima |
| **Tras las Finales NBA** (Jun-Sep 2026) | ✅ Reentrenar con toda la temporada completa (~900-1000 partidos) |
| **Inicio temporada 2026-27** (Oct 2026) | ✅ Reentrenar obligatorio con `--season 2026-27` |
| **Durante temporada regular** | Cada 4-6 semanas para refrescar la ventana de 90 días |

### Comando para reentrenar

```powershell
cd C:\Betplay
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "C:\Betplay"
# Post-Finales: usa 2.5 temporadas con optimización Bayesiana (recomendado)
python train_model.py --version v12 --start-date 2023-10-24 --season 2024-25 --season 2025-26 --model xgboost --optimize 50 --with-form
```

Reemplazar `v12` por la siguiente versión disponible.

Opciones del comando:
| Opción | Descripción |
|---|---|
| `--start-date` / `--days N` | Rango histórico de partidos (caché en `data/cache/`) |
| `--season YYYY-YY` | Temporada(s) para stats de equipo (puede repetirse) |
| `--model` | `xgboost` (recomendado) \| `stacking` \| `random_forest` \| `logistic` |
| `--optimize N` | **N trials de optimización Bayesiana** (Optuna TPE) — 0=desactivado |
| `--with-form` | Incluir features de forma reciente y rest days |
| `--with-travel` | Incluir features de jet lag (requiere `--with-form`) |
| `--version vN` | Tag del archivo de salida en `models/` |

> **`--optimize 50`**: Optuna prueba 50 combinaciones de hiperparámetros (n_estimators, max_depth,
> learning_rate, subsample, etc.) usando `TimeSeriesSplit(3)` para evaluar cada configuración
> sin data leakage temporal. Tarda ~20-40 min pero puede mejorar el ROC-AUC en 2-5%.

El modelo se entrena con:
- **Stats punto-en-el-tiempo** — rolling window por equipo (sin look-ahead bias)
- **Features de clasificación** — standings (seed, games_back) desde `LeagueStandingsV3`
- **Optimización Bayesiana** — Optuna TPESampler busca los mejores hiperparámetros XGBoost
- **Sin injury_impact_diff** — las lesiones se aplican post-modelo en `adjust_predictions()`

### Después de reentrenar

**1. Actualizar `.env`:**
```
MODEL_VERSION=vN
```

**2. Reiniciar el servidor:**
```
Ctrl+C  →  uvicorn web.app:app --reload --port 8000
```

---

## Referencia: comandos del Task Scheduler (ya configurado — solo como respaldo)

En caso de necesitar reconfigurar desde cero, ejecutar en PowerShell **como administrador**:

```powershell
# Eliminar tareas existentes
schtasks /delete /tn "Betplay Pipeline"  /f
schtasks /delete /tn "Betplay Reconcile" /f

# Crear tareas
schtasks /create /tn "Betplay Reconcile" /tr "cmd /c C:\Betplay\database\cron_local.bat" /sc daily /st 10:00 /ru SYSTEM /f
schtasks /create /tn "Betplay Pipeline"  /tr "cmd /c C:\Betplay\database\cron_local.bat" /sc daily /st 10:30 /ru SYSTEM /f

# Activar ejecución si se perdió la hora
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable
Set-ScheduledTask -TaskName "Betplay Reconcile" -Settings $s
Set-ScheduledTask -TaskName "Betplay Pipeline"  -Settings $s
```

---

## Resumen de archivos

| Archivo | Cuándo modificar |
|---|---|
| `output/bet_journal.csv` | **Nunca borrar** — historial acumulado de todas las versiones |
| `output/*.csv` | Nunca — se sobreescriben solos |
| `models/nba_model_vN.joblib` | Nunca — guardar todos como respaldo |
| `sports/atp/models/atp_model_v2.joblib` | Nunca — solo al re-entrenar |
| `sports/atp/models/current_elos.json` | Se regenera con `build_elos.py` |
| `database/cron_local.bat` | Solo si cambias horarios del Task Scheduler |
| `reconcile.py` | No tocar |

---

## Versiones del modelo

### NBA
| Versión | Tipo | Fecha | Accuracy | Notas |
|---|---|---|---|---|
| v9 | StackingClassifier (XGB+RF+LR) | May 2026 | 66.2% | Baseline |
| v10 | StackingClassifier (XGB+RF+LR) | May 2026 | 68.5% | +standings features, sin injury_impact_diff, CV-AUC 0.806 |
| v11 | XGBoost + Optuna (50 trials) | May 2026 | 70.8% | ROC-AUC 0.752 · CV-AUC 0.704±0.041 · Brier 0.201 · 4086 muestras · 53 features · best trial #21 (elo_diff feature #1) |
| v12+ | — | — | — | Próxima versión post-Finales NBA (Jun 2026) |

### ATP
| Versión | Tipo | Fecha | Val Accuracy | Val ROC-AUC | Notas |
|---|---|---|---|---|---|
| v1 | XGBoost + calibración isotónica | May 2026 | 63.6% | 0.698 | 13 features, train 2013-2023, val 2024 |
| v2 | StackingClassifier (XGB+RF+LR) | May 2026 | 64.0% | 0.6935 | 17 features (+saque rolling), train 2013-2025, val honesta 2025 |
