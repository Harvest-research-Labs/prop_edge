"""Matchup adjustment for the MLB model.

The base model is matchup-blind — it only knows a hitter's own form. This layer
scales a hitter's projection by:
  • the opposing STARTING PITCHER's strikeout and contact rates (facing an ace
    lowers your hits/total-bases and raises your strikeouts), and
  • the ballpark's run factor (Coors inflates offense, Tropicana suppresses it).

Everything degrades gracefully: if we can't find the matchup, the pitcher's
stats, or the park, we return the base projection unchanged — it never breaks,
worst case it's a no-op.

This is a first-cut, deliberately simple adjustment (clamped multipliers). It's
directionally right; tuning the magnitudes needs the backtest to accumulate.
"""

import requests

from model.mlb_stats import SCHEDULE_URL, HEADERS, _ip_to_outs, _fetch_group

# Run park factors (≈1.00 = neutral). Keyed by venue-name substring.
PARK_FACTORS = {
    "Coors": 1.15, "Fenway": 1.06, "Great American": 1.08, "Yankee": 1.03,
    "Citizens Bank": 1.05, "Globe Life": 1.04, "Chase Field": 1.03,
    "Wrigley": 1.02, "Camden": 1.02, "Kauffman": 1.01, "Truist": 1.00,
    "Dodger": 0.98, "Comerica": 0.97, "loanDepot": 0.97, "Busch": 0.97,
    "Oracle": 0.93, "Petco": 0.94, "T-Mobile": 0.92, "Tropicana": 0.95,
    "Citi Field": 0.97, "American Family": 0.99, "Progressive": 0.98,
}

HIT_STATS = {"hits", "total bases", "singles", "doubles", "triples",
             "home runs", "runs", "rbis", "hits+runs+rbis"}
K_STAT = "batter strikeouts"
WALK_STAT = "batter walks"


def _park(venue):
    if not venue:
        return 1.0
    for key, f in PARK_FACTORS.items():
        if key.lower() in venue.lower():
            return f
    return 1.0


def _clamp(x, lo=0.70, hi=1.45):
    return max(lo, min(hi, x))


# innings of league-average "phantom" pitching blended into each starter's
# rates, so a small-sample rookie doesn't swing a hitter's projection.
PITCHER_SHRINK_IP = 30


def _pitcher_rates(session):
    """{pitcher_id: {'k','h','bb' per IP}} shrunk to league + league averages."""
    splits = _fetch_group("pitching", session)
    raw, tk, th, tbb, tip = [], 0.0, 0.0, 0.0, 0.0
    for sp in splits:
        st = sp["stat"]
        ip = _ip_to_outs(st.get("inningsPitched", 0)) / 3.0
        if ip <= 0:
            continue
        k, h, bb = st.get("strikeOuts", 0), st.get("hits", 0), st.get("baseOnBalls", 0)
        raw.append((sp["player"]["id"], k, h, bb, ip))
        tk += k; th += h; tbb += bb; tip += ip
    league = {"k": tk / tip, "h": th / tip, "bb": tbb / tip} if tip else {"k": 1, "h": 1, "bb": 1}
    K = PITCHER_SHRINK_IP
    rates = {}
    for pid, k, h, bb, ip in raw:
        rates[pid] = {
            "k": (k + K * league["k"]) / (ip + K),
            "h": (h + K * league["h"]) / (ip + K),
            "bb": (bb + K * league["bb"]) / (ip + K),
            "ip": ip,
        }
    return rates, league


def _team_matchups(date_iso, session):
    """{TEAM_ABBR: {'opp_sp': pitcher_id, 'park': factor}} for the date."""
    r = session.get(
        SCHEDULE_URL,
        params={"sportId": 1, "date": date_iso, "hydrate": "probablePitcher,team,venue"},
        headers=HEADERS, timeout=20,
    )
    r.raise_for_status()
    out = {}
    for dt in r.json().get("dates", []):
        for g in dt.get("games", []):
            a, h = g["teams"]["away"], g["teams"]["home"]
            park = _park(g.get("venue", {}).get("name"))
            a_ab = a["team"].get("abbreviation")
            h_ab = h["team"].get("abbreviation")
            a_sp = (a.get("probablePitcher") or {}).get("id")
            h_sp = (h.get("probablePitcher") or {}).get("id")
            if a_ab:
                out[a_ab.upper()] = {"opp_sp": h_sp, "park": park}   # away faces home's SP
            if h_ab:
                out[h_ab.upper()] = {"opp_sp": a_sp, "park": park}   # home faces away's SP
    return out


def build_adjuster(date_iso, session=None):
    """Return adjust(canonical_stat, base_mean, team, opp) -> adjusted mean.

    Also exposes `.matchup(team)` so callers can show what was applied.
    """
    session = session or requests.Session()
    try:
        teams = _team_matchups(date_iso, session)
        prates, league = _pitcher_rates(session)
    except Exception:  # noqa: BLE001 - never let matchup data break projections
        teams, prates, league = {}, {}, {"k": 1, "h": 1, "bb": 1}

    def adjust(stat, base_mean, team=None, opp=None):
        if base_mean is None or not team:
            return base_mean
        m = teams.get(str(team).upper())
        if not m:
            return base_mean
        sp = prates.get(m["opp_sp"])
        park = m["park"]
        if stat in HIT_STATS:
            f = park * (_clamp(sp["h"] / league["h"]) if sp else 1.0)
            return base_mean * _clamp(f)
        if stat == K_STAT and sp:
            return base_mean * _clamp(sp["k"] / league["k"])
        if stat == WALK_STAT and sp:
            return base_mean * _clamp(sp["bb"] / league["bb"])
        return base_mean

    adjust.teams = teams
    adjust.matchup = lambda team: teams.get(str(team or "").upper())
    return adjust
