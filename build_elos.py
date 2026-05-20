"""
Script de inicialización ATP — Fase 1.

Descarga el historial completo de partidos ATP desde Jeff Sackmann (GitHub),
calcula los ratings Elo por superficie para todos los jugadores y guarda el
resultado en sports/atp/models/current_elos.json.

Este script solo necesita correrse UNA VEZ para inicializar el sistema, y
luego semanalmente (o al inicio de cada temporada) para mantener los Elos
actualizados con los partidos más recientes.

Uso:
    python build_elos.py                          # 2010-hoy, modo normal
    python build_elos.py --start-year 2005        # desde 2005
    python build_elos.py --force                  # re-descarga aunque exista caché
    python build_elos.py --dry-run                # muestra stats sin guardar

Salida:
    sports/atp/models/current_elos.json           # ratings por jugador y superficie
    data/atp_cache/atp_matches_YYYY.csv           # caché local de cada año

Tiempo estimado:
    Primera ejecución (descarga + cálculo 2010-2026): ~3-5 minutos
    Ejecuciones siguientes (desde caché):             ~30 segundos
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
))))

from sports.atp.config.settings import ATP_ELO_PATH, ATP_ELO_START_YEAR, ATP_DATA_END_YEAR
from sports.atp.ingestion.historical_client import download_atp_matches, download_atp_players
from sports.atp.ingestion.elo import (
    compute_elos_from_history,
    save_current_elos,
    load_current_elos,
    SURFACES,
)
from utils.logger import get_logger

logger = get_logger("build_elos_atp")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--start-year", type=int, default=ATP_ELO_START_YEAR,
        help=f"Año de inicio para el cálculo de Elo (default: {ATP_ELO_START_YEAR})",
    )
    parser.add_argument(
        "--end-year", type=int, default=ATP_DATA_END_YEAR,
        help=f"Año final (default: {ATP_DATA_END_YEAR})",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-descarga los CSVs aunque ya existan en caché",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Calcula Elos pero NO guarda el archivo JSON",
    )
    parser.add_argument(
        "--top", type=int, default=20,
        help="Muestra el TOP N jugadores por Elo en cada superficie (default: 20)",
    )
    return parser.parse_args()


def _print_top_players(
    elos: dict[int, dict[str, float]],
    players_df,
    surface: str,
    top_n: int,
) -> None:
    """Imprime el ranking de los mejores jugadores según Elo en una superficie."""
    ranked = sorted(elos.items(), key=lambda x: x[1].get(surface, 1500), reverse=True)
    print(f"\n  ── Top {top_n} {surface} Elo ──")
    print(f"  {'#':>3}  {'Jugador':<28}  {'Elo':>7}")
    print(f"  {'─'*3}  {'─'*28}  {'─'*7}")

    # Mapa player_id → nombre desde players_df
    id_to_name: dict[int, str] = {}
    if players_df is not None and not players_df.empty:
        for _, row in players_df.iterrows():
            pid = int(row.get("player_id", 0) or 0)
            first = str(row.get("name_first", "") or "").strip()
            last  = str(row.get("name_last",  "") or "").strip()
            id_to_name[pid] = f"{first} {last}".strip()

    shown = 0
    for pid, surf_elos in ranked:
        if shown >= top_n:
            break
        elo  = surf_elos.get(surface, 1500)
        name = id_to_name.get(pid, f"ID:{pid}")
        print(f"  {shown+1:>3}. {name:<28}  {elo:>7.1f}")
        shown += 1


def main() -> int:
    args = _parse_args()

    print("=" * 60)
    print("  ATP Elo Builder — Fase 1")
    print(f"  Rango: {args.start_year} – {args.end_year}")
    print(f"  Force: {args.force}  |  Dry-run: {args.dry_run}")
    print("=" * 60)

    # ── 1. Descargar dataset histórico ────────────────────────────────────────
    print(f"\n[1/3] Descargando partidos ATP {args.start_year}-{args.end_year}…")
    matches_df = download_atp_matches(
        start_year=args.start_year,
        end_year=args.end_year,
        force=args.force,
    )

    if matches_df.empty:
        logger.error("No se pudieron descargar partidos. Verifica la conexión a internet.")
        return 1

    print(f"      → {len(matches_df):,} partidos cargados")

    # Distribución por superficie
    if "surface" in matches_df.columns:
        surf_counts = matches_df["surface"].value_counts()
        for surf, cnt in surf_counts.items():
            print(f"         {surf:<8}: {cnt:,} partidos")

    # ── 2. Calcular Elos ──────────────────────────────────────────────────────
    print("\n[2/3] Calculando Elos por superficie…")
    elos = compute_elos_from_history(matches_df)
    print(f"      → {len(elos):,} jugadores con Elo calculado")

    # ── 3. Descargar jugadores para mostrar nombres ───────────────────────────
    print("\n[3/3] Descargando catálogo de jugadores para los rankings…")
    players_df = download_atp_players(force=args.force)

    # ── Mostrar top jugadores ─────────────────────────────────────────────────
    for surface in SURFACES:
        _print_top_players(elos, players_df, surface, args.top)

    # Estadísticas de Elo
    all_hard  = [v["Hard"]  for v in elos.values() if "Hard"  in v]
    all_clay  = [v["Clay"]  for v in elos.values() if "Clay"  in v]
    all_grass = [v["Grass"] for v in elos.values() if "Grass" in v]
    print(f"\n  Elo medio Hard : {sum(all_hard)/len(all_hard):.1f}"  if all_hard  else "")
    print(f"  Elo medio Clay : {sum(all_clay)/len(all_clay):.1f}"  if all_clay  else "")
    print(f"  Elo medio Grass: {sum(all_grass)/len(all_grass):.1f}" if all_grass else "")

    # ── Guardar ──────────────────────────────────────────────────────────────
    if args.dry_run:
        print("\n[dry-run] Elos calculados pero NO guardados.")
    else:
        save_current_elos(elos, ATP_ELO_PATH)
        print(f"\n✓ Elos guardados en: {ATP_ELO_PATH}")

    print("\n" + "=" * 60)
    print("  Fase 1 completada.")
    print("  Siguiente paso: Fase 2 (ingesta en tiempo real)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
