"""Own projection model for college football, from the CollegeFootballData API.

Two calls per season: player season stat totals (passing, rushing, receiving)
and the game list, which gives each team's games played. A player's per-game
rate is their total over their team's completed games, shrunk toward their own
last-season rate when they have one (rosters turn over too much for a league
average to mean anything across positions). Before a season's first game it
projects from last season alone.

Known limits, so it sits *below* the market in priority (fills gaps only):
- per-game uses TEAM games, so a player who missed games is under-projected;
- no opponent, pace, depth-chart or injury adjustment;
- two players with the same name are ambiguous without a team join, so
  those names are left unprojected rather than guessed.

Needs a free API key from collegefootballdata.com, passed in or read from the
CFBD_API_KEY environment variable. Without one the model is simply off.
"""

import datetime
import os
import requests

from resolver.normalize import normalize_name

BASE_URL = "https://api.collegefootballdata.com"

# Phantom games of last-season form blended into this season's rate. Seasons
# are short (12 games), so a small K: 3 games in, last season is still half.
SHRINK_K = 3

# (CFBD category, statType) -> our canonical stat (see config.canonical_stat).
_STAT_MAP = {
    ("passing", "YDS"): "pass yards",
    ("passing", "TD"): "pass tds",
    ("passing", "COMPLETIONS"): "pass completions",
    ("passing", "ATT"): "pass attempts",
    ("passing", "INT"): "pass ints",
    ("rushing", "YDS"): "rush yards",
    ("rushing", "CAR"): "rush attempts",
    ("rushing", "TD"): "rush tds",
    ("receiving", "YDS"): "receiving yards",
    ("receiving", "REC"): "receptions",
    ("receiving", "TD"): "receiving tds",
}


def season_year(today=None):
    """CFB season for a date: Aug 2026 - Jan 2027 -> 2026."""
    today = today or datetime.date.today()
    return today.year if today.month >= 8 else today.year - 1


def _get(session, path, params, api_key, timeout=20):
    r = session.get(
        f"{BASE_URL}{path}", params=params, timeout=timeout,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    r.raise_for_status()
    return r.json() or []


def _games_played(games):
    """{team: completed regular-season games}. Accepts the API's camelCase
    (v2) and snake_case (v1) field names."""
    out = {}
    for g in games:
        if not g.get("completed"):
            continue
        for side in ("homeTeam", "awayTeam", "home_team", "away_team"):
            team = g.get(side)
            if team:
                out[team] = out.get(team, 0) + 1
    return out


def _totals(stat_rows):
    """{norm_name: {"team": str, "tot": {stat: total}}}; names that appear on
    two different teams map to None (ambiguous)."""
    out = {}
    for row in stat_rows:
        stat = _STAT_MAP.get(((row.get("category") or "").lower(), (row.get("statType") or "").upper()))
        if not stat:
            continue
        try:
            value = float(row.get("stat"))
        except (TypeError, ValueError):
            continue
        name, team = normalize_name(row.get("player")), row.get("team")
        if not name:
            continue
        cur = out.get(name, {"team": team, "tot": {}})
        if cur is None:
            continue
        if cur["team"] != team:
            out[name] = None
            continue
        cur["tot"][stat] = value
        out[name] = cur
    for rec in out.values():
        if rec:
            t = rec["tot"]
            t["rush+rec yds"] = t.get("rush yards", 0.0) + t.get("receiving yards", 0.0)
            t["pass+rush yds"] = t.get("pass yards", 0.0) + t.get("rush yards", 0.0)
            t["rush+rec tds"] = t.get("rush tds", 0.0) + t.get("receiving tds", 0.0)
    return out


def _per_game(players, games_played):
    rates = {}
    for name, rec in players.items():
        if rec is None:
            rates[name] = None
            continue
        gp = games_played.get(rec["team"], 0)
        if gp > 0:
            rates[name] = (rec["team"], gp, rec["tot"])
    return rates


def build_table(season_stats, season_games, last_stats, last_games):
    """{norm_name: {canonical_stat: per-game mean}}, or None for ambiguous names."""
    season = _per_game(_totals(season_stats), _games_played(season_games))
    last = _per_game(_totals(last_stats), _games_played(last_games))

    table = {}
    for name in set(season) | set(last):
        cur, prev = season.get(name, "absent"), last.get(name, "absent")
        if cur is None or (cur == "absent" and prev is None):
            table[name] = None                     # ambiguous name
            continue
        prior = None
        if prev not in ("absent", None):
            _, lgp, ltot = prev
            prior = {k: v / lgp for k, v in ltot.items()}
        if cur == "absent":
            table[name] = prior
            continue
        _, gp, tot = cur
        if prior is None:
            table[name] = {k: v / gp for k, v in tot.items()}
        else:
            keys = set(tot) | set(prior)
            table[name] = {
                k: (tot.get(k, 0.0) + SHRINK_K * prior.get(k, 0.0)) / (gp + SHRINK_K)
                for k in keys
            }
    return table


def load_projector(api_key=None, session=None, today=None):
    """Return proj(player_name, canonical_stat, team=None, opp=None) -> per-game mean or None.

    Raises if there's no API key, so callers fall back to market-only pricing."""
    api_key = api_key or os.environ.get("CFBD_API_KEY")
    if not api_key:
        raise RuntimeError("CFBD_API_KEY not set; college football model is off")
    session = session or requests.Session()
    year = season_year(today)

    def pull(y):
        q = {"year": y, "seasonType": "regular"}
        return (_get(session, "/stats/player/season", q, api_key),
                _get(session, "/games", q, api_key))

    season_stats, season_games = pull(year)
    last_stats, last_games = pull(year - 1)
    table = build_table(season_stats, season_games, last_stats, last_games)

    def proj(player_name, canonical_stat, team=None, opp=None):
        # team/opp accepted for the uniform projector signature; unused here.
        rates = table.get(normalize_name(player_name))
        return rates.get(canonical_stat) if rates else None

    proj.n_players = sum(1 for v in table.values() if v)
    return proj
