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
from ingestion.elo import apply_elos_to_games, load_current_elos
from ingestion.injuries_client import adjust_predictions, get_injuries_summary_for_game
from ingestion.nba_client import get_combined_team_stats, get_daily_games, get_team_stats
from ingestion.odds_client import get_odds
from ingestion.recent_form import enrich_with_form
from model.predictor import predict
from model.value_detector import detect_value_bets
from processing.features import build_features
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

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_TEAMS_MAP: dict[int, str] = {t["id"]: t["full_name"] for t in nba_teams_static.get_teams()}


@app.on_event("startup")
async def _startup_preload() -> None:
    """Pre-carga stats de equipo en un hilo para que el primer request sea rápido."""
    import asyncio
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _get_team_stats_cached)
    logger.info("Startup: pre-carga de team stats lanzada en background")

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
        from database.repository import DatabaseRepository
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
        if not predictions_df.empty and "game_id" in predictions_df.columns:
            p = predictions_df[predictions_df["game_id"] == game_id]
            if not p.empty:
                home_prob = float(p["home_win_prob"].iloc[0] or 0.5)
                away_prob = float(p["away_win_prob"].iloc[0] or 0.5)

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
            odds_list.append({"bookmaker": bm, "home_odds": ho, "away_odds": ao})
            if ho > best_home["odds"]:
                best_home = {"bookmaker": bm, "odds": ho}
            if ao > best_away["odds"]:
                best_away = {"bookmaker": bm, "odds": ao}

        # Value bets
        vb_list = []
        if not value_bets_df.empty and "game_id" in value_bets_df.columns:
            for _, vb in value_bets_df[value_bets_df["game_id"] == game_id].iterrows():
                is_vb    = bool(vb.get("is_value_bet", False))
                model_p  = float(vb.get("model_prob", 0) or 0)
                odd_v    = float(vb.get("odds", 0) or 0)
                val      = float(vb.get("value", 0) or 0)
                kel      = _kelly(model_p, odd_v)
                team_n   = str(vb.get("team_name", "") or "")
                side_str = str(vb.get("side", ""))
                if not team_n or team_n.replace(".", "").isdigit():
                    team_n = home_name if side_str == "home" else away_name
                vb_list.append({
                    "team":           team_n,
                    "side":           side_str,
                    "bookmaker":      str(vb.get("bookmaker", "")),
                    "odds":           round(odd_v, 2),
                    "model_prob_pct": round(model_p * 100, 1),
                    "value_pct":      round(val * 100, 2),
                    "kelly_pct":      round(kel * 100, 3),
                    "is_value_bet":   is_vb,
                })

        vb_list.sort(key=lambda x: x["value_pct"], reverse=True)

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
            "recommended_bet": recommended,
            "recommended_side": rec_side,
            "confidence":      confidence,
            "handicap":        handicap,
            "odds":            odds_list,
            "best_odds":       {"home": best_home, "away": best_away},
            "value_bets":      vb_list,
            "has_value":       any(v["is_value_bet"] for v in vb_list),
            "arb":             arb,
            "injuries":        injury_info,
            "injury_adjustment_pct": inj_adjustment,
        }
        game_obj["action"] = _best_action(game_obj)
        games.append(game_obj)

    # Ordenar: value bets primero, luego por confianza
    conf_order = {"Alta": 0, "Media": 1, "Baja": 2}
    games.sort(key=lambda g: (not g["has_value"], conf_order.get(g["confidence"], 3)))

    all_vb  = [v for g in games for v in g["value_bets"] if v["is_value_bet"]]
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


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root():
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))


@app.get("/api/analysis")
def get_analysis(date: Optional[str] = Query(default=None)):
    """Ejecuta el pipeline completo y devuelve el análisis del día."""
    import time
    date_str = date or _date_cls.today().isoformat()
    cached = _CACHE.get(date_str)
    if cached and (time.time() - cached["ts"]) < _CACHE_TTL:
        return cached["data"]
    try:
        data = _run_pipeline(date_str)
        _CACHE[date_str] = {"data": data, "ts": time.time()}
    except Exception as exc:
        logger.error("Pipeline error [%s]: %s", date_str, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    return _CACHE[date_str]["data"]


@app.delete("/api/cache")
def clear_cache(date: Optional[str] = Query(default=None)):
    """Limpia la caché para forzar re-ejecución del pipeline."""
    if date:
        _CACHE.pop(date, None)
    else:
        _CACHE.clear()
        _ODDS_CACHE.clear()
    return {"status": "ok", "cleared": date or "all"}


@app.get("/api/backtest")
def get_backtest(bankroll: float = Query(default=500_000)):
    """Simula el rendimiento histórico apostando un 2 % del bankroll inicial por predicción."""
    from collections import defaultdict

    BASE      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pred_path = os.path.join(BASE, "output", "predictions.csv")
    ls_path   = os.path.join(BASE, "output", "line_scores.csv")
    vb_path   = os.path.join(BASE, "output", "value_bets.csv")

    if not os.path.exists(pred_path):
        raise HTTPException(status_code=404, detail="No hay predicciones guardadas en output/predictions.csv")

    predictions_df = pd.read_csv(pred_path)

    # ── Mapa game_id → home_team_id (desde predictions.csv) ──────────────────
    home_map: dict[str, int] = {}
    for _, r in predictions_df.iterrows():
        gid = str(r.get("game_id", ""))
        hid = int(r.get("home_team_id", 0) or 0)
        if gid:
            home_map[gid] = hid

    # ── Resultados reales (ganador: "home" | "away") ──────────────────────────
    actual_winner: dict[str, str] = {}
    if os.path.exists(ls_path):
        ls_df = pd.read_csv(ls_path)
        for gid, grp in ls_df.groupby("game_id"):
            gid_str = str(gid)
            if len(grp) < 2:
                continue
            winner_tid = int(grp.loc[grp["pts"].idxmax(), "team_id"])
            actual_winner[gid_str] = "home" if winner_tid == home_map.get(gid_str, -1) else "away"

    # ── Mejores cuotas disponibles por (game_id, side) ───────────────────────
    best_odds: dict[tuple, float] = {}
    if os.path.exists(vb_path):
        vb_df = pd.read_csv(vb_path)
        for _, r in vb_df.iterrows():
            key      = (str(r.get("game_id", "")), str(r.get("side", "")))
            odds_val = float(r.get("odds", 0) or 0)
            if odds_val > best_odds.get(key, 0):
                best_odds[key] = odds_val

    # ── Simulación ────────────────────────────────────────────────────────────
    unit    = round(bankroll * 0.02)   # 2 % del bankroll inicial por apuesta
    records: list[dict] = []

    for _, p in predictions_df.iterrows():
        gid      = str(p.get("game_id", ""))
        actual   = actual_winner.get(gid)
        if not actual:
            continue   # partido futuro o sin datos de score

        predicted = str(p.get("predicted_winner", ""))
        model_v   = str(p.get("model_version", "?"))
        game_date = str(p.get("game_date", ""))[:10]
        home_id   = int(p.get("home_team_id",    0) or 0)
        away_id   = int(p.get("visitor_team_id", 0) or 0)
        home_prob = float(p.get("home_win_prob", 0.5) or 0.5)
        away_prob = float(p.get("away_win_prob", 0.5) or 0.5)

        odds_val = best_odds.get((gid, predicted), 1.91)
        correct  = (predicted == actual)
        pl       = unit * (odds_val - 1) if correct else -unit

        records.append({
            "game_id":   gid,
            "game_date": game_date,
            "model":     model_v,
            "home_team": _TEAMS_MAP.get(home_id, str(home_id)),
            "away_team": _TEAMS_MAP.get(away_id, str(away_id)),
            "predicted": predicted,
            "actual":    actual,
            "correct":   correct,
            "odds":      round(odds_val, 2),
            "home_prob": round(home_prob * 100, 1),
            "away_prob": round(away_prob * 100, 1),
            "pl":        round(pl),
        })

    records.sort(key=lambda x: (x["game_date"], x["game_id"]))

    # Running bankroll cronológico
    running = bankroll
    for r in records:
        running += r["pl"]
        r["running_bankroll"] = round(running)

    # ── Estadísticas por modelo ───────────────────────────────────────────────
    model_stats: dict[str, dict] = {}
    for r in records:
        mv = r["model"]
        if mv not in model_stats:
            model_stats[mv] = {"bets": 0, "wins": 0, "pl": 0}
        model_stats[mv]["bets"] += 1
        if r["correct"]:
            model_stats[mv]["wins"] += 1
        model_stats[mv]["pl"] += r["pl"]

    for mv, ms in model_stats.items():
        ms["losses"]   = ms["bets"] - ms["wins"]
        ms["win_rate"] = round(ms["wins"] / ms["bets"] * 100, 1) if ms["bets"] else 0
        ms["roi"]      = round(ms["pl"] / (ms["bets"] * unit) * 100, 1) if ms["bets"] and unit else 0
        ms["pl"]       = round(ms["pl"])

    # ── Agrupación diaria ─────────────────────────────────────────────────────
    daily: dict[str, dict] = {}
    for r in records:
        d = r["game_date"]
        if d not in daily:
            daily[d] = {"date": d, "bets": [], "day_pl": 0, "running_bankroll": 0}
        daily[d]["bets"].append(r)
        daily[d]["day_pl"] += r["pl"]

    daily_list = []
    for d in sorted(daily.keys()):
        entry = daily[d]
        entry["day_pl"]           = round(entry["day_pl"])
        entry["running_bankroll"] = entry["bets"][-1]["running_bankroll"] if entry["bets"] else bankroll
        daily_list.append(entry)

    total_bets = len(records)
    total_wins = sum(1 for r in records if r["correct"])
    total_pl   = sum(r["pl"] for r in records)

    return {
        "initial_bankroll": bankroll,
        "current_bankroll": round(bankroll + total_pl),
        "total_profit":     round(total_pl),
        "total_bets":       total_bets,
        "total_wins":       total_wins,
        "total_losses":     total_bets - total_wins,
        "win_rate":         round(total_wins / total_bets * 100, 1) if total_bets else 0,
        "roi":              round(total_pl / (total_bets * unit) * 100, 1) if total_bets and unit else 0,
        "unit_size":        unit,
        "models":           dict(sorted(model_stats.items())),
        "daily":            daily_list,
        "sparkline":        [{"date": d["date"], "bankroll": d["running_bankroll"]} for d in daily_list],
    }




# Archivos estáticos (al final para no interceptar rutas de la API)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
