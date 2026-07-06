"""Backtesting: grade stored prop snapshots against actual MLB results.

For a given game date we pull every player's real box-score line from the MLB
Stats API (one hitting + one pitching call), then mark each snapshotted prop a
hit or miss for the Over. This is what turns the projection into something
falsifiable — "our Goblins hit 81% of the time, our model said 84%."

It only produces output once snapshots exist for completed games (snapshots are
written every time the app fetches lines). Until then it returns empty.
"""

import requests

from config import canonical_stat
from model.mlb_stats import _fetch_group, _hit_totals, _pit_totals, _norm_name
import storage


def fetch_results(date_iso, session=None):
    """{norm_name: {canonical_stat: actual_value}} for one game date."""
    session = session or requests.Session()
    out = {}
    for group, fn in (("hitting", _hit_totals), ("pitching", _pit_totals)):
        for sp in _fetch_group(group, session, date_iso, date_iso):
            name = _norm_name(sp["player"]["fullName"])
            out.setdefault(name, {}).update(fn(sp["stat"]))
    return out


def grade_date(date_iso, session=None):
    """Grade all snapshotted MLB lines for `date_iso`.

    Returns {"rows": [...graded...], "summary": {by flavor + calibration}}.
    A row is graded only if we found the player's actual stat that day.
    """
    lines = [r for r in storage.snapshots_by_game_date(date_iso) if r["sport"] == "MLB"]
    if not lines:
        return {"rows": [], "summary": {}, "note": "no MLB snapshots for this date"}

    results = fetch_results(date_iso, session)
    rows = []
    for r in lines:
        actual = results.get(_norm_name(r["player"]), {}).get(canonical_stat(r["stat"]))
        if actual is None:
            continue
        over_hit = actual > r["line"]
        rows.append({
            **r,
            "actual": actual,
            "over_hit": bool(over_hit),
            "predicted": r["hit_prob"],
        })
    return {"rows": rows, "summary": _summarize(rows), "note": None}


def _summarize(rows):
    """Hit rate vs model prediction, overall and by flavor."""
    def block(subset):
        n = len(subset)
        if not n:
            return None
        hits = sum(1 for x in subset if x["over_hit"])
        preds = [x["predicted"] for x in subset if x["predicted"] is not None]
        return {
            "n": n,
            "actual_hit_rate": round(hits / n, 4),
            "model_avg_prob": round(sum(preds) / len(preds), 4) if preds else None,
        }

    out = {"overall": block(rows)}
    for fl in ("goblin", "demon", "standard"):
        b = block([x for x in rows if x["flavor"] == fl])
        if b:
            out[fl] = b
    return out
