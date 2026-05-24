import requests, json

r = requests.get(
    "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard",
    params={"dates": "20260521"},
    headers={"User-Agent": "Mozilla/5.0"},
    timeout=20
)
data = r.json()

print("=== ESPN May 21 ATP Results ===")
results = []
for event in data.get("events", []):
    for grouping in event.get("groupings", []):
        grp_name = grouping.get("name", "").lower()
        for comp in grouping.get("competitions", []):
            status = (comp.get("status") or {}).get("type", {}).get("name", "")
            comp_type = (comp.get("type") or {}).get("text", "").lower()
            if status != "STATUS_FINAL":
                continue
            if "women" in comp_type or "women" in grp_name:
                continue
            if "men" not in comp_type and "men" not in grp_name:
                continue
            if "singles" not in comp_type and "singles" not in grp_name:
                continue
            competitors = [c for c in comp.get("competitors", []) if c.get("type") == "athlete"]
            if len(competitors) < 2:
                continue
            winner_name = None
            player_names = []
            for c in competitors:
                name = (c.get("athlete") or {}).get("displayName", "")
                if name:
                    player_names.append(name)
                if c.get("winner"):
                    winner_name = name
            if winner_name and len(player_names) == 2:
                print(f"  {player_names[0]} vs {player_names[1]} -> WINNER: {winner_name}")
                results.append({"p1": player_names[0], "p2": player_names[1], "winner": winner_name})

print(f"Total: {len(results)} completed men singles")
print()
print("=== Looking for Tommy Paul and Prado ===")
for r2 in results:
    key = (r2["p1"] + r2["p2"]).lower()
    if any(x in key for x in ["paul", "prado", "altmaier", "chak"]):
        print(f"  FOUND: {r2['p1']} vs {r2['p2']} -> {r2['winner']}")
