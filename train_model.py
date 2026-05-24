"""
Script de entrenamiento del modelo NBA.

Descarga datos históricos de múltiples fechas (o temporadas completas),
construye el dataset de features y entrena el RandomForestClassifier.

Uso básico:
    python train_model.py

Uso avanzado:
    python train_model.py --season 2024-25 --days 60 --model random_forest
    python train_model.py --season 2023-24 --season 2024-25 --days 90
    python train_model.py --start-date 2024-10-22 --end-date 2025-04-13

Opciones:
    --season     Temporada(s) a usar para stats (default: 2024-25).
                 Puede repetirse: --season 2023-24 --season 2024-25
    --days       Número de días hacia atrás desde hoy para buscar partidos (default: 60).
    --start-date Fecha de inicio YYYY-MM-DD (alternativa a --days).
    --end-date   Fecha de fin   YYYY-MM-DD (default: ayer).
    --model      Tipo de modelo: random_forest | logistic (default: random_forest).
    --version    Tag del archivo de salida (default: v1).
    --min-games  Mínimo de partidos completos necesarios (default: 30).
    --skip-db    No conectar a la base de datos.
    --dry-run    Solo muestra las fechas y estadísticas, no entrena.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import List, Optional

import pandas as pd

from config.settings import NBA_API_DELAY, NBA_SEASON
from sports.nba.ingestion.injuries_client import get_historical_injury_proxy
from sports.nba.ingestion.nba_client import get_daily_games, get_line_scores, get_team_stats, get_combined_team_stats
from sports.nba.model.predictor import train
from sports.nba.processing.features import (
    build_features,
    clean_games,
    clean_team_stats,
    get_feature_columns,
    prepare_training_dataset,
)
from utils.logger import get_logger

logger = get_logger("train_model")


# ─────────────────────────────────────────────────────────────────────────────
# Generación de rango de fechas
# ─────────────────────────────────────────────────────────────────────────────

def _date_range(start: date, end: date) -> List[date]:
    """Devuelve lista de fechas (inclusive) entre start y end."""
    days = (end - start).days
    return [start + timedelta(days=i) for i in range(days + 1)]


# ─────────────────────────────────────────────────────────────────────────────
# Carga de partidos históricos con marcadores finales
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_completed_games(dates: List[date], cache_dir: str = "data/cache") -> pd.DataFrame:
    """
    Itera sobre las fechas y acumula partidos ya jugados (con marcador final).

    Usa caché en disco (data/cache/YYYY-MM-DD.parquet) para no re-descargar
    fechas ya procesadas. Borra el archivo de caché si quieres re-descargar.

    Args:
        dates:     Lista de fechas a consultar.
        cache_dir: Directorio donde guardar los archivos de caché.

    Returns:
        DataFrame con partidos completados y columnas:
        game_id, home_team_id, visitor_team_id, home_pts, visitor_pts,
        home_win (1 = victoria local, 0 = victoria visitante).
    """
    os.makedirs(cache_dir, exist_ok=True)
    all_games: List[pd.DataFrame] = []
    total   = len(dates)
    skipped = 0
    cached  = 0

    for i, game_date in enumerate(dates, 1):
        date_str   = game_date.strftime("%Y-%m-%d")
        cache_path = os.path.join(cache_dir, f"{date_str}.parquet")

        # ── Caché hit ────────────────────────────────────────────
        if os.path.exists(cache_path):
            try:
                cached_df = pd.read_parquet(cache_path)
                if not cached_df.empty:
                    all_games.append(cached_df)
                cached += 1
                continue
            except Exception:
                pass  # caché corrupta → re-descargar

        logger.info("[%d/%d] Cargando partidos del %s …", i, total, date_str)

        try:
            # Partidos del día
            games_df = get_daily_games(date_str)
            if games_df.empty:
                # Guardar parquet vacío para no reintentar
                pd.DataFrame().to_parquet(cache_path)
                skipped += 1
                continue

            # Marcadores finales del día
            scores_df = get_line_scores(date_str)

            if scores_df.empty:
                pd.DataFrame().to_parquet(cache_path)
                skipped += 1
                continue

            # Unir marcadores a los partidos
            merged = _merge_scores(games_df, scores_df)
            completed = merged[merged["home_pts"].notna() & (merged["home_pts"] > 0)]

            if completed.empty:
                pd.DataFrame().to_parquet(cache_path)
                skipped += 1
                continue

            # Guardar en caché
            completed.to_parquet(cache_path, index=False)
            all_games.append(completed)
            logger.info("  → %d partidos completados encontrados", len(completed))

        except Exception as exc:  # noqa: BLE001
            logger.warning("  → Error en %s: %s. Saltando.", date_str, exc)
            skipped += 1

        # Rate-limit
        time.sleep(NBA_API_DELAY)

    if not all_games:
        return pd.DataFrame()

    result = pd.concat(all_games, ignore_index=True)
    result = result.drop_duplicates(subset=["game_id"])
    logger.info(
        "Total partidos completados: %d (de %d fechas; %d en caché; %d días sin datos)",
        len(result), total, cached, skipped,
    )
    return result


def _merge_scores(games_df: pd.DataFrame, scores_df: pd.DataFrame) -> pd.DataFrame:
    """
    Añade columnas home_pts / visitor_pts al DataFrame de partidos
    a partir del DataFrame de line_scores.

    line_scores tiene una fila por equipo por partido. Identificamos
    el equipo local comparando team_id con home_team_id del DataFrame
    de partidos.
    """
    games = games_df.copy()

    # Asegurar que los IDs son del mismo tipo para el merge
    scores_df = scores_df.copy()
    scores_df["team_id"] = pd.to_numeric(scores_df["team_id"], errors="coerce")
    games["home_team_id"]    = pd.to_numeric(games["home_team_id"],    errors="coerce")
    games["visitor_team_id"] = pd.to_numeric(games["visitor_team_id"], errors="coerce")

    # Extraer puntos del equipo local
    home_scores = (
        scores_df[["game_id", "team_id", "pts"]]
        .rename(columns={"team_id": "home_team_id", "pts": "home_pts"})
    )
    # Extraer puntos del equipo visitante
    away_scores = (
        scores_df[["game_id", "team_id", "pts"]]
        .rename(columns={"team_id": "visitor_team_id", "pts": "visitor_pts"})
    )

    games = games.merge(home_scores, on=["game_id", "home_team_id"], how="left")
    games = games.merge(away_scores, on=["game_id", "visitor_team_id"], how="left")

    # Convertir a numérico
    for col in ["home_pts", "visitor_pts"]:
        games[col] = pd.to_numeric(games[col], errors="coerce")

    # Calcular resultado (1 = victoria local)
    games["home_win"] = (games["home_pts"] > games["visitor_pts"]).astype(int)

    return games


# ─────────────────────────────────────────────────────────────────────────────
# Stats rolling (point-in-time) — elimina look-ahead bias en entrenamiento
# ─────────────────────────────────────────────────────────────────────────────

def _compute_rolling_stats_for_training(games_df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula estadísticas punto-en-el-tiempo (point-in-time) para cada equipo
    en cada partido, usando ÚNICAMENTE partidos completados ANTES de esa fecha.

    Problema que resuelve
    ─────────────────────
    Cuando entrenamos, si usamos los stats de toda la temporada (ej. win% final
    de abril) para un partido de noviembre, el modelo ve datos futuros que no
    existían en el momento del partido (look-ahead bias). Esto infla el accuracy
    en entrenamiento pero reduce la generalización en producción.

    Stats calculados (solo requieren scores finales, ya disponibles en caché):
      - w_pct_rolling  : win% acumulado hasta ese partido
      - pts_rolling    : promedio de puntos anotados por partido
      - pts_all_rolling: promedio de puntos recibidos

    Para equipos con < MIN_GAMES partidos previos se devuelve NaN
    (el imputer de medianas del pipeline lo resolverá).

    Args:
        games_df: DataFrame con columnas game_id, home_team_id, visitor_team_id,
                  home_pts, visitor_pts, home_win, game_date (o game_date_est).

    Returns:
        DataFrame con columnas game_id + {home,visitor}_{w_pct,pts,pts_all}_rolling
    """
    MIN_GAMES = 5   # mínimo de partidos previos para que el stat sea confiable

    df = games_df.copy()

    # Normalizar columna de fecha
    if "game_date" not in df.columns:
        if "game_date_est" in df.columns:
            df["game_date"] = pd.to_datetime(df["game_date_est"]).dt.date
        else:
            logger.warning("_compute_rolling_stats: sin columna de fecha — retornando sin rolling stats")
            return pd.DataFrame({"game_id": df["game_id"]})

    df["game_date"] = pd.to_datetime(df["game_date"]).dt.date
    df = df.sort_values("game_date").reset_index(drop=True)

    # Asegurar tipos numéricos
    for col in ("home_pts", "visitor_pts", "home_win"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Acumuladores por equipo: {team_id: {"w": wins, "g": games, "pts_f": scored, "pts_a": allowed}}
    team_acc: dict = {}

    rows_out = []
    for _, row in df.iterrows():
        home_id = int(row["home_team_id"])
        vis_id  = int(row["visitor_team_id"])
        gid     = str(row["game_id"])

        def _stat(tid: int) -> dict:
            return team_acc.get(tid, {"w": 0, "g": 0, "pts_f": 0.0, "pts_a": 0.0})

        hs = _stat(home_id)
        vs = _stat(vis_id)

        def _wpct(s: dict) -> "float | None":
            return (s["w"] / s["g"]) if s["g"] >= MIN_GAMES else float("nan")

        def _pts_f(s: dict) -> "float | None":
            return (s["pts_f"] / s["g"]) if s["g"] >= MIN_GAMES else float("nan")

        def _pts_a(s: dict) -> "float | None":
            return (s["pts_a"] / s["g"]) if s["g"] >= MIN_GAMES else float("nan")

        rows_out.append({
            "game_id":                    gid,
            "home_w_pct_rolling":         _wpct(hs),
            "visitor_w_pct_rolling":      _wpct(vs),
            "home_pts_rolling":           _pts_f(hs),
            "visitor_pts_rolling":        _pts_f(vs),
            "home_pts_allowed_rolling":   _pts_a(hs),
            "visitor_pts_allowed_rolling": _pts_a(vs),
        })

        # Actualizar acumuladores CON el resultado de este partido (post-hoc)
        home_won = int(row["home_win"]) if pd.notna(row.get("home_win")) else 0
        h_pts  = float(row["home_pts"])    if pd.notna(row.get("home_pts"))    else 0.0
        v_pts  = float(row["visitor_pts"]) if pd.notna(row.get("visitor_pts")) else 0.0

        team_acc[home_id] = {
            "w":     hs["w"] + home_won,
            "g":     hs["g"] + 1,
            "pts_f": hs["pts_f"] + h_pts,
            "pts_a": hs["pts_a"] + v_pts,
        }
        team_acc[vis_id] = {
            "w":     vs["w"] + (1 - home_won),
            "g":     vs["g"] + 1,
            "pts_f": vs["pts_f"] + v_pts,
            "pts_a": vs["pts_a"] + h_pts,
        }

    result = pd.DataFrame(rows_out)
    logger.info(
        "_compute_rolling_stats: %d partidos procesados | "
        "home_w_pct_rolling válido en %.1f%% de casos",
        len(result),
        result["home_w_pct_rolling"].notna().mean() * 100,
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Construcción del dataset de entrenamiento
# ─────────────────────────────────────────────────────────────────────────────

def build_training_data(
    dates: List[date],
    seasons: List[str],
    cache_dir: str = "data/cache",
    enrich_form: bool = False,
    enrich_travel: bool = False,
) -> Optional[tuple[pd.DataFrame, pd.Series]]:
    """
    Descarga datos históricos y construye (X, y) para entrenamiento.

    Args:
        dates:         Lista de fechas a procesar.
        seasons:       Lista de temporadas para obtener estadísticas de equipo.
        cache_dir:     Directorio de caché de partidos diarios.
        enrich_form:   Añadir features de forma reciente.
        enrich_travel: Añadir features de viaje/jet lag (requiere enrich_form).

    Returns:
        Tupla (X, y) o None si no hay suficientes datos.
    """
    # 1. Estadísticas de equipo (promediamos si hay varias temporadas)
    logger.info("Cargando estadísticas de %d temporada(s): %s", len(seasons), seasons)
    stats_frames: List[pd.DataFrame] = []

    for season in seasons:
        try:
            df = get_team_stats(season)
            if not df.empty:
                df["season"] = season
                stats_frames.append(df)
                logger.info("  → %d equipos para temporada %s", len(df), season)
        except Exception as exc:  # noqa: BLE001
            logger.warning("  → Error cargando stats %s: %s", season, exc)
        time.sleep(NBA_API_DELAY)

    if not stats_frames:
        logger.error("No se pudieron obtener estadísticas de ninguna temporada.")
        return None

    # Usar la m\u00e1s reciente con estad\u00edsticas avanzadas (ORTG, DRTG, Pace, TS%)
    try:
        team_stats_df = clean_team_stats(get_combined_team_stats(seasons[-1]))
        logger.info("Stats avanzadas cargadas: %d columnas", len(team_stats_df.columns))
    except Exception as exc:
        logger.warning("get_combined_team_stats fall\u00f3 (%s) \u2014 usando solo stats b\u00e1sicas", exc)
        team_stats_df = clean_team_stats(stats_frames[-1])

    # 2. Partidos históricos completados
    games_df = _fetch_completed_games(dates, cache_dir=cache_dir)

    if games_df.empty:
        logger.error("No se encontraron partidos completados en el rango de fechas.")
        return None

    # 3. Feature engineering
    logger.info("Construyendo features para %d partidos …", len(games_df))

    # Enriquecer con forma reciente si se pidió
    if enrich_form and seasons:
        logger.info("Enriqueciendo con forma reciente (puede tardar varios minutos)…")
        try:
            from sports.nba.ingestion.recent_form import enrich_with_form
            if "game_date" not in games_df.columns and "game_date_est" in games_df.columns:
                games_df["game_date"] = pd.to_datetime(games_df["game_date_est"]).dt.date
            games_df = enrich_with_form(games_df, season=seasons[-1], n=5)
        except Exception as exc:
            logger.warning("enrich_with_form fall\u00f3 (%s) \u2014 se entrena sin forma reciente", exc)
    # Enriquecer con viaje / jet lag si se pidió
    if enrich_travel and seasons:
        logger.info("Enriqueciendo con datos de viaje y jet lag…")
        try:
            from sports.nba.ingestion.travel_client import enrich_with_travel
            if "game_date" not in games_df.columns and "game_date_est" in games_df.columns:
                games_df["game_date"] = pd.to_datetime(games_df["game_date_est"]).dt.date
            games_df = enrich_with_travel(games_df, season=seasons[-1])
        except Exception as exc:
            logger.warning("enrich_with_travel falló (%s) — se entrena sin features de viaje", exc)
    # Enriquecer con Elo (procesa partidos en orden cronol\u00f3gico)
    try:
        from sports.nba.ingestion.elo import enrich_with_elo, get_current_elos, save_current_elos
        if "game_date" not in games_df.columns and "game_date_est" in games_df.columns:
            games_df["game_date"] = pd.to_datetime(games_df["game_date_est"]).dt.date
        games_df = enrich_with_elo(games_df)
        logger.info("Elo enriquecido: elo_diff range [%.0f, %.0f]",
                    games_df["elo_diff"].min(), games_df["elo_diff"].max())
    except Exception as exc:
        logger.warning("enrich_with_elo fall\u00f3 (%s) \u2014 se entrena sin features Elo", exc)
    # Enriquecer con contexto de clasificación (standings)
    # Usamos standings actuales para todos los partidos del período de entrenamiento:
    # es una aproximación válida para las últimas 8-12 semanas (valores estables).
    try:
        from sports.nba.ingestion.standings_client import enrich_with_standings
        if "game_date" not in games_df.columns and "game_date_est" in games_df.columns:
            games_df["game_date"] = pd.to_datetime(games_df["game_date_est"]).dt.date
        games_df = enrich_with_standings(games_df, seasons[-1])
        logger.info("Standings enriquecidos en dataset de entrenamiento")
    except Exception as exc:
        logger.warning("enrich_with_standings falló (%s) — se entrena sin features de clasificación", exc)
    feature_df = build_features(games_df, team_stats_df)

    if feature_df.empty:
        logger.error("El DataFrame de features quedó vacío tras el join.")
        return None

    # ── Rolling stats (point-in-time) ────────────────────────────────────────
    # Sobreescribe home_w_pct / visitor_w_pct / home_pts / visitor_pts con
    # valores calculados solo con partidos ANTERIORES a cada game_date.
    # Elimina el look-ahead bias: al entrenar un partido de noviembre,
    # el modelo ya no "ve" los stats de abril de la misma temporada.
    try:
        rolling_df = _compute_rolling_stats_for_training(games_df)
        if not rolling_df.empty and "home_w_pct_rolling" in rolling_df.columns:
            feature_df = feature_df.merge(rolling_df, on="game_id", how="left")

            # Sobreescribir solo donde el rolling stat es válido (≥ MIN_GAMES previos)
            for side in ("home", "visitor"):
                wpct_roll = f"{side}_w_pct_rolling"
                wpct_orig = f"{side}_w_pct"
                if wpct_roll in feature_df.columns and wpct_orig in feature_df.columns:
                    valid = feature_df[wpct_roll].notna()
                    feature_df.loc[valid, wpct_orig] = feature_df.loc[valid, wpct_roll]

                pts_roll = f"{side}_pts_rolling"
                pts_orig = f"{side}_pts"
                if pts_roll in feature_df.columns and pts_orig in feature_df.columns:
                    valid = feature_df[pts_roll].notna()
                    feature_df.loc[valid, pts_orig] = feature_df.loc[valid, pts_roll]

            # Recomputar features derivadas que dependen de w_pct y pts
            if "home_w_pct" in feature_df.columns and "visitor_w_pct" in feature_df.columns:
                feature_df["wpct_diff"] = feature_df["home_w_pct"] - feature_df["visitor_w_pct"]

            # Limpiar columnas temporales de rolling
            roll_tmp = [c for c in feature_df.columns if c.endswith("_rolling")]
            feature_df.drop(columns=roll_tmp, inplace=True, errors="ignore")

            logger.info(
                "Rolling stats aplicados: wpct_diff y pts recalculados sin look-ahead bias "
                "(%d/%d filas con w_pct rolling válido)",
                (feature_df["wpct_diff"].notna()).sum(), len(feature_df),
            )
        else:
            logger.warning("Rolling stats vacíos — se usan stats de temporada completa")
    except Exception as exc:
        logger.warning(
            "Rolling stats fallaron (%s) — continuando con stats de temporada completa", exc
        )

    # build_features busca 'home_team_score'/'visitor_team_score' para home_win,
    # pero _merge_scores generó 'home_pts'/'visitor_pts'.  Recuperamos home_win
    # desde games_df (que ya tiene el resultado calculado por _merge_scores).
    if "home_win" not in feature_df.columns or feature_df["home_win"].isna().all():
        if "home_win" in games_df.columns:
            hw = games_df[["game_id", "home_win"]].copy()
            # Eliminar columna preexistente si existe (NaN) antes del merge
            if "home_win" in feature_df.columns:
                feature_df = feature_df.drop(columns=["home_win"])
            feature_df = feature_df.merge(hw, on="game_id", how="left")
            logger.info(
                "home_win inyectado desde marcadores: %d/%d partidos con resultado conocido",
                feature_df["home_win"].notna().sum(), len(feature_df),
            )

    # ── Proxy de lesiones históricas (point-in-time) ──────────────────────────
    # Añade injury_impact_diff = visitor_ppg_lost - home_ppg_lost.
    # Para partidos de temporadas completas disponemos de LeagueGameLog;
    # para los que no hay datos se rellena con 0 (neutral — sin información).
    try:
        game_team_df = get_historical_injury_proxy(seasons, cache_dir)
        if not game_team_df.empty:
            # Pivotar a formato ancho: una fila por partido con home/visitor ppg_lost
            game_team_df["team_id"] = game_team_df["team_id"].astype(float)
            home_inj = (
                games_df[["game_id", "home_team_id"]]
                .assign(home_team_id=games_df["home_team_id"].astype(float))
                .merge(
                    game_team_df.rename(columns={"team_id": "home_team_id", "ppg_lost": "home_ppg_lost"}),
                    on=["game_id", "home_team_id"], how="left",
                )[["game_id", "home_ppg_lost"]]
            )
            vis_inj = (
                games_df[["game_id", "visitor_team_id"]]
                .assign(visitor_team_id=games_df["visitor_team_id"].astype(float))
                .merge(
                    game_team_df.rename(columns={"team_id": "visitor_team_id", "ppg_lost": "visitor_ppg_lost"}),
                    on=["game_id", "visitor_team_id"], how="left",
                )[["game_id", "visitor_ppg_lost"]]
            )
            inj_wide = home_inj.merge(vis_inj, on="game_id", how="outer")
            inj_wide["injury_impact_diff"] = (
                inj_wide["visitor_ppg_lost"].fillna(0) - inj_wide["home_ppg_lost"].fillna(0)
            )
            feature_df = feature_df.merge(
                inj_wide[["game_id", "injury_impact_diff"]], on="game_id", how="left"
            )
            # Rellenar partidos sin datos de lesiones con 0 (neutral)
            feature_df["injury_impact_diff"] = feature_df["injury_impact_diff"].fillna(0.0)
            n_with = (feature_df["injury_impact_diff"] != 0).sum()
            logger.info(
                "Injury proxy añadido: %d/%d partidos con impacto != 0 (resto = 0 neutral)",
                n_with, len(feature_df),
            )
        else:
            feature_df["injury_impact_diff"] = 0.0
            logger.warning("Injury proxy vacío — usando 0 neutral para todos los partidos")
    except Exception as exc:
        feature_df["injury_impact_diff"] = 0.0
        logger.warning("Injury proxy falló (%s) — usando 0 neutral para todos los partidos", exc)

    # 4. Preparar dataset (filtra filos sin target, etc.)
    X, y = prepare_training_dataset(feature_df)

    logger.info(
        "Dataset listo: %d muestras × %d features | victorias locales: %.1f%%",
        len(X), X.shape[1], y.mean() * 100,
    )
    # Guardar Elo actuales para uso en producci\u00f3n (web app)
    try:
        from sports.nba.ingestion.elo import get_current_elos, save_current_elos
        current_elos = get_current_elos(games_df)
        save_current_elos(current_elos)
    except Exception as exc:
        logger.warning("No se pudo guardar Elos actuales: %s", exc)
    return X, y


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Entrena el modelo NBA con datos históricos reales.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Rango temporal
    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument(
        "--days",
        type=int,
        default=60,
        metavar="N",
        help="Días hacia atrás desde ayer para buscar partidos (default: 60).",
    )
    date_group.add_argument(
        "--start-date",
        type=str,
        metavar="YYYY-MM-DD",
        help="Fecha de inicio del rango (alternativa a --days).",
    )

    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Fecha de fin del rango (default: ayer).",
    )

    # Temporadas
    parser.add_argument(
        "--season",
        action="append",
        dest="seasons",
        default=None,
        metavar="YYYY-YY",
        help="Temporada(s) para stats de equipo. Puede repetirse.",
    )

    # Modelo
    parser.add_argument(
        "--model",
        choices=["random_forest", "logistic", "xgboost", "ensemble", "stacking"],
        default="stacking",
        help="Tipo de clasificador (default: stacking).",
    )
    parser.add_argument(
        "--with-form",
        action="store_true",
        help="Incluir features de forma reciente y rest days (más lento).",
    )
    parser.add_argument(
        "--with-travel",
        action="store_true",
        help="Incluir features de viaje y jet lag (requiere --with-form).",
    )
    parser.add_argument(
        "--optimize",
        type=int,
        default=0,
        metavar="N",
        help="N\u00famero de trials Optuna para optimizar hiperpar\u00e1metros XGBoost (0=desactivado).",
    )
    parser.add_argument(
        "--version",
        type=str,
        default="v1",
        help="Tag del archivo de modelo guardado (default: v1).",
    )
    parser.add_argument(
        "--min-games",
        type=int,
        default=30,
        metavar="N",
        help="Mínimo de partidos completados necesarios para entrenar (default: 30).",
    )

    # Flags
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo muestra fechas y stats; no descarga partidos ni entrena.",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default="data/cache",
        metavar="DIR",
        help="Directorio de caché de partidos diarios (default: data/cache).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignorar caché y re-descargar todos los datos.",
    )

    return parser.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    args = parse_args()

    # ── Calcular rango de fechas ──────────────────────────────────────────────
    yesterday = date.today() - timedelta(days=1)

    if args.end_date:
        try:
            end = datetime.strptime(args.end_date, "%Y-%m-%d").date()
        except ValueError:
            logger.error("Formato de --end-date inválido. Use YYYY-MM-DD.")
            return 1
    else:
        end = yesterday

    if args.start_date:
        try:
            start = datetime.strptime(args.start_date, "%Y-%m-%d").date()
        except ValueError:
            logger.error("Formato de --start-date inválido. Use YYYY-MM-DD.")
            return 1
    else:
        start = end - timedelta(days=args.days - 1)

    dates = _date_range(start, end)

    # ── Temporadas ────────────────────────────────────────────────────────────
    seasons: List[str] = args.seasons if args.seasons else [NBA_SEASON]

    # ── Caché ─────────────────────────────────────────────────────────────────
    cache_dir = args.cache_dir
    if args.no_cache and os.path.exists(cache_dir):
        import shutil
        shutil.rmtree(cache_dir)
        logger.info("--no-cache: caché eliminada en %s", cache_dir)

    # ── Resumen del plan ──────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("ENTRENAMIENTO DEL MODELO NBA")
    logger.info("=" * 60)
    logger.info("Rango de fechas : %s → %s (%d días)", start, end, len(dates))
    logger.info("Temporada(s)    : %s", ", ".join(seasons))
    logger.info("Modelo          : %s", args.model)
    logger.info("Versión         : %s", args.version)
    logger.info("Min. partidos   : %d", args.min_games)
    logger.info("Caché           : %s", cache_dir)
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("--dry-run activado. No se descarga ni se entrena.")
        logger.info("Fechas a procesar: %s … %s", dates[0], dates[-1])
        return 0

    # ── Construir dataset ─────────────────────────────────────────────────────
    enrich_form   = getattr(args, "with_form",   False)
    enrich_travel = getattr(args, "with_travel", False) and enrich_form
    if getattr(args, "with_travel", False) and not enrich_form:
        logger.warning("--with-travel requiere --with-form; activando ambos.")
        enrich_form   = True
        enrich_travel = True
    result = build_training_data(
        dates, seasons, cache_dir=cache_dir,
        enrich_form=enrich_form, enrich_travel=enrich_travel,
    )

    if result is None:
        logger.error("No se pudo construir el dataset. Abortando.")
        return 1

    X, y = result

    if len(X) < args.min_games:
        logger.error(
            "Solo se encontraron %d partidos (mínimo requerido: %d). "
            "Amplía el rango con --days o --start-date.",
            len(X), args.min_games,
        )
        return 1

    # ── Entrenar ──────────────────────────────────────────────────────────────
    logger.info("Iniciando entrenamiento …")
    try:
        metrics = train(X, y, model_type=args.model, version=args.version)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error durante el entrenamiento: %s", exc)
        return 1

    # ── Resultados ────────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("MODELO ENTRENADO EXITOSAMENTE")
    logger.info("  Accuracy (test)  : %.3f", metrics.get("accuracy", 0))
    logger.info("  ROC-AUC (test)   : %.3f", metrics.get("roc_auc", 0))
    logger.info("  Log Loss         : %.4f", metrics.get("log_loss", 0))
    logger.info("  Brier Score      : %.4f", metrics.get("brier", 0))
    logger.info("  CV AUC (TSS)     : %.3f \u00b1 %.3f",
                metrics.get("cv_mean", 0), metrics.get("cv_std", 0))
    logger.info("  Muestras usadas  : %d", metrics.get("n_samples", len(X)))
    logger.info("  Features         : %d", metrics.get("n_features", X.shape[1]))
    logger.info("  Archivo          : models/nba_model_%s.joblib", args.version)
    logger.info("=" * 60)
    logger.info("El pipeline principal ahora usará el modelo entrenado en vez de la heurística.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
