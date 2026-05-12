"""Diagnóstico del matching game_ids para hoy."""
from ingestion.nba_client import get_daily_games
from ingestion.co_odds_scraper import get_co_odds, normalize_team
from nba_api.stats.static import teams as nba_teams_static
import pandas as pd

teams_map = {t['id']: t['full_name'] for t in nba_teams_static.get_teams()}

# Partidos NBA hoy
games = get_daily_games('2026-05-11')
print("=== Partidos NBA API hoy ===")
if games.empty:
    print("VACIO")
else:
    for _, g in games.iterrows():
        h = teams_map.get(int(g['home_team_id']), str(g['home_team_id']))
        v = teams_map.get(int(g['visitor_team_id']), str(g['visitor_team_id']))
        hn = normalize_team(h)
        vn = normalize_team(v)
        print(f"  [{g['game_id']}] {h} ({hn}) vs {v} ({vn})")

print()
print("=== Cuotas Kambi hoy (SIN games_df) ===")
odds = get_co_odds()
if not odds.empty:
    print(odds[['home_team','away_team','bookmaker','home_odds','away_odds']].to_string(index=False))

print()
print("=== Cuotas Kambi hoy (CON games_df) ===")
odds2 = get_co_odds(games)
if not odds2.empty:
    print(odds2[['game_id','home_team','away_team','bookmaker']].to_string(index=False))
    assigned = (odds2['game_id'] != '').sum()
    print(f"\n{assigned}/{len(odds2)} cuotas con game_id asignado")
