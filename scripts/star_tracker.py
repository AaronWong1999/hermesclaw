#!/usr/bin/env python3
"""star_tracker.py — poll GitHub for HermesClaw star count + traffic.

Usage:
    python3 scripts/star_tracker.py                  # one-shot snapshot
    python3 scripts/star_tracker.py --watch          # poll every 15 min forever
    python3 scripts/star_tracker.py --watch --interval 600  # custom interval seconds

Writes a CSV row to scripts/star_history.csv on every poll. Prints a milestone
banner the first time the repo crosses 50, 100, 250, 500, 1000 stars in a session.

Requires: gh CLI (https://cli.github.com/) authenticated with `gh auth login`.
No API keys needed — uses your existing gh credentials.
"""

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = "AaronWong1999/hermesclaw"
HISTORY = Path(__file__).parent / "star_history.csv"
MILESTONES = [50, 100, 250, 500, 1000, 2500, 5000]


def gh_api(path):
    r = subprocess.run(
        ["gh", "api", path],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        print(f"[warn] gh api {path} failed: {r.stderr.strip()}", file=sys.stderr)
        return None
    return json.loads(r.stdout)


def snapshot():
    repo = gh_api(f"repos/{REPO}")
    if not repo:
        return None
    refs = gh_api(f"repos/{REPO}/traffic/popular/referrers") or []
    views = gh_api(f"repos/{REPO}/traffic/views") or {}
    paths = gh_api(f"repos/{REPO}/traffic/popular/paths") or []

    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stars": repo.get("stargazers_count", 0),
        "forks": repo.get("forks_count", 0),
        "watchers": repo.get("subscribers_count", 0),
        "open_issues": repo.get("open_issues_count", 0),
        "views_14d": views.get("count", 0),
        "uniques_14d": views.get("uniques", 0),
        "top_referrer": refs[0]["referrer"] if refs else "",
        "top_referrer_uniques": refs[0]["uniques"] if refs else 0,
        "top_path": paths[0]["path"] if paths else "",
        "top_path_uniques": paths[0]["uniques"] if paths else 0,
    }


def append_csv(row):
    is_new = not HISTORY.exists()
    with HISTORY.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if is_new:
            w.writeheader()
        w.writerow(row)


def render(row, prev_stars):
    delta = row["stars"] - prev_stars if prev_stars is not None else 0
    arrow = f" (+{delta})" if delta > 0 else ""
    print(
        f"[{row['ts']}] ⭐ {row['stars']}{arrow}  "
        f"🍴 {row['forks']}  👀 {row['watchers']}  "
        f"views(14d)={row['views_14d']} uniq={row['uniques_14d']}"
    )
    if row["top_referrer"]:
        print(
            f"           top referrer: {row['top_referrer']} "
            f"({row['top_referrer_uniques']} uniq)"
        )

    if prev_stars is not None and delta > 0:
        for m in MILESTONES:
            if prev_stars < m <= row["stars"]:
                print()
                print("=" * 60)
                print(f"  🎉  MILESTONE: {m}+ STARS REACHED  🎉")
                print(f"  Top referrer right now: {row['top_referrer'] or 'unknown'}")
                print("=" * 60)
                print()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--watch", action="store_true", help="poll forever")
    p.add_argument("--interval", type=int, default=900,
                   help="seconds between polls in watch mode (default 900 = 15m)")
    args = p.parse_args()

    prev = None
    while True:
        row = snapshot()
        if row:
            append_csv(row)
            render(row, prev)
            prev = row["stars"]
        if not args.watch:
            return
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[stopped]")
            return


if __name__ == "__main__":
    main()
