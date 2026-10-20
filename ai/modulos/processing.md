# Processing

## Objetivo

Transformar datos crudos de ingestion en features punto-en-el-tiempo para entrenamiento y predicción, evitando data leakage.

## Responsabilidad

- Feature engineering con rolling windows
- Normalización de stats
- Construcción de diferenciales entre equipos
- Validación de datos (no NaN)

## Archivos involucrados

### NBA
- `sports/nba/processing/features.py` (198 líneas)
- `processing/features.py` (legacy, 187 líneas)

### ATP
- `sports/atp/processing/features.py` (156 líneas)
- `sports/atp/processing/recent_form.py` (89 líneas)

## Features NBA (53 totales)

### Función principal
```python
def build_features(
    games_df: pd.DataFrame,
    team_stats_df: pd.DataFrame
) -> pd.DataFrame:
    """
    Construye 53 features para predicción.
    
    Input:
        games_df: con home_team_id, visitor_team_id, elo_diff, etc.
        team_stats_df: con pts, ast, reb, fg_pct, w_pct, etc.
    
    Output:
        DataFrame con features listos para modelo
    """
```

### Categorías de features

#### 1. Elo (1 feature)
- `elo_diff` — diferencia Elo local - visitante

#### 2. Stats ofensivas diferenciales (8)
- `pts_diff` — puntos por partido
- `ast_diff` — asistencias
- `reb_diff` — rebotes totales
- `oreb_diff` — rebotes ofensivos
- `dreb_diff` — rebotes defensivos
- `stl_diff` — robos
- `blk_diff` — tapones
- `tov_diff` — pérdidas

#### 3. Shooting diferenciales (3)
- `fg_pct_diff` — % campo
- `fg3_pct_diff` — % triples
- `ft_pct_diff` — % tiros libres

#### 4. Forma reciente (10)
- `home_last_5_wins` — victorias locales últimos 5
- `home_last_5_losses` — derrotas locales últimos 5
- `home_last_5_wpct` — W% local últimos 5
- `visitor_last_5_wins`
- `visitor_last_5_losses`
- `visitor_last_5_wpct`
- `home_streak` — racha actual local (+ = ganando, - = perdiendo)
- `visitor_streak`
- `home_vs_visitor_wins` — victorias locales vs. este rival (últimos 3 años)
- `home_vs_visitor_losses`

#### 5. Standings (4)
- `seed_diff` — diferencia de seed en conferencia
- `games_back_diff` — diferencia de juegos atrás del líder
- `home_seed` — seed local (raw)
- `visitor_seed` — seed visitante (raw)

#### 6. Travel & Rest (4)
- `home_jet_lag` — [0-1] impacto jet lag local
- `visitor_jet_lag`
- `home_rest_days` — días desde último partido local
- `visitor_rest_days`

#### 7. Contexto (3)
- `is_home` — 1 si predicción es para local, 0 si visitante
- `season` — codificado como entero (ej: 2024-25 → 20)
- `month` — mes del partido (1-12)

#### 8. Stats absolutas (20)
- `home_pts`, `visitor_pts` — puntos por partido (raw)
- `home_ast`, `visitor_ast`
- `home_reb`, `visitor_reb`
- `home_fg_pct`, `visitor_fg_pct`
- `home_fg3_pct`, `visitor_fg3_pct`
- `home_ft_pct`, `visitor_ft_pct`
- `home_w_pct`, `visitor_w_pct` — W% temporada
- `home_oreb`, `visitor_oreb`
- `home_dreb`, `visitor_dreb`
- `home_stl`, `visitor_stl`
- `home_blk`, `visitor_blk`
- `home_tov`, `visitor_tov`

### Nota crítica: `injury_impact_diff` NO incluido
Este feature se mencionaba en documentación antigua pero **no está en el training actual**. Las lesiones se aplican post-modelo.

## Features ATP (17 totales)

### Función principal
```python
def build_atp_features(
    player1: str,
    player2: str,
    surface: str,
    tourney_name: str
) -> dict:
    """
    Construye 17 features para predicción ATP.
    """
```

### Categorías

#### 1. Elo (1)
- `elo_diff`

#### 2. Rankings (2)
- `rank_diff` — diferencia ranking ATP
- `rank_points_diff`

#### 3. H2H (2)
- `h2h_wins` — victorias jugador 1 vs 2
- `h2h_losses`

#### 4. Forma superficie (4)
- `surface_win_pct_diff` — W% en superficie (últimos 12 meses)
- `hard_win_pct_diff`
- `clay_win_pct_diff`
- `grass_win_pct_diff`

#### 5. Saque rolling (4)
- `ace_rate_diff` — % aces (últimos 20 partidos)
- `first_serve_pct_diff`
- `first_serve_won_diff`
- `second_serve_won_diff`

#### 6. Contexto (4)
- `surface` — codificado (Hard=0, Clay=1, Grass=2)
- `tourney_level` — (Grand Slam=3, Masters=2, ATP500=1, ATP250=0)
- `player1_age`
- `player2_age`

## Point-in-Time enforcement

### Rolling windows
```python
# Forma reciente: últimos N partidos ANTES de este partido
game_log = get_game_log(team_id, season)
game_log_before = game_log[game_log['game_date'] < current_date]
last_5 = game_log_before.tail(5)
```

### Override de W%
```python
# Stats de API pueden incluir partidos futuros
# Se sobrescribe con W% acumulado hasta hoy
wpct = get_season_wpct(team_id, pit_date, season)
team_stats_df.loc[mask, "w_pct"] = wpct
```

## Normalización

### Stats diferenciales
```python
# Ej: pts_diff = home_pts - visitor_pts
# Positivo → local anota más, negativo → visitante anota más
```

### Features categóricos
```python
# surface: Hard=0, Clay=1, Grass=2
# tourney_level: ATP250=0, ATP500=1, Masters=2, GrandSlam=3
```

## Validación

**Actualmente:** Sin validación explícita (riesgo de NaN en producción)

**Recomendado:**
```python
assert not features_df.isnull().any().any(), "Features contienen NaN"
```

## Riesgos encontados

### Críticos
- **🔴 Sin validación de NaN** — modelo puede recibir features con NaN
- **🔴 Injury impact NO en training** — asimetría training vs producción

### Medios
- **🟡 Rolling windows sin verificar orden** — si game_log desordenado, falla

## Relaciones con otros módulos

- **Ingestion** → provee datos crudos
- **Pipeline NBA/ATP** → llama `build_features()`
- **Modelos ML** → consume features

## Pendientes de documentación

### Feature importance
**¿Qué features aportan más?**

**Evidencia:** NO existe análisis de feature importance documentado en el código.

**Archivos revisados:**
- `sports/nba/processing/features.py` — solo construye features, no analiza importancia
- `train_model.py` — entrena modelo pero no extrae feature importance
- No existe archivo de importancia exportado (ej: `feature_importance.json`)

**Limitación:** Sin este análisis, no se puede determinar qué features son redundantes o cuáles aportan más poder predictivo.

### Features removidos
**¿Existen features removidos del modelo actual?**

**Evidencia (processing/features.py vs train_model.py):**

**Removidos:**
- `injury_impact_diff` — **NO está en features de entrenamiento** (ver nota crítica en este documento línea 108)

```python
# train_model.py líneas 478-522: injury_impact_diff se calcula pero se usa como proxy
# NO se incluye en el dataset de entrenamiento final
feature_df["injury_impact_diff"] = feature_df["injury_impact_diff"].fillna(0.0)
```

**Razón:** Las lesiones se aplican post-modelo en producción, no durante entrenamiento.

**Features legacy mencionados en documentación antigua pero no en código actual:**
- No se detectaron otros features removidos. Las 53 features actuales coinciden con `get_feature_columns()`.

## Historial de actualizaciones

| Fecha | Cambio |
|-------|--------|
| 2026-10-02 | Documentación inicial |
