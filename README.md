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
# Post-Finales: usa 180 días para cubrir toda la temporada
python train_model.py --version v11 --days 180 --with-form --with-travel --model stacking
```

Reemplazar `v11` por la siguiente versión disponible.

El modelo se entrena con:
- **Stats punto-en-el-tiempo** — rolling window por equipo (sin look-ahead bias)
- **Features de clasificación** — standings (seed, games_back) desde `LeagueStandingsV3`
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
| v11+ | — | — | — | Próxima versión post-Finales NBA (Jun 2026) |

### ATP
| Versión | Tipo | Fecha | Val Accuracy | Val ROC-AUC | Notas |
|---|---|---|---|---|---|
| v1 | XGBoost + calibración isotónica | May 2026 | 63.6% | 0.698 | 13 features, train 2013-2023, val 2024 |
| v2 | StackingClassifier (XGB+RF+LR) | May 2026 | 64.0% | 0.6935 | 17 features (+saque rolling), train 2013-2025, val honesta 2025 |
