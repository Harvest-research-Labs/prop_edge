"""Own projection model for MLB, from the free MLB Stats API.

Two league-wide calls (hitting + pitching season stats) give per-player season
totals; we turn those into per-game (hitters) / per-start (pitchers) rates and
map them onto the canonical prop stats. This prices lines neither book anchors
— e.g. a Total Bases line that only exists as a PrizePicks Demon.

Season-rate is matchup-blind (no park/opponent/pitcher adjustment), so it sits
*below* the market-derived mean in priority — it fills gaps, it doesn't override
a priced market.
"""

import datetime
import unicodedata
import requests

SEASON = 2026
STATS_URL = "https://statsapi.mlb.com/api/v1/stats"
SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def fetch_schedule(date_iso, session=None, timeout=20):
    """Every MLB game on a date with live scores/status. Returns game dicts
    shaped like the Underdog slate (away/home/scores/status/progress)."""
    session = session or requests.Session()
    r = session.get(
        SCHEDULE_URL,
        params={"sportId": 1, "date": date_iso, "hydrate": "linescore,team"},
        headers=HEADERS, timeout=timeout,
    )
    r.raise_for_status()
    games = []
    for dt in r.json().get("dates", []):
        for g in dt.get("games", []):
            a, h = g["teams"]["away"], g["teams"]["home"]
            ls = g.get("linescore", {}) or {}
            state = g["status"].get("abstractGameState")  # Preview | Live | Final
            status = {"Preview": "scheduled", "Live": "live", "Final": "final"}.get(state, "scheduled")
            if state == "Live":
                progress = f"{ls.get('inningState', '')} {ls.get('currentInningOrdinal', '')}".strip()
            elif state == "Final":
                progress = g["status"].get("detailedState", "Final")
            else:
                progress = g.get("gameDate")  # formatted to local time by caller
            games.append({
                "sport": "MLB",
                "away": a["team"].get("abbreviation"), "home": h["team"].get("abbreviation"),
                "away_score": a.get("score"), "home_score": h.get("score"),
                "status": status, "progress": progress,
                "start_time": g.get("gameDate"), "props": None,
            })
    return games

# Recency window and how strongly to trust it. alpha = recent_games /
# (recent_games + RECENCY_K): ~15 recent games -> ~65% weight on recent form.
RECENCY_DAYS = 15
RECENCY_K = 8

# canonical prop stat -> ("hit"|"pit", function(stat_dict, denom) -> per-unit value)
# denom is gamesPlayed for hitters, gamesStarted (fallback gamesPlayed) for pitchers.


def _norm_name(name):
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace(".", "").strip()
    for suf in (" jr", " sr", " ii", " iii", " iv"):
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
    return s


def _ip_to_outs(ip):
    """MLB innings-pitched notation '78.2' -> outs (78*3 + 2)."""
    try:
        whole, _, frac = str(ip).partition(".")
        return int(whole) * 3 + (int(frac[0]) if frac else 0)
    except (ValueError, IndexError):
        return 0


def _fetch_group(group, session, start=None, end=None, timeout=30):
    """League-wide stats for a group. Season totals by default, or a date range
    (byDateRange) when start/end are given."""
    params = {"group": group, "season": SEASON, "sportId": 1,
              "limit": 2000, "playerPool": "ALL"}
    if start and end:
        params.update({"stats": "byDateRange", "startDate": start, "endDate": end})
    else:
        params["stats"] = "season"
    r = session.get(STATS_URL, params=params, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    splits = r.json().get("stats", [])
    return splits[0]["splits"] if splits else []


# How many league-average "phantom games" to blend into each player's rate.
# Tames noisy small samples (a rookie with 2 games won't show 3.5 K/game) while
# barely touching established players with 70+ games.
SHRINK_K = 5


def _hit_totals(st):
    singles = st.get("hits", 0) - st.get("doubles", 0) - st.get("triples", 0) - st.get("homeRuns", 0)
    return {
        "hits": st.get("hits", 0),
        "total bases": st.get("totalBases", 0),
        "runs": st.get("runs", 0),
        "rbis": st.get("rbi", 0),
        "home runs": st.get("homeRuns", 0),
        "batter walks": st.get("baseOnBalls", 0),
        "batter strikeouts": st.get("strikeOuts", 0),
        "stolen bases": st.get("stolenBases", 0),
        "singles": max(singles, 0),
        "doubles": st.get("doubles", 0),
        "triples": st.get("triples", 0),
        "plate appearances": st.get("plateAppearances", 0),
        "hits+runs+rbis": st.get("hits", 0) + st.get("runs", 0) + st.get("rbi", 0),
    }


def _pit_totals(st):
    outs = _ip_to_outs(st.get("inningsPitched", 0))
    ip = outs / 3.0
    try:
        era = float(st.get("era"))
    except (TypeError, ValueError):
        era = None
    er_total = (era * ip / 9.0) if era is not None else st.get("earnedRuns", 0)
    return {
        "pitcher strikeouts": st.get("strikeOuts", 0),
        "hits allowed": st.get("hits", 0),
        "walks allowed": st.get("baseOnBalls", 0),
        "earned runs allowed": er_total,
        "pitching outs": outs,
    }


def _totals_by_name(splits, totals_fn, denom_fn):
    """{norm_name: (totals_dict, denom)} plus league (sum_totals, sum_denom)."""
    out, league_tot, league_g = {}, {}, 0.0
    for sp in splits:
        st = sp["stat"]
        denom = denom_fn(st)
        if denom <= 0:
            continue
        tot = totals_fn(st)
        out[_norm_name(sp["player"]["fullName"])] = (tot, denom)
        league_g += denom
        for k, v in tot.items():
            league_tot[k] = league_tot.get(k, 0.0) + v
    return out, league_tot, league_g


def _build_table(season_splits, recent_splits, totals_fn, denom_fn):
    """Per-player per-game rates: season shrunk to league average, then blended
    toward recent (last-N-day) form by sample size."""
    season, league_tot, league_g = _totals_by_name(season_splits, totals_fn, denom_fn)
    recent, _, _ = _totals_by_name(recent_splits, totals_fn, denom_fn)
    prior = {k: (v / league_g if league_g else 0.0) for k, v in league_tot.items()}

    table = {}
    for name, (tot, denom) in season.items():
        season_rate = {
            k: (tot[k] + SHRINK_K * prior.get(k, 0.0)) / (denom + SHRINK_K) for k in tot
        }
        rec = recent.get(name)
        if rec:
            rtot, rdenom = rec
            alpha = rdenom / (rdenom + RECENCY_K)
            table[name] = {
                k: alpha * (rtot.get(k, 0.0) / rdenom) + (1 - alpha) * season_rate[k]
                for k in tot
            }
        else:
            table[name] = season_rate
    return table


def load_projector(session=None, today=None):
    """Return proj(player_name, canonical_stat) -> per-game mean or None.

    Blends season rates (shrunk to league average) with the last RECENCY_DAYS
    of form."""
    session = session or requests.Session()
    today = today or datetime.date.today()
    start = (today - datetime.timedelta(days=RECENCY_DAYS)).isoformat()
    end = today.isoformat()

    hit_denom = lambda st: st.get("gamesPlayed", 0)
    pit_denom = lambda st: (st.get("gamesStarted") or 0) or (st.get("gamesPlayed") or 0)
    hitters = _build_table(
        _fetch_group("hitting", session),
        _fetch_group("hitting", session, start, end),
        _hit_totals, hit_denom,
    )
    pitchers = _build_table(
        _fetch_group("pitching", session),
        _fetch_group("pitching", session, start, end),
        _pit_totals, pit_denom,
    )

    PITCH_STATS = {"pitcher strikeouts", "hits allowed", "walks allowed",
                   "earned runs allowed", "pitching outs"}

    def proj(player_name, canonical_stat, team=None, opp=None):
        # team/opp accepted for a uniform projector signature (matchup layer
        # uses them); the base season+recency model ignores them.
        key = _norm_name(player_name)
        table = pitchers if canonical_stat in PITCH_STATS else hitters
        rates = table.get(key)
        if not rates:
            return None
        return rates.get(canonical_stat)

    proj.n_hitters = len(hitters)
    proj.n_pitchers = len(pitchers)
    return proj
