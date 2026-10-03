"""Own projection model for NBA and WNBA, from stats.nba.com's league-wide player stats.

Three league-wide calls give per-player totals: this season, the last
RECENT_GAMES games, and last season. Each player's per-game rate is this
season's totals shrunk toward a prior (their own last-season rate, or league
average for rookies), then blended toward recent form by sample size. The
last-season prior is what makes the model usable in the first weeks of a
season, when a player has only a handful of games.

Like the MLB model it is matchup- and minutes-blind (no opponent, pace,
injury or rotation adjustment), so it sits *below* the market-derived mean in
priority: it fills gaps, it doesn't override a priced market.

The WNBA lives on the same endpoint under LeagueID 10, with single-year
season names ("2026") because its season runs May-October inside one year.
The model, stat mapping and constants are shared; only the league id and the
season naming differ.

stats.nba.com refuses datacenter IPs and bare clients; it needs the browser
headers below and works from a residential connection (same constraint as
PrizePicks).
"""

import datetime
import requests

from resolver.normalize import normalize_name

STATS_URL = "https://stats.nba.com/stats/leaguedashplayerstats"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}

# Games of league-average-or-last-season "phantom" form blended into this
# season's rate. ~10 games means a player 5 games in is still mostly last
# season; by game 40 it's ~80% this season.
SHRINK_K = 10
# Recent window and how strongly to trust it: alpha = gp / (gp + RECENCY_K),
# so a full 10-game window gets ~55% weight.
RECENT_GAMES = 10
RECENCY_K = 8

LEAGUE_IDS = {"NBA": "00", "WNBA": "10"}


def season_label(today=None, league="NBA"):
    """Season string for a date.

    NBA: Oct 2026 - Sep 2027 -> '2026-27'.
    WNBA: the calendar year, '2026'. Before May that season hasn't started, so
    the pull comes back empty and the projection falls back to last season.
    """
    today = today or datetime.date.today()
    if league == "WNBA":
        return str(today.year)
    start = today.year if today.month >= 10 else today.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def previous_season(label):
    """'2026-27' -> '2025-26'; '2026' -> '2025'."""
    start = int(label[:4]) - 1
    if len(label) == 4:
        return str(start)
    return f"{start}-{(start + 1) % 100:02d}"


def _fetch(session, season, last_n=0, timeout=15, league_id="00"):
    """League-wide per-player season TOTALS as a list of {column: value} dicts."""
    params = {
        "Season": season, "SeasonType": "Regular Season", "PerMode": "Totals",
        "MeasureType": "Base", "LastNGames": last_n, "LeagueID": league_id,
        # stats.nba.com 400s unless every filter is present, even if blank.
        "College": "", "Conference": "", "Country": "", "DateFrom": "", "DateTo": "",
        "Division": "", "DraftPick": "", "DraftYear": "", "GameScope": "",
        "GameSegment": "", "Height": "", "Location": "", "Month": 0,
        "OpponentTeamID": 0, "Outcome": "", "PORound": 0, "PaceAdjust": "N",
        "Period": 0, "PlayerExperience": "", "PlayerPosition": "", "PlusMinus": "N",
        "Rank": "N", "SeasonSegment": "", "ShotClockRange": "", "StarterBench": "",
        "TeamID": 0, "TwoWay": 0, "VsConference": "", "VsDivision": "", "Weight": "",
    }
    r = session.get(STATS_URL, params=params, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    sets = r.json().get("resultSets") or []
    if not sets:
        return []
    cols = sets[0]["headers"]
    return [dict(zip(cols, row)) for row in sets[0]["rowSet"]]


def box_totals(row):
    """Box-score totals -> canonical prop stats (matching config.canonical_stat)."""
    g = lambda k: row.get(k) or 0
    pts, reb, ast = g("PTS"), g("REB"), g("AST")
    blk, stl, tov = g("BLK"), g("STL"), g("TOV")
    # PrizePicks/Underdog NBA and WNBA fantasy scoring.
    fantasy = pts + 1.2 * reb + 1.5 * ast + 3 * blk + 3 * stl - tov
    return {
        "points": pts,
        "rebounds": reb,
        "assists": ast,
        "3-pt made": g("FG3M"),
        "3-pt attempted": g("FG3A"),
        "pts+rebs+asts": pts + reb + ast,
        "pts+rebs": pts + reb,
        "pts+asts": pts + ast,
        "rebs+asts": reb + ast,
        "blocked shots": blk,
        "steals": stl,
        "blks+stls": blk + stl,
        "turnovers": tov,
        "offensive rebounds": g("OREB"),
        "defensive rebounds": g("DREB"),
        "free throws made": g("FTM"),
        "field goals made": g("FGM"),
        "fg attempted": g("FGA"),
        "two pointers made": g("FGM") - g("FG3M"),
        # Underdog's "Fantasy Points" is left out on purpose: that label shares
        # MLB's wide stat shape (cv 0.6), which would mis-price an NBA line.
        "fantasy score": fantasy,
    }


def _by_name(rows):
    """{norm_name: (totals, games)} for players with at least one game."""
    out = {}
    for r in rows:
        gp = r.get("GP") or 0
        if gp > 0:
            out[normalize_name(r.get("PLAYER_NAME"))] = (box_totals(r), gp)
    return out


def _league_rate(players):
    tot, games = {}, 0
    for t, gp in players.values():
        games += gp
        for k, v in t.items():
            tot[k] = tot.get(k, 0.0) + v
    return {k: v / games for k, v in tot.items()} if games else {}


def build_table(season_rows, recent_rows, last_season_rows):
    """{norm_name: {canonical_stat: per-game mean}} from the three stat pulls.

    A player with no games this season is projected from last season alone,
    so opening-week lines still get a read.
    """
    season = _by_name(season_rows)
    recent = _by_name(recent_rows)
    last = _by_name(last_season_rows)
    league = _league_rate(season) or _league_rate(last)

    table = {}
    for name in set(season) | set(last):
        if name in last:
            ltot, lgp = last[name]
            prior = {k: v / lgp for k, v in ltot.items()}
        else:
            prior = league
        if name not in season:
            table[name] = prior
            continue
        tot, gp = season[name]
        rate = {k: (tot[k] + SHRINK_K * prior.get(k, 0.0)) / (gp + SHRINK_K) for k in tot}
        if name in recent:
            rtot, rgp = recent[name]
            alpha = rgp / (rgp + RECENCY_K)
            rate = {k: alpha * rtot[k] / rgp + (1 - alpha) * rate[k] for k in rate}
        table[name] = rate
    return table


def load_projector(session=None, today=None, league="NBA"):
    """Return proj(player_name, canonical_stat, team=None, opp=None) -> per-game mean or None.

    `league` is "NBA" or "WNBA"."""
    session = session or requests.Session()
    lid = LEAGUE_IDS[league]
    season = season_label(today, league)
    try:
        season_rows = _fetch(session, season, league_id=lid)
    except requests.HTTPError:
        # A season that hasn't started may 4xx instead of returning no rows;
        # the last-season call below still raises on a real failure.
        season_rows = []
    recent_rows = _fetch(session, season, last_n=RECENT_GAMES, league_id=lid) if season_rows else []
    table = build_table(season_rows, recent_rows,
                        _fetch(session, previous_season(season), league_id=lid))

    def proj(player_name, canonical_stat, team=None, opp=None):
        # team/opp accepted for the uniform projector signature; unused here.
        rates = table.get(normalize_name(player_name))
        return rates.get(canonical_stat) if rates else None

    proj.n_players = len(table)
    return proj
