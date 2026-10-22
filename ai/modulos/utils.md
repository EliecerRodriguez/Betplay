# Utils

## Objetivo

Proveer utilidades compartidas para logging, backtesting y funciones auxiliares reutilizables.

## Responsabilidad

- Configuración centralizada de logging
- Backtesting de predicciones históricas
- Funciones auxiliares compartidas

## Archivos involucrados

- `utils/logger.py` — sistema de logging
- `utils/backtest.py` — backtesting framework
- `utils/__init__.py`

## Logging (`logger.py`)

### Configuración
```python
def get_logger(name: str, level: str = None) -> logging.Logger:
    """
    Retorna logger configurado.
    
    Args:
        name: nombre del módulo (ej: "run_pipeline")
        level: nivel de logging (default: LOG_LEVEL desde .env)
    
    Output:
        - Consola: formato "[YYYY-MM-DD HH:MM:SS] [LEVEL] mensaje"
        - Archivo: logs/{name}_YYYY-MM-DD.log
    """
```

### Niveles
- `DEBUG` — detalles exhaustivos
- `INFO` — progreso normal (default)
- `WARNING` — advertencias no críticas
- `ERROR` — errores recuperables
- `CRITICAL` — fallos catastróficos

### Rotación
- **Archivo diario:** `logs/run_pipeline_2026-10-02.log`
- **Sin rotación automática** — archivos se acumulan

## Backtest (`backtest.py`)

### Propósito
Evaluar accuracy histórico del modelo comparando predicciones vs. resultados reales.

### Función principal
```python
def run_backtest(
    start_date: str,
    end_date: str,
    model_version: str = "v11"
) -> dict:
    """
    Ejecuta backtest entre fechas.
    
    Returns:
        {
            "accuracy": float,
            "total_predictions": int,
            "correct": int,
            "incorrect": int,
            "by_confidence": {...}
        }
    """
```

### Métricas
- **Accuracy global** — % aciertos total
- **Accuracy by confidence** — accuracy segmentado por prob (50-60%, 60-70%, etc.)
- **Brier score** — (prob - real)² promedio
- **ROI simulado** — si se hubiera apostado según value bets

### Ejemplo
```bash
python -c "from utils.backtest import run_backtest; print(run_backtest('2024-10-01', '2026-10-01'))"
```

## Archivos involucrados

- `utils/logger.py` (78 líneas)
- `utils/backtest.py` (142 líneas)
- `utils/__init__.py`

## Uso en módulos

### Logging
```python
from utils.logger import get_logger

logger = get_logger("mi_modulo")
logger.info("Iniciando pipeline...")
logger.error("Falló la API: %s", error)
```

### Backtest
```python
from utils.backtest import run_backtest

results = run_backtest("2024-10-01", "2026-10-01", "v11")
print(f"Accuracy: {results['accuracy']:.1%}")
```

## Dependencias

- `logging` (stdlib)
- `pandas`
- `sqlalchemy` (para backtest)

## Riesgos encontrados

### Críticos
- **🔴 Sin rotación de logs** — carpeta `logs/` crece indefinidamente
- **🔴 Backtest sin caché** — recalcula todo en cada ejecución (lento)

### Medios
- **🟡 Sin alertas críticas** — logs no notifican al usuario
- **🟡 Backtest sin paralelización** — podría ser más rápido

## Relaciones con otros módulos

- **Todos los módulos** → usan `get_logger()`
- **Reconciliación** → podría usar backtest para validar accuracy

## Pendientes de documentación

### Limpieza de logs
**¿Existe política de limpieza?**

**Evidencia (utils/logger.py):** ✅ **SÍ existe rotación automática** (2026-10-21):

```python
# utils/logger.py líneas 44-50
file_handler = RotatingFileHandler(
    filename=os.path.join(LOG_DIR, "betplay.log"),
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=3,
    encoding="utf-8",
)
```

**Política:** Log único `logs/betplay.log` con rotación a 5 MB y 3 backups (máx. ~20 MB en disco). El archivo se rota automáticamente sin intervención.

**Pendiente:** No hay job de limpieza en GitHub Actions para logs de CI (los artifacts `logs/` se suben con retención de 14 días en los workflows).

### Frecuencia de backtest
**¿Backtest es automático o manual?**

**Evidencia:**
- **Manual:** Se ejecuta vía `/api/backtest` endpoint (web/app.py:1515-1756)
- **No hay scheduler:** No existe cron job ni ejecución automática
- **Reconciliación previa:** Backtest llama `_fetch_and_reconcile_results()` antes de calcular métricas

```python
# web/app.py líneas 1520-1523
try:
    _fetch_and_reconcile_results()
except Exception as exc:
    logger.warning("reconcile en backtest falló: %s", exc)
```

**Uso típico:** Usuario accede al dashboard web y consulta backtest manualmente.

## Historial de actualizaciones

| Fecha | Cambio |
|-------|--------|
| 2026-10-02 | Documentación inicial |
| 2026-10-21 | Pendiente "Limpieza de logs" resuelto: `logger.py` usa `RotatingFileHandler` (5 MB × 3 backups). |
