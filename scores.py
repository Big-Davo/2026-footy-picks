#!/usr/bin/env python3
"""
scores.py - PPW-based NRL/AFL tipping scorer for GitHub Actions.

Scoring formula (confirmed from competition Working sheet):
  - Each NRL/AFL team has a fixed PPW (Points Per Win) set at season start.
  - Win  → tipster earns that team's PPW value.
  - Loss / Bye → tipster earns 0.

Week alignment:
  Competition Week N = NRL Round N + AFL Round (N-1)
  AFL Opening Round = round index 0 in Squiggle = competition Week 1 AFL.
"""

import csv, io, json, os, re, base64
import requests
from bs4 import BeautifulSoup

# ── PPW VALUES (from Working sheet of weekly email files) ──────────────────────

AFL_PPW = {
    "Adelaide Crows": 9, "Brisbane Lions": 5, "Carlton Blues": 16,
    "Collingwood Magpies": 12, "Essendon Bombers": 21, "Fremantle Dockers": 11,
    "Geelong Cats": 7, "Gold Coast Suns": 6, "GWS Giants": 10,
    "Hawthorn Hawks": 8, "Melbourne Demons": 20, "North Melbourne Kangaroos": 22,
    "Port Adelaide Power": 18, "Richmond Tigers": 24, "St Kilda Saints": 14,
    "Sydney Swans": 8, "West Coast Eagles": 24, "Western Bulldogs": 10,
}

NRL_PPW = {
    "Brisbane Broncos": 5, "Canberra Raiders": 10, "Canterbury Bulldogs": 9,
    "Cronulla Sharks": 10, "Gold Coast Titans": 19, "Manly Sea Eagles": 14,
    "Melbourne Storm": 7, "Newcastle Knights": 19, "New Zealand Warriors": 13,
    "North Queensland Cowboys": 15, "Parramatta Eels": 12, "Penrith Panthers": 6,
    "Redcliffe Dolphins": 11, "South Sydney Rabbitohs": 11,
    "St George Illawarra Dragons": 19, "Sydney Roosters": 7, "Wests Tigers": 16,
}

# Wikipedia → competition name
NRL_NAME_MAP = {
    "Cronulla-Sutherland Sharks": "Cronulla Sharks",
    "Canterbury-Bankstown Bulldogs": "Canterbury Bulldogs",
    "Dolphins": "Redcliffe Dolphins",
    "St. George Illawarra Dragons": "St George Illawarra Dragons",
    "Manly Warringah Sea Eagles": "Manly Sea Eagles",
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

# Squiggle → competition name
AFL_NAME_MAP = {
    "Adelaide": "Adelaide Crows", "Brisbane Lions": "Brisbane Lions",
    "Carlton": "Carlton Blues", "Collingwood": "Collingwood Magpies",
    "Essendon": "Essendon Bombers", "Fremantle": "Fremantle Dockers",
    "Geelong": "Geelong Cats", "Gold Coast": "Gold Coast Suns",
    "Greater Western Sydney": "GWS Giants", "Hawthorn": "Hawthorn Hawks",
    "Melbourne": "Melbourne Demons", "North Melbourne": "North Melbourne Kangaroos",
    "Port Adelaide": "Port Adelaide Power", "Richmond": "Richmond Tigers",
    "St Kilda": "St Kilda Saints", "Sydney": "Sydney Swans",
    "West Coast": "West Coast Eagles", "Western Bulldogs": "Western Bulldogs",
}

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = "Big-Davo/2026-footy-picks"

FRIENDS = [
    "Big Davo", "Cameron 01", "Cameron 02", "BigDavo 2",
    "JohnC", "JohnC2", "Ginger1", "Ginger2",
    "Wcord2", "Dylan C", "RobynC",
]


# ── NRL RESULTS FROM WIKIPEDIA ────────────────────────────────────────────────

def normalize_nrl(name):
    return NRL_NAME_MAP.get(name.strip(), name.strip())


def get_nrl_results():
    """
    Each wikitable on the NRL results page = one competition round (in order).
    Round 1 = table 1, Round 2 = table 2, etc.
    Only rows with a completed score (digits–digits) are counted.
    """
    url = "https://en.wikipedia.org/wiki/2026_NRL_season_results"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FootyTipping/1.0)"}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ERROR fetching NRL Wikipedia: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table", class_="wikitable")
    print(f"  NRL: {len(tables)} wikitables found on page")

    results = {}
    score_re = re.compile(r"^(\d+)\s*[–—\-]\s*(\d+)\*?$")

    for round_num, table in enumerate(tables, start=1):
        winners = set()
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            # Score is always in the second data cell (index 1)
            raw = cells[1].get_text(strip=True)
            # Normalise unicode dashes and non-breaking spaces
            raw = raw.replace("\u2013", "-").replace("\u2014", "-").replace("\u00a0", "")
            m = score_re.match(raw)
            if not m:
                continue
            home_score, away_score = int(m.group(1)), int(m.group(2))
            home = normalize_nrl(cells[0].get_text(strip=True))
            away = normalize_nrl(cells[2].get_text(strip=True))
            if home_score > away_score:
                winner = home
            elif away_score > home_score:
                winner = away
            else:
                continue
            if winner in NRL_PPW:
                winners.add(winner)

        if winners:
            results[round_num] = winners

    print(f"  NRL: rounds with results → {sorted(results.keys())}")
    for r, wins in sorted(results.items()):
        print(f"    Round {r}: {sorted(wins)}")
    return results


# ── AFL RESULTS FROM SQUIGGLE ─────────────────────────────────────────────────

def normalize_afl(name):
    return AFL_NAME_MAP.get(name.strip(), name.strip())


def get_afl_results():
    """
    Squiggle round 0 = AFL Opening Round = competition Week 1 AFL component.
    Squiggle round N = AFL Round N = competition Week (N+1) AFL component.
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
        round_num = int(round_num)
        hteam  = normalize_afl(game.get("hteam", ""))
        ateam  = normalize_afl(game.get("ateam", ""))
        hscore = game.get("hscore") or 0
        ascore = game.get("ascore") or 0
        if hscore > ascore:
            winner = hteam
        elif ascore > hscore:
            winner = ateam
        else:
            continue
        if winner in AFL_PPW:
            results.setdefault(round_num, set()).add(winner)

    print(f"  AFL: rounds with results → {sorted(results.keys())}")
    for r, wins in sorted(results.items()):
        label = "OR" if r == 0 else f"R{r}"
        print(f"    AFL {label}: {sorted(wins)}")
    return results


# ── SCORE COMPUTATION ─────────────────────────────────────────────────────────

def compute_scores(rows, nrl_results, afl_results):
    """
    Column layout (0-indexed):
      0:Rank  1:LW  2:+/-  3:Tipster  4:FullName
      5:TotalScore  6:PRT  7:RdScore
      8:AFL1name  9:AFL1score ... 16:AFL5name 17:AFL5score
      18:NRL1name 19:NRL1score ... 26:NRL5name 27:NRL5score
    """
    nrl_current = max(nrl_results.keys())
    afl_current = nrl_current - 1   # AFL round index aligned to this competition week

    print(f"  Scoring: NRL up to Round {nrl_current}, AFL up to Round {afl_current} (0=OR)")

    AFL_TEAM_COLS  = [8,  10, 12, 14, 16]
    AFL_SCORE_COLS = [9,  11, 13, 15, 17]
    NRL_TEAM_COLS  = [18, 20, 22, 24, 26]
    NRL_SCORE_COLS = [19, 21, 23, 25, 27]

    header = rows[0]
    scored = []

    for row in rows[1:]:
        if not row or len(row) < 5 or not row[3]:
            continue
        row = list(row) + [""] * max(0, 28 - len(row))

        afl_total = nrl_total = afl_rd = nrl_rd = 0

        for tc, sc in zip(AFL_TEAM_COLS, AFL_SCORE_COLS):
            team = (row[tc] or "").strip()
            if not team or team not in AFL_PPW:
                continue
            ppw  = AFL_PPW[team]
            wins = sum(1 for r in range(0, afl_current + 1)
                       if r in afl_results and team in afl_results[r])
            cum  = wins * ppw
            row[sc] = str(cum)
            afl_total += cum
            if afl_current in afl_results and team in afl_results[afl_current]:
                afl_rd += ppw

        for tc, sc in zip(NRL_TEAM_COLS, NRL_SCORE_COLS):
            team = (row[tc] or "").strip()
            if not team or team not in NRL_PPW:
                continue
            ppw  = NRL_PPW[team]
            wins = sum(1 for r in range(1, nrl_current + 1)
                       if r in nrl_results and team in nrl_results[r])
            cum  = wins * ppw
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
        scored.append(row)

    # Sort by total descending and update ranks
    scored.sort(key=lambda r: int(r[5]) if str(r[5]).lstrip("-").isdigit() else 0, reverse=True)
    for i, row in enumerate(scored, start=1):
        try:
            change = int(row[0]) - i if row[0] and row[0] != "-" else 0
        except (ValueError, TypeError):
            change = 0
        row[0] = str(i)
        row[2] = str(change)

    return [header] + scored


# ── GITHUB HELPERS ────────────────────────────────────────────────────────────

def get_github_file(filename):
    url  = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
    resp = requests.get(url, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "User-Agent": "FootyTipping/1.0",
    })
    if resp.status_code != 200:
        print(f"  GitHub GET {filename}: {resp.status_code}")
        return None, None
    data    = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return content, data["sha"]


def push_github_file(filename, content, sha, message):
    url     = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "sha": sha,
    }
    resp = requests.put(url, json=payload, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "User-Agent": "FootyTipping/1.0",
    })
    if resp.status_code in (200, 201):
        print(f"  Pushed {filename} ✓")
    else:
        print(f"  ERROR pushing {filename}: {resp.status_code} {resp.text[:200]}")


def rows_to_csv(rows):
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("=== Footy Tipping Scorer (PPW) ===")

    print("\n[1] Fetching NRL results...")
    nrl_results = get_nrl_results()

    print("\n[2] Fetching AFL results...")
    afl_results = get_afl_results()

    if not nrl_results:
        print("\nNo NRL results found — aborting.")
        return

    print("\n[3] Loading competition-data.csv from GitHub...")
    comp_csv, comp_sha = get_github_file("competition-data.csv")
    if not comp_csv:
        print("  Could not load — aborting.")
        return
    rows = list(csv.reader(io.StringIO(comp_csv)))
    print(f"  Loaded {len(rows)-1} tipsters.")

    print("\n[4] Computing PPW scores...")
    updated = compute_scores(rows, nrl_results, afl_results)

    print("\n[5] Building friends subset...")
    friends_raw, friends_sha = get_github_file("friends-data.csv")
    week_label = ""
    if friends_raw:
        first_line = friends_raw.strip().split("\n")[0]
        if not first_line.startswith("Rank"):   # first line is week label, not header
            week_label = first_line

    friends_rows = [updated[0]] + [r for r in updated[1:] if len(r) > 3 and r[3] in FRIENDS]
    friends_content = (week_label + "\n" if week_label else "") + rows_to_csv(friends_rows)

    print("\n[6] Pushing to GitHub...")
    push_github_file("competition-data.csv", rows_to_csv(updated), comp_sha, "Auto-score update (PPW)")
    push_github_file("friends-data.csv",     friends_content,       friends_sha, "Auto-score update (PPW)")

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
