"""Auto-build recommended slips from the board.

Three modes, each returns up to n legs (one per player so the slip is realistic):

  safest      — highest individual hit probability (Goblins shine here). Takes
                whichever side (More/Less) the model favors.
  value       — biggest disagreement between our model and the market price
                (needs Underdog odds to compare against). The "edge" plays.
  insane      — swing-for-the-fences: Demons (the juiced lines) with the best
                shot among them. Low combined probability, big payout.
"""

import math

from model.evaluate import poisson_binomial, _nm, ev_pct, kelly_fraction
from sources.base import devig_two_way


def _num(v):
    """Coerce to float, treating None/NaN/non-numeric as missing (None)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def allowed_sides(r):
    """Which sides you can actually bet on this prop.

    PrizePicks Goblins and Demons are More-only — you take the over on the
    adjusted line. Standard PrizePicks and Underdog lines allow both."""
    if r.get("book") == "PrizePicks" and r.get("flavor") in ("goblin", "demon"):
        return ("more",)
    return ("more", "less")


def _conf_side(r):
    """(probability, side) for the highest-confidence *placeable* side."""
    p = r["hit_prob"]
    sides = allowed_sides(r)
    best = max(sides, key=lambda s: p if s == "more" else 1 - p)
    return (p if best == "more" else 1 - p, best)


def best_plays(rows, mode, n=6):
    """Return a list of leg dicts for the given mode."""
    cand = [r for r in rows if r.get("hit_prob") is not None]
    ranked = []  # (sort_score, side, leg_prob, row)

    if mode == "safest":
        for r in cand:
            conf, side = _conf_side(r)
            ranked.append((conf, side, conf, r))

    elif mode == "value":
        for r in cand:
            edge, market = r.get("edge"), r.get("market_over")
            if edge is None or market is None:   # need a real market price
                continue
            side = "more" if edge >= 0 else "less"
            if side not in allowed_sides(r):     # don't suggest an unplaceable side
                continue
            prob = r["hit_prob"] if side == "more" else 1 - r["hit_prob"]
            ranked.append((abs(edge), side, prob, r))

    elif mode == "insane":
        demons = [r for r in cand if r.get("flavor") == "demon"]
        pool = demons if len(demons) >= n else cand
        for r in pool:
            p = r["hit_prob"]
            # among long shots, rank the most makeable first so the slip isn't hopeless
            ranked.append((p, "more", p, r))

    ranked.sort(key=lambda x: -x[0])

    seen, out = set(), []
    for _, side, prob, r in ranked:
        key = _nm(r.get("player"))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "player": r["player"], "stat": r["stat"], "line": r["line"],
            "flavor": r.get("flavor"), "book": r["book"], "side": side, "prob": prob,
        })
        if len(out) >= n:
            break
    return out


def quick_six(rows, n=6, mix=None):
    """A fast, high-percentage slip that intentionally mixes all three PrizePicks
    flavors — Goblins 🟢, Demons 😈, and Standard.

    Takes each prop's highest-confidence *placeable* side, guarantees the target
    flavor mix when the board has it, then fills any remaining slots with the best
    remaining legs overall. One leg per player; final slip sorted by hit %.
    """
    mix = mix or {"goblin": 3, "standard": 2, "demon": 1}
    cand = [r for r in rows if r.get("hit_prob") is not None]

    buckets = {"goblin": [], "demon": [], "standard": []}
    for r in cand:
        fl = r.get("flavor")
        b = fl if fl in ("goblin", "demon") else "standard"   # everything else = regular
        conf, side = _conf_side(r)
        buckets[b].append((conf, side, r))
    for b in buckets.values():
        b.sort(key=lambda x: -x[0])

    seen, out = set(), []

    def _add(conf, side, r):
        key = _nm(r.get("player"))
        if key in seen:
            return False
        seen.add(key)
        out.append({
            "player": r["player"], "stat": r["stat"], "line": r["line"],
            "flavor": r.get("flavor"), "book": r["book"], "side": side, "prob": conf,
        })
        return True

    # first pass: satisfy each flavor's target with its highest-% legs
    for flavor, target in mix.items():
        added = 0
        for conf, side, r in buckets.get(flavor, []):
            if added >= target or len(out) >= n:
                break
            if _add(conf, side, r):
                added += 1

    # fill any shortfall with the best remaining legs across every flavor
    if len(out) < n:
        pool = sorted((leg for b in buckets.values() for leg in b), key=lambda x: -x[0])
        for conf, side, r in pool:
            if len(out) >= n:
                break
            _add(conf, side, r)

    out.sort(key=lambda l: -(l["prob"] or 0))
    return out[:n]


def _game_key(r):
    t, o = (r.get("team") or "").upper(), (r.get("opponent") or "").upper()
    pair = sorted(x for x in (t, o) if x)
    return " / ".join(pair) if pair else "?"


def ev_plays(rows, min_ev=0.0, n=15, max_edge=None):
    """+EV plays: our independent model probability vs the book's real odds.

    Only Underdog rows (they carry two-way American odds). Compares our own_prob
    against the de-vigged market for the displayed edge, computes EV and Kelly
    from the actual offered price, and tags same-game legs so you don't parlay
    correlated picks as if independent.

    `max_edge` (e.g. 0.20) hides implausibly large edges — a matchup-blind model
    showing a 30%+ disagreement with a sharp price is almost always model error,
    not a real opportunity.
    """
    found = []
    for r in rows:
        if r.get("book") != "Underdog":
            continue
        oo, uo, op = _num(r.get("over_odds")), _num(r.get("under_odds")), _num(r.get("own_prob"))
        if oo is None or uo is None or op is None:
            continue
        mkt_over, mkt_under = devig_two_way(int(oo), int(uo))
        for side, p, am, mkt in (("Over", op, oo, mkt_over), ("Under", 1 - op, uo, mkt_under)):
            ev = ev_pct(p, am)
            if ev is None or ev <= min_ev or not (0.05 < p < 0.97):
                continue
            edge = (p - mkt) if mkt is not None else None
            if max_edge is not None and edge is not None and edge > max_edge:
                continue  # too big to believe from a matchup-blind model
            found.append({
                "player": r["player"], "stat": r["stat"], "line": r["line"], "side": side,
                "our_p": p, "mkt_p": mkt, "edge": (p - mkt) if mkt is not None else None,
                "odds": am, "ev": ev, "kelly": kelly_fraction(p, am),
                "game": _game_key(r), "sport": r["sport"],
            })

    best = {}
    for x in found:
        k = (x["player"], x["stat"], x["side"])
        if k not in best or x["ev"] > best[k]["ev"]:
            best[k] = x
    plays = sorted(best.values(), key=lambda x: -x["ev"])[:n]

    # flag games that appear more than once (correlated legs)
    from collections import Counter
    gc = Counter(p["game"] for p in plays)
    for p in plays:
        p["correlated"] = gc[p["game"]] > 1
    return plays


def combined_prob(legs):
    """Probability all legs hit (independent assumption)."""
    ps = [l["prob"] for l in legs if l.get("prob") is not None]
    if not ps:
        return 0.0
    return poisson_binomial(ps)[len(ps)]
