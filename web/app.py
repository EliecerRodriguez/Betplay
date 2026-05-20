"""
Dashboard web para NBA Betplay Analytics.

Iniciar:
    uvicorn web.app:app --reload --port 8000

Acceder en:  http://localhost:8000
"""
from __future__ import annotations

import os
import sys
from datetime import date as _date_cls
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from nba_api.stats.static import teams as nba_teams_static

from config.settings import NBA_SEASON
from sports.nba.ingestion.elo import apply_elos_to_games, load_current_elos
from sports.nba.ingestion.injuries_client import adjust_predictions, get_injuries_summary_for_game
from sports.nba.ingestion.nba_client import get_combined_team_stats, get_daily_games, get_line_scores, get_team_stats
from sports.nba.ingestion.odds_client import get_odds
from sports.nba.ingestion.recent_form import enrich_with_form
from sports.nba.model.predictor import predict
from sports.nba.model.value_detector import detect_value_bets
from sports.nba.processing.features import build_features
from utils.logger import get_logger

logger = get_logger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="NBA Betplay Analytics", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

_STATIC_DIR    = os.path.join(os.path.dirname(__file__), "static")
_BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SNAPSHOTS_DIR = os.path.join(_BASE_DIR, "output", "snapshots")
_TEAMS_MAP: dict[int, str] = {t["id"]: t["full_name"] for t in nba_teams_static.get_teams()}


@app.on_event("startup")
async def _startup_preload() -> None:
    """Pre-carga stats de equipo y reconcilia resultados pasados en background."""
    import asyncio
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _get_team_stats_cached)
    loop.run_in_executor(None, _fetch_and_reconcile_results)
    logger.info("Startup: pre-carga de team stats y reconciliación de resultados lanzadas en background")

_CACHE: dict[str, dict] = {}   # key: date_str -> {"data": ..., "ts": float}
_CACHE_TTL = 10 * 60           # 10 minutos — refresca cuotas y predicciones automáticamente

# Caché de stats de equipo (se renueva cada 6 h para no saturar la NBA API en cada request)
_TEAM_STATS_CACHE: dict = {"df": None, "ts": 0.0}
_TEAM_STATS_TTL = 6 * 3600   # segundos

# Caché de cuotas por fecha (30 min) para evitar scraping repetido
_ODDS_CACHE: dict[str, dict] = {}   # key: date_str -> {"df": DataFrame, "ts": float}
_ODDS_TTL = 30 * 60   # 30 minutos

# ── Persistencia (Supabase / PostgreSQL) ─────────────────────────────────────

_repo = None   # singleton lazy — se inicializa en el primer request

def _get_repo():
    """Devuelve el repositorio de BD o None si no está configurado."""
    global _repo
    if _repo is not None:
        return _repo
    from config.settings import DATABASE_URL
    if not DATABASE_URL or "localhost" in DATABASE_URL:
        return None
    try:
        from sports.nba.database.repository import DatabaseRepository
        _repo = DatabaseRepository(DATABASE_URL)
        logger.info("Repositorio BD conectado a Supabase")
    except Exception as exc:
        logger.warning("BD no disponible (%s) — la app funciona sin persistencia", exc)
        _repo = None
    return _repo


def _persist_to_db(
    games_df: "pd.DataFrame",
    predictions_df: "pd.DataFrame",
    odds_df: "pd.DataFrame",
    value_bets_df: "pd.DataFrame",
    date_str: str,
) -> None:
    """Persiste los DataFrames del pipeline en Supabase. Se ejecuta en hilo separado."""
    repo = _get_repo()
    if repo is None:
        return
    from datetime import date as _d
    today = _d.today()
    try:
        # Games
        if not games_df.empty:
            gdf = games_df.copy()
            gdf["fetch_date"] = today
            gdf["season"] = NBA_SEASON
            repo.upsert_games(gdf)
        # Predictions — añadir columnas de contexto que necesita la tabla
        if not predictions_df.empty:
            pdf = predictions_df.copy()
            pdf["fetch_date"] = today
            if "game_date" not in pdf.columns:
                pdf["game_date"] = today
            # Unir home/visitor team_id desde games_df si faltan
            for col in ("home_team_id", "visitor_team_id"):
                if col not in pdf.columns and not games_df.empty and col in games_df.columns:
                    pdf = pdf.merge(
                        games_df[["game_id", col]], on="game_id", how="left"
                    )
            repo.upsert_predictions(pdf)
        # Odds
        if not odds_df.empty:
            odf = odds_df.copy()
            odf["fetch_date"] = today
            repo.upsert_odds(odf)
        # Value bets
        if not value_bets_df.empty:
            vdf = value_bets_df.copy()
            vdf["fetch_date"] = today
            if "game_date" not in vdf.columns:
                vdf["game_date"] = today
            repo.upsert_value_bets(vdf)
        logger.info("Persistencia Supabase completada para %s", date_str)
    except Exception as exc:
        logger.warning("Error persitiendo en BD para %s: %s", date_str, exc)

def _get_team_stats_cached() -> "pd.DataFrame":
    """Devuelve stats de equipo cacheadas; recarga si TTL expiró o primera vez."""
    import time
    now = time.time()
    if _TEAM_STATS_CACHE["df"] is None or (now - _TEAM_STATS_CACHE["ts"]) > _TEAM_STATS_TTL:
        try:
            df = get_combined_team_stats(NBA_SEASON)
        except Exception as exc:
            logger.warning("get_combined_team_stats falló (%s) — usando stats básicas", exc)
            df = get_team_stats(NBA_SEASON)
        _TEAM_STATS_CACHE["df"] = df
        _TEAM_STATS_CACHE["ts"] = now
        logger.info("Team stats cacheadas: %d equipos, %d cols", len(df), len(df.columns))
    return _TEAM_STATS_CACHE["df"]


def _get_odds_cached(games_df: "pd.DataFrame", date_str: str) -> "pd.DataFrame":
    """Devuelve cuotas cacheadas por fecha; vuelve a scrapear si TTL expiró."""
    import time
    now = time.time()
    cached = _ODDS_CACHE.get(date_str)
    if cached and (now - cached["ts"]) < _ODDS_TTL:
        logger.info("Cuotas para %s servidas desde caché", date_str)
        return cached["df"]
    try:
        df = get_odds(games_df)
    except Exception as exc:
        logger.warning("get_odds falló: %s — devolviendo DataFrame vacío", exc)
        df = pd.DataFrame()
    _ODDS_CACHE[date_str] = {"df": df, "ts": now}
    logger.info("Cuotas para %s cacheadas (%d filas)", date_str, len(df))
    return df


# ── Helpers ───────────────────────────────────────────────────────────────────

def _kelly(prob: float, decimal_odds: float) -> float:
    """Full Kelly fraction (0–1). Retorna 0 si no hay valor."""
    b = decimal_odds - 1.0
    if b <= 0 or prob <= 0 or prob >= 1:
        return 0.0
    return max(0.0, (b * prob - (1.0 - prob)) / b)


def _handicap_label(fav_prob: float, home_is_fav: bool) -> str:
    """Handicap referencial aproximado desde probabilidad."""
    if fav_prob < 0.53:
        return "Pick'em"
    thresholds = [(0.88, 15), (0.83, 12.5), (0.78, 10), (0.73, 7.5),
                  (0.68, 5.5), (0.62, 3.5), (0.55, 1.5), (0.53, 0.5)]
    spread = next((s for t, s in thresholds if fav_prob >= t), 0.5)
    sign = "-" if home_is_fav else "+"
    return f"{sign}{spread}"


def _utc_to_et(utc_str: str) -> str:
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        dt_et = dt.astimezone(ZoneInfo("America/New_York"))
        return dt_et.strftime("%I:%M %p ET").lstrip("0")
    except Exception:
        return "TBD"


def _find_arbitrage(odds_list: list, home_name: str, away_name: str) -> dict | None:
    """Detecta arbitraje: mejor cuota local en cualquier casa + mejor visitante en cualquier otra."""
    if not odds_list:
        return None
    valid = [o for o in odds_list if o["home_odds"] > 1.01 and o["away_odds"] > 1.01]
    if not valid:
        return None
    best_h = max(valid, key=lambda x: x["home_odds"])
    best_a = max(valid, key=lambda x: x["away_odds"])
    bh, ba = best_h["home_odds"], best_a["away_odds"]
    implied = 1 / bh + 1 / ba
    if implied >= 1.0:
        return None
    profit_pct    = round((1 / implied - 1) * 100, 2)
    stake_home_pct = round((1 / bh) / implied * 100, 1)
    stake_away_pct = round((1 / ba) / implied * 100, 1)
    return {
        "profit_pct":  profit_pct,
        "home_side": {"team": home_name, "bookmaker": best_h["bookmaker"], "odds": bh, "stake_pct": stake_home_pct},
        "away_side": {"team": away_name, "bookmaker": best_a["bookmaker"], "odds": ba, "stake_pct": stake_away_pct},
    }


def _best_action(game: dict) -> dict:
    """Devuelve la acción más clara y directa para el partido."""
    arb = game.get("arb")
    if arb:
        return {
            "type":   "arb",
            "label":  f"ARBITRAJE DISPONIBLE · +{arb['profit_pct']}% GARANTIZADO",
            "line1":  f"▶ Apuesta {arb['home_side']['stake_pct']}% del bankroll a {arb['home_side']['team']} en {arb['home_side']['bookmaker']} (cuota {arb['home_side']['odds']})",
            "line2":  f"▶ Apuesta {arb['away_side']['stake_pct']}% del bankroll a {arb['away_side']['team']} en {arb['away_side']['bookmaker']} (cuota {arb['away_side']['odds']})",
            "note":   "Cubriendo ambos lados obtienes ganancia sin importar el resultado",
        }
    top_vb = next((v for v in game.get("value_bets", []) if v["is_value_bet"]), None)
    if top_vb:
        return {
            "type":  "value",
            "label": f"APUESTA A: {top_vb['team']}",
            "line1": f"▶ Casa: {top_vb['bookmaker']} · Cuota: {top_vb['odds']} · EV esperado: +{top_vb['value_pct']:.1f}%",
            "line2": f"▶ Modelo: {top_vb['model_prob_pct']}% prob. real vs {100/top_vb['odds']:.1f}% implícita en cuota",
            "note":  f"Confianza {game['confidence']} · Kelly ½ = {top_vb['kelly_pct']/2:.2f}% del bankroll",
        }
    top_total = next((v for v in game.get("totals_value_bets", []) if v["is_value_bet"]), None)
    if top_total:
        mc_t = game.get("mc_total") or 0.0
        line = top_total.get("total_line") or 0.0
        return {
            "type":  "totals_value",
            "label": f"APUESTA TOTAL: {top_total['team']}",
            "line1": f"▶ Casa: {top_total['bookmaker']} · Cuota: {top_total['odds']} · EV esperado: +{top_total['value_pct']:.1f}%",
            "line2": f"▶ Total predicho: {mc_t:.1f} pts · Línea: {line:.1f} · Modelo: {top_total['model_prob_pct']}%",
            "note":  f"Confianza {game['confidence']} · Kelly ½ = {top_total['kelly_pct']/2:.2f}% del bankroll",
        }
    side = game["recommended_side"]
    bm   = game["best_odds"][side]
    return {
        "type":  "model",
        "label": f"APUESTA A: {game['recommended_bet']}",
        "line1": f"▶ Mejor cuota: {bm['odds']} en {bm['bookmaker']}",
        "line2": f"▶ Probabilidad modelo: {game['home_win_prob' if side=='home' else 'away_win_prob']}% · Handicap ref.: {game['handicap']}",
        "note":  f"Confianza {game['confidence']} (sin valor estadístico detectado — apostar con cautela)",
    }


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _run_pipeline(date_str: str) -> dict:
    # 1. Partidos
    games_df = get_daily_games(date_str)
    if games_df.empty:
        return {
            "date": date_str, "games": [], "error": "No hay partidos NBA para esta fecha.",
            "summary": {"total_games": 0, "value_bets_count": 0, "best_opportunity": None},
        }

    # 2. Team stats (básicas + avanzadas) — cacheadas 6 h para no saturar la NBA API
    team_stats_df = _get_team_stats_cached()

    # Enriquecer con forma reciente — con timeout para evitar que la NBA API cuelgue el request
    try:
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as _TimeoutError
        with ThreadPoolExecutor(max_workers=1) as _pool:
            _fut = _pool.submit(enrich_with_form, games_df, NBA_SEASON, 5)
            try:
                games_enriched = _fut.result(timeout=20)
            except _TimeoutError:
                logger.warning("enrich_with_form tardó >20s — predicciones sin forma reciente")
                games_enriched = games_df
    except Exception as exc:
        logger.warning("enrich_with_form falló: %s — predicciones sin forma reciente", exc)
        games_enriched = games_df

    # Enriquecer con Elo (carga ratings actuales guardados tras el último entrenamiento)
    try:
        current_elos = load_current_elos("models/current_elos.json")
        if current_elos:
            games_enriched = apply_elos_to_games(games_enriched, current_elos)
            logger.debug("Elo aplicado: %d equipos | elo_diff range [%.0f, %.0f]",
                         len(current_elos),
                         games_enriched["elo_diff"].min(),
                         games_enriched["elo_diff"].max())
    except Exception as exc:
        logger.debug("Elo no disponible: %s", exc)

    feature_df = (
        build_features(games_enriched, team_stats_df)
        if not team_stats_df.empty else pd.DataFrame()
    )

    # 3. Predicciones
    predictions_df = predict(feature_df) if not feature_df.empty else pd.DataFrame()

    # 3b. Ajustar probabilidades según lesiones en tiempo real
    if not predictions_df.empty:
        try:
            predictions_df = adjust_predictions(predictions_df, games_df, season=NBA_SEASON)
        except Exception as exc:
            logger.warning("adjust_predictions falló: %s — predicciones sin ajuste de lesiones", exc)

    # 3c. Monte Carlo — enriquece predicciones con mc_total (necesario para O/U)
    if not predictions_df.empty:
        try:
            from sports.nba.model.monte_carlo import enrich_predictions_with_mc
            predictions_df = enrich_predictions_with_mc(predictions_df, feature_df)
        except Exception as exc:
            logger.warning("Monte Carlo falló: %s — sin mc_total para O/U", exc)

    # 4. Cuotas cacheadas por fecha para evitar scraping repetido cada request
    odds_df = _get_odds_cached(games_df, date_str)

    # 5. Value bets
    value_bets_df = pd.DataFrame()
    if not predictions_df.empty and not odds_df.empty:
        try:
            value_bets_df = detect_value_bets(predictions_df, odds_df)
        except Exception as exc:
            logger.warning("detect_value_bets falló: %s", exc)

    # 6. Persistir en Supabase en background (no bloquea la respuesta)
    try:
        from concurrent.futures import ThreadPoolExecutor as _TPE
        _TPE(max_workers=1).submit(
            _persist_to_db, games_df, predictions_df, odds_df, value_bets_df, date_str
        )
    except Exception as exc:
        logger.debug("No se pudo lanzar hilo de persistencia: %s", exc)

    return _build_response(date_str, games_df, predictions_df, odds_df, value_bets_df)


def _build_response(date_str, games_df, predictions_df, odds_df, value_bets_df) -> dict:
    games = []

    for _, game in games_df.iterrows():
        game_id   = str(game.get("game_id", ""))
        home_id   = int(game.get("home_team_id", 0) or 0)
        away_id   = int(game.get("visitor_team_id", 0) or 0)
        home_name = _TEAMS_MAP.get(home_id, str(home_id))
        away_name = _TEAMS_MAP.get(away_id, str(away_id))
        game_time = _utc_to_et(str(game.get("game_time_utc", "") or ""))

        # Probabilidades
        home_prob = away_prob = 0.50
        mc_total = None
        if not predictions_df.empty and "game_id" in predictions_df.columns:
            p = predictions_df[predictions_df["game_id"] == game_id]
            if not p.empty:
                home_prob = float(p["home_win_prob"].iloc[0] or 0.5)
                away_prob = float(p["away_win_prob"].iloc[0] or 0.5)
                if "mc_total" in p.columns and pd.notna(p["mc_total"].iloc[0]):
                    mc_total = round(float(p["mc_total"].iloc[0]), 1)

        max_prob     = max(home_prob, away_prob)
        home_is_fav  = home_prob >= away_prob
        confidence   = "Alta" if max_prob >= 0.70 else "Media" if max_prob >= 0.60 else "Baja"
        recommended  = home_name if home_is_fav else away_name
        rec_side     = "home" if home_is_fav else "away"
        handicap     = _handicap_label(max_prob, home_is_fav)

        # Cuotas
        game_odds_df = (
            odds_df[odds_df["game_id"] == game_id]
            if not odds_df.empty and "game_id" in odds_df.columns
            else pd.DataFrame()
        )
        odds_list  = []
        best_home  = {"bookmaker": "—", "odds": 0.0}
        best_away  = {"bookmaker": "—", "odds": 0.0}

        for _, odd in game_odds_df.iterrows():
            ho = round(float(odd.get("home_odds", 0) or 0), 2)
            ao = round(float(odd.get("away_odds", 0) or 0), 2)
            bm = str(odd.get("bookmaker", ""))
            ol = odd.get("over_line");  oo = odd.get("over_odds");  uo = odd.get("under_odds")
            odds_list.append({
                "bookmaker":  bm,
                "home_odds":  ho,
                "away_odds":  ao,
                "over_line":  round(float(ol), 1)  if pd.notna(ol)  and ol  is not None else None,
                "over_odds":  round(float(oo), 2)  if pd.notna(oo)  and oo  is not None else None,
                "under_odds": round(float(uo), 2)  if pd.notna(uo)  and uo  is not None else None,
            })
            if ho > best_home["odds"]:
                best_home = {"bookmaker": bm, "odds": ho}
            if ao > best_away["odds"]:
                best_away = {"bookmaker": bm, "odds": ao}

        # Value bets — separar moneyline (home/away) de totales (over/under)
        vb_list         = []
        totals_vb_list  = []
        if not value_bets_df.empty and "game_id" in value_bets_df.columns:
            for _, vb in value_bets_df[value_bets_df["game_id"] == game_id].iterrows():
                is_vb    = bool(vb.get("is_value_bet", False))
                model_p  = float(vb.get("model_prob", 0) or 0)
                odd_v    = float(vb.get("odds", 0) or 0)
                val      = float(vb.get("value", 0) or 0)
                kel      = _kelly(model_p, odd_v)
                team_n   = str(vb.get("team_name", "") or "")
                side_str = str(vb.get("side", ""))
                tl       = vb.get("total_line")
                if not team_n or team_n.replace(".", "").isdigit():
                    team_n = home_name if side_str == "home" else away_name
                vb_obj = {
                    "team":           team_n,
                    "side":           side_str,
                    "bookmaker":      str(vb.get("bookmaker", "")),
                    "odds":           round(odd_v, 2),
                    "model_prob_pct": round(model_p * 100, 1),
                    "value_pct":      round(val * 100, 2),
                    "kelly_pct":      round(kel * 100, 3),
                    "is_value_bet":   is_vb,
                    "total_line":     float(tl) if pd.notna(tl) and tl is not None else None,
                }
                if side_str in ("home", "away"):
                    vb_list.append(vb_obj)
                else:
                    totals_vb_list.append(vb_obj)

        vb_list.sort(key=lambda x: x["value_pct"], reverse=True)
        totals_vb_list.sort(key=lambda x: x["value_pct"], reverse=True)

        arb = _find_arbitrage(odds_list, home_name, away_name)

        # Lesiones en tiempo real
        injury_info = {"home": [], "visitor": [], "home_pts_lost": 0, "visitor_pts_lost": 0, "net_impact": 0}
        try:
            injury_info = get_injuries_summary_for_game(home_id, away_id, season=NBA_SEASON)
        except Exception as exc:
            logger.debug("injuries para %s: %s", game_id, exc)

        # Ajuste de probabilidad aplicado (desde predictions_df si está disponible)
        inj_adjustment = 0.0
        if not predictions_df.empty and "injury_adjustment" in predictions_df.columns:
            p = predictions_df[predictions_df["game_id"] == game_id]
            if not p.empty:
                inj_adjustment = round(float(p["injury_adjustment"].iloc[0] or 0) * 100, 1)

        game_obj = {
            "game_id":         game_id,
            "home_team":       home_name,
            "away_team":       away_name,
            "game_time":       game_time,
            "home_win_prob":   round(home_prob * 100, 1),
            "away_win_prob":   round(away_prob * 100, 1),
            "mc_total":        mc_total,
            "recommended_bet": recommended,
            "recommended_side": rec_side,
            "confidence":      confidence,
            "handicap":        handicap,
            "odds":            odds_list,
            "best_odds":       {"home": best_home, "away": best_away},
            "value_bets":      vb_list,
            "has_value":       any(v["is_value_bet"] for v in vb_list),
            "totals_value_bets": totals_vb_list,
            "has_totals_value":  any(v["is_value_bet"] for v in totals_vb_list),
            "arb":             arb,
            "injuries":        injury_info,
            "injury_adjustment_pct": inj_adjustment,
        }
        game_obj["action"] = _best_action(game_obj)
        games.append(game_obj)

    # Ordenar: value bets (moneyline o totales) primero, luego por confianza
    conf_order = {"Alta": 0, "Media": 1, "Baja": 2}
    games.sort(key=lambda g: (not (g["has_value"] or g.get("has_totals_value", False)), conf_order.get(g["confidence"], 3)))

    all_vb  = [v for g in games for v in (g["value_bets"] + g.get("totals_value_bets", [])) if v["is_value_bet"]]
    best_vb = max(all_vb, key=lambda x: x["value_pct"], default=None)
    arb_games = [g for g in games if g["arb"]]

    return {
        "date": date_str,
        "games": games,
        "summary": {
            "total_games":      len(games),
            "value_bets_count": len(all_vb),
            "arb_count":        len(arb_games),
            "best_opportunity": (
                f"{best_vb['team']} @ {best_vb['bookmaker']} (+{best_vb['value_pct']:.1f}% EV)"
                if best_vb else None
            ),
        },
    }


def _log_bets_to_journal(data: dict, bankroll: float) -> None:
    """Guarda en output/bet_journal.csv los partidos con valor detectado.
    Solo escribe entradas nuevas (no duplica si el (game_id, predicted_side) ya existe)."""
    BASE         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    journal_path = os.path.join(BASE, "output", "bet_journal.csv")
    date_str     = data.get("date", "")
    model_ver    = os.environ.get("MODEL_VERSION", "?")

    # Leer journal existente
    existing_keys: set[tuple] = set()  # (game_id, predicted_side)
    rows: list[dict] = []
    if os.path.exists(journal_path):
        try:
            jdf = pd.read_csv(journal_path, dtype=str)
            rows = jdf.to_dict("records")
            existing_keys = {
                (str(r.get("game_id", "")), str(r.get("predicted_side", "")))
                for r in rows
            }
        except Exception:
            pass

    logged_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_rows: list[dict] = []

    for game in data.get("games", []):
        gid = str(game.get("game_id", ""))
        if not gid:
            continue

        # ── Moneyline: mejor bookmaker por lado (home/away) ───────────────────
        best_per_side: dict[str, dict] = {}
        for vb in game.get("value_bets", []):
            if not vb.get("is_value_bet") or vb.get("kelly_pct", 0) <= 0:
                continue
            side = vb["side"]
            if side not in best_per_side or vb["kelly_pct"] > best_per_side[side]["kelly_pct"]:
                best_per_side[side] = vb

        for side, vb in best_per_side.items():
            if (gid, side) in existing_keys:
                continue
            bet_amount     = max(1, round(bankroll * vb["kelly_pct"] / 200))
            potential_gain = round(bet_amount * (vb["odds"] - 1))
            new_rows.append({
                "game_id":           gid,
                "game_date":         date_str,
                "logged_date":       logged_dt,
                "home_team":         game.get("home_team", ""),
                "away_team":         game.get("away_team", ""),
                "predicted_side":    side,
                "team_name":         vb["team"],
                "model_version":     model_ver,
                "model_prob_pct":    vb["model_prob_pct"],
                "odds":              vb["odds"],
                "kelly_pct":         vb["kelly_pct"],
                "bankroll_snapshot": bankroll,
                "bet_amount":        bet_amount,
                "potential_gain":    potential_gain,
                "bet_type":          "moneyline",
                "total_line":        "",
            })
            existing_keys.add((gid, side))

        # ── Totales: mejor bookmaker por lado (over/under) ────────────────────
        best_totals: dict[str, dict] = {}
        for vb in game.get("totals_value_bets", []):
            if not vb.get("is_value_bet") or vb.get("kelly_pct", 0) <= 0:
                continue
            side = vb["side"]
            if side not in best_totals or vb["kelly_pct"] > best_totals[side]["kelly_pct"]:
                best_totals[side] = vb

        for side, vb in best_totals.items():
            if (gid, side) in existing_keys:
                continue
            bet_amount     = max(1, round(bankroll * vb["kelly_pct"] / 200))
            potential_gain = round(bet_amount * (vb["odds"] - 1))
            tl = vb.get("total_line")
            new_rows.append({
                "game_id":           gid,
                "game_date":         date_str,
                "logged_date":       logged_dt,
                "home_team":         game.get("home_team", ""),
                "away_team":         game.get("away_team", ""),
                "predicted_side":    side,
                "team_name":         vb["team"],
                "model_version":     model_ver,
                "model_prob_pct":    vb["model_prob_pct"],
                "odds":              vb["odds"],
                "kelly_pct":         vb["kelly_pct"],
                "bankroll_snapshot": bankroll,
                "bet_amount":        bet_amount,
                "potential_gain":    potential_gain,
                "bet_type":          "total",
                "total_line":        tl if tl is not None else "",
            })
            existing_keys.add((gid, side))

    if new_rows:
        all_rows = rows + new_rows
        pd.DataFrame(all_rows).to_csv(journal_path, index=False)


# ── Reconciliación predicciones → resultados reales ──────────────────────────

def _fetch_and_reconcile_results() -> int:
    """
    Cierra el loop predicción → resultado real.

    1. Lee predictions.csv; detecta filas con home_win vacío y game_date < hoy.
    2. Por cada fecha pendiente: descarga line_scores si no están en line_scores.csv.
    3. Calcula home_win (1 = local gana, 0 = visitante gana) desde los marcadores.
    4. Actualiza predictions.csv con los resultados reales.
    5. Persiste los nuevos line_scores en line_scores.csv.

    Returns:
        Número de predicciones actualizadas.
    """
    pred_path = os.path.join(_BASE_DIR, "output", "predictions.csv")
    ls_path   = os.path.join(_BASE_DIR, "output", "line_scores.csv")
    today_str = _date_cls.today().isoformat()

    if not os.path.exists(pred_path):
        return 0

    try:
        pred_df = pd.read_csv(pred_path, dtype=str)
    except Exception as exc:
        logger.warning("_fetch_and_reconcile_results: no se pudo leer predictions.csv: %s", exc)
        return 0

    # Filas sin resultado y con fecha ya jugada
    if "home_win" not in pred_df.columns:
        pred_df["home_win"] = pd.NA
    if "game_date" not in pred_df.columns:
        logger.warning("_fetch_and_reconcile_results: predictions.csv no tiene columna 'game_date'")
        return 0
    mask = (
        (pred_df["home_win"].isna() | (pred_df["home_win"].astype(str).str.strip().isin(["", "nan"])))
        & (pred_df["game_date"].str[:10] < today_str)
    )
    needs_result = pred_df[mask]
    if needs_result.empty:
        logger.info("_fetch_and_reconcile_results: todas las predicciones pasadas ya tienen resultado")
        return 0

    pending_dates = sorted(needs_result["game_date"].str[:10].dropna().unique())
    logger.info(
        "_fetch_and_reconcile_results: %d predicciones sin resultado en %d fecha(s): %s",
        len(needs_result), len(pending_dates), pending_dates,
    )

    # Cargar line_scores existentes
    ls_df = pd.DataFrame()
    if os.path.exists(ls_path):
        try:
            ls_df = pd.read_csv(ls_path, dtype=str)
        except Exception:
            ls_df = pd.DataFrame()

    existing_ls_dates: set[str] = (
        set(ls_df["fetch_date"].str[:10].dropna().unique())
        if not ls_df.empty and "fetch_date" in ls_df.columns else set()
    )

    # Descargar line_scores para fechas que faltan
    new_ls_frames: list = []
    for date_str in pending_dates:
        if date_str in existing_ls_dates:
            continue
        try:
            logger.info("_fetch_and_reconcile_results: descargando line scores para %s…", date_str)
            day_ls = get_line_scores(date_str)
            if not day_ls.empty:
                new_ls_frames.append(day_ls)
                logger.info("  → %d line scores obtenidos para %s", len(day_ls), date_str)
            else:
                logger.info("  → sin line scores para %s (partidos sin resultado aún)", date_str)
        except Exception as exc:
            logger.warning("Error descargando line scores para %s: %s", date_str, exc)

    # Consolidar y persistir line_scores
    if new_ls_frames:
        new_ls = pd.concat(new_ls_frames, ignore_index=True)
        if not ls_df.empty:
            ls_combined = pd.concat(
                [ls_df, new_ls.astype(str)], ignore_index=True
            ).drop_duplicates(subset=["game_id", "team_id"])
        else:
            ls_combined = new_ls.astype(str)
        try:
            ls_combined.to_csv(ls_path, index=False)
            logger.info("line_scores.csv actualizado (%d filas totales)", len(ls_combined))
        except Exception as exc:
            logger.warning("No se pudo guardar line_scores.csv: %s", exc)
        ls_df = ls_combined

    if ls_df.empty or not {"game_id", "team_id", "pts"}.issubset(ls_df.columns):
        logger.info("_fetch_and_reconcile_results: sin line_scores disponibles para reconciliar")
        return 0

    # Mapa game_id → home_team_id desde predictions.csv
    home_map: dict[str, int] = {}
    for _, row in pred_df.iterrows():
        gid = str(row.get("game_id", "")).strip()
        htid_raw = str(row.get("home_team_id", "")).strip()
        if gid and htid_raw not in ("", "nan"):
            try:
                home_map[gid] = int(float(htid_raw))
            except (ValueError, TypeError):
                pass

    # Calcular resultado real por partido desde line_scores
    ls_work = ls_df.copy()
    ls_work["pts"]     = pd.to_numeric(ls_work["pts"],     errors="coerce")
    ls_work["team_id"] = pd.to_numeric(ls_work["team_id"], errors="coerce")
    ls_work["game_id"] = ls_work["game_id"].astype(str).str.strip()

    actual_results: dict[str, int] = {}
    for gid, grp in ls_work.groupby("game_id"):
        gid_str = str(gid)
        grp = grp.dropna(subset=["pts"])
        if len(grp) < 2:
            continue
        winner_tid = int(grp.loc[grp["pts"].idxmax(), "team_id"])
        home_tid = home_map.get(gid_str)
        if home_tid is not None:
            actual_results[gid_str] = 1 if winner_tid == home_tid else 0

    if not actual_results:
        logger.info("_fetch_and_reconcile_results: no se pudieron determinar resultados (line_scores insuficientes)")
        return 0

    # Actualizar predictions.csv en memoria
    updated = 0
    for idx, row in pred_df.iterrows():
        gid = str(row.get("game_id", "")).strip()
        hw_raw = str(row.get("home_win", "")).strip()
        if gid in actual_results and hw_raw in ("", "nan"):
            pred_df.at[idx, "home_win"] = actual_results[gid]
            updated += 1

    if updated > 0:
        try:
            pred_df.to_csv(pred_path, index=False)
            logger.info(
                "_fetch_and_reconcile_results: %d predicciones actualizadas con resultado real "
                "(%d totales en archivo)",
                updated, len(pred_df),
            )
        except Exception as exc:
            logger.warning("No se pudo guardar predictions.csv: %s", exc)

        # ── Persistir en Supabase ─────────────────────────────────────────────
        try:
            from sports.nba.database.repository import DatabaseRepository
            repo = DatabaseRepository()
            # 1. Guardar line_scores recien descargados
            if new_ls_frames:
                new_ls_combined = pd.concat(new_ls_frames, ignore_index=True)
                repo.upsert_line_scores(new_ls_combined)
            # 2. Actualizar columna home_win en predictions
            rows_to_update = pred_df[
                pred_df["game_id"].astype(str).isin(actual_results)
            ].copy()
            if not rows_to_update.empty:
                repo.upsert_prediction_results(rows_to_update)
            repo.close()
            logger.info("Supabase actualizado: %d resultados sincronizados", updated)
        except Exception as exc:
            logger.warning("No se pudo actualizar Supabase con resultados: %s", exc)

    return updated


# ── Snapshots de predicciones ─────────────────────────────────────────────────

def _save_snapshot(date_str: str, data: dict) -> None:
    """Persiste en disco el resultado completo del pipeline para una fecha.

    Permite recuperar la predicción original aunque los datos de entrada
    (stats, lesiones, ELO, forma reciente) cambien con el tiempo.
    """
    import json
    try:
        os.makedirs(_SNAPSHOTS_DIR, exist_ok=True)
        path = os.path.join(_SNAPSHOTS_DIR, f"{date_str}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
        logger.info("Snapshot guardado: %s", path)
    except Exception as exc:
        logger.warning("No se pudo guardar snapshot para %s: %s", date_str, exc)


def _load_snapshot(date_str: str) -> "dict | None":
    """Carga desde disco el snapshot de una fecha si existe."""
    import json
    path = os.path.join(_SNAPSHOTS_DIR, f"{date_str}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info("Snapshot cargado desde disco para %s", date_str)
        return data
    except Exception as exc:
        logger.warning("No se pudo cargar snapshot para %s: %s", date_str, exc)
        return None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root():
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))


@app.get("/api/analysis")
def get_analysis(date: Optional[str] = Query(default=None),
                 bankroll: float = Query(default=500_000)):
    """Ejecuta el pipeline completo y devuelve el análisis del día."""
    import time
    today_str = _date_cls.today().isoformat()
    date_str  = date or today_str
    is_past   = date_str < today_str

    # Fechas pasadas: si hay snapshot en disco, devolverlo directamente.
    # Esto preserva la predicción original con los datos del momento exacto
    # en que se generó (lesiones, forma reciente, ELO vigentes ese día).
    if is_past:
        snapshot = _load_snapshot(date_str)
        if snapshot:
            return snapshot

    # Caché en memoria (válida para el día de hoy y re-requests rápidos)
    cached = _CACHE.get(date_str)
    if cached and (time.time() - cached["ts"]) < _CACHE_TTL:
        # Aunque esté cacheado, intentar loguear (puede ser nuevo bankroll)
        try:
            _log_bets_to_journal(cached["data"], bankroll)
        except Exception:
            pass
        return cached["data"]
    try:
        data = _run_pipeline(date_str)
        _CACHE[date_str] = {"data": data, "ts": time.time()}
        # Guardar snapshot en disco para que futuras consultas de esta fecha
        # devuelvan siempre la predicción generada con los datos de hoy.
        _save_snapshot(date_str, data)
        try:
            _log_bets_to_journal(data, bankroll)
        except Exception as log_exc:
            logger.warning("No se pudo loguear al journal: %s", log_exc)
    except Exception as exc:
        logger.error("Pipeline error [%s]: %s", date_str, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    return _CACHE[date_str]["data"]


@app.delete("/api/cache")
def clear_cache(date: Optional[str] = Query(default=None)):
    """Limpia la caché (y el snapshot si existe) para forzar re-ejecución del pipeline."""
    if date:
        _CACHE.pop(date, None)
        # Borrar también el snapshot para que el próximo request regenere
        snapshot_path = os.path.join(_SNAPSHOTS_DIR, f"{date}.json")
        if os.path.exists(snapshot_path):
            try:
                os.remove(snapshot_path)
                logger.info("Snapshot eliminado: %s", snapshot_path)
            except Exception as exc:
                logger.warning("No se pudo eliminar snapshot %s: %s", snapshot_path, exc)
    else:
        _CACHE.clear()
        _ODDS_CACHE.clear()
    return {"status": "ok", "cleared": date or "all"}


@app.get("/api/reconcile")
def reconcile_results():
    """
    Dispara manualmente la reconciliación de predicciones pasadas con resultados reales.
    Descarga line_scores faltantes, actualiza predictions.csv con home_win.
    """
    try:
        updated = _fetch_and_reconcile_results()
        return {"status": "ok", "predictions_updated": updated}
    except Exception as exc:
        logger.error("reconcile_results error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/accuracy")
def get_accuracy():
    """
    Calcula el % de acierto real del modelo sobre predicciones con resultado conocido.
    Lee primero desde Supabase (vista v_model_accuracy); si no está disponible, lee CSV.
    """
    # ── Intentar Supabase ─────────────────────────────────────────────────────
    try:
        from config.settings import DATABASE_URL
        if DATABASE_URL:
            from sqlalchemy import create_engine, text
            eng = create_engine(DATABASE_URL, pool_pre_ping=True)
            with eng.connect() as conn:
                rows = conn.execute(text("SELECT * FROM v_model_accuracy")).mappings().all()
            if rows:
                by_model = {
                    r["model_version"]: {
                        "total":        r["resolved_predictions"] or 0,
                        "correct":      None,  # la vista no guarda correct en bruto
                        "accuracy_pct": float(r["accuracy_pct"]) if r["accuracy_pct"] else None,
                        "avg_prob_error": float(r["avg_prob_error"]) if r["avg_prob_error"] else None,
                    }
                    for r in rows
                }
                total    = sum(v["total"] for v in by_model.values())
                all_accs = [v["accuracy_pct"] for v in by_model.values() if v["accuracy_pct"]]
                overall  = round(sum(all_accs) / len(all_accs), 1) if all_accs else None
                return {
                    "source":            "supabase",
                    "total_with_result": total,
                    "accuracy_pct":      overall,
                    "by_model":          by_model,
                }
    except Exception as exc:
        logger.debug("accuracy: Supabase no disponible, usando CSV: %s", exc)

    # ── Fallback: CSV local ───────────────────────────────────────────────────
    pred_path = os.path.join(_BASE_DIR, "output", "predictions.csv")
    if not os.path.exists(pred_path):
        return {"error": "predictions.csv no encontrado"}

    try:
        df = pd.read_csv(pred_path, dtype=str)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    has_result = df[df["home_win"].notna() & (~df["home_win"].astype(str).str.strip().isin(["", "nan"]))]
    if has_result.empty:
        return {
            "total_with_result": 0,
            "correct": 0,
            "accuracy_pct": None,
            "message": "Sin predicciones con resultado aun. Ejecuta /api/reconcile primero.",
        }

    results = []
    for _, row in has_result.iterrows():
        try:
            hw = int(float(str(row["home_win"]).strip()))
            pw = str(row.get("predicted_winner", "")).strip()
            if pw not in ("home", "away"):
                continue
            correct = (pw == "home" and hw == 1) or (pw == "away" and hw == 0)
            results.append({
                "game_id":       row.get("game_id", ""),
                "game_date":     str(row.get("game_date", ""))[:10],
                "model_version": row.get("model_version", "?"),
                "predicted":     pw,
                "actual":        "home" if hw == 1 else "away",
                "correct":       correct,
            })
        except (ValueError, TypeError):
            continue

    if not results:
        return {"total_with_result": 0, "correct": 0, "accuracy_pct": None}

    total   = len(results)
    correct = sum(1 for r in results if r["correct"])
    accuracy = round(correct / total * 100, 1)

    by_model: dict = {}
    for r in results:
        mv = r["model_version"]
        if mv not in by_model:
            by_model[mv] = {"total": 0, "correct": 0}
        by_model[mv]["total"]  += 1
        by_model[mv]["correct"] += int(r["correct"])
    for mv in by_model:
        t = by_model[mv]["total"]
        c = by_model[mv]["correct"]
        by_model[mv]["accuracy_pct"] = round(c / t * 100, 1) if t else None

    return {
        "source":            "csv",
        "total_with_result": total,
        "correct":           correct,
        "accuracy_pct":      accuracy,
        "by_model":          by_model,
        "detail":            results,
    }


@app.get("/api/backtest")
def get_backtest(bankroll: float = Query(default=500_000)):
    """Historial real de apuestas: lee bet_journal.csv y cruza con line_scores para resultados."""
    # Reconciliar resultados antes de construir el historial (actualiza predictions.csv y line_scores.csv)
    try:
        _fetch_and_reconcile_results()
    except Exception as exc:
        logger.warning("reconcile en backtest fallO (no critico): %s", exc)

    BASE         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    journal_path = os.path.join(BASE, "output", "bet_journal.csv")
    pred_path    = os.path.join(BASE, "output", "predictions.csv")
    ls_path      = os.path.join(BASE, "output", "line_scores.csv")

    # ── Intentar enriquecer resultados desde Supabase (v_backtest_history) ───
    # Si Supabase tiene datos de home_win mas actualizados que el CSV local,
    # los usamos para resolver apuestas pendientes.
    supabase_results: dict[str, int] = {}  # game_id -> home_win (0 o 1)
    try:
        from config.settings import DATABASE_URL
        if DATABASE_URL:
            from sqlalchemy import create_engine, text
            eng = create_engine(DATABASE_URL, pool_pre_ping=True)
            with eng.connect() as conn:
                rows = conn.execute(
                    text("SELECT game_id, actual_result FROM v_backtest_history WHERE actual_result IS NOT NULL")
                ).mappings().all()
            supabase_results = {r["game_id"]: int(r["actual_result"]) for r in rows}
            if supabase_results:
                logger.debug("backtest: %d resultados cargados desde Supabase", len(supabase_results))
    except Exception as exc:
        logger.debug("backtest: Supabase no disponible para resultados, usando CSV: %s", exc)

    # ── Cargar journal ────────────────────────────────────────────────────────
    if not os.path.exists(journal_path):
        return {
            "initial_bankroll": bankroll,
            "current_bankroll": bankroll,
            "total_profit": 0,
            "total_bets": 0, "total_wins": 0, "total_losses": 0,
            "win_rate": 0, "roi": 0, "strategy": "Kelly ½",
            "models": {}, "daily": [], "sparkline": [],
            "empty": True,
        }

    journal_df = pd.read_csv(journal_path)

    # ── Mapa game_id → home_team_id (desde predictions.csv) ──────────────────
    home_map: dict[str, int] = {}
    if os.path.exists(pred_path):
        pred_df = pd.read_csv(pred_path)
        for _, r in pred_df.iterrows():
            gid = str(r.get("game_id", ""))
            hid = int(r.get("home_team_id", 0) or 0)
            if gid:
                home_map[gid] = hid

    # ── Resultados reales ─────────────────────────────────────────────────────
    actual_winner: dict[str, str]   = {}
    actual_totals: dict[str, float] = {}
    if os.path.exists(ls_path):
        ls_df = pd.read_csv(ls_path)
        for gid, grp in ls_df.groupby("game_id"):
            gid_str = str(gid)
            if len(grp) < 2:
                continue
            winner_tid = int(grp.loc[grp["pts"].idxmax(), "team_id"])
            actual_winner[gid_str] = "home" if winner_tid == home_map.get(gid_str, -1) else "away"
            actual_totals[gid_str] = float(grp["pts"].sum())

    # Enriquecer con resultados de Supabase (tienen prioridad si estan disponibles)
    for gid, hw in supabase_results.items():
        actual_winner[gid] = "home" if hw == 1 else "away"

    # ── Construir registros desde el journal ──────────────────────────────────
    records: list[dict] = []
    for _, row in journal_df.iterrows():
        gid           = str(row.get("game_id", ""))
        game_date     = str(row.get("game_date", ""))[:10]
        home_team     = str(row.get("home_team", ""))
        away_team     = str(row.get("away_team", ""))
        predicted     = str(row.get("predicted_side", ""))
        team_name     = str(row.get("team_name", ""))
        model_v       = str(row.get("model_version", "?"))
        odds_val      = float(row.get("odds", 1.91) or 1.91)
        kelly_pct     = float(row.get("kelly_pct", 0) or 0)
        bet_amount    = int(float(row.get("bet_amount", 0) or 0))
        pot_gain      = int(float(row.get("potential_gain", 0) or 0))
        model_prob_pct = float(row.get("model_prob_pct", 50) or 50)
        bet_type      = str(row.get("bet_type", "") or "moneyline")
        tl_raw        = row.get("total_line")
        total_line: Optional[float] = None
        try:
            if pd.notna(tl_raw) and str(tl_raw).strip():
                total_line = float(tl_raw)
        except (ValueError, TypeError):
            pass

        if bet_type == "total" and total_line is not None:
            actual_total = actual_totals.get(gid)
            if actual_total is not None:
                actual = "over" if actual_total > total_line else ("under" if actual_total < total_line else None)
            else:
                actual = None
        else:
            actual = actual_winner.get(gid)   # None si aún no hay resultado

        if actual is not None:
            correct = (predicted == actual)
            pl      = pot_gain if correct else -bet_amount
            status  = "win" if correct else "loss"
        else:
            correct = None
            pl      = None
            status  = "pending"

        records.append({
            "game_id":        gid,
            "game_date":      game_date,
            "model":          model_v,
            "home_team":      home_team,
            "away_team":      away_team,
            "predicted":      predicted,
            "actual":         actual,
            "team_name":      team_name,
            "correct":        correct,
            "status":         status,
            "odds":           round(odds_val, 2),
            "kelly_pct":      round(kelly_pct, 2),
            "model_prob_pct": round(model_prob_pct, 1),
            "bet_amount":     bet_amount,
            "potential_gain": pot_gain,
            "pl":             pl,
            "bet_type":       bet_type,
            "total_line":     total_line,
        })

    records.sort(key=lambda x: (x["game_date"], x["game_id"]))

    # Running bankroll: solo apuestas resueltas, en orden cronológico
    rb = bankroll
    for r in records:
        if r["status"] != "pending":
            rb = round(rb + r["pl"])
        r["running_bankroll"] = rb   # muestra bankroll actual después de cada apuesta resuelta

    # ── Estadísticas por modelo (solo resueltas) ──────────────────────────────
    model_stats: dict[str, dict] = {}
    for r in records:
        if r["status"] == "pending":
            continue
        mv = r["model"]
        if mv not in model_stats:
            model_stats[mv] = {"bets": 0, "wins": 0, "pl": 0, "wagered": 0}
        model_stats[mv]["bets"]    += 1
        model_stats[mv]["wagered"] += r["bet_amount"]
        if r["correct"]:
            model_stats[mv]["wins"] += 1
        model_stats[mv]["pl"] += r["pl"]

    for mv, ms in model_stats.items():
        ms["losses"]   = ms["bets"] - ms["wins"]
        ms["win_rate"] = round(ms["wins"] / ms["bets"] * 100, 1) if ms["bets"] else 0
        ms["roi"]      = round(ms["pl"] / ms["wagered"] * 100, 1) if ms["wagered"] else 0
        ms["pl"]       = round(ms["pl"])
        ms["wagered"]  = round(ms["wagered"])

    # ── Agrupación diaria ─────────────────────────────────────────────────────
    daily: dict[str, dict] = {}
    for r in records:
        d = r["game_date"]
        if d not in daily:
            daily[d] = {"date": d, "bets": [], "day_pl": 0, "running_bankroll": bankroll,
                        "day_wagered": 0, "has_pending": False}
        daily[d]["bets"].append(r)
        if r["status"] != "pending":
            daily[d]["day_pl"]      += r["pl"]
            daily[d]["day_wagered"] += r["bet_amount"]
        else:
            daily[d]["has_pending"] = True

    daily_list = []
    for d in sorted(daily.keys()):
        entry = daily[d]
        entry["day_pl"]           = round(entry["day_pl"])
        entry["day_wagered"]      = round(entry["day_wagered"])
        entry["running_bankroll"] = entry["bets"][-1]["running_bankroll"]
        daily_list.append(entry)

    resolved      = [r for r in records if r["status"] != "pending"]
    total_bets    = len(resolved)
    total_wins    = sum(1 for r in resolved if r["correct"])
    total_pl      = sum(r["pl"] for r in resolved)
    total_wagered = sum(r["bet_amount"] for r in resolved)
    pending_count = len([r for r in records if r["status"] == "pending"])

    return {
        "initial_bankroll": bankroll,
        "current_bankroll": round(bankroll + total_pl),
        "total_profit":     round(total_pl),
        "total_bets":       total_bets,
        "total_wins":       total_wins,
        "total_losses":     total_bets - total_wins,
        "pending_count":    pending_count,
        "win_rate":         round(total_wins / total_bets * 100, 1) if total_bets else 0,
        "roi":              round(total_pl / total_wagered * 100, 1) if total_wagered else 0,
        "strategy":         "Kelly ½",
        "models":           dict(sorted(model_stats.items())),
        "daily":            daily_list,
        "sparkline":        [{"date": d["date"], "bankroll": d["running_bankroll"]} for d in daily_list if not d["has_pending"]],
    }


# Archivos estáticos (al final para no interceptar rutas de la API)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
