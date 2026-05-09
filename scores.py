#!/usr/bin/env python3
"""
scores.py - PPW-based NRL/AFL tipping scorer for GitHub Actions.

Scoring formula (confirmed from competition Working sheet):
  - Each NRL/AFL team has a fixed PPW (Points Per Win) set at season start.
  - Best teams = small PPW; worst teams = large PPW.
  - Win  -> tipster earns that team's PPW value for that round.
  - Loss / Bye -> tipster earns 0.

Week alignment:
  Competition Week N = NRL Round N + AFL Round (N-1)
  AFL Opening Round = round index 0 in Squiggle = competition Week 1 AFL component.

Rank / LW / +/- logic:
  - Rank: computed each run by sorting all tipsters by current total descending.
  - LW:   loaded from rankings-snapshot.json — the saved rankings from the end of
          the PREVIOUS competition round. Stable throughout a round; only changes
          when the NRL round number advances.
  - +/-:  LW minus Rank (positive = moved up, negative = dropped).
  - If no snapshot exists yet, LW and +/- are left as-is from the last VBA push.

Ladder colouring:
  - round-results.json is pushed each run, showing each NRL/AFL team's status
    in the current round: "won" / "lost" / "pending" (not yet played or bye).
  - The web app reads this file to colour ladder rows green / red / grey.
"""

import csv, io, json, os, re, base64
import requests
from bs4 import BeautifulSoup

# ── PPW VALUES (from competition Working sheet) ────────────────────────────────

AFL_PPW = {
    "Adelaide Crows": 9,        "Brisbane Lions": 5,         "Carlton Blues": 16,
    "Collingwood Magpies": 12,  "Essendon Bombers": 21,      "Fremantle Dockers": 11,
    "Geelong Cats": 7,          "Gold Coast Suns": 6,        "GWS Giants": 10,
    "Hawthorn Hawks": 8,        "Melbourne Demons": 20,      "North Melbourne Kangaroos": 22,
    "Port Adelaide Power": 18,  "Richmond Tigers": 24,       "St Kilda Saints": 14,
    "Sydney Swans": 8,          "West Coast Eagles": 24,     "Western Bulldogs": 10,
}

NRL_PPW = {
    "Brisbane Broncos": 5,      "Canberra Raiders": 10,      "Canterbury Bulldogs": 9,
    "Cronulla Sharks": 10,      "Gold Coast Titans": 19,     "Manly Sea Eagles": 14,
    "Melbourne Storm": 7,       "Newcastle Knights": 19,     "New Zealand Warriors": 13,
    "North Queensland Cowboys": 15, "Parramatta Eels": 12,   "Penrith Panthers": 6,
    "Redcliffe Dolphins": 11,   "South Sydney Rabbitohs": 11,
    "St George Illawarra Dragons": 19, "Sydney Roosters": 7, "Wests Tigers": 16,
}

# Wikipedia team name -> competition name
NRL_NAME_MAP = {
    "Cronulla-Sutherland Sharks":  "Cronulla Sharks",
    "Canterbury-Bankstown Bulldogs": "Canterbury Bulldogs",
    "Dolphins":                    "Redcliffe Dolphins",
    "St. George Illawarra Dragons": "St George Illawarra Dragons",
    "Manly Warringah Sea Eagles":  "Manly Sea Eagles",
    "Brisbane Broncos":            "Brisbane Broncos",
    "Canberra Raiders":            "Canberra Raiders",
    "Gold Coast Titans":           "Gold Coast Titans",
    "Melbourne Storm":             "Melbourne Storm",
    "Newcastle Knights":           "Newcastle Knights",
    "New Zealand Warriors":        "New Zealand Warriors",
    "North Queensland Cowboys":    "North Queensland Cowboys",
    "Parramatta Eels":             "Parramatta Eels",
    "Penrith Panthers":            "Penrith Panthers",
    "South Sydney Rabbitohs":      "South Sydney Rabbitohs",
    "Sydney Roosters":             "Sydney Roosters",
    "Wests Tigers":                "Wests Tigers",
}

# Squiggle API name -> competition name
AFL_NAME_MAP = {
    "Adelaide":              "Adelaide Crows",
    "Brisbane Lions":        "Brisbane Lions",
    "Carlton":               "Carlton Blues",
    "Collingwood":           "Collingwood Magpies",
    "Essendon":              "Essendon Bombers",
    "Fremantle":             "Fremantle Dockers",
    "Geelong":               "Geelong Cats",
    "Gold Coast":            "Gold Coast Suns",
    "Greater Western Sydney": "GWS Giants",
    "Hawthorn":              "Hawthorn Hawks",
    "Melbourne":             "Melbourne Demons",
    "North Melbourne":       "North Melbourne Kangaroos",
    "Port Adelaide":         "Port Adelaide Power",
    "Richmond":              "Richmond Tigers",
    "St Kilda":              "St Kilda Saints",
    "Sydney":                "Sydney Swans",
    "West Coast":            "West Coast Eagles",
    "Western Bulldogs":      "Western Bulldogs",
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
    Fetches completed NRL results from the 2026 season results Wikipedia page.
    Each wikitable = one competition round (table 1 = Round 1, etc).
    Returns: {round_num: {"won": set_of_winners, "lost": set_of_losers}}
    """
    url     = "https://en.wikipedia.org/wiki/2026_NRL_season_results"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FootyTipping/1.0)"}
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ERROR fetching NRL Wikipedia: {e}")
        return {}

    soup     = BeautifulSoup(resp.text, "html.parser")
    tables   = soup.find_all("table", class_="wikitable")
    score_re = re.compile(r"^(\d+)\s*[-\u2013\u2014]\s*(\d+)\*?$")
    results  = {}

    for round_num, table in enumerate(tables, start=1):
        winners = set()
        losers  = set()
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            # Score is always in the second data cell (index 1)
            raw = cells[1].get_text(strip=True).replace("\u00a0", "")
            m   = score_re.match(raw)
            if not m:
                continue
            home_score = int(m.group(1))
            away_score = int(m.group(2))
            home = normalize_nrl(cells[0].get_text(strip=True))
            away = normalize_nrl(cells[2].get_text(strip=True))
            if home_score > away_score:
                winner, loser = home, away
            elif away_score > home_score:
                winner, loser = away, home
            else:
                continue   # drawn (shouldn't happen in NRL)
            if winner in NRL_PPW:
                winners.add(winner)
            if loser in NRL_PPW:
                losers.add(loser)

        if winners or losers:
            results[round_num] = {"won": winners, "lost": losers}

    print(f"  NRL: rounds with results -> {sorted(results.keys())}")
    for r, d in sorted(results.items()):
        print(f"    Round {r}: {len(d['won'])} won, {len(d['lost'])} lost")
    return results


# ── AFL RESULTS FROM SQUIGGLE ─────────────────────────────────────────────────

def normalize_afl(name):
    return AFL_NAME_MAP.get(name.strip(), name.strip())


def get_afl_results():
    """
    Fetches all completed AFL games for 2026 from the Squiggle API.
    round=0 = AFL Opening Round = competition Week 1 AFL component.
    Returns: {round_num: {"won": set_of_winners, "lost": set_of_losers}}
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
            winner, loser = hteam, ateam
        elif ascore > hscore:
            winner, loser = ateam, hteam
        else:
            continue   # drawn

        if round_num not in results:
            results[round_num] = {"won": set(), "lost": set()}
        if winner in AFL_PPW:
            results[round_num]["won"].add(winner)
        if loser in AFL_PPW:
            results[round_num]["lost"].add(loser)

    print(f"  AFL: rounds with results -> {sorted(results.keys())}")
    for r, d in sorted(results.items()):
        label = "OR" if r == 0 else f"R{r}"
        print(f"    AFL {label}: {len(d['won'])} won, {len(d['lost'])} lost")
    return results


# ── SCORE HELPERS ─────────────────────────────────────────────────────────────

def compute_tipster_totals(rows, nrl_results, afl_results, nrl_up_to):
    """
    Computes the total PPW score for every tipster up to a specific NRL round.
    Used for building the LW snapshot. Does not modify rows.
    Returns: {tipster_name: total_score}
    """
    afl_up_to = nrl_up_to - 1
    totals    = {}

    for row in rows[1:]:
        if not row or len(row) < 5 or not row[3]:
            continue
        row     = list(row) + [""] * max(0, 28 - len(row))
        tipster = row[3]

        afl_total = sum(
            AFL_PPW[team] * sum(
                1 for r in range(0, afl_up_to + 1)
                if r in afl_results and team in afl_results[r]["won"]
            )
            for tc in AFL_TEAM_COLS
            for team in [(row[tc] or "").strip()]
            if team and team in AFL_PPW
        )
        nrl_total = sum(
            NRL_PPW[team] * sum(
                1 for r in range(1, nrl_up_to + 1)
                if r in nrl_results and team in nrl_results[r]["won"]
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
    """
    sorted_scores = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    return {name: i for i, (name, _) in enumerate(sorted_scores, start=1)}


# ── SNAPSHOT MANAGEMENT ────────────────────────────────────────────────────────

def get_rankings_snapshot():
    """
    Loads rankings-snapshot.json from GitHub.
    Stores the end-of-round rankings so they can be used as LW throughout
    the following round.
    Returns (snapshot_dict, sha_or_None).
    """
    content, sha = get_github_file("rankings-snapshot.json")
    if content is None:
        print("  No snapshot found — will create on first round transition.")
        return {"lw_round": 0, "rankings": {}}, None
    try:
        return json.loads(content), sha
    except Exception as e:
        print(f"  Warning: could not parse snapshot ({e}) — using empty.")
        return {"lw_round": 0, "rankings": {}}, sha


def save_rankings_snapshot(snapshot, sha):
    """Saves rankings-snapshot.json to GitHub (creates if new, updates if exists)."""
    content = json.dumps(snapshot, separators=(",", ":"))
    push_github_file("rankings-snapshot.json", content, sha,
                     f"Update rankings snapshot — LW round {snapshot['lw_round']}")


# ── ROUND RESULTS FOR LADDER COLOURING ───────────────────────────────────────

def build_round_results(nrl_results, afl_results):
    """
    Builds round-results.json content showing each team's status in the
    current round for ladder row colouring in the web app:
      "won"     -> green row
      "lost"    -> red row
      "pending" -> grey row (not yet played, or on a bye)
    """
    nrl_current = max(nrl_results.keys()) if nrl_results else 0
    # For AFL colouring we use the most recent AFL round available (not the
    # competition-week-aligned round), so tipsters can see live status.
    afl_current = max(afl_results.keys()) if afl_results else -1

    # NRL status
    nrl_status = {}
    if nrl_current > 0 and nrl_current in nrl_results:
        won  = nrl_results[nrl_current]["won"]
        lost = nrl_results[nrl_current]["lost"]
        for team in NRL_PPW:
            if   team in won:  nrl_status[team] = "won"
            elif team in lost: nrl_status[team] = "lost"
            else:              nrl_status[team] = "pending"

    # AFL status
    afl_status = {}
    if afl_current >= 0 and afl_current in afl_results:
        won  = afl_results[afl_current]["won"]
        lost = afl_results[afl_current]["lost"]
        for team in AFL_PPW:
            if   team in won:  afl_status[team] = "won"
            elif team in lost: afl_status[team] = "lost"
            else:              afl_status[team] = "pending"

    return {
        "nrl_round": nrl_current,
        "afl_round": afl_current,
        "nrl": nrl_status,
        "afl": afl_status,
    }


# ── FULL SCORE COMPUTATION ────────────────────────────────────────────────────

def compute_scores(rows, nrl_results, afl_results, lw_rankings):
    """
    Computes and writes all seven leaderboard columns for every tipster.

    Columns written:
      col 0: Rank        — position after sorting by current total
      col 1: LW          — rank from lw_rankings snapshot (previous round)
      col 2: +/-         — LW minus Rank (positive = moved up)
      col 5: TotalScore  — cumulative PPW score across all completed rounds
      col 6: PRT         — total minus current round score
      col 7: RdScore     — points earned in the current (latest) round only
      cols 9,11,13,15,17 — individual AFL team cumulative scores
      cols 19,21,23,25,27 — individual NRL team cumulative scores

    If lw_rankings is empty (no snapshot yet), LW and +/- are left untouched
    from whatever VBA wrote, preserving correct values from the last email.
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
            wins = sum(
                1 for r in range(0, afl_current + 1)
                if r in afl_results and team in afl_results[r]["won"]
            )
            cum       = wins * ppw
            row[sc]   = str(cum)
            afl_total += cum
            if afl_current in afl_results and team in afl_results[afl_current]["won"]:
                afl_rd += ppw

        # NRL: cumulative score for each of the 5 picked teams
        for tc, sc in zip(NRL_TEAM_COLS, NRL_SCORE_COLS):
            team = (row[tc] or "").strip()
            if not team or team not in NRL_PPW:
                continue
            ppw  = NRL_PPW[team]
            wins = sum(
                1 for r in range(1, nrl_current + 1)
                if r in nrl_results and team in nrl_results[r]["won"]
            )
            cum       = wins * ppw
            row[sc]   = str(cum)
            nrl_total += cum
            if nrl_current in nrl_results and team in nrl_results[nrl_current]["won"]:
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
    # If no snapshot exists yet, leave LW and +/- untouched (preserve VBA values).
    have_snapshot = bool(lw_rankings)
    for rank, row in enumerate(scored, start=1):
        tipster = row[3]
        row[0]  = str(rank)   # always update Rank
        if have_snapshot:
            lw     = lw_rankings.get(tipster)
            row[1] = str(lw) if lw else "-"
            # positive = moved up (lower rank number = better position)
            row[2] = str(lw - rank) if lw else "0"
        # else: leave row[1] (LW) and row[2] (+/-) exactly as from the CSV

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
    sha=None -> creates a new file.
    sha=value -> updates an existing file.
    """
    url     = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha   # required for updates; omit for new file creation

    resp = requests.put(url, json=payload, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type":  "application/json",
        "User-Agent":    "FootyTipping/1.0",
    })
    if resp.status_code in (200, 201):
        print(f"  Pushed {filename} OK")
    else:
        print(f"  ERROR pushing {filename}: {resp.status_code} {resp.text[:300]}")


def rows_to_csv(rows):
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("=== Footy Tipping Scorer (PPW — Full Auto) ===")

    # [1] Fetch game results
    print("\n[1] Fetching NRL results...")
    nrl_results = get_nrl_results()

    print("\n[2] Fetching AFL results...")
    afl_results = get_afl_results()

    if not nrl_results:
        print("\nNo NRL results found — aborting.")
        return

    current_nrl_round = max(nrl_results.keys())

    # [2] Load LW rankings snapshot
    print("\n[3] Loading rankings snapshot...")
    snapshot, snapshot_sha = get_rankings_snapshot()
    lw_round    = snapshot.get("lw_round", 0)
    lw_rankings = snapshot.get("rankings", {})
    print(f"  Snapshot: LW round {lw_round} ({len(lw_rankings)} tipsters)")

    # [3] Load competition data
    print("\n[4] Loading competition-data.csv from GitHub...")
    comp_csv, comp_sha = get_github_file("competition-data.csv")
    if not comp_csv:
        print("  Could not load competition-data.csv — aborting.")
        return
    rows = list(csv.reader(io.StringIO(comp_csv)))
    print(f"  Loaded {len(rows)-1} tipsters.")

    # [4] Check if the NRL round has advanced — update LW snapshot if so.
    # Condition: current_nrl_round > lw_round + 1
    #   - lw_round=6, current=7: 7>7? No  -> mid-round, LW stays stable (correct)
    #   - lw_round=6, current=8: 8>7? Yes -> round 8 started, save round 7 as LW
    if current_nrl_round > lw_round + 1:
        snapshot_round = current_nrl_round - 1
        print(f"\n[5] New round detected — saving LW snapshot for round {snapshot_round}...")
        prev_totals  = compute_tipster_totals(rows, nrl_results, afl_results, snapshot_round)
        lw_rankings  = build_rankings(prev_totals)
        new_snapshot = {"lw_round": snapshot_round, "rankings": lw_rankings}
        save_rankings_snapshot(new_snapshot, snapshot_sha)
        snapshot_sha = None   # SHA stale after push
        print(f"  Saved: {len(lw_rankings)} tipsters at LW round {snapshot_round}")
    else:
        print(f"\n[5] Still in round {current_nrl_round} — LW snapshot unchanged "
              f"(round {lw_round})")

    # [5] Compute full scores including Rank, LW, +/-
    print("\n[6] Computing PPW scores and rankings...")
    updated = compute_scores(rows, nrl_results, afl_results, lw_rankings)

    # [6] Build friends subset with smart week label
    print("\n[7] Building friends subset...")
    friends_raw, friends_sha = get_github_file("friends-data.csv")
    week_label = ""
    if friends_raw:
        first_line = friends_raw.strip().split("\n")[0]
        if not first_line.lower().startswith("rank"):
            week_label = first_line

    # Show transition indicator when scores.py is ahead of the last official email.
    # e.g. "2026 - Footy Tipping week 6 -> 7 (live)" when NRL is in round 7
    # but the last official email was for week 6.
    vba_week_match = re.search(r"week\s*(\d+)", week_label, re.IGNORECASE)
    vba_week       = int(vba_week_match.group(1)) if vba_week_match else 0
    if vba_week and current_nrl_round > vba_week:
        display_label = (f"2026 - Footy Tipping week {vba_week} "
                         f"\u2192 {current_nrl_round} \u21bb")
        print(f"  Week label: '{display_label}' (live update in progress)")
    else:
        display_label = week_label
        print(f"  Week label: '{display_label}' (matches official email)")

    friends_rows    = [updated[0]] + [
        r for r in updated[1:] if len(r) > 3 and r[3] in FRIENDS
    ]
    friends_content = (display_label + "\n" if display_label else "") + rows_to_csv(friends_rows)

    # [7] Build round-results.json for ladder colouring
    print("\n[8] Building round results for ladder colouring...")
    round_results    = build_round_results(nrl_results, afl_results)
    rr_content, rr_sha = get_github_file("round-results.json")
    rr_json          = json.dumps(round_results, separators=(",", ":"))
    nrl_s = round_results["nrl"]
    afl_s = round_results["afl"]
    print(f"  NRL R{round_results['nrl_round']}: "
          f"{sum(v=='won' for v in nrl_s.values())} won / "
          f"{sum(v=='lost' for v in nrl_s.values())} lost / "
          f"{sum(v=='pending' for v in nrl_s.values())} pending")
    print(f"  AFL R{round_results['afl_round']}: "
          f"{sum(v=='won' for v in afl_s.values())} won / "
          f"{sum(v=='lost' for v in afl_s.values())} lost / "
          f"{sum(v=='pending' for v in afl_s.values())} pending")

    # [8] Push everything to GitHub
    print("\n[9] Pushing to GitHub...")
    push_github_file("competition-data.csv", rows_to_csv(updated), comp_sha,
                     f"Auto-score update (PPW, NRL R{current_nrl_round})")
    push_github_file("friends-data.csv",     friends_content,      friends_sha,
                     f"Auto-score update (PPW, NRL R{current_nrl_round})")
    push_github_file("round-results.json",   rr_json,              rr_sha,
                     f"Update round results (NRL R{current_nrl_round})")

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
