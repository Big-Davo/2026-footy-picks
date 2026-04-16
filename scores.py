#!/usr/bin/env python3
"""
scores.py - PPW-based NRL/AFL tipping scorer for GitHub Actions.

Scoring formula (confirmed from competition Working sheet):
  - Each NRL/AFL team has a fixed PPW (Points Per Win) set at season start.
  - Best teams = small PPW; worst teams = large PPW.
  - Win  → tipster earns that team's PPW value for that round.
  - Loss / Bye → tipster earns 0.

Week alignment:
  Competition Week N = NRL Round N + AFL Round (N-1)
  AFL Opening Round = round index 0 in Squiggle = competition Week 1 AFL component.

Rank / LW / +/- logic:
  - Rank:  computed each run by sorting all tipsters by current total, descending.
  - LW:    loaded from rankings-snapshot.json — the saved rankings from the end of
           the PREVIOUS competition round. This only changes when the NRL round number
           advances, so LW stays stable throughout a round even as scores update.
  - +/-:   LW minus Rank (positive = moved up, negative = dropped).
  - The snapshot is updated automatically whenever a new NRL round is detected.
"""

import csv, io, json, os, re, base64
import requests
from bs4 import BeautifulSoup

# ── PPW VALUES ─────────────────────────────────────────────────────────────────

AFL_PPW = {
    "Adelaide Crows": 9,   "Brisbane Lions": 5,        "Carlton Blues": 16,
    "Collingwood Magpies": 12, "Essendon Bombers": 21, "Fremantle Dockers": 11,
    "Geelong Cats": 7,     "Gold Coast Suns": 6,        "GWS Giants": 10,
    "Hawthorn Hawks": 8,   "Melbourne Demons": 20,      "North Melbourne Kangaroos": 22,
    "Port Adelaide Power": 18, "Richmond Tigers": 24,  "St Kilda Saints": 14,
    "Sydney Swans": 8,     "West Coast Eagles": 24,     "Western Bulldogs": 10,
}

NRL_PPW = {
    "Brisbane Broncos": 5,  "Canberra Raiders": 10,    "Canterbury Bulldogs": 9,
    "Cronulla Sharks": 10,  "Gold Coast Titans": 19,   "Manly Sea Eagles": 14,
    "Melbourne Storm": 7,   "Newcastle Knights": 19,   "New Zealand Warriors": 13,
    "North Queensland Cowboys": 15, "Parramatta Eels": 12, "Penrith Panthers": 6,
    "Redcliffe Dolphins": 11, "South Sydney Rabbitohs": 11,
    "St George Illawarra Dragons": 19, "Sydney Roosters": 7, "Wests Tigers": 16,
}

# Wikipedia team name → competition name
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

# Squiggle API team name → competition name
AFL_NAME_MAP = {
    "Adelaide": "Adelaide Crows",           "Brisbane Lions": "Brisbane Lions",
    "Carlton": "Carlton Blues",             "Collingwood": "Collingwood Magpies",
    "Essendon": "Essendon Bombers",         "Fremantle": "Fremantle Dockers",
    "Geelong": "Geelong Cats",              "Gold Coast": "Gold Coast Suns",
    "Greater Western Sydney": "GWS Giants", "Hawthorn": "Hawthorn Hawks",
    "Melbourne": "Melbourne Demons",        "North Melbourne": "North Melbourne Kangaroos",
    "Port Adelaide": "Port Adelaide Power", "Richmond": "Richmond Tigers",
    "St Kilda": "St Kilda Saints",          "Sydney": "Sydney Swans",
    "West Coast": "West Coast Eagles",      "Western Bulldogs": "Western Bulldogs",
}

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = "Big-Davo/2026-footy-picks"

FRIENDS = [
    "Big Davo", "Cameron 01", "Cameron 02", "BigDavo 2",
    "JohnC", "JohnC2", "Ginger1", "Ginger2",
    "Wcord2", "Dylan C", "RobynC",
]

# Column layout in competition-data.csv (0-indexed)
AFL_TEAM_COLS  = [8,  10, 12, 14, 16]
AFL_SCORE_COLS = [9,  11, 13, 15, 17]
NRL_TEAM_COLS  = [18, 20, 22, 24, 26]
NRL_SCORE_COLS = [19, 21, 23, 25, 27]


# ── NRL RESULTS FROM WIKIPEDIA ────────────────────────────────────────────────

def normalize_nrl(name):
    return NRL_NAME_MAP.get(name.strip(), name.strip())


def get_nrl_results():
    """
    Fetches the 2026 NRL results page from Wikipedia.
    Each wikitable = one competition round (in order, table 1 = Round 1 etc).
    Only rows with a completed score (digits-digits) are counted.
    Returns: {round_num: set_of_winner_names}
    """
    url     = "https://en.wikipedia.org/wiki/2026_NRL_season_results"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FootyTipping/1.0)"}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ERROR fetching NRL Wikipedia: {e}")
        return {}

    soup      = BeautifulSoup(resp.text, "html.parser")
    tables    = soup.find_all("table", class_="wikitable")
    score_re  = re.compile(r"^(\d+)\s*[–—\-]\s*(\d+)\*?$")
    results   = {}

    for round_num, table in enumerate(tables, start=1):
        winners = set()
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            # Score always in second data cell (index 1); normalise dashes
            raw = cells[1].get_text(strip=True)
            raw = raw.replace("\u2013", "-").replace("\u2014", "-").replace("\u00a0", "")
            m   = score_re.match(raw)
            if not m:
                continue
            home_score, away_score = int(m.group(1)), int(m.group(2))
            home   = normalize_nrl(cells[0].get_text(strip=True))
            away   = normalize_nrl(cells[2].get_text(strip=True))
            winner = home if home_score > away_score else (away if away_score > home_score else None)
            if winner and winner in NRL_PPW:
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
    Fetches all completed AFL games for 2026 from the Squiggle API.
    round=0 = AFL Opening Round = competition Week 1 AFL component.
    round=N = AFL Round N       = competition Week N+1 AFL component.
    Returns: {round_num: set_of_winner_names}
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
        print(f"    AFL {'OR' if r == 0 else f'R{r}'}: {sorted(wins)}")
    return results


# ── SCORE HELPERS ─────────────────────────────────────────────────────────────

def compute_tipster_totals(rows, nrl_results, afl_results, nrl_up_to):
    """
    Computes the TOTAL score for every tipster up to a specific NRL round.
    Used for building the LW snapshot — does not modify rows.
    Returns: {tipster_name: total_score}
    """
    afl_up_to = nrl_up_to - 1   # AFL round aligned to this competition week
    totals    = {}

    for row in rows[1:]:
        if not row or len(row) < 5 or not row[3]:
            continue
        row     = list(row) + [""] * max(0, 28 - len(row))
        tipster = row[3]

        afl_total = sum(
            AFL_PPW[team] * sum(
                1 for r in range(0, afl_up_to + 1)
                if r in afl_results and team in afl_results[r]
            )
            for tc in AFL_TEAM_COLS
            for team in [(row[tc] or "").strip()]
            if team and team in AFL_PPW
        )
        nrl_total = sum(
            NRL_PPW[team] * sum(
                1 for r in range(1, nrl_up_to + 1)
                if r in nrl_results and team in nrl_results[r]
            )
            for tc in NRL_TEAM_COLS
            for team in [(row[tc] or "").strip()]
            if team and team in NRL_PPW
        )
        totals[tipster] = afl_total + nrl_total

    return totals


def build_rankings(totals):
    """
    Given {tipster: total}, returns {tipster: rank} sorted by score descending.
    Tipsters with equal scores receive the same rank.
    """
    sorted_scores = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    rankings      = {}
    for i, (name, score) in enumerate(sorted_scores, start=1):
        rankings[name] = i
    return rankings


# ── SNAPSHOT MANAGEMENT ────────────────────────────────────────────────────────

def get_rankings_snapshot():
    """
    Loads rankings-snapshot.json from GitHub.
    The snapshot stores the rankings at the END of the last completed round,
    so they can be used as LW (Last Week) throughout the following round.
    Returns (snapshot_dict, sha_or_None).
    """
    content, sha = get_github_file("rankings-snapshot.json")
    if content is None:
        # File doesn't exist yet — first ever run
        print("  No snapshot found — will create on first round transition.")
        return {"lw_round": 0, "rankings": {}}, None
    try:
        return json.loads(content), sha
    except Exception as e:
        print(f"  Warning: could not parse snapshot ({e}) — using empty.")
        return {"lw_round": 0, "rankings": {}}, sha


def save_rankings_snapshot(snapshot, sha):
    """
    Saves rankings-snapshot.json to GitHub.
    Works for both creating a new file (sha=None) and updating an existing one.
    """
    content = json.dumps(snapshot, separators=(",", ":"))
    push_github_file("rankings-snapshot.json", content, sha,
                     f"Update rankings snapshot — LW round {snapshot['lw_round']}")


# ── FULL SCORE COMPUTATION ────────────────────────────────────────────────────

def compute_scores(rows, nrl_results, afl_results, lw_rankings):
    """
    Computes and writes all seven leaderboard columns for every tipster:
      col 0: Rank        — position after sorting by current total
      col 1: LW          — rank from lw_rankings snapshot (previous round)
      col 2: +/-         — LW minus Rank (positive = moved up)
      col 5: TotalScore  — cumulative PPW score across all completed rounds
      col 6: PRT         — previous round total (total minus this round's Rd)
      col 7: RdScore     — points earned in the current (latest) round only
      cols 9,11,13,15,17 — individual AFL team cumulative scores
      cols 19,21,23,25,27 — individual NRL team cumulative scores
    """
    nrl_current = max(nrl_results.keys())
    afl_current = nrl_current - 1   # AFL round aligned to this competition week
    print(f"  Scoring: NRL up to Round {nrl_current}, AFL up to Round {afl_current} (0=OR)")

    header = rows[0]
    scored = []

    for row in rows[1:]:
        if not row or len(row) < 5 or not row[3]:
            continue
        row = list(row) + [""] * max(0, 28 - len(row))

        afl_total = nrl_total = afl_rd = nrl_rd = 0

        # AFL: cumulative score for each of the 5 picked teams
        for tc, sc in zip(AFL_TEAM_COLS, AFL_SCORE_COLS):
            team = (row[tc] or "").strip()
            if not team or team not in AFL_PPW:
                continue
            ppw  = AFL_PPW[team]
            wins = sum(1 for r in range(0, afl_current + 1)
                       if r in afl_results and team in afl_results[r])
            cum  = wins * ppw
            row[sc]    = str(cum)
            afl_total += cum
            # Does this team win in the CURRENT round? That contributes to Rd score
            if afl_current in afl_results and team in afl_results[afl_current]:
                afl_rd += ppw

        # NRL: cumulative score for each of the 5 picked teams
        for tc, sc in zip(NRL_TEAM_COLS, NRL_SCORE_COLS):
            team = (row[tc] or "").strip()
            if not team or team not in NRL_PPW:
                continue
            ppw  = NRL_PPW[team]
            wins = sum(1 for r in range(1, nrl_current + 1)
                       if r in nrl_results and team in nrl_results[r])
            cum  = wins * ppw
            row[sc]    = str(cum)
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

    # Sort by total descending to establish current rankings
    scored.sort(key=lambda r: int(r[5]) if str(r[5]).lstrip("-").isdigit() else 0, reverse=True)

    # Assign Rank, LW, and +/- for every tipster.
    # If lw_rankings is empty (no snapshot exists yet), we leave LW and +/-
    # completely untouched — preserving whatever VBA wrote from the last official
    # email. We still update Rank because the re-sort above may have changed
    # positions as scores.py's calculations differ slightly from the email snapshot.
    # Once a proper snapshot exists, all three columns are fully managed here.
    have_snapshot = bool(lw_rankings)
    for rank, row in enumerate(scored, start=1):
        tipster = row[3]
        row[0]  = str(rank)     # always update Rank (reflects current sort order)
        if have_snapshot:
            lw      = lw_rankings.get(tipster)
            row[1]  = str(lw) if lw else "-"
            # positive +/- means moved UP (lower rank number = better position)
            row[2]  = str(lw - rank) if lw else "0"
        # else: leave row[1] (LW) and row[2] (+/-) exactly as they came from the CSV

    return [header] + scored


# ── GITHUB HELPERS ────────────────────────────────────────────────────────────

def get_github_file(filename):
    """Fetches a file from GitHub. Returns (content_string, sha) or (None, None)."""
    url  = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
    resp = requests.get(url, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "User-Agent":    "FootyTipping/1.0",
    })
    if resp.status_code != 200:
        print(f"  GitHub GET {filename}: {resp.status_code}")
        return None, None
    data    = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return content, data["sha"]


def push_github_file(filename, content, sha, message):
    """
    Creates or updates a file on GitHub.
    If sha is None the file will be created; if sha is provided it will be updated.
    """
    url     = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    }
    # sha is required for updates; omit it entirely for new file creation
    if sha:
        payload["sha"] = sha

    resp = requests.put(url, json=payload, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type":  "application/json",
        "User-Agent":    "FootyTipping/1.0",
    })
    if resp.status_code in (200, 201):
        print(f"  Pushed {filename} ✓")
    else:
        print(f"  ERROR pushing {filename}: {resp.status_code} {resp.text[:300]}")


def rows_to_csv(rows):
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("=== Footy Tipping Scorer (PPW — Full Auto) ===")

    # Step 1: Fetch game results
    print("\n[1] Fetching NRL results...")
    nrl_results = get_nrl_results()

    print("\n[2] Fetching AFL results...")
    afl_results = get_afl_results()

    if not nrl_results:
        print("\nNo NRL results found — aborting.")
        return

    current_nrl_round = max(nrl_results.keys())

    # Step 2: Load the LW rankings snapshot
    print("\n[3] Loading rankings snapshot...")
    snapshot, snapshot_sha = get_rankings_snapshot()
    lw_round    = snapshot.get("lw_round", 0)
    lw_rankings = snapshot.get("rankings", {})
    print(f"  Snapshot is for LW round {lw_round} ({len(lw_rankings)} tipsters stored)")

    # Step 3: Load competition data
    print("\n[4] Loading competition-data.csv from GitHub...")
    comp_csv, comp_sha = get_github_file("competition-data.csv")
    if not comp_csv:
        print("  Could not load competition-data.csv — aborting.")
        return
    rows = list(csv.reader(io.StringIO(comp_csv)))
    print(f"  Loaded {len(rows)-1} tipsters.")

    # Step 4: Check if the NRL round has advanced since the last snapshot.
    # If current_nrl_round > lw_round + 1, we've moved into a new round and
    # need to capture the PREVIOUS round's final rankings as the new LW snapshot.
    # Example: lw_round=5, current=7 → capture round 6 final rankings as LW.
    if current_nrl_round > lw_round + 1:
        snapshot_round = current_nrl_round - 1
        print(f"\n[5] New round detected — saving LW snapshot for round {snapshot_round}...")
        prev_totals  = compute_tipster_totals(rows, nrl_results, afl_results, snapshot_round)
        lw_rankings  = build_rankings(prev_totals)
        new_snapshot = {"lw_round": snapshot_round, "rankings": lw_rankings}
        save_rankings_snapshot(new_snapshot, snapshot_sha)
        snapshot_sha = None   # SHA is now stale after the push — reset so next save creates fresh
        print(f"  Snapshot saved ({len(lw_rankings)} tipsters, LW round = {snapshot_round})")
    else:
        print(f"\n[5] Still in round {current_nrl_round} — LW snapshot unchanged (round {lw_round})")

    # Step 5: Compute full scores including Rank, LW, +/-
    print("\n[6] Computing PPW scores and rankings...")
    updated = compute_scores(rows, nrl_results, afl_results, lw_rankings)

    # Step 6: Build friends subset with smart week label
    print("\n[7] Building friends subset...")
    friends_raw, friends_sha = get_github_file("friends-data.csv")
    week_label = ""
    if friends_raw:
        first_line = friends_raw.strip().split("\n")[0]
        if not first_line.lower().startswith("rank"):   # first line is the week label
            week_label = first_line

    # Build a display label that shows when scores.py is running ahead of the
    # last official email. Examples:
    #   Official email data only:    "2026 - Footy Tipping week 6"
    #   scores.py ahead by 1 round:  "2026 - Footy Tipping week 6 \u2192 7 \u21bb"
    # When VBA processes the next email it resets the label cleanly to "week 7".
    vba_week_match = re.search(r'week\s*(\d+)', week_label, re.IGNORECASE)
    vba_week       = int(vba_week_match.group(1)) if vba_week_match else 0
    if vba_week and current_nrl_round > vba_week:
        # scores.py has data beyond the last official email -- show transition indicator
        display_label = f"2026 - Footy Tipping week {vba_week} \u2192 {current_nrl_round} \u21bb"
        print(f"  Week label: '{display_label}' (live update in progress)")
    else:
        # Still on same week as last official email -- keep label unchanged
        display_label = week_label
        print(f"  Week label: '{display_label}' (matches official email)")

    friends_rows    = [updated[0]] + [r for r in updated[1:] if len(r) > 3 and r[3] in FRIENDS]
    friends_content = (display_label + "\n" if display_label else "") + rows_to_csv(friends_rows)

    # Step 7: Push everything to GitHub
    print("\n[8] Pushing to GitHub...")
    push_github_file("competition-data.csv", rows_to_csv(updated), comp_sha,
                     f"Auto-score update (PPW, NRL R{current_nrl_round})")
    push_github_file("friends-data.csv",     friends_content,       friends_sha,
                     f"Auto-score update (PPW, NRL R{current_nrl_round})")

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
