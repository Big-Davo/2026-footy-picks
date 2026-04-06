"""
2026 Footy Tipping - Automated Score Calculator
================================================
Runs hourly via GitHub Actions.
Fetches NRL and AFL results from fixturedownload.com,
calculates tipping scores from picks in competition-data.csv,
and pushes updated competition-data.csv and friends-data.csv
back to GitHub.
"""

import requests
import csv
import io
import json
import base64
import os
from datetime import datetime

# ============================================================
# CONFIGURATION
# ============================================================

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO  = "Big-Davo/2026-footy-picks"
GITHUB_API   = "https://api.github.com"

NRL_CSV_URL  = "https://fixturedownload.com/download/csv/nrl-2026"
AFL_CSV_URL  = "https://fixturedownload.com/download/csv/afl-2026"

# Friends group — shown on the Friends screen of the web app
FRIENDS = [
    "Big Davo", "Cameron 01", "Cameron 02", "BigDavo 2",
    "JohnC", "JohnC2", "Ginger1", "Ginger2",
    "Wcord2", "Dylan C", "RobynC"
]

# ============================================================
# NRL TEAM NAME MAPPING
# fixturedownload.com short name -> competition full name
# ============================================================
NRL_MAP = {
    "Knights":     "Newcastle Knights",
    "Cowboys":     "North Queensland Cowboys",
    "Bulldogs":    "Canterbury Bulldogs",
    "Dragons":     "St George Illawarra Dragons",
    "Storm":       "Melbourne Storm",
    "Eels":        "Parramatta Eels",
    "Warriors":    "New Zealand Warriors",
    "Roosters":    "Sydney Roosters",
    "Broncos":     "Brisbane Broncos",
    "Panthers":    "Penrith Panthers",
    "Sharks":      "Cronulla Sharks",
    "Titans":      "Gold Coast Titans",
    "Rabbitohs":   "South Sydney Rabbitohs",
    "Raiders":     "Canberra Raiders",
    "Sea Eagles":  "Manly Sea Eagles",
    "Dolphins":    "Redcliffe Dolphins",
    "Wests Tigers":"Wests Tigers",
}

# ============================================================
# AFL TEAM NAME MAPPING
# fixturedownload.com short name -> competition full name
# ============================================================
AFL_MAP = {
    "Carlton":          "Carlton Blues",
    "GWS GIANTS":       "GWS Giants",
    "Gold Coast SUNS":  "Gold Coast Suns",
    "Hawthorn":         "Hawthorn Hawks",
    "Melbourne":        "Melbourne Demons",
    "North Melbourne":  "North Melbourne Kangaroos",
    "Port Adelaide":    "Port Adelaide Power",
    "St Kilda":         "St Kilda Saints",
    "Fremantle":        "Fremantle Dockers",
    "Collingwood":      "Collingwood Magpies",
    "Essendon":         "Essendon Bombers",
    "Richmond":         "Richmond Tigers",
    "West Coast Eagles":"West Coast Eagles",
    "Western Bulldogs": "Western Bulldogs",
    "Adelaide Crows":   "Adelaide Crows",
    "Brisbane Lions":   "Brisbane Lions",
    "Geelong Cats":     "Geelong Cats",
    "Sydney Swans":     "Sydney Swans",
}


# ============================================================
# FETCH RESULTS FROM FIXTUREDOWNLOAD.COM
# Returns dict: "TeamFullName|RoundNumber" -> margin (winner)
#                                          -> 0 (loser)
# ============================================================
def fetch_results(url, team_map, skip_rounds=("OR",)):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; FootyTipping/1.0)",
        "Accept": "text/csv,text/plain,*/*",
        "Referer": "https://fixturedownload.com/",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    results = {}
    reader = csv.DictReader(io.StringIO(resp.text))

    for row in reader:
        round_num = row.get("Round Number", "").strip()
        home_raw  = row.get("Home Team", "").strip()
        away_raw  = row.get("Away Team", "").strip()
        result    = row.get("Result", "").strip()

        # Skip unplayed games and ignored rounds (e.g. AFL Opening Round)
        if not result or round_num in skip_rounds:
            continue

        # Map to competition team names
        home = team_map.get(home_raw, home_raw)
        away = team_map.get(away_raw, away_raw)

        # Parse "HomeScore - AwayScore"
        try:
            parts = result.split(" - ")
            home_score = int(parts[0].strip())
            away_score = int(parts[1].strip())
        except (ValueError, IndexError):
            continue

        margin = abs(home_score - away_score)

        if home_score > away_score:
            results[f"{home}|{round_num}"] = margin
            results[f"{away}|{round_num}"] = 0
        elif away_score > home_score:
            results[f"{away}|{round_num}"] = margin
            results[f"{home}|{round_num}"] = 0
        else:
            # Draw — both score 0
            results[f"{home}|{round_num}"] = 0
            results[f"{away}|{round_num}"] = 0

    return results


# ============================================================
# READ FILE FROM GITHUB
# ============================================================
def github_get(filename):
    url  = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{filename}"
    hdrs = {"Authorization": f"token {GITHUB_TOKEN}", "User-Agent": "FootyTipping"}
    resp = requests.get(url, headers=hdrs, timeout=30)
    resp.raise_for_status()
    data    = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    sha     = data["sha"]
    return content, sha


# ============================================================
# PUSH FILE TO GITHUB
# ============================================================
def github_put(filename, content, sha, message):
    url  = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{filename}"
    hdrs = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type":  "application/json",
        "User-Agent":    "FootyTipping",
    }
    body = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "sha":     sha,
    }
    resp = requests.put(url, headers=hdrs, json=body, timeout=30)
    if resp.status_code not in (200, 201):
        print(f"  WARNING: push failed for {filename}: {resp.status_code} {resp.text[:200]}")
    else:
        print(f"  Pushed {filename} OK")


# ============================================================
# CALCULATE SCORES
# ============================================================
def calculate_scores(competition_csv, nrl_results, afl_results):
    """
    Reads all tipsters and their picks from competition-data.csv.
    Calculates total score and latest round score for each tipster.
    Returns updated rows ready to write back to CSV.
    """

    # Find all completed rounds
    all_rounds = set()
    for key in list(nrl_results.keys()) + list(afl_results.keys()):
        round_num = key.split("|")[1]
        try:
            int(round_num)   # only numeric rounds
            all_rounds.add(round_num)
        except ValueError:
            pass

    if not all_rounds:
        print("  No completed rounds found — nothing to calculate")
        return None, None

    latest_round = str(max(int(r) for r in all_rounds))
    print(f"  Completed rounds: {sorted(all_rounds, key=int)}")
    print(f"  Latest round: {latest_round}")

    # Parse competition CSV
    reader = csv.reader(io.StringIO(competition_csv))
    rows   = list(reader)
    if not rows:
        return None, None

    header = rows[0]

    # Find column indices
    def col(name):
        try:
            return header.index(name)
        except ValueError:
            return None

    tipster_col   = col("Tipster")
    total_col     = col("Total Score")
    prt_col       = col("PRT")
    rd_col        = col("Rd score")
    afl1_col      = col("AFL Team 1")
    nrl1_col      = col("NRL Team 1")

    if None in (tipster_col, total_col, prt_col, rd_col, afl1_col, nrl1_col):
        print("  ERROR: Could not find required columns in competition-data.csv")
        print(f"  Headers found: {header}")
        return None, None

    updated_rows = [header]

    for row in rows[1:]:
        if not row or len(row) <= tipster_col:
            updated_rows.append(row)
            continue

        tipster = row[tipster_col].strip()
        if not tipster:
            updated_rows.append(row)
            continue

        # Get AFL picks (5 teams, every 2 columns from afl1_col)
        afl_picks = []
        for t in range(5):
            idx = afl1_col + t * 2
            afl_picks.append(row[idx].strip() if idx < len(row) else "")

        # Get NRL picks (5 teams, every 2 columns from nrl1_col)
        nrl_picks = []
        for t in range(5):
            idx = nrl1_col + t * 2
            nrl_picks.append(row[idx].strip() if idx < len(row) else "")

        # Calculate total score across all completed rounds
        total_score = 0
        rd_score    = 0

        for rnd in all_rounds:
            round_score = 0

            for team in afl_picks:
                if team:
                    key = f"{team}|{rnd}"
                    if key in afl_results:
                        pts = afl_results[key]
                        round_score += pts
                        # Update individual score column in row
                        for t in range(5):
                            if afl_picks[t] == team:
                                score_col = afl1_col + t * 2 + 1
                                if score_col < len(row):
                                    if rnd == latest_round:
                                        row[score_col] = str(pts)

            for team in nrl_picks:
                if team:
                    key = f"{team}|{rnd}"
                    if key in nrl_results:
                        pts = nrl_results[key]
                        round_score += pts
                        # Update individual score column in row
                        for t in range(5):
                            if nrl_picks[t] == team:
                                score_col = nrl1_col + t * 2 + 1
                                if score_col < len(row):
                                    if rnd == latest_round:
                                        row[score_col] = str(pts)

            total_score += round_score
            if rnd == latest_round:
                rd_score = round_score

        # Update score columns
        if total_col < len(row):
            row[total_col] = str(total_score)
        if prt_col < len(row):
            row[prt_col] = str(total_score - rd_score)
        if rd_col < len(row):
            row[rd_col] = str(rd_score)

        updated_rows.append(row)

    # Sort by total score descending (skip header)
    data_rows = updated_rows[1:]
    data_rows.sort(
        key=lambda r: int(r[total_col]) if r and len(r) > total_col and r[total_col].lstrip("-").isdigit() else 0,
        reverse=True
    )

    # Add rank and LW columns
    for i, row in enumerate(data_rows):
        if row and len(row) > 0:
            lw_rank = row[col("Rank")] if col("Rank") is not None and len(row) > col("Rank") else str(i + 1)
            row[col("Rank")] = str(i + 1)
            if col("LW") is not None and len(row) > col("LW"):
                row[col("LW")] = lw_rank
            change_col = col("+/-")
            if change_col is not None and len(row) > change_col:
                try:
                    change = int(lw_rank) - (i + 1)
                    row[change_col] = f"+{change}" if change > 0 else str(change)
                except ValueError:
                    row[change_col] = "-"

    updated_rows = [header] + data_rows
    return updated_rows, latest_round


# ============================================================
# GENERATE CSV STRING FROM ROWS
# ============================================================
def rows_to_csv(rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(rows)
    return output.getvalue()


# ============================================================
# MAIN
# ============================================================
def main():
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print(f"=== Footy Tipping Score Update — {now} ===")

    # 1. Fetch match results
    print("\nFetching NRL results...")
    nrl_results = fetch_results(NRL_CSV_URL, NRL_MAP)
    print(f"  {len(nrl_results)} NRL team/round results loaded")

    print("Fetching AFL results...")
    afl_results = fetch_results(AFL_CSV_URL, AFL_MAP, skip_rounds=("OR",))
    print(f"  {len(afl_results)} AFL team/round results loaded")

    # 2. Read competition data from GitHub
    print("\nReading competition-data.csv from GitHub...")
    competition_csv, comp_sha = github_get("competition-data.csv")
    print(f"  Read OK ({len(competition_csv)} bytes)")

    # 3. Calculate scores
    print("\nCalculating scores...")
    updated_rows, latest_round = calculate_scores(competition_csv, nrl_results, afl_results)

    if updated_rows is None:
        print("  Nothing to update — exiting")
        return

    print(f"  {len(updated_rows) - 1} tipsters processed")

    # 4. Generate updated CSVs
    updated_comp_csv = rows_to_csv(updated_rows)

    # Build friends CSV (week label + friends rows only)
    friends_rows = [updated_rows[0]]  # header
    for row in updated_rows[1:]:
        tipster_col = updated_rows[0].index("Tipster")
        if row and len(row) > tipster_col and row[tipster_col] in FRIENDS:
            friends_rows.append(row)

    week_label = f"2026 - Footy Tipping week {latest_round}"
    friends_csv = week_label + "\n" + rows_to_csv(friends_rows)

    # 5. Push to GitHub
    print("\nPushing competition-data.csv...")
    _, friends_sha = github_get("friends-data.csv")
    github_put(
        "competition-data.csv",
        updated_comp_csv,
        comp_sha,
        f"Auto-update scores — Round {latest_round} — {now}"
    )

    print("Pushing friends-data.csv...")
    github_put(
        "friends-data.csv",
        friends_csv,
        friends_sha,
        f"Auto-update friends scores — Round {latest_round} — {now}"
    )

    print(f"\n=== Done — Round {latest_round} scores published ===")


if __name__ == "__main__":
    main()
