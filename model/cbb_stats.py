"""Own projection model for men's college basketball, from the CollegeBasketballData API.

One call per season (/stats/player/season) gives every D-I player's season
totals and games played. A player's per-game rate is this season's totals
shrunk toward their own last-season rate when they have one. Freshmen and
transfers without a matching last season use their raw rate: a league average
over ~5,000 players, most of them deep bench, would drag every new starter
toward a few points a game. Before the first tip-off it projects from last
season alone.

Known limits, so it sits *below* the market in priority (fills gaps only):
- no opponent, pace, minutes or injury adjustment, and no recent-form window;
- two players with the same name are ambiguous without a team join, so those
  names are left unprojected rather than guessed.

Box-score totals map onto prop stats through nba_stats.box_totals, so NBA and
CBB stats share one definition.

Needs a free API key from collegebasketballdata.com, passed in or read from
CBBD_API_KEY (falling back to CFBD_API_KEY). Without one the model is off.
"""

import datetime
import os
import requests

from model.nba_stats import box_totals
from resolver.normalize import normalize_name

BASE_URL = "https://api.collegebasketballdata.com"

# Phantom games of last-season form blended into this season's rate. ~30-game
# seasons: 4 games in, last season still carries half the weight.
SHRINK_K = 4

# CBBD field -> NBA-style box-score column, so box_totals can be reused.
# Each entry is a path into the (possibly nested) CBBD row.
_FIELDS = {
    "PTS": ("points",),
    "REB": ("rebounds", "total"),
    "OREB": ("rebounds", "offensive"),
    "DREB": ("rebounds", "defensive"),
    "AST": ("assists",),
    "STL": ("steals",),
    "BLK": ("blocks",),
    "TOV": ("turnovers",),
    "FGM": ("fieldGoals", "made"),
    "FGA": ("fieldGoals", "attempted"),
    "FG3M": ("threePointFieldGoals", "made"),
    "FG3A": ("threePointFieldGoals", "attempted"),
    "FTM": ("freeThrows", "made"),
}


def season_year(today=None):
    """CBBD names a season by the year it ends: Nov 2026 - Apr 2027 -> 2027.
    From May on it points at the next (not yet started) season, which comes
    back empty, so the projection falls back to the one just finished."""
    today = today or datetime.date.today()
    return today.year + 1 if today.month >= 5 else today.year


def _num(row, path):
    """Follow a field path; a bare number where a dict was expected (e.g.
    `rebounds: 7` instead of `{total: 7}`) counts as the total."""
    v = row
    for key in path:
        if isinstance(v, dict):
            v = v.get(key)
        elif key != "total":
            return 0.0
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _per_game(rows):
    """{norm_name: {stat: per-game}} for players with games; a name seen on
    two different teams maps to None (ambiguous)."""
    seen, out = {}, {}
    for r in rows:
        name = normalize_name(r.get("name") or r.get("athleteName") or r.get("player"))
        gp = _num(r, ("games",))
        if not name or gp <= 0:
            continue
        team = r.get("team")
        if name in seen and seen[name] != team:
            out[name] = None
            continue
        seen[name] = team
        if out.get(name, 0) is None:
            continue
        box = {col: _num(r, path) for col, path in _FIELDS.items()}
        tot = box_totals(box)
        tot.pop("fantasy score", None)        # NBA scoring; no CBB fantasy lines
        out[name] = {k: v / gp for k, v in tot.items()} | {"_gp": gp}
    return out


def build_table(season_rows, last_rows):
    """{norm_name: {canonical_stat: per-game mean}}, or None for ambiguous names."""
    season, last = _per_game(season_rows), _per_game(last_rows)
    table = {}
    for name in set(season) | set(last):
        cur, prev = season.get(name, "absent"), last.get(name, "absent")
        if cur is None or (cur == "absent" and prev is None):
            table[name] = None
            continue
        prior = None if prev in ("absent", None) else {k: v for k, v in prev.items() if k != "_gp"}
        if cur == "absent":
            table[name] = prior
            continue
        gp = cur["_gp"]
        rate = {k: v for k, v in cur.items() if k != "_gp"}
        if prior is not None:
            rate = {k: (rate[k] * gp + SHRINK_K * prior.get(k, 0.0)) / (gp + SHRINK_K) for k in rate}
        table[name] = rate
    return table


def _fetch(session, season, api_key, timeout=20):
    r = session.get(
        f"{BASE_URL}/stats/player/season",
        params={"season": season, "seasonType": "regular"}, timeout=timeout,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    r.raise_for_status()
    return r.json() or []


def load_projector(api_key=None, session=None, today=None):
    """Return proj(player_name, canonical_stat, team=None, opp=None) -> per-game mean or None.

    Raises if there's no API key, so callers fall back to market-only pricing."""
    api_key = api_key or os.environ.get("CBBD_API_KEY") or os.environ.get("CFBD_API_KEY")
    if not api_key:
        raise RuntimeError("CBBD_API_KEY not set; college basketball model is off")
    session = session or requests.Session()
    year = season_year(today)
    table = build_table(_fetch(session, year, api_key), _fetch(session, year - 1, api_key))

    def proj(player_name, canonical_stat, team=None, opp=None):
        # team/opp accepted for the uniform projector signature; unused here.
        rates = table.get(normalize_name(player_name))
        return rates.get(canonical_stat) if rates else None

    proj.n_players = sum(1 for v in table.values() if v)
    return proj
