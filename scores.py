"""
2026 Footy Tipping - Automated Score Calculator
================================================
Runs hourly via GitHub Actions.
- NRL results: parsed from Wikipedia 2026 NRL season results page
- AFL results: fetched from api.squiggle.com.au (free public API)
Calculates tipping scores from picks in competition-data.csv
and pushes updated competition-data.csv and friends-data.csv to GitHub.
"""

import re
import requests
import csv
import io
import base64
import os
import calendar
from datetime import datetime
from bs4 import BeautifulSoup

# ============================================================
# CONFIGURATION
# ============================================================

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

# ============================================================
# NRL TEAM NAME MAPPING
# ============================================================
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

# ============================================================
# AFL TEAM NAME MAPPING
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


def is_round_heading(text):
    return bool(re.match(r"round\s+\d+", text.strip().lower()))


def round_number_from_heading(text):
    m = re.search(r"round\s+(\d+)", text.strip().lower())
    return int(m.group(1)) if m else None


# ============================================================
# FETCH NRL RESULTS FROM WIKIPEDIA
# DEBUG VERSION — dumps HTML structure around Round 1
# ============================================================
def fetch_nrl_results():
    results = {}
    headers = {
        "User-Agent": "FootyTipping/1.0 (github.com/Big-Davo/2026-footy-picks; automated scoring)",
        "Accept": "text/html",
    }

    try:
        resp = requests.get(NRL_WIKI_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        print(f"  ERROR fetching NRL Wikipedia page: {e}")
        return results

    # Find the position of "Round_1" anchor in the raw HTML
    # and print 2000 chars of HTML after it so we can see the structure
    pos = html.find('id="Round_1"')
    if pos == -1:
        pos = html.find("Round 1")
    if pos >= 0:
        sample = html[pos:pos+2000]
        # Strip most tags for readability but keep structure tags
        sample = re.sub(r'<(?!/?(?:h[23]|table|tr|td|th|div|span|a)\b)[^>]+>', '', sample)
        print(f"  HTML AROUND ROUND 1:\n{sample[:1500]}")
    else:
        print("  Could not find Round 1 in HTML")

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
        resp  = requests.get(AFL_API_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        games = resp.json().get("games", [])
    except Exception as e:
        print(f"  ERROR fetching AFL results: {e}")
        return results

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
# MAIN — debug only, no scoring this run
# ============================================================
def main():
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print(f"=== Footy Tipping Score Update — {now} ===")

    print("\nFetching NRL results from Wikipedia (DEBUG MODE)...")
    nrl_results = fetch_nrl_results()
    print(f"  {len(nrl_results)} NRL team/round results loaded")

    print("Fetching AFL results from api.squiggle.com.au...")
    afl_results = fetch_afl_results()
    print(f"  {len(afl_results)} AFL team/round results loaded")

    # SAFETY GUARD — always abort this debug run
    print("\n  DEBUG MODE: not pushing any data this run")
    print("  Existing competition-data.csv on GitHub is unchanged")


if __name__ == "__main__":
    main()
