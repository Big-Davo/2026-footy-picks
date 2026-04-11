#!/usr/bin/env python3
"""
scores.py - PPW-based NRL/AFL tipping scorer for GitHub Actions.

Scoring formula (confirmed from competition Working sheet):
  - Each NRL/AFL team has a fixed PPW (Points Per Win) set at season start.
  - Best teams = small PPW, worst teams = large PPW.
  - Win  → tipster earns that team's PPW value.
  - Loss / Bye → tipster earns 0.

Competition week alignment:
  - Week N = NRL Round N  +  AFL Round (N-1)
  - AFL Opening Round = AFL "Round 0" in Squiggle = competition Week 1 AFL component
  - AFL Round 1 = competition Week 2 AFL component, etc.

Data sources:
  - NRL results: Wikipedia (2026 NRL season results page)
  - AFL results: Squiggle API (https://api.squiggle.com.au)
"""

import csv
import io
import json
import os
import re
import base64
import requests
from bs4 import BeautifulSoup

# ============================================================
# PPW VALUES (from Working sheet of weekly email files)
# ============================================================
AFL_PPW = {
    "Adelaide Crows": 9,
    "Brisbane Lions": 5,
    "Carlton Blues": 16,
    "Collingwood Magpies": 12,
    "Essendon Bombers": 21,
    "Fremantle Dockers": 11,
    "Geelong Cats": 7,
    "Gold Coast Suns": 6,
    "GWS Giants": 10,
    "Hawthorn Hawks": 8,
    "Melbourne Demons": 20,
    "North Melbourne Kangaroos": 22,
    "Port Adelaide Power": 18,
    "Richmond Tigers": 24,
    "St Kilda Saints": 14,
    "Sydney Swans": 8,
    "West Coast Eagles": 24,
    "Western Bulldogs": 10,
}

NRL_PPW = {
    "Brisbane Broncos": 5,
    "Canberra Raiders": 10,
    "Canterbury Bulldogs": 9,
    "Cronulla Sharks": 10,
    "Gold Coast Titans": 19,
    "Manly Sea Eagles": 14,
    "Melbourne Storm": 7,
    "Newcastle Knights": 19,
    "New Zealand Warriors": 13,
    "North Queensland Cowboys": 15,
    "Parramatta Eels": 12,
    "Penrith Panthers": 6,
    "Redcliffe Dolphins": 11,
    "South Sydney Rabbitohs": 11,
    "St George Illawarra Dragons": 19,
    "Sydney Roosters": 7,
    "Wests Tigers": 16,
}

# Wikipedia NRL name → competition name
NRL_NAME_MAP = {
    "Cronulla-Sutherland Sharks": "Cronulla Sharks",
    "Canterbury-Bankstown Bulldogs": "Canterbury Bulldogs",
    "Dolphins": "Redcliffe Dolphins",
    "St. George Illawarra Dragons": "St George Illawarra Dragons",
    "Manly Warringah Sea Eagles": "Manly Sea Eagles",
    # Names that are already correct
    "Brisbane Broncos": "Brisbane Broncos",
    "Canberra Raiders": "Canberra Raiders",
    "Gold Coast Titans": "Gold Coast Titans",
    "Melbourne Storm": "Melbourne Storm",
    "Newcastle Knights": "Newcastle Knights",
    "New Zealand Warriors": "New Zealand Warriors",
    "North Queensland Cowboys": "North Queensland Cowboys",
    "Parramatta Eels": "Parramatta Eels",
    "Penrith Panthers": "Penrith Panthers",
    "South Sydney Rabbitohs": "South Sydney Rabbitohs",
    "Sydney Roosters": "Sydney Roosters",
    "Wests Tigers": "Wests Tigers",
}

# Squiggle AFL name → competition name
AFL_NAME_MAP = {
    "Adelaide": "Adelaide Crows",
    "Brisbane Lions": "Brisbane Lions",
    "Carlton": "Carlton Blues",
    "Collingwood": "Collingwood Magpies",
    "Essendon": "Essendon Bombers",
    "Fremantle": "Fremantle Dockers",
    "Geelong": "Geelong Cats",
    "Gold Coast": "Gold Coast Suns",
    "Greater Western Sydney": "GWS Giants",
    "Hawthorn": "Hawthorn Hawks",
    "Melbourne": "Melbourne Demons",
    "North Melbourne": "North Melbourne Kangaroos",
    "Port Adelaide": "Port Adelaide Power",
    "Richmond": "Richmond Tigers",
    "St Kilda": "St Kilda Saints",
    "Sydney": "Sydney Swans",
    "West Coast": "West Coast Eagles",
    "Western Bulldogs": "Western Bulldogs",
}

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = "Big-Davo/2026-footy-picks"

FRIENDS = [
    "Big Davo", "Cameron 01", "Cameron 02", "BigDavo 2",
    "JohnC", "JohnC2", "Ginger1", "Ginger2",
    "Wcord2", "Dylan C", "RobynC"
]


# ============================================================
# NRL RESULTS FROM WIKIPEDIA
# ============================================================
def normalize_nrl(name):
    name = name.strip()
    return NRL_NAME_MAP.get(name, name)


def get_nrl_results():
    """
    Returns: {round_num (1-based): set of competition-name winners}
    e.g. {1: {"Canberra Raiders", "Cronulla Sharks", ...}, 2: {...}, ...}
    """
    url = "https://en.wikipedia.org/wiki/2026_NRL_season_results"
    try:
        resp = requests.get(url, headers={"User-Agent": "FootyTipping/1.0"}, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ERROR fetching NRL Wikipedia: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    results = {}   # {round_num: set(winners)}
    current_round = 0

    content = soup.find("div", {"class": "mw-parser-output"}) or soup

    for element in content.find_all(["h2", "h3", "table"]):
        tag = element.name

        # New round section when we see a heading with a month name
        if tag in ("h2", "h3"):
            text = element.get_text()
            months = ["January","February","March","April","May","June",
                      "July","August","September","October","November","December"]
            if any(m in text for m in months):
                current_round += 1

        elif tag == "table" and "wikitable" in element.get("class", []):
            if current_round < 1:
                continue

            if current_round not in results:
                results[current_round] = set()

            for row in element.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue

                # Score is expected in cell index 1; home in 0, away in 2
                score_text = cells[1].get_text(strip=True)
                m = re.match(r"^(\d+)\s*[–—\-]\s*(\d+)\*?$", score_text)
                if not m:
                    continue

                home_score = int(m.group(1))
                away_score = int(m.group(2))

                home = normalize_nrl(cells[0].get_text(strip=True))
                away = normalize_nrl(cells[2].get_text(strip=True))

                if home_score > away_score:
                    winner = home
                elif away_score > home_score:
                    winner = away
                else:
                    continue  # drawn (shouldn't happen in NRL)

                if winner in NRL_PPW:
                    results[current_round].add(winner)

    print(f"  NRL: found results for rounds {sorted(results.keys())}")
    for r, wins in sorted(results.items()):
        print(f"    Round {r}: {len(wins)} winners → {sorted(wins)}")

    return results


# ============================================================
# AFL RESULTS FROM SQUIGGLE
# ============================================================
def normalize_afl(name):
    name = name.strip()
    return AFL_NAME_MAP.get(name, name)


def get_afl_results():
    """
    Returns: {round_num (0=Opening Round, 1=AFL R1, ...): set of winners}
    Only includes complete games.
    AFL round included in competition week N = round index (N-1).
    e.g. competition Week 1 uses AFL round 0 (Opening Round).
    """
    url = "https://api.squiggle.com.au/?q=games;year=2026;complete=100"
    try:
        resp = requests.get(url, headers={"User-Agent": "FootyTipping/1.0"}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  ERROR fetching AFL Squiggle: {e}")
        return {}

    results = {}

    for game in data.get("games", []):
        if game.get("complete", 0) != 100:
            continue

        round_num = game.get("round")
        if round_num is None:
            continue
        round_num = int(round_num)  # 0 = Opening Round, 1 = AFL R1, etc.

        hteam = normalize_afl(game.get("hteam", ""))
        ateam = normalize_afl(game.get("ateam", ""))
        hscore = game.get("hscore") or 0
        ascore = game.get("ascore") or 0

        if hscore > ascore:
            winner = hteam
        elif ascore > hscore:
            winner = ateam
        else:
            continue  # drawn

        if round_num not in results:
            results[round_num] = set()
        if winner in AFL_PPW:
            results[round_num].add(winner)

    print(f"  AFL: found results for rounds {sorted(results.keys())}")
    for r, wins in sorted(results.items()):
        label = "OR" if r == 0 else f"R{r}"
        print(f"    AFL {label}: {len(wins)} winners → {sorted(wins)}")

    return results


# ============================================================
# SCORE COMPUTATION
# ============================================================
def compute_scores(rows, nrl_results, afl_results):
    """
    Compute PPW-based cumulative scores for all tipsters.

    Column layout (0-indexed, matches competition-data.csv):
      0:Rank  1:LW  2:+/-  3:Tipster  4:FullName
      5:TotalScore  6:PRT  7:RdScore
      8:AFL1team  9:AFL1score  10:AFL2team  11:AFL2score ...  17:AFL5score
      18:NRL1team 19:NRL1score 20:NRL2team 21:NRL2score ... 27:NRL5score

    Week alignment:
      Competition Week N  →  NRL Round N  +  AFL Round (N-1)
    """
    if not nrl_results:
        print("  No NRL results available, skipping score update.")
        return rows

    nrl_current = max(nrl_results.keys())
    # AFL round that aligns with the current competition week
    afl_current = nrl_current - 1   # e.g. NRL R3 → AFL R2 (squiggle index 2)

    print(f"  Scoring up to NRL Round {nrl_current} / AFL Round {afl_current} (OR=0)")

    AFL_TEAM_COLS  = [8,  10, 12, 14, 16]
    AFL_SCORE_COLS = [9,  11, 13, 15, 17]
    NRL_TEAM_COLS  = [18, 20, 22, 24, 26]
    NRL_SCORE_COLS = [19, 21, 23, 25, 27]

    header = rows[0]
    updated = [header]

    scored_rows = []
    for row in rows[1:]:
        if not row or len(row) < 5 or not row[3]:
            updated.append(row)
            continue

        row = list(row) + [""] * max(0, 28 - len(row))

        afl_total     = 0
        afl_rd        = 0   # contribution from AFL current week
        nrl_total     = 0
        nrl_rd        = 0   # contribution from NRL current round

        # --- AFL ---
        for tc, sc in zip(AFL_TEAM_COLS, AFL_SCORE_COLS):
            team = row[tc].strip()
            if not team or team not in AFL_PPW:
                row[sc] = "0" if row[sc] == "" else row[sc]
                continue
            ppw = AFL_PPW[team]
            # Cumulative wins: AFL rounds 0 .. afl_current
            wins = sum(
                1 for r in range(0, afl_current + 1)
                if r in afl_results and team in afl_results[r]
            )
            cum = wins * ppw
            row[sc] = str(cum)
            afl_total += cum
            if afl_current in afl_results and team in afl_results[afl_current]:
                afl_rd += ppw

        # --- NRL ---
        for tc, sc in zip(NRL_TEAM_COLS, NRL_SCORE_COLS):
            team = row[tc].strip()
            if not team or team not in NRL_PPW:
                row[sc] = "0" if row[sc] == "" else row[sc]
                continue
            ppw = NRL_PPW[team]
            # Cumulative wins: NRL rounds 1 .. nrl_current
            wins = sum(
                1 for r in range(1, nrl_current + 1)
                if r in nrl_results and team in nrl_results[r]
            )
            cum = wins * ppw
            row[sc] = str(cum)
            nrl_total += cum
            if nrl_current in nrl_results and team in nrl_results[nrl_current]:
                nrl_rd += ppw

        total    = afl_total + nrl_total
        rd_score = afl_rd + nrl_rd
        prt      = total - rd_score

        row[5] = str(total)
        row[6] = str(prt)
        row[7] = str(rd_score)

        scored_rows.append(row)

    # Sort by total score descending, update Rank / LW / +/-
    scored_rows.sort(key=lambda r: int(r[5]) if r[5].lstrip('-').isdigit() else 0, reverse=True)
    for i, row in enumerate(scored_rows, start=1):
        lw   = row[0]   # previous rank (keep as-is from last email)
        rank = str(i)
        try:
            change = int(lw) - i if lw and lw != "-" else 0
        except (ValueError, TypeError):
            change = 0
        row[0] = rank
        row[2] = str(change)
    updated.extend(scored_rows)

    return updated


# ============================================================
# GITHUB HELPERS
# ============================================================
def get_github_file(filename):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
    resp = requests.get(url, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "User-Agent": "FootyTipping/1.0"
    })
    if resp.status_code != 200:
        print(f"  GitHub GET {filename}: {resp.status_code}")
        return None, None
    data = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    sha = data["sha"]
    return content, sha


def push_github_file(filename, content, sha, message):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "sha": sha
    }
    resp = requests.put(url, json=payload, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "User-Agent": "FootyTipping/1.0"
    })
    if resp.status_code in (200, 201):
        print(f"  Pushed {filename} ✓")
    else:
        print(f"  ERROR pushing {filename}: {resp.status_code} {resp.text[:200]}")


def rows_to_csv(rows):
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


# ============================================================
# MAIN
# ============================================================
def main():
    print("=== Footy Tipping Scorer (PPW) ===")

    # 1. Fetch game results
    print("\n[1] Fetching NRL results...")
    nrl_results = get_nrl_results()

    print("\n[2] Fetching AFL results...")
    afl_results = get_afl_results()

    if not nrl_results:
        print("\nNo NRL results found — aborting.")
        return

    # 2. Load competition data from GitHub
    print("\n[3] Loading competition-data.csv from GitHub...")
    comp_csv, comp_sha = get_github_file("competition-data.csv")
    if not comp_csv:
        print("  Could not load competition-data.csv — aborting.")
        return

    reader = csv.reader(io.StringIO(comp_csv))
    rows = list(reader)
    print(f"  Loaded {len(rows)-1} tipsters.")

    # 3. Compute scores
    print("\n[4] Computing PPW scores...")
    updated_rows = compute_scores(rows, nrl_results, afl_results)

    # 4. Build friends subset (keep week label from existing friends-data.csv)
    print("\n[5] Building friends subset...")
    friends_csv_raw, friends_sha = get_github_file("friends-data.csv")
    # Preserve the week label (first line) from the last email push
    week_label = ""
    if friends_csv_raw:
        lines = friends_csv_raw.strip().split("\n")
        if lines:
            week_label = lines[0]

    friends_rows = [updated_rows[0]]  # header
    for row in updated_rows[1:]:
        if len(row) > 3 and row[3] in FRIENDS:
            friends_rows.append(row)

    friends_content = (week_label + "\n" if week_label else "") + rows_to_csv(friends_rows)

    # 5. Push to GitHub
    print("\n[6] Pushing to GitHub...")
    comp_content = rows_to_csv(updated_rows)
    push_github_file("competition-data.csv", comp_content, comp_sha, "Auto-score update (PPW)")
    push_github_file("friends-data.csv", friends_content, friends_sha, "Auto-score update (PPW)")

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
