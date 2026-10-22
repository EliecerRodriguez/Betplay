"""
Cliente de datos históricos de la Liga BetPlay.

Construye el dataset histórico de tarjetas amarillas y córners iterando
el calendario de la temporada en ESPN (scoreboard por fecha + summary por partido).

El dataset resultante es el activo principal para entrenar el modelo 50/50:
cada fila = un partido con:
  - total_yellow_cards (variable objetivo Over/Under 4.5)
  - total_corners     (variable objetivo Over/Under 9.5)
  - stats de contexto (posesión, faltas, tiros, forma, tabla) = features

Caché en data/betplay_cache/*.parquet para no re-descargar.
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from sports.betplay.config.settings import (
    BETPLAY_CACHE_DIR,
    BETPLAY_HISTORY_START,
    ESPN_DELAY,
)
from sports.betplay.ingestion.espn_client import (
    get_daily_games,
    get_match_summary,
)
from utils.logger import get_logger

logger = get_logger(__name__)

# Fechas en que la liga está en receso (enero-julio = Apertura, jul-dic = Clausura;
# el calendario ESPN define las fechas reales, esto es solo el rango por defecto)
_DEFAULT_START = date(BETPLAY_HISTORY_START, 1, 1)


def _date_range(start: date, end: date):
    """Itera fechas inclusive."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def build_historical_dataset(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    cache_dir: str = BETPLAY_CACHE_DIR,
) -> pd.DataFrame:
    """
    Descarga partidos + stats históricos de la Liga BetPlay.

    Para cada fecha del rango: scoreboard → partidos; para cada partido
    terminado: summary → tarjetas/córners. Cachea en parquet por fecha.

    Returns:
        DataFrame con columnas: game_id, game_date, home_team_id, away_team_id,
        home_team, away_team, home_yellow_cards, away_yellow_cards,
        total_yellow_cards, home_corners, away_corners, total_corners, ...
    """
    start_date = start_date or _DEFAULT_START
    end_date = end_date or date.today()
    os.makedirs(cache_dir, exist_ok=True)

    all_games: list[pd.DataFrame] = []
    total = 0
    cached = 0

    for d in _date_range(start_date, end_date):
        date_str = d.strftime("%Y-%m-%d")
        cache_path = os.path.join(cache_dir, f"{date_str}.parquet")

        # Caché hit
        if os.path.exists(cache_path):
            try:
                df = pd.read_parquet(cache_path)
                if not df.empty:
                    all_games.append(df)
                cached += 1
                continue
            except Exception:
                pass

        try:
            games = get_daily_games(date_str)
            if games.empty:
                # Guardar vacío para no reintentar (a menos que se borre caché)
                pd.DataFrame().to_parquet(cache_path)
                continue

            # Solo procesar partidos terminados
            rows = []
            for _, g in games.iterrows():
                if g["status"] != "STATUS_FULL_TIME":
                    continue
                summary = get_match_summary(g["game_id"])
                if not summary or summary.get("total_yellow_cards") is None:
                    continue
                g_dict = g.to_dict()
                g_dict.update(summary)
                rows.append(g_dict)
                time.sleep(ESPN_DELAY)

            if rows:
                day_df = pd.DataFrame(rows)
                day_df.to_parquet(cache_path, index=False)
                all_games.append(day_df)
                total += len(day_df)
                logger.info("  %s: %d partidos con stats", date_str, len(day_df))
            else:
                pd.DataFrame().to_parquet(cache_path)
        except Exception as exc:
            logger.warning("Error en %s: %s", date_str, exc)

    if not all_games:
        return pd.DataFrame()

    result = pd.concat(all_games, ignore_index=True)
    result = result.drop_duplicates(subset=["game_id"])
    logger.info(
        "Dataset histórico BetPlay: %d partidos (2025→hoy) | %d en caché",
        len(result), cached,
    )
    return result


# ── Análisis exploratorio rápido ────────────────────────────────────────────

def dataset_summary(df: pd.DataFrame) -> dict:
    """Resumen rápido del dataset (distribución de target variables)."""
    if df.empty:
        return {"n": 0}

    summary = {
        "n_partidos": len(df),
        "total_yellow_mean": round(float(df["total_yellow_cards"].mean()), 2) if "total_yellow_cards" in df else None,
        "total_yellow_over45_pct": round(float((df["total_yellow_cards"] > 4.5).mean() * 100), 1) if "total_yellow_cards" in df else None,
        "total_corners_mean": round(float(df["total_corners"].mean()), 2) if "total_corners" in df else None,
        "total_corners_over95_pct": round(float((df["total_corners"] > 9.5).mean() * 100), 1) if "total_corners" in df else None,
    }
    return summary
