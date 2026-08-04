"""Registry refresh CLI.

    python -m resolver full          [--dates 2026-07-25 2026-07-26]
    python -m resolver incremental   [--dates 2026-07-25] [--teams 119 147]

Full = all teams + active rosters (+ schedule for dates). Incremental = schedule
for dates (+ re-pull the given team rosters). Prints a JSON report.
"""
import argparse
import json

from .registry import get_registry
from . import backfill


def main():
    ap = argparse.ArgumentParser(prog="python -m resolver")
    sub = ap.add_subparsers(dest="cmd")
    f = sub.add_parser("full")
    f.add_argument("--dates", nargs="*", default=[])
    i = sub.add_parser("incremental")
    i.add_argument("--dates", nargs="*", default=[])
    i.add_argument("--teams", nargs="*", type=int, default=[])
    a = ap.parse_args()

    reg = get_registry()
    if a.cmd == "full":
        rep = backfill.full_refresh(reg, dates=a.dates)
    elif a.cmd == "incremental":
        rep = backfill.incremental_refresh(reg, dates=a.dates, team_mlb_ids=a.teams)
    else:
        ap.print_help(); return
    print(json.dumps(rep, indent=2))


main()
