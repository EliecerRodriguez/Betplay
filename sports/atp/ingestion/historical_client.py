"""
Cliente de datos históricos ATP.

Fuente: Jeff Sackmann tennis_atp (GitHub, licencia CC BY 4.0)
Repositorio: https://github.com/JeffSackmann/tennis_atp

Descarga y parsea los CSVs de partidos ATP por año.  Los archivos se
cachean localmente en ATP_CACHE_DIR para evitar descargas repetidas.

Cobertura: 1968-presente (actualizado regularmente por Sackmann).
Para el cálculo de Elo se recomienda usar ATP_ELO_START_YEAR (2010 por
defecto) — suficiente para que los ratings converjan, sin sobrecarga.

Funciones públicas:
  - download_atp_matches(start_year, end_year) → DataFrame con partidos
  - download_atp_players()                     → DataFrame con jugadores
  - download_atp_rankings_current()            → DataFrame con ranking ATP actual
  - load_cached_matches(start_year, end_year)  → carga desde caché local

Columnas clave del DataFrame de partidos (subset relevante):
  tourney_id, tourney_name, surface, tourney_level, tourney_date,
  winner_id, winner_name, winner_rank, winner_age,
  loser_id,  loser_name,  loser_rank,  loser_age,
  score, best_of, round, minutes,
  w_ace, w_df, w_svpt, w_1stIn, w_1stWon, w_2ndWon, w_bpSaved, w_bpFaced,
  l_ace, l_df, l_svpt, l_1stIn, l_1stWon, l_2ndWon, l_bpSaved, l_bpFaced
"""
from __future__ import annotations

import os
import time
from datetime import date
from typing import List, Optional

import pandas as pd
import requests

from sports.atp.config.settings import ATP_CACHE_DIR, ATP_DATA_BASE_URL, ATP_DATA_END_YEAR
from utils.logger import get_logger

logger = get_logger(__name__)

# Timeout HTTP para descargas de GitHub
_HTTP_TIMEOUT = 30   # segundos
_RETRY_DELAY  = 2.0  # segundos entre reintentos

# Columnas a conservar tras la descarga (reduce memoria)
_MATCH_COLS = [
    "tourney_id", "tourney_name", "surface", "draw_size",
    "tourney_level", "tourney_date",
    "match_num", "winner_id", "winner_seed", "winner_name",
    "winner_hand", "winner_ht", "winner_ioc", "winner_age",
    "winner_rank", "winner_rank_points",
    "loser_id", "loser_seed", "loser_name",
    "loser_hand", "loser_ht", "loser_ioc", "loser_age",
    "loser_rank", "loser_rank_points",
    "score", "best_of", "round", "minutes",
    "w_ace", "w_df", "w_svpt", "w_1stIn", "w_1stWon",
    "w_2ndWon", "w_SvGms", "w_bpSaved", "w_bpFaced",
    "l_ace", "l_df", "l_svpt", "l_1stIn", "l_1stWon",
    "l_2ndWon", "l_SvGms", "l_bpSaved", "l_bpFaced",
]

# Niveles de torneo válidos para el modelo (excluye Davis Cup, futures, etc.)
_VALID_LEVELS = {"G", "M", "A", "F", "D"}
# G = Grand Slam | M = Masters 1000 | A = ATP 250/500 | F = Finals | D = Davis Cup


# ── Descarga individual por año ───────────────────────────────────────────────

def _cache_path(filename: str) -> str:
    os.makedirs(ATP_CACHE_DIR, exist_ok=True)
    return os.path.join(ATP_CACHE_DIR, filename)


def _download_csv(url: str, cache_file: str, force: bool = False) -> Optional[pd.DataFrame]:
    """
    Descarga un CSV desde url, lo guarda en cache_file y lo devuelve como DataFrame.
    Si ya existe en caché y force=False, carga desde disco.
    """
    fpath = _cache_path(cache_file)

    if os.path.exists(fpath) and not force:
        logger.debug("Cargando desde caché: %s", fpath)
        try:
            return pd.read_csv(fpath, low_memory=False)
        except Exception as exc:
            logger.warning("Error leyendo caché %s (%s) — re-descargando", fpath, exc)

    logger.info("Descargando: %s", url)
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=_HTTP_TIMEOUT)
            if resp.status_code == 404:
                logger.debug("404 para %s — archivo no existe en el repositorio", url)
                return None
            resp.raise_for_status()
            with open(fpath, "wb") as f:
                f.write(resp.content)
            df = pd.read_csv(fpath, low_memory=False)
            logger.info("  → %d filas descargadas", len(df))
            return df
        except requests.exceptions.RequestException as exc:
            logger.warning("Intento %d/3 falló: %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(_RETRY_DELAY)
    logger.error("No se pudo descargar: %s", url)
    return None


def _download_year(year: int, force: bool = False) -> Optional[pd.DataFrame]:
    """Descarga el CSV de partidos ATP para un año específico."""
    url   = f"{ATP_DATA_BASE_URL}/atp_matches_{year}.csv"
    fname = f"atp_matches_{year}.csv"
    return _download_csv(url, fname, force=force)


# ── API pública ───────────────────────────────────────────────────────────────

def download_atp_matches(
    start_year: int = 2010,
    end_year: Optional[int] = None,
    force: bool = False,
    valid_levels_only: bool = True,
) -> pd.DataFrame:
    """
    Descarga y concatena los partidos ATP para el rango de años indicado.

    Los archivos se cachean localmente; las descargas sólo ocurren la primera
    vez o cuando force=True.

    Args:
        start_year:        Primer año a incluir (default: 2010).
        end_year:          Último año incluido (default: año actual).
        force:             Re-descarga aunque exista en caché.
        valid_levels_only: Si True, filtra solo G/M/A/F (excluye Davis Cup futures).

    Returns:
        DataFrame combinado con todos los partidos del rango, ordenado por fecha.
    """
    if end_year is None:
        end_year = ATP_DATA_END_YEAR

    frames: List[pd.DataFrame] = []
    for year in range(start_year, end_year + 1):
        df = _download_year(year, force=force)
        if df is None or df.empty:
            continue

        # Conservar sólo las columnas que existen en este año (el dataset
        # fue ampliando columnas con el tiempo)
        cols_available = [c for c in _MATCH_COLS if c in df.columns]
        df = df[cols_available].copy()

        # Normalizar surface → título
        if "surface" in df.columns:
            df["surface"] = df["surface"].str.strip().str.title()
            # Carpet se trata como Hard (muy pocos partidos modernos)
            df["surface"] = df["surface"].replace("Carpet", "Hard")

        # Filtrar niveles válidos
        if valid_levels_only and "tourney_level" in df.columns:
            df = df[df["tourney_level"].isin(_VALID_LEVELS)]

        # Añadir columna de año para facilitar filtros posteriores
        df["year"] = year
        frames.append(df)

    if not frames:
        logger.warning("No se encontraron partidos para %d-%d", start_year, end_year)
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)

    # Convertir tourney_date a datetime (formato: YYYYMMDD)
    if "tourney_date" in combined.columns:
        combined["tourney_date"] = pd.to_datetime(
            combined["tourney_date"].astype(str), format="%Y%m%d", errors="coerce"
        )
        combined = combined.sort_values("tourney_date").reset_index(drop=True)

    # Asegurar tipos numéricos en IDs y rankings
    for col in ["winner_id", "loser_id", "winner_rank", "loser_rank",
                "winner_age", "loser_age"]:
        if col in combined.columns:
            combined[col] = pd.to_numeric(combined[col], errors="coerce")

    logger.info(
        "Dataset ATP cargado: %d partidos (%d-%d)", len(combined), start_year, end_year
    )
    return combined


def download_atp_players(force: bool = False) -> pd.DataFrame:
    """
    Descarga el catálogo maestro de jugadores ATP.

    Returns:
        DataFrame con: player_id, name_first, name_last, hand, dob, ioc, height, wikidata_id
    """
    url = f"{ATP_DATA_BASE_URL}/atp_players.csv"
    df  = _download_csv(url, "atp_players.csv", force=force)
    if df is None or df.empty:
        return pd.DataFrame()

    # El CSV no tiene encabezados explícitos en versiones antiguas
    expected_cols = ["player_id", "name_first", "name_last", "hand", "dob", "ioc", "height", "wikidata_id"]
    if list(df.columns) == list(range(len(df.columns))):
        df.columns = expected_cols[: len(df.columns)]
    else:
        df.columns = [str(c).strip() for c in df.columns]

    if "player_id" in df.columns:
        df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce")
    if "dob" in df.columns:
        df["dob"] = pd.to_datetime(df["dob"].astype(str), format="%Y%m%d", errors="coerce")

    logger.info("Catálogo de jugadores ATP: %d jugadores", len(df))
    return df


def download_atp_rankings_current(force: bool = False) -> pd.DataFrame:
    """
    Descarga el ranking ATP más reciente disponible en el repositorio Sackmann.

    Returns:
        DataFrame con: ranking_date, rank, player_id, points
    """
    url = f"{ATP_DATA_BASE_URL}/atp_rankings_current.csv"
    df  = _download_csv(url, "atp_rankings_current.csv", force=force)
    if df is None or df.empty:
        return pd.DataFrame()

    df.columns = [str(c).strip().lower() for c in df.columns]
    rename = {"ranking_date": "ranking_date", "rank": "rank",
               "player": "player_id", "pts": "points"}
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    if "ranking_date" in df.columns:
        df["ranking_date"] = pd.to_datetime(
            df["ranking_date"].astype(str), format="%Y%m%d", errors="coerce"
        )
    for col in ["rank", "player_id", "points"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    logger.info(
        "Ranking ATP: %d entradas (hasta %s)",
        len(df),
        df["ranking_date"].max().date() if "ranking_date" in df.columns and not df.empty else "?",
    )
    return df


def get_current_rankings_dict(force: bool = False) -> dict[int, int]:
    """
    Devuelve {player_id: rank} con el ranking más reciente disponible.
    Útil para enriquecer partidos con el ranking actual de cada jugador.
    """
    df = download_atp_rankings_current(force=force)
    if df.empty or "player_id" not in df.columns or "rank" not in df.columns:
        return {}

    # Quedarse sólo con la fecha más reciente
    latest = df["ranking_date"].max()
    df = df[df["ranking_date"] == latest]
    return dict(zip(df["player_id"].astype(int), df["rank"].astype(int)))
