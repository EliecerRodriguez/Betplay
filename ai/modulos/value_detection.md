# Value Detection

## Objetivo

Identificar oportunidades de apuesta con valor positivo (value bets) comparando probabilidades del modelo ML contra cuotas de casas de apuestas.

## Responsabilidad

- Comparar probabilidad del modelo vs. cuota decimal
- Calcular valor esperado: `(prob × odds) - 1`
- Filtrar oportunidades con value > 0
- Formatear reporte legible

## Fórmula de valor esperado

```
value = (model_prob × decimal_odds) - 1

Ejemplo:
- Modelo predice: Lakers 65% de ganar
- Cuota: Lakers 1.85
- Value = (0.65 × 1.85) - 1 = 0.2025 = +20.25%
```

**Interpretación:**
- `value > 0` → value bet (modelo ve más probabilidad que la casa)
- `value < 0` → no apostar (casa sobrevalora al equipo)

## Archivos involucrados

### NBA
- `sports/nba/model/value_detector.py`
- `model/value_detector.py` (legacy)

### ATP
- `sports/atp/model/value_detector.py`

## Función principal (NBA)

```python
def detect_value_bets(
    predictions_df: pd.DataFrame,
    odds_df: pd.DataFrame,
    threshold: float = 0.0
) -> pd.DataFrame:
    """
    Detecta value bets.
    
    Args:
        predictions_df: con home_win_prob, away_win_prob
        odds_df: con home_odds, away_odds, bookmaker
        threshold: mínimo value para considerar (default 0.0)
    
    Returns:
        DataFrame con value_bets (game_id, side, value, ...)
    """
```

## Flujo funcional

```
1. Merge predictions + odds por game_id
2. Para cada bookmaker:
   - Calcular value_home = (home_prob × home_odds) - 1
   - Calcular value_away = (away_prob × away_odds) - 1
3. Filtrar value > threshold
4. Añadir columna is_value_bet = True
5. Retornar DataFrame
```

## Salida

### Columnas
- `game_id`
- `game_date`
- `bookmaker` — casa de apuestas
- `side` — 'home' | 'away'
- `team_name`
- `model_prob` — probabilidad del modelo [0-1]
- `odds` — cuota decimal
- `value` — valor esperado
- `is_value_bet` — siempre True (ya filtrado)

### Persistencia
- **NBA:** Supabase (`value_bets` table) + CSV (`bet_journal.csv`)
- **ATP:** CSV (`atp_value_bets.csv`)

## Threshold configurado

**Actual:** `threshold = 0.0`

Cualquier `value > 0` se considera oportunidad.

**Alternativa común:** `threshold = 0.05` (5% mínimo)

## Ejemplo práctico

```
Predicción:
- Lakers vs. Warriors
- home_win_prob = 0.68 (68%)

Cuotas:
- FanDuel: Lakers 1.75
- DraftKings: Lakers 1.90

Value bets:
┌───────────┬────────┬────────┬─────────┐
│ Bookmaker │  Odds  │ Value  │ ¿Apostar?│
├───────────┼────────┼────────┼─────────┤
│ FanDuel   │  1.75  │ +19%   │    ✅    │
│ DraftKings│  1.90  │ +29%   │    ✅    │
└───────────┴────────┴────────┴─────────┘
```

Mejor oportunidad: **DraftKings** (mayor value).

## Riesgos encontrados

### Críticos
- **🔴 Sin ajuste por margin** — cuotas incluyen margen de casa (~5-10%), no se corrige
- **🔴 Sin Kelly Criterion** — no calcula tamaño óptimo de apuesta
- **🔴 Value = 0 puede ser ruido** — threshold 0.0 muy permisivo

### Medios
- **🟡 Sin tracking de ROI** — no hay registro de si value bets reales ganaron
- **🟡 Sin filtro por confianza** — no diferencia predicción 51% vs 70%

## Relaciones con otros módulos

- **Pipeline NBA** → llama `detect_value_bets()`
- **Pipeline ATP** → llama `detect_value_bets()`
- **Modelos ML** → provee probabilidades
- **Database** → persiste value bets

## Pendientes de documentación

### Threshold óptimo
**¿Cuál es el mejor threshold?**

**Evidencia:**
- **NBA:** `threshold = 0.0` (cualquier valor esperado positivo)
- **ATP:** `val >= 0.05` (mínimo 5% de valor esperado)

**No existe análisis de threshold óptimo.** No hay backtesting que compare diferentes thresholds ni validación de cuál genera mejor ROI.

### Kelly Criterion
**¿Por qué no se implementa?**

**Evidencia (web/app.py:219-224):** Kelly Criterion **SÍ está implementado**.

```python
def _kelly(prob: float, decimal_odds: float) -> float:
    """Full Kelly fraction (0–1). Retorna 0 si no hay valor."""
    b = decimal_odds - 1.0
    if b <= 0 or prob <= 0 or prob >= 1:
        return 0.0
    return max(0.0, (b * prob - (1.0 - prob)) / b)
```

**Uso:** Se calcula `kelly_pct` para cada value bet y se usa en el journal:

```python
# web/app.py línea 664
bet_amount = max(1, round(bankroll * vb["kelly_pct"] / 200))  # Kelly ½
```

**Corrección:** La documentación anterior era incorrecta. Kelly SÍ está implementado (versión Half-Kelly por defecto).

### Backtesting de value bets
**¿Se valida ROI histórico?**

**Evidencia (web/app.py:1515-1756):** Sí existe backtesting de value bets.

**Endpoint:** `/api/backtest` — retorna ROI histórico, win rate, profit/loss por cada apuesta registrada en `bet_journal.csv`.

**Métricas disponibles:**
- `total_profit` — ganancia acumulada
- `win_rate` — % de aciertos
- `roi` — retorno sobre inversión
- `models` — estadísticas por versión de modelo
- `daily` — breakdown por día

**Limitación:** No hay análisis comparativo de estrategias (threshold 0% vs 5%, Kelly completo vs ½ Kelly, etc.).

## Historial de actualizaciones

| Fecha | Cambio |
|-------|--------|
| 2026-10-02 | Documentación inicial |
