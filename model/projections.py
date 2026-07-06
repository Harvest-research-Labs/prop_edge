"""Projection / hit-probability model.

The core question the user wants answered: for a PrizePicks Goblin or Demon
line, how likely is the OVER to hit?

Approach
--------
1. Estimate a *mean* expected stat value for each player+stat ("group").
   Priority of sources:
     a. PrizePicks STANDARD line for that group  -> PrizePicks' own neutral
        projection (their standard line is set near a ~50/50 outcome).
     b. Underdog's de-vigged odds, inverted through the stat's distribution
        -> the market's implied mean.
     c. Fall back to the line itself (uninformative, ~50%).
2. Given the mean and the stat's distribution shape (Poisson for low-count
   counting stats, Normal for high-volume continuous), compute P(value > line)
   for any flavor's line — Goblin (lower line, easy over) or Demon (higher
   line, hard over).

This is a transparent baseline. It improves the moment a real per-player
projection source is plugged into `group_means` (own model / paid API).
"""

import math

from config import stat_shape
from sources.base import Prop, devig_two_way

try:
    from scipy.stats import poisson, norm
    _HAVE_SCIPY = True
except Exception:  # noqa: BLE001
    _HAVE_SCIPY = False


# --- distribution helpers ----------------------------------------------

def _norm_sf(x, mu, sigma):
    if sigma <= 0:
        return 1.0 if mu > x else 0.0
    if _HAVE_SCIPY:
        return float(norm.sf(x, loc=mu, scale=sigma))
    return 0.5 * math.erfc((x - mu) / (sigma * math.sqrt(2)))


def _poisson_sf(k, mu):
    """P(X >= k) for X ~ Poisson(mu)."""
    if mu <= 0:
        return 0.0
    if _HAVE_SCIPY:
        return float(poisson.sf(k - 1, mu))
    # P(X >= k) = 1 - sum_{i<k} e^-mu mu^i / i!
    cdf, term = 0.0, math.exp(-mu)
    for i in range(0, max(0, k)):
        if i > 0:
            term *= mu / i
        cdf += term
    return max(0.0, 1.0 - cdf)


def over_prob(line, mean, stat):
    """Probability the OVER (higher) hits, given an expected `mean`."""
    if mean is None or mean <= 0:
        return None
    shape = stat_shape(stat)
    if shape["kind"] == "discrete":
        # over a half-line L  ->  X >= ceil(L); over an integer L -> X >= L+1
        k = math.floor(line) + 1
        return _poisson_sf(k, mean)
    sigma = max(shape.get("cv", 0.35) * mean, 1e-6)
    return _norm_sf(line, mean, sigma)


def _invert_mean(line, p_over_target, stat, lo=1e-3, hi=None):
    """Find the mean whose over_prob at `line` equals the target (bisection)."""
    if p_over_target is None:
        return None
    hi = hi if hi is not None else max(line * 4, line + 20, 1.0)
    for _ in range(40):
        mid = (lo + hi) / 2
        p = over_prob(line, mid, stat) or 0.0
        if p < p_over_target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# --- mean estimation across all props ----------------------------------

def group_means(props, projectors=None):
    """Return {group_key: {"mean": float, "source": str}}.

    Source priority (best estimate first):
      a) ud_devig   — Underdog's two-way odds, de-vigged and inverted through
                      the stat distribution to a market-implied mean. Most
                      reliable because it reflects a priced market.
      b) pp_standard— PrizePicks' standard line (their neutral projection),
                      used when Underdog doesn't cover the player+stat.
      c) own_model  — our own per-player projection (e.g. MLB season rates),
                      passed in as `projectors[sport](player, canonical_stat)`.
                      Matchup-blind, so it only fills gaps the market leaves.

    If none exist we record NO mean. We deliberately do not fall back to a
    goblin/demon line — that would invent a mean from a deliberately skewed
    number and produce nonsense probabilities.
    """
    from config import canonical_stat
    projectors = projectors or {}
    # bucket props by player+stat
    buckets = {}
    for p in props:
        buckets.setdefault(p.group_key(), []).append(p)

    means = {}
    for key, items in buckets.items():
        # a) Underdog de-vigged odds inverted to a mean (prefer a standard/
        #    balanced line over an alternate one for the cleanest read)
        ud_items = [p for p in items if p.book == "Underdog" and p.over_odds is not None]
        ud_items.sort(key=lambda p: 0 if p.flavor == "standard" else 1)
        ud = ud_items[0] if ud_items else None
        if ud:
            p_over, _ = devig_two_way(ud.over_odds, ud.under_odds)
            m = _invert_mean(ud.line, p_over, ud.stat)
            if m:
                means[key] = {"mean": m, "source": "ud_devig"}
                continue
        # b) PrizePicks standard line
        pp_std = next(
            (p for p in items if p.book == "PrizePicks" and p.flavor == "standard"),
            None,
        )
        if pp_std and pp_std.line > 0:
            means[key] = {"mean": pp_std.line, "source": "pp_standard"}
            continue
        # c) our own projection model for this sport, if available
        base = items[0]
        proj = projectors.get(base.sport)
        if proj:
            m = proj(base.player, canonical_stat(base.stat), base.team, base.opponent)
            if m and m > 0:
                means[key] = {"mean": m, "source": "own_model"}
                continue
        # d) no trustworthy anchor — leave unpriced
        means[key] = {"mean": None, "source": "none"}
    return means


def annotate(props, projectors=None):
    """Attach model fields to each prop; returns a list of dict rows.

    Adds: mean, mean_source, hit_prob (over), under_prob, edge.
      - hit_prob: model probability the higher/over side hits.
      - edge: for Underdog (has odds) = hit_prob - de-vigged market over prob,
              i.e. how much our model disagrees with the market.
              for PrizePicks = hit_prob - 0.5 (lean vs a coin-flip standard).
    """
    from config import canonical_stat
    projectors = projectors or {}
    means = group_means(props, projectors)
    rows = []
    for p in props:
        m = means.get(p.group_key(), {})
        mean = m.get("mean")
        hit = over_prob(p.line, mean, p.stat)
        row = p.to_row()
        row["mean"] = round(mean, 2) if mean else None
        row["mean_source"] = m.get("source")
        # our OWN model's independent read (for +EV vs the market), if available
        proj = projectors.get(p.sport)
        own_mean = proj(p.player, canonical_stat(p.stat), p.team, p.opponent) if proj else None
        own = over_prob(p.line, own_mean, p.stat) if (own_mean and own_mean > 0) else None
        row["own_prob"] = round(own, 4) if own is not None else None
        row["own_mean"] = round(own_mean, 2) if own_mean else None
        row["hit_prob"] = round(hit, 4) if hit is not None else None
        row["under_prob"] = round(1 - hit, 4) if hit is not None else None
        if p.over_odds is not None:
            p_over, _ = devig_two_way(p.over_odds, p.under_odds)
            row["market_over"] = round(p_over, 4) if p_over is not None else None
            row["edge"] = round(hit - p_over, 4) if (hit is not None and p_over is not None) else None
        else:
            row["market_over"] = None
            row["edge"] = round(hit - 0.5, 4) if hit is not None else None
        rows.append(row)
    return rows
