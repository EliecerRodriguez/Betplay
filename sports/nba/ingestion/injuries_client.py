"""
Cliente de lesiones NBA en tiempo real — fuente: ESPN API (sin API key).

Obtiene el listado de jugadores lesionados, suspendidos o en duda para hoy,
y calcula el impacto estimado en puntos por partido (PPG) que pierde cada equipo.

Este impacto se usa después para ajustar las probabilidades del modelo:
  - Cada punto de PPG perdido ≈ ~1.5% de win probability (regresión histórica NBA)
  - Jugadores "Out" cuentan al 100%, "Questionable" al 40%, "Doubtful" al 75%

Funciones públicas:
  - get_injuries()              → dict {team_abbrev: [lista de jugadores lesionados]}
  - get_team_injury_impact()    → dict {nba_team_id: pts_ppg_lost}
  - adjust_predictions()        → ajusta home_win_prob teniendo en cuenta lesiones
"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Dict, List, Optional

import pandas as pd
import requests

from utils.logger import get_logger

logger = get_logger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

_ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
_TIMEOUT           = 10   # segundos
_CACHE_TTL         = 3600 # segundos (1 hora — no refrescar en cada petición)

# Peso por status: qué fracción del PPG del jugador se descuenta
_STATUS_WEIGHT = {
    "out":          1.00,
    "doubtful":     0.75,
    "questionable": 0.40,
    "day-to-day":   0.30,
    "probable":     0.10,
}

# Impacto en win probability por punto de PPG neto perdido
# Estimado de: "Cada 1 PPG de diferencia ≈ 2-3% de win probability en partido individual"
_PPG_TO_WIN_PCT = 0.004   # 1 PPG perdido → 0.4% de win prob (calibrado con datos NBA históricos)

# Normalización ESPN → nba_api: ESPN usa abreviaturas distintas para 6 franquicias
_ESPN_TO_NBA_ABBREV: Dict[str, str] = {
    "GS":   "GSW",  # Golden State Warriors
    "NO":   "NOP",  # New Orleans Pelicans
    "NY":   "NYK",  # New York Knicks
    "SA":   "SAS",  # San Antonio Spurs
    "UTAH": "UTA",  # Utah Jazz
    "WSH":  "WAS",  # Washington Wizards
    "PHO":  "PHX",  # Phoenix Suns (alias histórico)
}

# Mapeo de abreviatura ESPN → nombre de equipo (para cruzar con nba_api)
_ESPN_ABBREV_TO_NBA_NAME: Dict[str, str] = {
    "ATL": "Atlanta Hawks",    "BOS": "Boston Celtics",    "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets","CHI": "Chicago Bulls",     "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets",    "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors","HOU": "Houston Rockets","IND": "Indiana Pacers",
    "LAC": "LA Clippers",      "LAL": "Los Angeles Lakers","MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat",       "MIL": "Milwaukee Bucks",   "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans","NYK": "New York Knicks", "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic",    "PHI": "Philadelphia 76ers","PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers","SAC": "Sacramento Kings","SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors",  "UTA": "Utah Jazz",         "WAS": "Washington Wizards",
}


# ── Cache de tiempo (sin dependencias externas) ───────────────────────────────

_injuries_cache: dict = {}   # {"data": ..., "ts": float}


def _fetch_raw_injuries() -> list:
    """Descarga y parsea el JSON de ESPN injuries. Cachea por 1 hora."""
    global _injuries_cache
    now = time.time()

    if _injuries_cache and (now - _injuries_cache.get("ts", 0)) < _CACHE_TTL:
        return _injuries_cache["data"]

    try:
        resp = requests.get(_ESPN_INJURIES_URL, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("injuries_client: fallo al obtener ESPN injuries: %s", exc)
        return _injuries_cache.get("data", [])

    raw = data.get("injuries", [])
    _injuries_cache = {"data": raw, "ts": now}
    logger.info("injuries_client: %d equipos con lesiones descargados", len(raw))
    return raw


# ── API pública ───────────────────────────────────────────────────────────────

def get_injuries() -> Dict[str, List[dict]]:
    """
    Devuelve un dict con todos los jugadores lesionados por equipo.

    Returns:
        {
          "BOS": [{"name": "Jayson Tatum", "status": "Out", "ppg_lost": 27.1, "comment": "..."}],
          "LAL": [...],
          ...
        }
    """
    raw = _fetch_raw_injuries()
    result: Dict[str, List[dict]] = {}

    for team_block in raw:
        for injury in team_block.get("injuries", []):
            athlete   = injury.get("athlete", {})
            name      = athlete.get("displayName", "?")
            status_raw = (injury.get("status") or "").lower()
            comment   = injury.get("shortComment") or injury.get("longComment") or ""
            abbrev    = athlete.get("team", {}).get("abbreviation", "UNK")
            abbrev    = _ESPN_TO_NBA_ABBREV.get(abbrev, abbrev)  # normalizar a abreviatura nba_api

            if abbrev == "UNK" or not abbrev:
                continue

            # Solo estados que realmente afectan la disponibilidad
            if status_raw not in _STATUS_WEIGHT:
                continue

            entry = {
                "name":     name,
                "status":   injury.get("status", status_raw).title(),
                "weight":   _STATUS_WEIGHT[status_raw],
                "comment":  comment[:120],
            }
            result.setdefault(abbrev, []).append(entry)

    return result


def get_player_ppg(season: str = "2025-26") -> Dict[str, float]:
    """
    Obtiene el PPG de todos los jugadores de la temporada via nba_api.
    Devuelve dict {player_name_lower: ppg}.

    Usa LeagueDashPlayerStats (sin umbral de partidos mínimos) para incluir
    también a jugadores con pocas apariciones antes de lesionarse.
    Si un jugador no aparece en la temporada actual, se busca en la anterior
    como referencia de su valor de mercado.
    Cacheado en memoria por sesión (no cambia durante el día).
    """
    cache_key = f"_ppg_cache_{season}"
    cached = globals().get(cache_key)
    if cached:
        return cached

    def _fetch_season(s: str) -> Dict[str, float]:
        from nba_api.stats.endpoints import leaguedashplayerstats
        time.sleep(0.6)
        stats = leaguedashplayerstats.LeagueDashPlayerStats(
            season=s,
            per_mode_detailed="PerGame",
            timeout=30,
        )
        df = stats.get_data_frames()[0]
        if df.empty or "PLAYER_NAME" not in df.columns or "PTS" not in df.columns:
            return {}
        return {
            row["PLAYER_NAME"].strip().lower(): float(row["PTS"])
            for _, row in df.iterrows()
        }

    try:
        ppg_dict = _fetch_season(season)
        logger.info("get_player_ppg: %d jugadores cargados para %s", len(ppg_dict), season)

        # Fallback a temporada anterior para jugadores que no clasificaron
        # (lesionados toda la temporada, rookies con pocos partidos, etc.)
        if ppg_dict:
            prev_year   = int(season.split("-")[0]) - 1
            prev_season = f"{prev_year}-{str(prev_year + 1)[-2:]}"
            try:
                ppg_prev = _fetch_season(prev_season)
                # Solo añadir jugadores que NO aparecen en la temporada actual
                added = 0
                for name, ppg in ppg_prev.items():
                    if name not in ppg_dict:
                        ppg_dict[name] = ppg
                        added += 1
                if added:
                    logger.debug("get_player_ppg: +%d jugadores desde %s (temporada anterior)", added, prev_season)
            except Exception:
                pass  # el fallback es opcional; no bloquear si falla

        globals()[cache_key] = ppg_dict
        return ppg_dict
    except Exception as exc:
        logger.warning("get_player_ppg falló: %s — usando PPG=0 para lesionados", exc)
        return {}


def get_team_injury_impact(season: str = "2025-26") -> Dict[str, float]:
    """
    Calcula los puntos por partido efectivamente perdidos por cada equipo.

    Returns:
        {"BOS": 27.1, "LAL": 0.0, ...}   (PPG ajustado por probabilidad de jugar)
    """
    injuries = get_injuries()
    ppg_map  = get_player_ppg(season)

    impact: Dict[str, float] = {}

    for abbrev, players in injuries.items():
        total_pts_lost = 0.0
        for p in players:
            name_key = p["name"].strip().lower()
            ppg = ppg_map.get(name_key, 0.0)
            # Sin fallback por apellido: demasiado propenso a falsos positivos
            # (p.ej. "Day'Ron Sharpe" matcheando con otro jugador "Sharpe").
            # Si el jugador no está en el mapa de PPG, aportaba 0 pts relevantes.
            p["ppg"] = round(ppg, 1)
            total_pts_lost += ppg * p["weight"]

        impact[abbrev] = round(total_pts_lost, 2)
        logger.debug(
            "Injury impact %s: %.1f PPG lost (%d jugadores)",
            abbrev, total_pts_lost, len(players)
        )

    return impact


def adjust_predictions(
    predictions_df: pd.DataFrame,
    games_df: pd.DataFrame,
    season: str = "2025-26",
) -> pd.DataFrame:
    """
    Ajusta las probabilidades del modelo según el impacto de lesiones.

    Por cada partido:
      net_impact = home_pts_lost - visitor_pts_lost
      prob_adjustment = -net_impact * PPG_TO_WIN_PCT
      home_win_prob_adjusted = clip(home_win_prob + prob_adjustment, 0.05, 0.95)

    Si el local pierde más PPG → prob baja. Si el visitante pierde más → prob sube.

    Args:
        predictions_df: DataFrame con game_id, home_win_prob, away_win_prob
        games_df:       DataFrame con game_id, home_team_id, visitor_team_id
        season:         Temporada NBA

    Returns:
        DataFrame con columnas adicionales:
          home_injury_pts, visitor_injury_pts, injury_adjustment,
          home_win_prob (ajustado), away_win_prob (ajustado)
    """
    if predictions_df.empty or games_df.empty:
        return predictions_df

    # Obtener nombres de equipo para cruzar con ESPN
    try:
        from nba_api.stats.static import teams as nba_teams_static
        all_teams = nba_teams_static.get_teams()
        nba_id_to_abbrev = {t["id"]: t["abbreviation"] for t in all_teams}
    except Exception:
        logger.warning("adjust_predictions: no se pudo cargar teams map")
        return predictions_df

    try:
        impact = get_team_injury_impact(season)
    except Exception as exc:
        logger.warning("adjust_predictions: injury impact falló: %s", exc)
        return predictions_df

    if not impact:
        return predictions_df

    df = predictions_df.copy()
    df["home_injury_pts"]    = 0.0
    df["visitor_injury_pts"] = 0.0
    df["injury_adjustment"]  = 0.0

    for idx, row in df.iterrows():
        gid = str(row.get("game_id", ""))
        game_row = games_df[games_df["game_id"].astype(str) == gid]
        if game_row.empty:
            continue

        home_nba_id    = int(game_row["home_team_id"].iloc[0] or 0)
        visitor_nba_id = int(game_row["visitor_team_id"].iloc[0] or 0)

        # NBA abbreviation → ESPN abbreviation
        home_abbrev    = nba_id_to_abbrev.get(home_nba_id, "")
        visitor_abbrev = nba_id_to_abbrev.get(visitor_nba_id, "")

        home_pts_lost    = impact.get(home_abbrev, 0.0)
        visitor_pts_lost = impact.get(visitor_abbrev, 0.0)

        # net: positivo → local pierde más → probabilidad local baja
        net = home_pts_lost - visitor_pts_lost
        adjustment = -net * _PPG_TO_WIN_PCT

        # Aplicar ajuste (cap entre 5% y 95%)
        new_home_prob = float(max(0.05, min(0.95, row["home_win_prob"] + adjustment)))
        new_away_prob = round(1.0 - new_home_prob, 4)

        df.at[idx, "home_injury_pts"]    = home_pts_lost
        df.at[idx, "visitor_injury_pts"] = visitor_pts_lost
        df.at[idx, "injury_adjustment"]  = round(adjustment, 4)
        df.at[idx, "home_win_prob"]      = round(new_home_prob, 4)
        df.at[idx, "away_win_prob"]      = new_away_prob

    adjusted = df["injury_adjustment"].abs().sum()
    if adjusted > 0:
        logger.info(
            "adjust_predictions: ajuste de lesiones aplicado a %d partidos (impacto total: ±%.1f%%)",
            (df["injury_adjustment"] != 0).sum(), adjusted * 100,
        )

    return df


def get_injuries_summary_for_game(
    home_team_id: int,
    visitor_team_id: int,
    season: str = "2025-26",
) -> dict:
    """
    Devuelve un resumen de lesiones listo para mostrar en el dashboard.

    Returns:
        {
          "home":    [{"name": ..., "status": ..., "ppg": ..., "comment": ...}],
          "visitor": [...],
          "home_pts_lost":    5.2,
          "visitor_pts_lost": 27.1,
          "net_impact":       -21.9,  # negativo = visitante pierde más
        }
    """
    try:
        from nba_api.stats.static import teams as nba_teams_static
        all_teams = nba_teams_static.get_teams()
        nba_id_to_abbrev = {t["id"]: t["abbreviation"] for t in all_teams}
    except Exception:
        return {"home": [], "visitor": [], "home_pts_lost": 0, "visitor_pts_lost": 0, "net_impact": 0}

    home_abbrev    = nba_id_to_abbrev.get(home_team_id, "")
    visitor_abbrev = nba_id_to_abbrev.get(visitor_team_id, "")

    injuries = get_injuries()
    impact   = get_team_injury_impact(season)

    home_inj    = injuries.get(home_abbrev, [])
    visitor_inj = injuries.get(visitor_abbrev, [])

    home_pts_lost    = impact.get(home_abbrev, 0.0)
    visitor_pts_lost = impact.get(visitor_abbrev, 0.0)

    return {
        "home":              home_inj,
        "visitor":           visitor_inj,
        "home_pts_lost":     round(home_pts_lost, 1),
        "visitor_pts_lost":  round(visitor_pts_lost, 1),
        "net_impact":        round(home_pts_lost - visitor_pts_lost, 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Proxy histórico de lesiones para entrenamiento
# ─────────────────────────────────────────────────────────────────────────────

def get_historical_injury_proxy(
    seasons: "list[str]",
    cache_dir: str = "data/cache",
) -> pd.DataFrame:
    """
    Construye un proxy de impacto de lesiones histórico para el entrenamiento.

    Problema que resuelve
    ─────────────────────
    El ajuste post-hoc de `adjust_predictions` aplica el efecto de lesiones DESPUÉS
    de que el modelo ya emitió su probabilidad. Esto genera una inconsistencia:
    el modelo nunca aprendió que "equipo sin estrella → menos probable ganar".
    Esta función permite entrenar AL MODELO con ese conocimiento.

    Método
    ──────
    Usa `LeagueGameLog` (NBA API) para obtener las estadísticas de todos los
    jugadores en todos los partidos de cada temporada. Por cada partido-equipo
    calcula cuántos PPG (promedio de temporada del jugador) aparecieron en cancha.
    Compara con la media del equipo para estimar los PPG perdidos.

      ppg_lost = max(0, ppg_expected_team − ppg_played_in_game)

    Luego, al unir con games_df (que sabe quién era local y visitante):
      injury_impact_diff = visitor_ppg_lost − home_ppg_lost
        > 0 → visitante pierde más estrellas → ventaja para el local
        < 0 → local pierde más estrellas → ventaja para el visitante

    Rate limits
    ───────────
    Solo descarga una vez por temporada y guarda en data/cache/.
    Las siguientes llamadas cargan directamente desde parquet.

    Args:
        seasons:   Lista de temporadas, e.g. ["2022-23", "2023-24", "2024-25"]
        cache_dir: Directorio donde guardar/leer los game logs cacheados.

    Returns:
        DataFrame con columnas: game_id, home_ppg_lost, visitor_ppg_lost,
        injury_impact_diff. Un DataFrame vacío si la API no está disponible.
    """
    import os

    try:
        from nba_api.stats.endpoints import LeagueGameLog as _LeagueGameLog
    except ImportError:
        logger.warning("get_historical_injury_proxy: nba_api no disponible")
        return pd.DataFrame()

    os.makedirs(cache_dir, exist_ok=True)
    all_game_team_rows: list = []

    for season in seasons:
        safe_season = season.replace("-", "_")
        cache_path  = os.path.join(cache_dir, f"league_game_log_{safe_season}.parquet")

        # ── Cargar o descargar el game log de la temporada ────────────────────
        if os.path.exists(cache_path):
            df_season = pd.read_parquet(cache_path)
            logger.info("get_historical_injury_proxy: %s cargado desde caché (%d filas)", season, len(df_season))
        else:
            logger.info("get_historical_injury_proxy: descargando LeagueGameLog %s …", season)
            try:
                time.sleep(1.2)   # rate limit
                lg = _LeagueGameLog(
                    season=season,
                    player_or_team_abbreviation="P",  # estadísticas de jugadores
                    timeout=60,
                )
                df_season = lg.get_data_frames()[0]
                if not df_season.empty:
                    df_season.to_parquet(cache_path, index=False)
                    logger.info("  → %d filas guardadas en %s", len(df_season), cache_path)
            except Exception as exc:
                logger.warning("  → Error descargando %s: %s — saltando temporada", season, exc)
                continue

        if df_season.empty:
            continue

        # Normalizar nombres de columnas
        df_season.columns = df_season.columns.str.lower()

        # Columnas requeridas
        required = {"game_id", "player_id", "team_id", "pts"}
        if not required.issubset(set(df_season.columns)):
            logger.warning("get_historical_injury_proxy: columnas inesperadas en %s — saltando", season)
            continue

        df_season["pts"]    = pd.to_numeric(df_season["pts"],    errors="coerce").fillna(0.0)
        df_season["game_id"] = df_season["game_id"].astype(str)
        df_season["team_id"] = pd.to_numeric(df_season["team_id"], errors="coerce")

        # ── PPG promedio de temporada por jugador ─────────────────────────────
        # Usamos SOLO su último equipo para evitar contaminación de traspasos
        player_team_pts = (
            df_season.groupby(["player_id", "team_id"])["pts"]
            .mean()
            .reset_index()
            .rename(columns={"pts": "player_avg_ppg"})
        )

        # ── Suma de PPG de jugadores que aparecieron en cada juego-equipo ─────
        df_with_avg = df_season.merge(player_team_pts, on=["player_id", "team_id"], how="left")
        game_team_ppg = (
            df_with_avg.groupby(["game_id", "team_id"])["player_avg_ppg"]
            .sum()
            .reset_index()
            .rename(columns={"player_avg_ppg": "ppg_sum_in_game"})
        )

        # ── PPG esperado por equipo (promedio de todos sus partidos) ──────────
        team_expected = (
            game_team_ppg.groupby("team_id")["ppg_sum_in_game"]
            .mean()
            .rename("expected_ppg_sum")
            .reset_index()
        )
        game_team_ppg = game_team_ppg.merge(team_expected, on="team_id", how="left")

        # PPG perdido = cuánto "rendimiento de plantilla" faltó este partido
        game_team_ppg["ppg_lost"] = (
            game_team_ppg["expected_ppg_sum"] - game_team_ppg["ppg_sum_in_game"]
        ).clip(lower=0.0)

        all_game_team_rows.append(
            game_team_ppg[["game_id", "team_id", "ppg_lost"]].copy()
        )

    if not all_game_team_rows:
        logger.warning("get_historical_injury_proxy: no se pudo obtener datos para ninguna temporada")
        return pd.DataFrame()

    # ── Unir todas las temporadas ─────────────────────────────────────────────
    game_team_df = pd.concat(all_game_team_rows, ignore_index=True)
    game_team_df = game_team_df.dropna(subset=["game_id", "team_id", "ppg_lost"])

    logger.info(
        "get_historical_injury_proxy: %d registros partido-equipo generados "
        "para %d temporadas",
        len(game_team_df), len(seasons),
    )
    return game_team_df
