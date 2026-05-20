"""
Rankings ATP y mapeo bidireccional nombre ↔ player_id.

Fuente principal: Jeff Sackmann atp_rankings_current.csv + atp_players.csv
(descargados en Fase 1 y cacheados localmente — sin llamadas externas).

El problema más difícil de la integración de datos en tenis es que cada
fuente usa un formato de nombre diferente:
  Sackmann CSV    → winner_name: "Novak Djokovic"
  Kambi / The Odds → home_team: "N. Djokovic" o "Djokovic N."
  ESPN            → "Djokovic, Novak"

Este módulo resuelve ese problema con un motor de búsqueda de 3 pasos:
  1. Coincidencia exacta normalizada (minúsculas, sin acentos)
  2. Coincidencia por apellido + inicial del nombre
  3. Coincidencia difusa con difflib (umbral configurable)

Funciones públicas:
  - get_current_rankings()              → DataFrame con ranking actual
  - get_player_id_by_name(name)         → int | None
  - get_ranking_for_player(player_id)   → int | None
  - build_name_to_id_map()              → dict {nombre_norm: player_id}
  - get_player_name(player_id)          → str | None
"""
from __future__ import annotations

import unicodedata
from difflib import get_close_matches
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import pandas as pd

from sports.atp.ingestion.historical_client import (
    download_atp_players,
    download_atp_rankings_current,
)
from utils.logger import get_logger

logger = get_logger(__name__)

# Umbral mínimo de similitud para búsqueda difusa (0.0–1.0)
_FUZZY_THRESHOLD = 0.75

# Cache en memoria de los DataFrames (se recarga si el proceso reinicia)
_players_cache: Optional[pd.DataFrame] = None
_rankings_cache: Optional[pd.DataFrame] = None
_name_map_cache: Optional[Dict[str, int]] = None


# ── Normalización de nombres ──────────────────────────────────────────────────

def _normalize(name: str) -> str:
    """
    Normaliza un nombre de jugador para comparación:
      - Minúsculas
      - Sin tildes/diacríticos (Djokovic, Ñ→N, etc.)
      - Sin puntos, guiones y espacios extra
    """
    if not name:
        return ""
    # Remover diacríticos
    nfd = unicodedata.normalize("NFD", str(name))
    ascii_str = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return " ".join(ascii_str.lower().replace(".", "").split())


def _last_first_initial(full_name: str) -> str:
    """'Carlos Alcaraz' → 'alcaraz c'  (para búsqueda por apellido + inicial)."""
    parts = _normalize(full_name).split()
    if len(parts) >= 2:
        return f"{parts[-1]} {parts[0][0]}"
    return _normalize(full_name)


# ── Carga de datos ────────────────────────────────────────────────────────────

def _get_players_df() -> pd.DataFrame:
    """Devuelve el DataFrame de jugadores, cargando desde caché o descargando."""
    global _players_cache
    if _players_cache is None or _players_cache.empty:
        _players_cache = download_atp_players()
        if not _players_cache.empty:
            # Añadir full_name y variantes normalizadas
            _players_cache["full_name"] = (
                _players_cache.get("name_first", pd.Series(dtype=str)).fillna("").str.strip()
                + " "
                + _players_cache.get("name_last", pd.Series(dtype=str)).fillna("").str.strip()
            ).str.strip()
            _players_cache["name_norm"]  = _players_cache["full_name"].apply(_normalize)
            _players_cache["last_init"]  = _players_cache["full_name"].apply(_last_first_initial)
    return _players_cache if _players_cache is not None else pd.DataFrame()


def _get_rankings_df() -> pd.DataFrame:
    """Devuelve el DataFrame de rankings, cargando desde caché o descargando."""
    global _rankings_cache
    if _rankings_cache is None or _rankings_cache.empty:
        _rankings_cache = download_atp_rankings_current()
    return _rankings_cache if _rankings_cache is not None else pd.DataFrame()


# ── Mapa nombre → ID ─────────────────────────────────────────────────────────

def build_name_to_id_map() -> Dict[str, int]:
    """
    Construye el diccionario de búsqueda {nombre_normalizado: player_id}.
    Se construye una vez y se reutiliza (memoizado).

    Incluye múltiples variantes por jugador:
      - Nombre completo normalizado: "novak djokovic"
      - Apellido + inicial: "djokovic n"
    """
    global _name_map_cache
    if _name_map_cache is not None:
        return _name_map_cache

    df = _get_players_df()
    if df.empty:
        return {}

    mapping: Dict[str, int] = {}
    for _, row in df.iterrows():
        pid = int(row.get("player_id", 0) or 0)
        if not pid:
            continue
        # Nombre completo
        mapping[str(row.get("name_norm", ""))]  = pid
        # Apellido + inicial
        mapping[str(row.get("last_init", ""))]  = pid
        # Solo apellido (puede colisionar, pero útil como fallback)
        last = _normalize(str(row.get("name_last", "") or ""))
        if last and last not in mapping:
            mapping[last] = pid

    _name_map_cache = mapping
    logger.debug("Mapa nombre→ID construido: %d entradas", len(mapping))
    return mapping


# ── API pública ───────────────────────────────────────────────────────────────

def get_current_rankings(top_n: int = 500) -> pd.DataFrame:
    """
    Devuelve el ranking ATP más reciente disponible en el dataset Sackmann.

    Args:
        top_n: Número máximo de jugadores a devolver (default: top 500).

    Returns:
        DataFrame con columnas: rank, player_id, points, ranking_date,
        full_name, ioc (país), dob.
    """
    rankings = _get_rankings_df()
    if rankings.empty:
        return pd.DataFrame()

    # Quedarse con la fecha más reciente
    latest = rankings["ranking_date"].max()
    df = rankings[rankings["ranking_date"] == latest].copy()
    df = df.sort_values("rank").head(top_n).reset_index(drop=True)

    # Enriquecer con datos del jugador (nombre, país)
    players = _get_players_df()
    if not players.empty:
        merge_cols = ["player_id"]
        extra = ["full_name", "ioc", "dob", "hand"]
        extra_avail = [c for c in extra if c in players.columns]
        df = df.merge(players[merge_cols + extra_avail], on="player_id", how="left")

    logger.info(
        "Ranking ATP: top %d jugadores (fecha: %s)",
        len(df),
        str(latest.date()) if hasattr(latest, "date") else latest,
    )
    return df


def get_player_id_by_name(name: str) -> Optional[int]:
    """
    Resuelve un nombre de jugador (en cualquier formato) a su player_id Sackmann.

    Estrategia de búsqueda:
      1. Coincidencia exacta normalizada
      2. Coincidencia apellido + inicial
      3. Búsqueda difusa (difflib ≥ 75% similitud)

    Returns:
        player_id (int) si se encontró coincidencia, None en caso contrario.
    """
    if not name:
        return None

    name_map = build_name_to_id_map()
    norm = _normalize(name)

    # Paso 1: coincidencia exacta
    if norm in name_map:
        return name_map[norm]

    # Paso 2: apellido + inicial ("Carlos Alcaraz" → "alcaraz c")
    last_init = _last_first_initial(name)
    if last_init in name_map:
        return name_map[last_init]

    # Paso 3: búsqueda difusa sobre nombres normalizados
    candidates = list(name_map.keys())
    matches = get_close_matches(norm, candidates, n=1, cutoff=_FUZZY_THRESHOLD)
    if matches:
        logger.debug("Coincidencia difusa: '%s' → '%s'", name, matches[0])
        return name_map[matches[0]]

    logger.debug("No se encontró player_id para: '%s'", name)
    return None


def get_player_name(player_id: int) -> Optional[str]:
    """Devuelve el nombre completo de un jugador dado su player_id."""
    df = _get_players_df()
    if df.empty or "player_id" not in df.columns:
        return None
    rows = df[df["player_id"] == player_id]
    if rows.empty:
        return None
    return str(rows.iloc[0].get("full_name", ""))


def get_ranking_for_player(player_id: int) -> Optional[int]:
    """
    Devuelve el ranking ATP más reciente disponible para un player_id.
    Returns None si el jugador no está en el top 500 o no tiene datos.
    """
    df = _get_rankings_df()
    if df.empty or "player_id" not in df.columns:
        return None
    latest = df["ranking_date"].max()
    rows = df[(df["ranking_date"] == latest) & (df["player_id"] == player_id)]
    if rows.empty:
        return None
    return int(rows.iloc[0]["rank"])


def get_rankings_dict(top_n: int = 300) -> Dict[int, int]:
    """
    Devuelve {player_id: rank} para el ranking actual.
    Útil para enriquecer DataFrames sin iterar fila a fila.
    """
    df = get_current_rankings(top_n=top_n)
    if df.empty or "player_id" not in df.columns or "rank" not in df.columns:
        return {}
    return dict(zip(df["player_id"].astype(int), df["rank"].astype(int)))


def resolve_players(
    name1: str,
    name2: str,
) -> Tuple[Optional[int], Optional[int]]:
    """
    Resuelve los nombres de dos jugadores a sus player_ids en una sola llamada.
    Útil en el pipeline para resolver ambos jugadores de un partido.

    Returns:
        (player1_id, player2_id) — uno o ambos pueden ser None si no se resuelven.
    """
    return get_player_id_by_name(name1), get_player_id_by_name(name2)
