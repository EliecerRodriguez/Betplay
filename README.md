# Betplay Analytics — NBA + ATP

Sistema de predicción multi-deporte (NBA y tenis ATP) con detección de value bets usando modelos de Machine Learning.

---

## Estado de la infraestructura

| Componente | Estado |
|---|---|
| Supabase (PostgreSQL) | ✅ Conectado — almacena predictions, games, odds, line_scores |
| pg_cron 1.6.4 | ✅ Habilitado (sin jobs activos — servidor local sin URL pública) |
| pg_net 0.20.0 | ✅ Habilitado (disponible para futuro despliegue) |
| Tablas y vistas | ✅ Se crean automáticamente al iniciar el servidor |
| Task Scheduler | ✅ Configurado — Betplay Reconcile 10:00 / Betplay Pipeline 10:30 |

> Todas las escrituras a Supabase son **upserts** (INSERT … ON CONFLICT DO UPDATE).
> Correr los scripts más de una vez el mismo día no genera duplicados.

---

## Inicio rápido (abrir el dashboard)

```powershell
cd C:\Betplay
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "C:\Betplay"
uvicorn web.app:app --reload --port 8000
```

Abrir en el navegador: http://localhost:8000

> Al arrancar, el servidor crea automáticamente las tablas y vistas en Supabase si no existen.
> El dashboard es opcional — las tareas automáticas corren aunque el servidor esté apagado.

---

## Rutina DIARIA — no tienes que hacer nada

El Task Scheduler corre automáticamente:

| Hora | Tarea | Qué hace |
|---|---|---|
| 10:00 AM | Betplay Reconcile | Descarga resultados reales, actualiza `home_win` en predictions y Supabase |
| 10:30 AM | Betplay Pipeline | Genera predicciones del día, guarda en `output/` y Supabase |

Si el PC estaba apagado a esa hora, las tareas corren **solas en cuanto lo prendes** (opción StartWhenAvailable activada).

**No borrar nada. No tocar `.env`.**

---

## Recuperación manual (si algo falló o quieres forzar la actualización)

```powershell
cd C:\Betplay
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "C:\Betplay"
python reconcile.py       # recupera resultados de todos los días pendientes
python run_pipeline.py    # genera predicciones NBA del día actual
python run_atp_pipeline.py  # genera predicciones ATP del día actual
```

> `reconcile.py` revisa TODAS las predicciones sin resultado desde cualquier fecha pasada,
> no solo el día anterior. Si te olvidaste varios días, una sola ejecución lo recupera todo.

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
python build_elos.py --start-year 2010   # actualizar Elos (una vez por temporada)
python build_atp_model.py --force        # re-entrenar con datos hasta el año actual
```

Modelo guardado en `sports/atp/models/atp_model_v1.joblib`.

---

## Rutina SEMANAL (cada 1-2 semanas, preferiblemente lunes)

### 1. Reentrenar el modelo

```powershell
cd C:\Betplay
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "C:\Betplay"
python train_model.py --start-date 2021-10-19 --end-date AYER --season 2024-26 --model stacking --with-form --with-travel --version vN
```

Reemplazar `AYER` con la fecha real (ej. `2026-05-25`) y `vN` con la siguiente versión (v10, v11…).

El modelo se entrena con:
- **Stats punto-en-el-tiempo** — rolling window por equipo (sin look-ahead bias)
- **Impacto de lesiones** — `injury_impact_diff` calculado desde `LeagueGameLog` histórico

### 2. Actualizar `.env`

Abrir `C:\Betplay\.env` y cambiar:

```
MODEL_VERSION=vN
MODEL_TYPE=stacking
```

### 3. Borrar el journal (una vez por cambio de versión)

```powershell
Remove-Item "C:\Betplay\output\bet_journal.csv"
```

### 4. Reiniciar el servidor

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
| `output/bet_journal.csv` | Borrar solo al cambiar versión del modelo NBA |
| `output/*.csv` | Nunca — se sobreescriben solos |
| `models/nba_model_vN.joblib` | Nunca — guardar todos como respaldo |
| `sports/atp/models/atp_model_v1.joblib` | Nunca — solo al re-entrenar |
| `sports/atp/models/current_elos.json` | Se regenera con `build_elos.py` |
| `database/cron_local.bat` | Solo si cambias horarios del Task Scheduler |
| `reconcile.py` | No tocar |

---

## Versiones del modelo

### NBA
| Versión | Tipo | Fecha | Accuracy | Notas |
|---|---|---|---|---|
| v9 | StackingClassifier (XGB+RF+LR) | May 2026 | 66.2% | Baseline |
| v10+ | StackingClassifier (XGB+RF+LR) | — | — | Rolling stats + injury feature |

### ATP
| Versión | Tipo | Fecha | Val Accuracy | Val ROC-AUC | Notas |
|---|---|---|---|---|---|
| v1 | XGBoost + calibración isotónica | May 2026 | 63.6% | 0.698 | 13 features, train 2013-2023, val 2024 |
