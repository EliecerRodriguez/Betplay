"""
Pipeline diario ATP — predicciones + value bets.

Obtiene las cuotas del día desde Betplay/Rushbet (Kambi), genera
predicciones con el modelo entrenado y detecta value bets.

Uso:
    python run_atp_pipeline.py                    # fecha de hoy
    python run_atp_pipeline.py --date 2026-05-20  # fecha específica
    python run_atp_pipeline.py --no-save          # solo consola, sin CSV

Salida (output/):
    atp_predictions.csv   — predicciones del día (acumulativo)
    atp_value_bets.csv    — value bets detectadas (acumulativo)
    atp_odds.csv          — snapshot de cuotas (acumulativo)

El pipeline sobrescribe solo los registros de la fecha solicitada
para evitar duplicados en los CSV acumulativos.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from sports.atp.ingestion.co_odds_scraper import get_atp_co_odds
from sports.atp.model.predictor import predict_single
from sports.atp.model.value_detector import detect_value_bets, format_value_report
from utils.logger import get_logger

logger = get_logger("run_atp_pipeline")

OUTPUT_DIR = "output"

# ── Helpers CSV ───────────────────────────────────────────────────────────────

def _upsert_csv(path: str, new_df: pd.DataFrame, date_col: str = "fetch_date") -> None:
    """
    Añade/actualiza registros en un CSV acumulativo.
    Elimina registros anteriores con la misma fecha antes de añadir los nuevos.
    """
    if new_df.empty:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if os.path.exists(path):
        try:
            existing = pd.read_csv(path)
            if date_col in existing.columns and date_col in new_df.columns:
                fetch_date = new_df[date_col].iloc[0]
                existing   = existing[existing[date_col] != fetch_date]
            combined = pd.concat([existing, new_df], ignore_index=True)
        except Exception:
            combined = new_df
    else:
        combined = new_df

    combined.to_csv(path, index=False)
    logger.info("CSV guardado: %s (%d filas)", path, len(combined))


# ── Pasos del pipeline ────────────────────────────────────────────────────────

def _step_odds(date_str: str) -> pd.DataFrame:
    """Paso 1: Descargar cuotas Kambi para la fecha."""
    logger.info("[1/4] Descargando cuotas Kambi para %s …", date_str)
    odds_df = get_atp_co_odds(date_str=date_str)
    if odds_df.empty:
        logger.warning("Sin cuotas ATP disponibles para %s", date_str)
    else:
        logger.info("  → %d líneas de cuotas | %d partidos únicos",
                    len(odds_df),
                    odds_df[["player1_name", "player2_name"]].drop_duplicates().shape[0])
    return odds_df


def _step_predict(odds_df: pd.DataFrame, date_str: str) -> pd.DataFrame:
    """
    Paso 2: Generar predicciones para cada partido único en las cuotas.

    Deduplica por (player1_name, player2_name, surface, tourney_name) para
    no predecir el mismo partido N veces (una por casa de apuestas).
    """
    logger.info("[2/4] Generando predicciones …")

    # Partidos únicos (tomamos la primera línea de Kambi para obtener los metadatos)
    matchups = (
        odds_df
        .drop_duplicates(subset=["player1_name", "player2_name", "tourney_name"])
        .reset_index(drop=True)
    )

    records = []
    for _, row in matchups.iterrows():
        p1    = str(row["player1_name"])
        p2    = str(row["player2_name"])
        surf  = str(row.get("surface", "Hard"))
        tourney = str(row.get("tourney_name", ""))
        level = str(row.get("tourney_level", "A"))
        eid   = row.get("event_id", "")

        pred = predict_single(
            player1_name=p1,
            player2_name=p2,
            surface=surf,
            tourney_name=tourney,
            tourney_level=level,
        )
        pred["match_id"]    = str(eid)
        pred["fetch_date"]  = date_str
        pred["game_date"]   = str(row.get("match_datetime", date_str))[:10]
        records.append(pred)

    if not records:
        return pd.DataFrame()

    preds_df = pd.DataFrame(records)

    # Descartar columna 'features' (dict no serializable para CSV)
    if "features" in preds_df.columns:
        preds_df = preds_df.drop(columns=["features"])

    logger.info("  → %d predicciones generadas (método: %s)",
                len(preds_df),
                preds_df["method"].value_counts().to_dict() if "method" in preds_df.columns else "N/A")
    return preds_df


def _step_value_bets(preds_df: pd.DataFrame, odds_df: pd.DataFrame) -> pd.DataFrame:
    """Paso 3: Detectar value bets cruzando predicciones con cuotas."""
    logger.info("[3/4] Detectando value bets …")
    if preds_df.empty or odds_df.empty:
        return pd.DataFrame()

    vb_df = detect_value_bets(preds_df, odds_df)
    if vb_df.empty:
        logger.info("  → Sin value bets detectadas para hoy")
    else:
        logger.info("  → %d value bets encontradas", len(vb_df))
    return vb_df


def _step_save(
    date_str: str,
    odds_df: pd.DataFrame,
    preds_df: pd.DataFrame,
    vb_df: pd.DataFrame,
    save: bool = True,
) -> None:
    """Paso 4: Persistir resultados en CSV."""
    if not save:
        logger.info("[4/4] Guardado omitido (--no-save)")
        return

    logger.info("[4/4] Guardando resultados …")

    for df, fname in [
        (odds_df,  "atp_odds.csv"),
        (preds_df, "atp_predictions.csv"),
        (vb_df,    "atp_value_bets.csv"),
    ]:
        if not df.empty:
            df_out = df.copy()
            if "fetch_date" not in df_out.columns:
                df_out["fetch_date"] = date_str
            _upsert_csv(os.path.join(OUTPUT_DIR, fname), df_out)


# ── Reporte de consola ────────────────────────────────────────────────────────

def _print_report(
    date_str: str,
    preds_df: pd.DataFrame,
    vb_df: pd.DataFrame,
) -> None:
    """Imprime en consola el resumen del pipeline."""
    SEPARATOR = "=" * 65

    print(f"\n{SEPARATOR}")
    print(f"  PIPELINE ATP — {date_str}")
    print(SEPARATOR)

    if preds_df.empty:
        print("  Sin predicciones disponibles para hoy.\n")
        return

    # Predicciones
    print(f"\n  PREDICCIONES ({len(preds_df)} partidos)\n")
    print(f"  {'JUGADOR 1':<22} {'JUGADOR 2':<22} {'P1%':>6} {'P2%':>6} {'ELO':>6} {'TORNEO':<20} {'SUPERF':<8}")
    print(f"  {'-'*22} {'-'*22} {'-'*6} {'-'*6} {'-'*6} {'-'*20} {'-'*8}")

    for _, r in preds_df.iterrows():
        p1 = str(r.get("player1_name", ""))[:21]
        p2 = str(r.get("player2_name", ""))[:21]
        p1p = float(r.get("p1_win_prob", 0.5))
        p2p = float(r.get("p2_win_prob", 0.5))
        elo_p = float(r.get("p1_elo_prob", 0.5))
        tourney = str(r.get("tourney_name", ""))[:19]
        surf = str(r.get("surface", ""))[:7]
        method = str(r.get("method", "elo"))
        method_tag = "ML" if method == "blend" else "Elo"
        print(f"  {p1:<22} {p2:<22} {p1p:>5.1%} {p2p:>5.1%} {elo_p:>5.1%} {tourney:<20} {surf:<8} [{method_tag}]")

    # Value bets
    print(f"\n{SEPARATOR}")
    if vb_df.empty:
        print("  Sin value bets detectadas para hoy.")
    else:
        print(format_value_report(vb_df))

    print(f"{SEPARATOR}\n")


# ── Función principal ─────────────────────────────────────────────────────────

def run(date_str: Optional[str] = None, save: bool = True) -> dict:
    """
    Ejecuta el pipeline ATP completo para una fecha.

    Returns:
        {'odds': df, 'predictions': df, 'value_bets': df}
    """
    date_str = date_str or date.today().isoformat()
    logger.info("=" * 60)
    logger.info("PIPELINE ATP — %s", date_str)
    logger.info("=" * 60)

    # Paso 1: Cuotas
    odds_df = _step_odds(date_str)

    if odds_df.empty:
        logger.warning("Sin cuotas disponibles. Finalizando pipeline.")
        return {"odds": pd.DataFrame(), "predictions": pd.DataFrame(), "value_bets": pd.DataFrame()}

    # Paso 2: Predicciones
    preds_df = _step_predict(odds_df, date_str)

    # Paso 3: Value bets
    vb_df = _step_value_bets(preds_df, odds_df)

    # Paso 4: Guardar
    _step_save(date_str, odds_df, preds_df, vb_df, save=save)

    # Reporte
    _print_report(date_str, preds_df, vb_df)

    return {
        "odds":        odds_df,
        "predictions": preds_df,
        "value_bets":  vb_df,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pipeline diario ATP — predicciones + value bets"
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="Fecha ISO (YYYY-MM-DD). Por defecto: hoy."
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="No guardar CSVs, solo mostrar resultados en consola."
    )
    args = parser.parse_args()

    run(date_str=args.date, save=not args.no_save)
