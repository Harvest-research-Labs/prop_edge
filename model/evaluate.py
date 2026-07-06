"""Pick / slip evaluator.

Given a set of legs (player + stat + line + More/Less), compute each leg's hit
probability from our projection model, then the probability the whole entry
hits, the full distribution of how many legs hit, and expected value under
PrizePicks-style payout structures.

Independence assumption: legs are treated as independent. Real slips often
correlate (same game, same player), so combined probabilities are an
approximation — flagged in the UI.
"""

from config import canonical_stat
from model.projections import over_prob

MORE = {"more", "over", "higher", "o"}


def _nm(s):
    return (s or "").strip().lower()


def side_label(side, book=None):
    """Book-appropriate label for a pick direction.
    PrizePicks -> More/Less, Underdog -> Over/Under, otherwise generic."""
    over = _nm(side) in MORE
    if book == "PrizePicks":
        return "More" if over else "Less"
    if book == "Underdog":
        return "Over" if over else "Under"
    return "More/Over" if over else "Less/Under"


def parse_side(label):
    """Map any of More/Over/Higher/Less/Under/Lower -> 'more' | 'less'."""
    s = _nm(label)
    return "more" if any(w in s for w in ("more", "over", "higher", "more/over")) else "less"


def build_mean_lookup(rows):
    """{(player, canonical_stat): (mean, stat_label)} from annotated rows."""
    lut = {}
    for r in rows:
        m = r.get("mean")
        if m is None:
            continue
        lut[(_nm(r.get("player")), canonical_stat(r.get("stat")))] = (m, r.get("stat"))
    return lut


def leg_probability(player, stat, line, side, lut):
    """Probability this leg hits, or None if the player+stat isn't priced."""
    entry = lut.get((_nm(player), canonical_stat(stat)))
    if not entry:
        return None
    mean, stat_label = entry
    try:
        line = float(line)
    except (TypeError, ValueError):
        return None
    p_over = over_prob(line, mean, stat_label)
    if p_over is None:
        return None
    return p_over if _nm(side) in MORE else 1 - p_over


def decimal_odds(american):
    if american is None:
        return None
    return 1 + american / 100 if american > 0 else 1 + 100 / (-american)


def ev_pct(p, american):
    """Expected value per $1 stake at American `american` with win prob `p`."""
    d = decimal_odds(american)
    return None if not d else p * d - 1


def kelly_fraction(p, american):
    """Full-Kelly bankroll fraction (0 if no edge)."""
    d = decimal_odds(american)
    if not d or d <= 1:
        return 0.0
    return max(0.0, (p * d - 1) / (d - 1))


def poisson_binomial(ps):
    """Distribution of the number of successes for independent Bernoulli ps.
    Returns dp where dp[k] = P(exactly k of n hit)."""
    dp = [1.0]
    for p in ps:
        nxt = [0.0] * (len(dp) + 1)
        for k, val in enumerate(dp):
            nxt[k] += val * (1 - p)
            nxt[k + 1] += val * p
        dp = nxt
    return dp


def evaluate(legs):
    """legs: list with a 'prob' key (None for unpriced). Returns slip metrics."""
    ps = [l["prob"] for l in legs if l.get("prob") is not None]
    n = len(ps)
    dist = poisson_binomial(ps)
    return {
        "n_priced": n,
        "n_total": len(legs),
        "all_hit": dist[n] if n else 0.0,
        "dist": dist,                       # dist[k] = P(exactly k)
        "at_least": [sum(dist[k:]) for k in range(n + 1)],
    }


# --- payout presets (standard; vary by promo, Goblins/Demons adjust these) ---
PRIZEPICKS_POWER = {2: 3.0, 3: 5.0, 4: 10.0, 5: 20.0, 6: 37.5}
PRIZEPICKS_FLEX = {
    3: {3: 2.25, 2: 1.25},
    4: {4: 5.0, 3: 1.5},
    5: {5: 10.0, 4: 2.0, 3: 0.4},
    6: {6: 25.0, 5: 2.0, 4: 0.4},
}


def power_ev(all_hit, n, multiplier=None):
    """EV per $1 stake for an all-or-nothing (Power) entry."""
    m = multiplier if multiplier else PRIZEPICKS_POWER.get(n)
    if not m:
        return None, None
    return all_hit * m - 1, m


def flex_ev(dist, n):
    """EV per $1 stake for a Flex entry using the standard payout table."""
    tbl = PRIZEPICKS_FLEX.get(n)
    if not tbl:
        return None
    expected_return = sum(dist[k] * tbl.get(k, 0.0) for k in range(n + 1))
    return expected_return - 1
