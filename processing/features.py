"""
Fase 3: Procesamiento y feature engineering.

Transforma los DataFrames crudos de la ingesta en un dataset listo
para el modelo predictivo.

Features generadas por partido:
  - Promedios de puntos (home/away) de la temporada
  - Win % (home/away)
  - Factor local (home_advantage = 1)
  - Diferencial ofensivo: pts_diff = home_pts - away_pts
  - Diferencial de victorias: wpct_diff = home_wpct - away_wpct
  - Indicadores de rendimiento defensivo (fg_pct, tov)

Funciones principales:
  - build_features(games_df, team_stats_df) → DataFrame de features
  - clean_team_stats(df)                    → DataFrame limpio de estadísticas
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

# Columnas mínimas requeridas en team_stats para generar features
_REQUIRED_STATS = ["team_id", "pts", "w_pct", "fg_pct", "tov", "reb"]


# ── Limpieza ──────────────────────────────────────────────────────────────────

def clean_team_stats(df: pd.DataFrame) -> pd.DataFrame:
    """
    Limpia y normaliza el DataFrame de estadísticas de equipo.

    - Elimina filas con team_id nulo
    - Rellena NaN numéricos con la mediana de la columna
    - Normaliza tipos (int/float)

    Args:
        df: DataFrame crudo de nba_client.get_team_stats()

    Returns:
        DataFrame limpio.
    """
    if df.empty:
        logger.warning("clean_team_stats: DataFrame vacío")
        return df

    original_len = len(df)
    df = df.copy()

    # Eliminar filas sin team_id
    df = df[df["team_id"].notna()]

    # Columnas numéricas: rellenar NaN con mediana
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    for col in numeric_cols:
        if df[col].isna().any():
            median_val = df[col].median()
            df[col] = df[col].fillna(median_val)
            logger.debug("clean_team_stats: columna '%s' → NaN rellenados con mediana %.3f", col, median_val)

    logger.info(
        "clean_team_stats: %d filas originales → %d limpias",
        original_len, len(df),
    )
    return df.reset_index(drop=True)


def clean_games(df: pd.DataFrame) -> pd.DataFrame:
    """
    Limpia el DataFrame de partidos.

    - Elimina filas sin game_id o sin ambos team_ids
    - Parsea fechas

    Args:
        df: DataFrame crudo de nba_client.get_daily_games()

    Returns:
        DataFrame limpio.
    """
    if df.empty:
        return df

    df = df.copy()
    df = df[df["game_id"].notna()]
    df = df[df["home_team_id"].notna() & df["visitor_team_id"].notna()]

    # Parsear fecha si viene como string
    if "game_date_est" in df.columns:
        df["game_date"] = pd.to_datetime(df["game_date_est"], errors="coerce").dt.date

    return df.reset_index(drop=True)


# ── Feature Engineering ───────────────────────────────────────────────────────

def _merge_team_stats(
    games_df: pd.DataFrame,
    stats_df: pd.DataFrame,
    side: str,          # 'home' o 'visitor'
    team_col: str,      # nombre de la columna team_id en games_df
) -> pd.DataFrame:
    """
    Agrega las estadísticas del equipo local/visitante al DataFrame de partidos.

    Renombra las columnas de stats con prefijo `side_` para evitar colisiones.
    """
    cols_to_merge = [c for c in stats_df.columns if c not in ("season", "fetch_date", "created_at")]
    stats_sub = stats_df[cols_to_merge].copy()

    # Renombrar todas las columnas excepto team_id
    rename_map = {
        col: f"{side}_{col}"
        for col in stats_sub.columns
        if col != "team_id"
    }
    stats_sub = stats_sub.rename(columns=rename_map)

    merged = games_df.merge(
        stats_sub,
        left_on=team_col,
        right_on="team_id",
        how="left",
    )
    # Eliminar la columna team_id duplicada que viene de stats
    if "team_id" in merged.columns and team_col != "team_id":
        merged = merged.drop(columns=["team_id"])

    return merged


def build_features(
    games_df: pd.DataFrame,
    team_stats_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Construye el dataset de features para el modelo predictivo.

    Proceso:
      1. Limpieza de ambos DataFrames
      2. Join: partidos ← estadísticas del equipo local
      3. Join: partidos ← estadísticas del equipo visitante
      4. Generación de features derivadas
      5. Selección de columnas finales

    Args:
        games_df:      DataFrame de partidos (get_daily_games).
        team_stats_df: DataFrame de estadísticas (get_team_stats).

    Returns:
        DataFrame con columnas de features + columnas de identificación.
        Columna objetivo: 'home_win' (1 si ganó local, 0 si no).
        Si el partido no ha terminado, 'home_win' = NaN.
    """
    if games_df.empty:
        logger.warning("build_features: games_df vacío — no hay features que construir")
        return pd.DataFrame()

    if team_stats_df.empty:
        logger.warning("build_features: team_stats_df vacío — features de estadísticas omitidas")

    # ── Limpieza ──────────────────────────────────────────────────────────────
    games  = clean_games(games_df)
    stats  = clean_team_stats(team_stats_df) if not team_stats_df.empty else pd.DataFrame()

    # ── Joins ─────────────────────────────────────────────────────────────────
    if not stats.empty:
        games = _merge_team_stats(games, stats, side="home",    team_col="home_team_id")
        games = _merge_team_stats(games, stats, side="visitor", team_col="visitor_team_id")

    # ── Features derivadas ────────────────────────────────────────────────────
    feature_df = games.copy()

    # Diferencial de puntos promedio (ofensiva)
    if "home_pts" in feature_df.columns and "visitor_pts" in feature_df.columns:
        feature_df["pts_diff"] = feature_df["home_pts"] - feature_df["visitor_pts"]

    # Diferencial de win % 
    if "home_w_pct" in feature_df.columns and "visitor_w_pct" in feature_df.columns:
        feature_df["wpct_diff"] = feature_df["home_w_pct"] - feature_df["visitor_w_pct"]

    # Diferencial rebotes
    if "home_reb" in feature_df.columns and "visitor_reb" in feature_df.columns:
        feature_df["reb_diff"] = feature_df["home_reb"] - feature_df["visitor_reb"]

    # Diferencial pérdidas (tov negativo: más pérdidas → peor)
    if "home_tov" in feature_df.columns and "visitor_tov" in feature_df.columns:
        feature_df["tov_diff"] = feature_df["visitor_tov"] - feature_df["home_tov"]  # inverso

    # Diferencial % campo
    if "home_fg_pct" in feature_df.columns and "visitor_fg_pct" in feature_df.columns:
        feature_df["fg_pct_diff"] = feature_df["home_fg_pct"] - feature_df["visitor_fg_pct"]

    # Diferencial % triples
    if "home_fg3_pct" in feature_df.columns and "visitor_fg3_pct" in feature_df.columns:
        feature_df["fg3_pct_diff"] = feature_df["home_fg3_pct"] - feature_df["visitor_fg3_pct"]

    # Diferencial % tiros libres
    if "home_ft_pct" in feature_df.columns and "visitor_ft_pct" in feature_df.columns:
        feature_df["ft_pct_diff"] = feature_df["home_ft_pct"] - feature_df["visitor_ft_pct"]

    # Diferencial asistencias
    if "home_ast" in feature_df.columns and "visitor_ast" in feature_df.columns:
        feature_df["ast_diff"] = feature_df["home_ast"] - feature_df["visitor_ast"]

    # Diferencial robos
    if "home_stl" in feature_df.columns and "visitor_stl" in feature_df.columns:
        feature_df["stl_diff"] = feature_df["home_stl"] - feature_df["visitor_stl"]

    # Diferencial bloqueos
    if "home_blk" in feature_df.columns and "visitor_blk" in feature_df.columns:
        feature_df["blk_diff"] = feature_df["home_blk"] - feature_df["visitor_blk"]

    # Diferencial net rating (plus_minus de temporada: el predictor más fuerte)
    if "home_plus_minus" in feature_df.columns and "visitor_plus_minus" in feature_df.columns:
        feature_df["net_rating_diff"] = feature_df["home_plus_minus"] - feature_df["visitor_plus_minus"]

    # Ventaja de local (siempre 1 en esta implementación; se puede parametrizar)
    feature_df["home_advantage"] = 1

    # ── Features de forma reciente (si ya se enriquecieron) ───────────────────
    if "home_recent_wpct_5" in feature_df.columns and "visitor_recent_wpct_5" in feature_df.columns:
        feature_df["form_wpct_diff"] = (
            feature_df["home_recent_wpct_5"] - feature_df["visitor_recent_wpct_5"]
        )
    if (
        "home_recent_pts_scored_5"  in feature_df.columns and
        "home_recent_pts_allowed_5" in feature_df.columns and
        "visitor_recent_pts_scored_5"  in feature_df.columns and
        "visitor_recent_pts_allowed_5" in feature_df.columns
    ):
        home_net    = feature_df["home_recent_pts_scored_5"]    - feature_df["home_recent_pts_allowed_5"]
        visitor_net = feature_df["visitor_recent_pts_scored_5"] - feature_df["visitor_recent_pts_allowed_5"]
        feature_df["form_net_pts_diff"] = home_net - visitor_net

    if "home_rest_days" in feature_df.columns and "visitor_rest_days" in feature_df.columns:
        feature_df["rest_advantage"] = feature_df["home_rest_days"] - feature_df["visitor_rest_days"]

    # ── Features de Elo (si ya se enriquecieron con enrich_with_elo) ──────────
    # home_elo_pre, visitor_elo_pre, elo_diff, elo_home_win_prob → ya están en el df

    # ── Features de estadísticas avanzadas (pace-adjusted) ────────────────────
    # Ofensiva pace-adjusted (ORTG): mayor es mejor
    if "home_off_rating" in feature_df.columns and "visitor_off_rating" in feature_df.columns:
        feature_df["ortg_diff"] = feature_df["home_off_rating"] - feature_df["visitor_off_rating"]

    # Defensiva pace-adjusted (DRTG): menor es mejor → invertir signo del diferencial
    # drtg_diff > 0 → home tiene mejor defensa (encaja menos por 100 posesiones)
    if "home_def_rating" in feature_df.columns and "visitor_def_rating" in feature_df.columns:
        feature_df["drtg_diff"] = feature_df["visitor_def_rating"] - feature_df["home_def_rating"]

    # Net Rating avanzado (ORTG - DRTG): dominio neto pace-adjusted — el predictor más poderoso
    if "home_off_rating" in feature_df.columns and "home_def_rating" in feature_df.columns:
        home_net_adv    = feature_df["home_off_rating"]    - feature_df["home_def_rating"]
        visitor_net_adv = feature_df["visitor_off_rating"] - feature_df["visitor_def_rating"]
        feature_df["net_rtg_adv_diff"] = home_net_adv - visitor_net_adv

    # Pace differential — indica si uno de los equipos intenta dominar el tempo
    if "home_pace" in feature_df.columns and "visitor_pace" in feature_df.columns:
        feature_df["pace_diff"] = feature_df["home_pace"] - feature_df["visitor_pace"]

    # True Shooting % — eficiencia ofensiva ajustada por 3s y tiros libres
    if "home_ts_pct" in feature_df.columns and "visitor_ts_pct" in feature_df.columns:
        feature_df["ts_pct_diff"] = feature_df["home_ts_pct"] - feature_df["visitor_ts_pct"]

    # Effective FG% — eficiencia de tiro ponderada por triples
    if "home_efg_pct" in feature_df.columns and "visitor_efg_pct" in feature_df.columns:
        feature_df["efg_pct_diff"] = feature_df["home_efg_pct"] - feature_df["visitor_efg_pct"]

    # ── Variable objetivo ─────────────────────────────────────────────────────
    # Si el partido ya terminó y hay marcadores, calculamos el resultado real.
    # Prioridad: home_team_score > home_pts_x (después del merge con stats) > input home_win
    if "home_team_score" in feature_df.columns and "visitor_team_score" in feature_df.columns:
        scores_available = (
            feature_df["home_team_score"].notna() &
            feature_df["visitor_team_score"].notna()
        )
        feature_df["home_win"] = np.where(
            scores_available,
            (feature_df["home_team_score"] > feature_df["visitor_team_score"]).astype(int),
            np.nan,
        )
    elif "home_pts_x" in feature_df.columns and "visitor_pts_x" in feature_df.columns:
        # home_pts/visitor_pts de games_df se renombran con sufijo _x al hacer el merge con stats
        scores_available = (
            feature_df["home_pts_x"].notna() &
            feature_df["visitor_pts_x"].notna() &
            (feature_df["home_pts_x"] > 0)
        )
        feature_df["home_win"] = np.where(
            scores_available,
            (feature_df["home_pts_x"] > feature_df["visitor_pts_x"]).astype(int),
            np.nan,
        )
    elif "home_win" in feature_df.columns and feature_df["home_win"].notna().any():
        # Ya viene calculado desde games_df (e.g. _merge_scores en train_model.py)
        pass
    else:
        feature_df["home_win"] = np.nan

    logger.info(
        "build_features: %d partidos procesados, %d features generadas",
        len(feature_df),
        len([c for c in feature_df.columns if c.endswith("_diff") or c in ("home_advantage",)]),
    )
    return feature_df


# ── Dataset histórico para entrenamiento ──────────────────────────────────────

def get_feature_columns() -> list[str]:
    """
    Devuelve la lista de columnas de features usadas por el modelo.
    Debe mantenerse sincronizada con build_features().
    """
    return [
        # ── Diferenciales de temporada ───────────────────────────
        "wpct_diff",
        "reb_diff",
        "tov_diff",
        "fg_pct_diff",
        "fg3_pct_diff",        # % triples — señal de eficiencia ofensiva
        "ft_pct_diff",         # % tiros libres
        "ast_diff",            # asistencias — fluidez ofensiva
        "stl_diff",            # robos — presión defensiva
        "blk_diff",            # bloqueos — defensa interior
        "net_rating_diff",     # plus_minus de temporada — el predictor más fuerte
        "home_advantage",
        "home_w_pct",
        "visitor_w_pct",
        # ── Forma reciente (últimos 5 partidos) ──────────────────
        "home_recent_wpct_5",
        "visitor_recent_wpct_5",
        "home_recent_pts_scored_5",
        "visitor_recent_pts_scored_5",
        "home_recent_pts_allowed_5",
        "visitor_recent_pts_allowed_5",
        "form_wpct_diff",
        "form_net_pts_diff",
        # ── Descanso y fatiga ────────────────────────────────────
        "home_rest_days",
        "visitor_rest_days",
        "rest_advantage",
        "home_is_b2b",
        "visitor_is_b2b",
        # ── Elo (predictor dinámico más potente) ─────────────────
        "elo_diff",            # diferencial Elo home - visitor
        "home_elo_pre",        # rating Elo del local antes del partido
        "visitor_elo_pre",     # rating Elo del visitante
        "elo_home_win_prob",   # win probability implícita del Elo
        # ── Estadísticas avanzadas pace-adjusted ─────────────────
        "ortg_diff",           # ORTG diferencial (puntos por 100 pos)
        "drtg_diff",           # DRTG diferencial (defensa; positivo = home mejor)
        "net_rtg_adv_diff",    # net rating avanzado — predictor clave
        "pace_diff",           # ritmo de juego diferencial
        "ts_pct_diff",         # True Shooting % diferencial
        "efg_pct_diff",        # Effective FG% diferencial
    ]


def prepare_training_dataset(feature_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """
    Filtra el dataset de features para entrenamiento:
      - Solo filas con 'home_win' conocido (partidos terminados)
      - Elimina filas con NaN en features clave
      - Devuelve (X, y)

    Args:
        feature_df: Resultado de build_features().

    Returns:
        (X, y): X = DataFrame de features, y = Serie binaria de resultados.
    """
    feature_cols   = get_feature_columns()
    available_cols = [c for c in feature_cols if c in feature_df.columns]

    df = feature_df[feature_df["home_win"].notna()].copy()
    df = df.dropna(subset=available_cols)

    # Ordenar cronológicamente → necesario para TimeSeriesSplit sin data leakage
    for date_col in ("game_date", "game_date_est", "fetch_date"):
        if date_col in df.columns:
            df = df.sort_values(date_col).reset_index(drop=True)
            break

    X = df[available_cols].astype(float)
    y = df["home_win"].astype(int)

    logger.info("prepare_training_dataset: %d muestras, %d features (orden temporal preservado)",
                len(X), len(available_cols))
    return X, y
