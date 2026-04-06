"""
2026 Footy Tipping - Automated Score Calculator
DEBUG VERSION - prints per-round breakdown for Big Davo, does NOT push data
"""

import re
import requests
import csv
import io
import base64
import os
from datetime import datetime
from bs4 import BeautifulSoup

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO  = "Big-Davo/2026-footy-picks"
GITHUB_API   = "https://api.github.com"

NRL_WIKI_URL = "https://en.wikipedia.org/wiki/2026_NRL_season_results"
AFL_API_URL  = "https://api.squiggle.com.au/?q=games;year=2026"

FRIENDS = [
    "Big Davo", "Cameron 01", "Cameron 02", "BigDavo 2",
    "JohnC", "JohnC2", "Ginger1", "Ginger2",
    "Wcord2", "Dylan C", "RobynC"
]

NRL_MAP = {
    "Newcastle Knights":               "Newcastle Knights",
    "North Queensland Cowboys":        "North Queensland Cowboys",
    "Canterbury-Bankstown Bulldogs":   "Canterbury Bulldogs",
    "Canterbury\u2013Bankstown Bulldogs": "Canterbury Bulldogs",
    "St George Illawarra Dragons":     "St George Illawarra Dragons",
    "St. George Illawarra Dragons":    "St George Illawarra Dragons",
    "Melbourne Storm":                 "Melbourne Storm",
    "Parramatta Eels":                 "Parramatta Eels",
    "New Zealand Warriors":            "New Zealand Warriors",
    "Sydney Roosters":                 "Sydney Roosters",
    "Brisbane Broncos":                "Brisbane Broncos",
    "Penrith Panthers":                "Penrith Panthers",
    "Cronulla-Sutherland Sharks":      "Cronulla Sharks",
    "Cronulla\u2013Sutherland Sharks": "Cronulla Sharks",
    "Gold Coast Titans":               "Gold Coast Titans",
    "South Sydney Rabbitohs":          "South Sydney Rabbitohs",
    "Canberra Raiders":                "Canberra Raiders",
    "Manly-Warringah Sea Eagles":      "Manly Sea Eagles",
    "Manly\u2013Warringah Sea Eagles": "Manly Sea Eagles",
    "Manly Warringah Sea Eagles":      "Manly Sea Eagles",
    "Dolphins":                        "Redcliffe Dolphins",
    "Wests Tigers":                    "Wests Tigers",
}

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


def round_number_from_text(text):
    m = re.search(r"round\s+(\d+)", text.strip().lower())
    return int(m.group(1)) if m else None


def parse_nrl_table(table, round_num, results):
    count = 0
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 3:
            continue
        home_raw  = cells[0].get_text(strip=True)
        score_raw = cells[1].get_text(strip=True)
        away_raw  = cells[2].get_text(strip=True)
        score_match = re.search(r"(\d+)\s*[\u2013\u2014\-]\s*(\d+)", score_raw)
        if not score_match:
            continue
        home_score = int(score_match.group(1))
        away_score = int(score_match.group(2))
        if home_score == 0 and away_score == 0:
            continue
        home_team = NRL_MAP.get(home_raw, home_raw)
        away_team = NRL_MAP.get(away_raw, away_raw)
        margin    = abs(home_score - away_score)
        rnd_str   = str(round_num)
        if home_score > away_score:
            results[f"{home_team}|{rnd_str}"] = margin
            results[f"{away_team}|{rnd_str}"] = 0
        elif away_score > home_score:
            results[f"{away_team}|{rnd_str}"] = margin
            results[f"{home_team}|{rnd_str}"] = 0
        else:
            results[f"{home_team}|{rnd_str}"] = 0
            results[f"{away_team}|{rnd_str}"] = 0
        count += 1
    return count


def fetch_nrl_results():
    results = {}
    headers = {
        "User-Agent": "FootyTipping/1.0 (github.com/Big-Davo/2026-footy-picks; automated scoring)",
        "Accept": "text/html",
    }
    try:
        resp = requests.get(NRL_WIKI_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  ERROR fetching NRL: {e}")
        return results

    all_tables = soup.find_all("table", class_="wikitable")
    print(f"  Found {len(all_tables)} wikitables")
    rounds_processed = set()

    for table in all_tables:
        heading = table.find_previous(["h2", "h3"])
        if not heading:
            continue
        heading_text = heading.get_text().strip()
        round_num = round_number_from_text(heading_text)
        if round_num is None:
            continue
        count = parse_nrl_table(table, round_num, results)
        if count > 0 and round_num not in rounds_processed:
            print(f"  NRL Round {round_num}: {count} matches parsed")
            rounds_processed.add(round_num)

    return results


def fetch_afl_results():
    results = {}
    headers = {
        "User-Agent": "FootyTipping/1.0 (github.com/Big-Davo/2026-footy-picks)",
        "Accept": "application/json",
    }
    try:
        resp  = requests.get(AFL_API_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        games = resp.json().get("games", [])
    except Exception as e:
        print(f"  ERROR fetching AFL: {e}")
        return results

    # Print first 10 games to show round numbering
    print("  First 10 AFL games from squiggle (round, home, away, complete, hscore, ascore):")
    for g in games[:10]:
        print(f"    round={g.get('round')} {g.get('hteam')} vs {g.get('ateam')} complete={g.get('complete')} score={g.get('hscore')}-{g.get('ascore')}")

    rounds_seen = set()
    for game in games:
        try:
            complete = game.get("complete", 0)
            if complete != 100:
                continue
            round_num  = str(game.get("round", ""))
            home_raw   = game.get("hteam", "")
            away_raw   = game.get("ateam", "")
            home_score = game.get("hscore", None)
            away_score = game.get("ascore", None)
            if home_score is None or away_score is None:
                continue
            if round_num == "0":
                continue
            rounds_seen.add(round_num)
            home_team  = AFL_MAP.get(home_raw, home_raw)
            away_team  = AFL_MAP.get(away_raw, away_raw)
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

    print(f"  AFL complete rounds: {sorted(rounds_seen, key=lambda x: int(x) if x.isdigit() else 0)}")
    return results


def github_get(filename):
    url  = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{filename}"
    hdrs = {"Authorization": f"token {GITHUB_TOKEN}", "User-Agent": "FootyTipping"}
    resp = requests.get(url, headers=hdrs, timeout=30)
    resp.raise_for_status()
    data    = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    sha     = data["sha"]
    return content, sha


def debug_tipster(name, row, header, nrl_results, afl_results, all_rounds):
    """Print per-round breakdown for a specific tipster"""
    def col(n):
        try:
            return header.index(n)
        except ValueError:
            return None

    afl1_col = col("AFL Team 1")
    nrl1_col = col("NRL Team 1")
    if afl1_col is None or nrl1_col is None:
        return

    afl_picks = [row[afl1_col + t * 2].strip() if afl1_col + t * 2 < len(row) else "" for t in range(5)]
    nrl_picks = [row[nrl1_col + t * 2].strip() if nrl1_col + t * 2 < len(row) else "" for t in range(5)]

    print(f"\n  === {name} DEBUG ===")
    print(f"  NRL picks: {nrl_picks}")
    print(f"  AFL picks: {afl_picks}")

    grand_total = 0
    for rnd in sorted(all_rounds, key=lambda x: int(x) if x.isdigit() else 0):
        nrl_pts = []
        afl_pts = []
        for team in nrl_picks:
            if team:
                pts = nrl_results.get(f"{team}|{rnd}", None)
                nrl_pts.append(f"{team}={pts if pts is not None else '?'}")
        for team in afl_picks:
            if team:
                pts = afl_results.get(f"{team}|{rnd}", None)
                afl_pts.append(f"{team}={pts if pts is not None else '?'}")

        rnd_nrl = sum(nrl_results.get(f"{t}|{rnd}", 0) for t in nrl_picks if t and nrl_results.get(f"{t}|{rnd}") is not None)
        rnd_afl = sum(afl_results.get(f"{t}|{rnd}", 0) for t in afl_picks if t and afl_results.get(f"{t}|{rnd}") is not None)
        rnd_total = rnd_nrl + rnd_afl
        grand_total += rnd_total
        print(f"  Round {rnd}: NRL={rnd_nrl} {nrl_pts} | AFL={rnd_afl} {afl_pts} | RND={rnd_total} | CUMUL={grand_total}")


def main():
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print(f"=== Footy Tipping DEBUG — {now} ===")

    print("\nFetching NRL results from Wikipedia...")
    nrl_results = fetch_nrl_results()
    print(f"  {len(nrl_results)} NRL team/round results loaded")

    print("\nFetching AFL results from api.squiggle.com.au...")
    afl_results = fetch_afl_results()
    print(f"  {len(afl_results)} AFL team/round results loaded")

    # Build all_rounds
    all_rounds = set()
    for key in list(nrl_results.keys()) + list(afl_results.keys()):
        rnd = key.split("|")[1]
        if rnd.isdigit():
            all_rounds.add(rnd)
    print(f"\n  All rounds in play: {sorted(all_rounds, key=int)}")

    print("\nReading competition-data.csv from GitHub...")
    competition_csv, _ = github_get("competition-data.csv")

    reader = csv.reader(io.StringIO(competition_csv))
    rows   = list(reader)
    header = rows[0]

    tipster_col = header.index("Tipster") if "Tipster" in header else None
    if tipster_col is None:
        print("  ERROR: No Tipster column found")
        return

    for row in rows[1:]:
        if row and len(row) > tipster_col and row[tipster_col].strip() == "Big Davo":
            debug_tipster("Big Davo", row, header, nrl_results, afl_results, all_rounds)
            break

    print("\n  DEBUG MODE: no data pushed")


if __name__ == "__main__":
    main()
