"""
Feature engineering para Liga BetPlay (fútbol).

Transforma los datos crudos de ESPN (tarjetas, córners, faltas, posesión...)
en features para los modelos 50/50:
  - Over/Under tarjetas amarillas (target: total_yellow_cards > línea)
  - Over/Under córners (target: total_corners > línea)

Sigue el patrón de sports/nba/processing/features.py (modelo maestro),
adaptado a los mercados de tarjetas y córners.

Features clave (las que dan ventaja frente a la casa):
  - Promedios por equipo: tarjetas/partido, córners/partido, faltas/partido
  - Posesión y tiros/partido
  - Forma reciente (W/D/L últimos 5)
  - Posición en tabla (rank, puntos)
  - Local/visitante (home_advantage)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

# Líneas objetivo (configurables por mercado)
YELLOW_LINE = 4.5   # Over/Under total de tarjetas amarillas
CORNERS_LINE = 9.5  # Over/Under total de córners


# ── Aggregación por equipo (promedios históricos) ───────────────────────────

def build_team_averages(matches_df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula promedios por equipo de las stats clave, desde el histórico.

    Returns:
        DataFrame indexado por team_id con cols:
        team_avg_yellow, team_avg_corners, team_avg_fouls, team_avg_possession,
        team_avg_shots, team_avg_shots_on_target
    """
    if matches_df.empty:
        return pd.DataFrame()

    # Por equipo como local
    home = matches_df[[
        "home_team_id", "home_yellow_cards", "home_corners", "home_fouls",
        "home_possession", "home_shots", "home_shots_on_target"
    ]].rename(columns={
        "home_team_id": "team_id",
        "home_yellow_cards": "yellow", "home_corners": "corners",
        "home_fouls": "fouls", "home_possession": "possession",
        "home_shots": "shots", "home_shots_on_target": "shots_ot",
    })
    # Por equipo como visitante
    away = matches_df[[
        "away_team_id", "away_yellow_cards", "away_corners", "away_fouls",
        "away_possession", "away_shots", "away_shots_on_target"
    ]].rename(columns={
        "away_team_id": "team_id",
        "away_yellow_cards": "yellow", "away_corners": "corners",
        "away_fouls": "fouls", "away_possession": "possession",
        "away_shots": "shots", "away_shots_on_target": "shots_ot",
    })
    all_team = pd.concat([home, away], ignore_index=True)
    all_team = all_team.dropna(subset=["team_id"])

    avgs = all_team.groupby("team_id").agg(
        team_avg_yellow=("yellow", "mean"),
        team_avg_corners=("corners", "mean"),
        team_avg_fouls=("fouls", "mean"),
        team_avg_possession=("possession", "mean"),
        team_avg_shots=("shots", "mean"),
        team_avg_shots_ot=("shots_ot", "mean"),
    ).reset_index()
    return avgs


def parse_form(form_str) -> float:
    """Convierte la forma W/D/L en una puntuación: W=3, D=1, L=0 (promedio)."""
    # Manejar NaN numérico (parquet reading) y None
    if form_str is None or pd.isna(form_str):
        return 0.5  # neutral
    form_str = str(form_str).strip()
    if form_str in ("nan", "", "None"):
        return 0.5  # neutral
    scores = []
    for c in str(form_str):
        if c == "W":
            scores.append(3)
        elif c == "D":
            scores.append(1)
        elif c == "L":
            scores.append(0)
    return float(np.mean(scores)) if scores else 0.5


def parse_record(record_str) -> float:
    """Convierte record 'W-L-D' en win% (0-1)."""
    # Manejar NaN numérico (parquet reading) y None
    if record_str is None or pd.isna(record_str):
        return 0.5
    record_str = str(record_str).strip()
    if record_str in ("nan", "", "None"):
        return 0.5
    parts = record_str.replace(" ", "").split("-")
    if len(parts) != 3:
        return 0.5
    try:
        w, l, d = int(parts[0]), int(parts[1]), int(parts[2])
        total = w + l + d
        return round((w + 0.5 * d) / total, 3) if total else 0.5
    except ValueError:
        return 0.5


# ── Features por partido ──────────────────────────────────────────────────────

def build_features(
    games_df: pd.DataFrame,
    team_avgs: pd.DataFrame,
) -> pd.DataFrame:
    """
    Construye features para los partidos a predecir.

    Args:
        games_df: Partidos con home_team_id, away_team_id, home_form, away_form,
                  home_record, away_record, home_points, away_points (de ESPN).
        team_avgs: Promedios por equipo (build_team_averages).

    Returns:
        DataFrame con features numéricas + game_id/game_date para tracking.
    """
    if games_df.empty:
        return pd.DataFrame()

    df = games_df.copy()

    # Merge promedios de equipo
    df["home_team_id"] = df["home_team_id"].astype(str)
    df["away_team_id"] = df["away_team_id"].astype(str)
    avgs = team_avgs.copy()
    avgs["team_id"] = avgs["team_id"].astype(str)

    df = df.merge(avgs, left_on="home_team_id", right_on="team_id", how="left", suffixes=("", "_home"))
    df = df.merge(avgs, left_on="away_team_id", right_on="team_id", how="left", suffixes=("", "_away"))

    # Forma reciente — priorizar home_form_recent (de lastFiveGames de ESPN)
    if "home_form_recent" in df.columns and df["home_form_recent"].notna().any():
        df["home_form_raw"] = df["home_form_recent"]
        df["away_form_raw"] = df["away_form_recent"]
    else:
        df["home_form_raw"] = df["home_form"]
        df["away_form_raw"] = df["away_form"]
    df["home_form_score"] = df["home_form_raw"].apply(parse_form)
    df["away_form_score"] = df["away_form_raw"].apply(parse_form)
    df["form_diff"] = df["home_form_score"] - df["away_form_score"]

    # Record (win%)
    df["home_wpct"] = df["home_record"].apply(parse_record)
    df["away_wpct"] = df["away_record"].apply(parse_record)
    df["wpct_diff"] = df["home_wpct"] - df["away_wpct"]

    # Puntos en tabla
    df["home_points"] = pd.to_numeric(df["home_points"], errors="coerce")
    df["away_points"] = pd.to_numeric(df["away_points"], errors="coerce")
    df["points_diff"] = df["home_points"].fillna(0) - df["away_points"].fillna(0)

    # Ventaja local
    df["home_advantage"] = 1.0

    def _col_or_zero(name: str) -> "pd.Series":
        """Devuelve la columna o una Serie de ceros si no existe."""
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce").fillna(0)
        return pd.Series(0.0, index=df.index)

    # Diferenciales de promedios (las señales más fuertes)
    for col in ["yellow", "corners", "fouls", "possession", "shots", "shots_ot"]:
        h = _col_or_zero(f"team_avg_{col}_home")
        a = _col_or_zero(f"team_avg_{col}_away")
        df[f"{col}_diff"] = h - a

    # Sumas (contexto del partido — ambos equipos)
    for col in ["yellow", "corners", "fouls", "shots"]:
        key = f"team_avg_{col}"
        h = _col_or_zero(f"{key}_home")
        a = _col_or_zero(f"{key}_away")
        df[f"{key}_sum"] = h + a

    # Columnas de tracking
    keep = ["game_id", "game_date", "home_team", "away_team"] + [
        c for c in df.columns if c not in ["game_id", "game_date", "home_team", "away_team"]
        and df[c].dtype in (np.float64, np.int64, float, int)
    ]
    return df[keep].copy()


# ── Targets (para entrenamiento) ──────────────────────────────────────────────

def make_targets(matches_df: pd.DataFrame) -> pd.DataFrame:
    """
    Crea los targets binarios para entrenamiento desde el histórico.

    Returns:
        DataFrame con game_id + target_yellow (1=over línea) + target_corners.
    """
    df = matches_df.copy()
    df["total_yellow_cards"] = pd.to_numeric(df["total_yellow_cards"], errors="coerce")
    df["total_corners"] = pd.to_numeric(df["total_corners"], errors="coerce")
    df["target_yellow"] = (df["total_yellow_cards"] > YELLOW_LINE).astype(int)
    df["target_corners"] = (df["total_corners"] > CORNERS_LINE).astype(int)
    return df[["game_id", "target_yellow", "target_corners"]].copy()


# ── Columnas de features (para el modelo) ────────────────────────────────────

def get_feature_columns() -> list[str]:
    """Devuelve la lista de features usadas por el modelo (sincronizada con build_features)."""
    return [
        "form_diff",
        "wpct_diff",
        "points_diff",
        "home_advantage",
        "team_avg_yellow_home", "team_avg_yellow_away", "team_avg_yellow_sum",
        "team_avg_corners_home", "team_avg_corners_away", "team_avg_corners_sum",
        "team_avg_fouls_home", "team_avg_fouls_away", "team_avg_fouls_sum",
        "team_avg_shots_home", "team_avg_shots_away", "team_avg_shots_sum",
        "yellow_diff", "corners_diff", "fouls_diff", "possession_diff",
        "shots_diff", "shots_ot_diff",
    ]
