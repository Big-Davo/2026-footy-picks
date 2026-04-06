"""
2026 Footy Tipping - Automated Score Calculator
================================================
Runs hourly via GitHub Actions.
Fetches NRL results from Wikipedia and AFL results from api.squiggle.com.au
Calculates tipping scores from picks in competition-data.csv
and pushes updated competition-data.csv and friends-data.csv to GitHub.
"""

import requests
import csv
import io
import json
import base64
import os
import re
from datetime import datetime

# ============================================================
# CONFIGURATION
# ============================================================

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO  = "Big-Davo/2026-footy-picks"
GITHUB_API   = "https://api.github.com"

NRL_WIKI_URL = "https://en.wikipedia.org/w/api.php?action=parse&page=2026_NRL_season_results&prop=wikitext&format=json"
AFL_API_URL  = "https://api.squiggle.com.au/?q=games;year=2026"

# Friends group — shown on the Friends screen of the web app
FRIENDS = [
    "Big Davo", "Cameron 01", "Cameron 02", "BigDavo 2",
    "JohnC", "JohnC2", "Ginger1", "Ginger2",
    "Wcord2", "Dylan C", "RobynC"
]

# ============================================================
# NRL TEAM NAME MAPPING
# Wikipedia full name -> competition full name
# ============================================================
NRL_MAP = {
    "Newcastle Knights":             "Newcastle Knights",
    "North Queensland Cowboys":      "North Queensland Cowboys",
    "Canterbury-Bankstown Bulldogs": "Canterbury Bulldogs",
    "St George Illawarra Dragons":   "St George Illawarra Dragons",
    "Melbourne Storm":               "Melbourne Storm",
    "Parramatta Eels":               "Parramatta Eels",
    "New Zealand Warriors":          "New Zealand Warriors",
    "Sydney Roosters":               "Sydney Roosters",
    "Brisbane Broncos":              "Brisbane Broncos",
    "Penrith Panthers":              "Penrith Panthers",
    "Cronulla-Sutherland Sharks":    "Cronulla Sharks",
    "Gold Coast Titans":             "Gold Coast Titans",
    "South Sydney Rabbitohs":        "South Sydney Rabbitohs",
    "Canberra Raiders":              "Canberra Raiders",
    "Manly-Warringah Sea Eagles":    "Manly Sea Eagles",
    "Dolphins":                      "Redcliffe Dolphins",
    "Wests Tigers":                  "Wests Tigers",
}

# ============================================================
# AFL TEAM NAME MAPPING
# api.squiggle.com.au name -> competition full name
# ============================================================
AFL_MAP = {
    "Carlton":          "Carlton Blues",
    "GWS Giants":       "GWS Giants",
    "Gold Coast":       "Gold Coast Suns",
    "Hawthorn":         "Hawthorn Hawks",
    "Melbourne":        "Melbourne Demons",
    "North Melbourne":  "North Melbourne Kangaroos",
    "Port Adelaide":    "Port Adelaide Power",
    "St Kilda":         "St Kilda Saints",
    "Fremantle":        "Fremantle Dockers",
    "Collingwood":      "Collingwood Magpies",
    "Essendon":         "Essendon Bombers",
    "Richmond":         "Richmond Tigers",
    "West Coast":       "West Coast Eagles",
    "Western Bulldogs": "Western Bulldogs",
    "Adelaide":         "Adelaide Crows",
    "Brisbane Lions":   "Brisbane Lions",
    "Geelong":          "Geelong Cats",
    "Sydney":           "Sydney Swans",
}


# ============================================================
# FETCH NRL RESULTS FROM WIKIPEDIA
# Wikipedia is always publicly accessible and updated regularly.
# ============================================================
def fetch_nrl_results():
    results = {}

    headers = {
        "User-Agent": "FootyTipping/1.0 (github.com/Big-Davo/2026-footy-picks; automated scoring script)",
        "Accept": "application/json",
    }

    try:
        resp = requests.get(NRL_WIKI_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        wikitext = data["parse"]["wikitext"]["*"]
    except Exception as e:
        print(f"  ERROR fetching NRL Wikipedia data: {e}")
        return results

    current_round = None
    lines = wikitext.split("\n")

    for line in lines:
        # Detect round headers e.g. == Round 1 ==
        round_match = re.match(r"==+\s*Round\s+(\d+)\s*==+", line, re.IGNORECASE)
        if round_match:
            current_round = round_match.group(1)
            continue

        if current_round is None:
            continue

        # Match lines containing RL match templates
        if "{{RL match" in line or "{{rl match" in line.lower():
            home    = re.search(r"\|home\s*=\s*([^|}\n]+)", line)
            away    = re.search(r"\|away\s*=\s*([^|}\n]+)", line)
            hscore  = re.search(r"\|hscore\s*=\s*(\d+)", line)
            ascore  = re.search(r"\|ascore\s*=\s*(\d+)", line)

            if home and away and hscore and ascore:
                home_raw   = home.group(1).strip()
                away_raw   = away.group(1).strip()
                home_score = int(hscore.group(1))
                away_score = int(ascore.group(1))

                home_team = NRL_MAP.get(home_raw, home_raw)
                away_team = NRL_MAP.get(away_raw, away_raw)
                margin    = abs(home_score - away_score)

                if home_score > away_score:
                    results[f"{home_team}|{current_round}"] = margin
                    results[f"{away_team}|{current_round}"] = 0
                elif away_score > home_score:
                    results[f"{away_team}|{current_round}"] = margin
                    results[f"{home_team}|{current_round}"] = 0
                else:
                    results[f"{home_team}|{current_round}"] = 0
                    results[f"{away_team}|{current_round}"] = 0

    return results


# ============================================================
# FETCH AFL RESULTS FROM api.squiggle.com.au
# ============================================================
def fetch_afl_results():
    results = {}
    headers = {
        "User-Agent": "FootyTipping/1.0 (github.com/Big-Davo/2026-footy-picks)",
        "Accept": "application/json",
    }

    try:
        resp = requests.get(AFL_API_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  ERROR fetching AFL results: {e}")
        return results

    games = data.get("games", [])

    for game in games:
        try:
            round_num  = str(game.get("round", ""))
            home_raw   = game.get("hteam", "")
            away_raw   = game.get("ateam", "")
            home_score = game.get("hscore", None)
            away_score = game.get("ascore", None)

            # Skip unplayed games and opening round
            if home_score is None or away_score is None:
                continue
            if round_num == "0":
                continue

            home_team = AFL_MAP.get(home_raw, home_raw)
            away_team = AFL_MAP.get(away_raw, away_raw)

            home_score = int(home_score)
            away_score = int(away_score)
            margin     = abs(home_score - away_score)

            if home_score > away_score:
                results[f"{home_team}|{round_num}"] = margin
                results[f"{away_team}|{round_num}"] = 0
            elif away_score > home_score:
                results[f"{away_team}|{round_num}"] = margin
                results[f"{home_team}|{round_num}"] = 0
            else:
                results[f"{home_team}|{round_num}"] = 0
                results[f"{away_team}|{round_num}"] = 0

        except Exception:
            continue

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
    # Find all completed rounds from both sports
    all_rounds = set()
    for key in list(nrl_results.keys()) + list(afl_results.keys()):
        round_num = key.split("|")[1]
        try:
            int(round_num)
            all_rounds.add(round_num)
        except ValueError:
            pass

    if not all_rounds:
        print("  No completed rounds found — nothing to calculate")
        return None, None

    latest_round = str(max(int(r) for r in all_rounds))
    print(f"  Completed rounds: {sorted(all_rounds, key=int)}")
    print(f"  Latest round: {latest_round}")

    reader = csv.reader(io.StringIO(competition_csv))
    rows   = list(reader)
    if not rows:
        return None, None

    header = rows[0]

    def col(name):
        try:
            return header.index(name)
        except ValueError:
            return None

    tipster_col = col("Tipster")
    total_col   = col("Total Score")
    prt_col     = col("PRT")
    rd_col      = col("Rd score")
    afl1_col    = col("AFL Team 1")
    nrl1_col    = col("NRL Team 1")

    if None in (tipster_col, total_col, prt_col, rd_col, afl1_col, nrl1_col):
        print("  ERROR: Could not find required columns in competition-data.csv")
        print(f"  Headers: {header}")
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

        # Get picks
        afl_picks = [row[afl1_col + t * 2].strip() if afl1_col + t * 2 < len(row) else "" for t in range(5)]
        nrl_picks = [row[nrl1_col + t * 2].strip() if nrl1_col + t * 2 < len(row) else "" for t in range(5)]

        total_score = 0
        rd_score    = 0

        for rnd in all_rounds:
            round_score = 0

            # Score AFL picks
            for t, team in enumerate(afl_picks):
                if team:
                    pts = afl_results.get(f"{team}|{rnd}", None)
                    if pts is not None:
                        round_score += pts
                        if rnd == latest_round:
                            score_col = afl1_col + t * 2 + 1
                            if score_col < len(row):
                                row[score_col] = str(pts)

            # Score NRL picks
            for t, team in enumerate(nrl_picks):
                if team:
                    pts = nrl_results.get(f"{team}|{rnd}", None)
                    if pts is not None:
                        round_score += pts
                        if rnd == latest_round:
                            score_col = nrl1_col + t * 2 + 1
                            if score_col < len(row):
                                row[score_col] = str(pts)

            total_score += round_score
            if rnd == latest_round:
                rd_score = round_score

        row[total_col] = str(total_score)
        row[prt_col]   = str(total_score - rd_score)
        row[rd_col]    = str(rd_score)
        updated_rows.append(row)

    # Sort by total score descending
    data_rows = updated_rows[1:]
    data_rows.sort(
        key=lambda r: int(r[total_col]) if r and len(r) > total_col and r[total_col].lstrip("-").isdigit() else 0,
        reverse=True
    )

    # Update rank columns
    rank_col   = col("Rank")
    lw_col     = col("LW")
    change_col = col("+/-")

    for i, row in enumerate(data_rows):
        if not row:
            continue
        old_rank = row[rank_col] if rank_col is not None and len(row) > rank_col else str(i + 1)
        if rank_col is not None and len(row) > rank_col:
            row[rank_col] = str(i + 1)
        if lw_col is not None and len(row) > lw_col:
            row[lw_col] = old_rank
        if change_col is not None and len(row) > change_col:
            try:
                change = int(old_rank) - (i + 1)
                row[change_col] = f"+{change}" if change > 0 else str(change)
            except ValueError:
                row[change_col] = "-"

    return [header] + data_rows, latest_round


# ============================================================
# GENERATE CSV STRING
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

    # 1. Fetch results
    print("\nFetching NRL results from Wikipedia...")
    nrl_results = fetch_nrl_results()
    print(f"  {len(nrl_results)} NRL team/round results loaded")

    print("Fetching AFL results from api.squiggle.com.au...")
    afl_results = fetch_afl_results()
    print(f"  {len(afl_results)} AFL team/round results loaded")

    # 2. Read competition data from GitHub
    print("\nReading competition-data.csv from GitHub...")
    competition_csv, comp_sha = github_get("competition-data.csv")
    print(f"  Read OK ({len(competition_csv)} bytes)")

    # 3. Calculate scores
    print("\nCalculating scores...")
    updated_rows, latest_round = calculate_scores(
        competition_csv, nrl_results, afl_results
    )

    if updated_rows is None:
        print("  Nothing to update — exiting")
        return

    print(f"  {len(updated_rows) - 1} tipsters processed")

    # 4. Build updated CSVs
    updated_comp_csv = rows_to_csv(updated_rows)

    tipster_col  = updated_rows[0].index("Tipster")
    friends_rows = [updated_rows[0]] + [
        r for r in updated_rows[1:]
        if r and len(r) > tipster_col and r[tipster_col] in FRIENDS
    ]
    week_label  = f"2026 - Footy Tipping week {latest_round}"
    friends_csv = week_label + "\n" + rows_to_csv(friends_rows)

    # 5. Push to GitHub
    print("\nPushing competition-data.csv...")
    github_put(
        "competition-data.csv",
        updated_comp_csv,
        comp_sha,
        f"Auto-update scores — Round {latest_round} — {now}"
    )

    print("Pushing friends-data.csv...")
    _, friends_sha = github_get("friends-data.csv")
    github_put(
        "friends-data.csv",
        friends_csv,
        friends_sha,
        f"Auto-update friends — Round {latest_round} — {now}"
    )

    print(f"\n=== Done — Round {latest_round} scores published ===")


if __name__ == "__main__":
    main()
