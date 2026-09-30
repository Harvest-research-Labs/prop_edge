"""Underdog source.

One big payload holds everything. We join:
    over_under_line -> over_under.appearance_stat.appearance_id
                    -> appearance -> player_id  -> player
                    -> appearance.match_id      -> game (for opponent)
Each line's two options carry American odds, which become over/under odds.
"""

import requests

from config import UNDERDOG_LINES_URLS, UNDERDOG_HEADERS
from sources.base import Prop

# Underdog sport_id -> our canonical label.
SPORT_MAP = {
    "FIFA": "SOCCER",
    "MMA": "UFC",
    "MLB": "MLB",
    "NBA": "NBA",
    "WNBA": "WNBA",
    "NFL": "NFL",
    "NHL": "NHL",
    "PGA": "PGA",
    "TENNIS": "TENNIS",
}


def _split_title(title):
    """'ATL @ NYM' / 'ESP vs KSA' -> ('ATL','NYM') as (away, home)."""
    if not title:
        return None, None
    for sep in (" @ ", " vs ", " VS ", " v "):
        if sep in title:
            a, b = title.split(sep, 1)
            return a.strip(), b.strip()
    return title.strip(), None


def _get_payload(session, timeout, log):
    """GET the lines payload, walking UNDERDOG_LINES_URLS newest-first.
    Underdog answers a retired API version with 426 Upgrade Required, so on a
    426/404/410 we try the next version. Returns (payload, error)."""
    err = None
    for url in UNDERDOG_LINES_URLS:
        try:
            resp = session.get(url, headers=UNDERDOG_HEADERS, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            return None, str(e)
        if resp.status_code == 200:
            try:
                d = resp.json()
            except ValueError as e:
                return None, f"bad JSON: {e}"
            # Solo-sport events (PGA, tennis, MMA) live under solo_games.
            d.setdefault("games", [])
            d["games"] = list(d["games"]) + list(d.get("solo_games") or [])
            return d, None
        err = f"HTTP {resp.status_code}"
        if resp.status_code == 426:
            err += " (API version retired; set UNDERDOG_LINES_URL to the current endpoint)"
        if resp.status_code not in (404, 410, 426):
            return None, err
        log(f"Underdog: {url} -> {resp.status_code}, trying next version")
    return None, err


def fetch(sports, session=None, timeout=30, log=lambda m: None):
    """Fetch Underdog and return (props, errors) filtered to `sports`."""
    session = session or requests.Session()
    wanted = set(sports)
    d, err = _get_payload(session, timeout, log)
    if err:
        return [], {"Underdog": err}

    players = {p["id"]: p for p in d.get("players", [])}
    appearances = {a["id"]: a for a in d.get("appearances", [])}
    games = {g["id"]: g for g in d.get("games", [])}

    props = []
    for oul in d.get("over_under_lines", []):
        ou = oul.get("over_under", {})
        astat = ou.get("appearance_stat", {})
        appr = appearances.get(astat.get("appearance_id"))
        if not appr:
            continue
        player = players.get(appr.get("player_id"))
        if not player:
            continue
        sport = SPORT_MAP.get(player.get("sport_id"), player.get("sport_id"))
        if sport not in wanted:
            continue

        game = games.get(appr.get("match_id"))
        team = opp = None
        if game:
            away, home = _split_title(game.get("abbreviated_title"))
            if appr.get("team_id") == game.get("home_team_id"):
                team, opp = home, away
            else:
                team, opp = away, home

        over = under = None
        over_pay = under_pay = None
        for o in oul.get("options", []):
            if o.get("status") != "active":
                continue
            price = _american(o.get("american_price"))
            pay = _as_float(o.get("payout_multiplier"))
            if o.get("choice") == "higher":
                over, over_pay = price, pay
            elif o.get("choice") == "lower":
                under, under_pay = price, pay

        name = " ".join(x for x in [player.get("first_name"), player.get("last_name")] if x).strip()
        props.append(
            Prop(
                book="Underdog",
                sport=sport,
                player=name or "?",
                team=team,
                opponent=opp,
                stat=astat.get("display_stat") or astat.get("stat") or "",
                line=_as_float(oul.get("stat_value")),
                flavor="standard" if oul.get("line_type") == "balanced" else (oul.get("line_type") or "standard"),
                over_odds=over,
                under_odds=under,
                over_payout=over_pay,
                under_payout=under_pay,
                start_time=(game or {}).get("scheduled_at"),
                image_url=player.get("image_url"),
                source_id=oul.get("id"),
            )
        )
    log(f"Underdog: {len(props)} props across {len(wanted)} sport(s)")
    return props, {}


def fetch_games(sports, session=None, timeout=30, log=lambda m: None):
    """Today's slate: matchups, start times, live status/scores, prop counts.
    Returns (games, errors)."""
    from collections import Counter
    session = session or requests.Session()
    wanted = set(sports)
    d, err = _get_payload(session, timeout, log)
    if err:
        return [], {"Underdog": err}

    appr = {a["id"]: a for a in d.get("appearances", [])}
    counts = Counter()
    for oul in d.get("over_under_lines", []):
        aid = oul.get("over_under", {}).get("appearance_stat", {}).get("appearance_id")
        a = appr.get(aid)
        if a:
            counts[a.get("match_id")] += 1

    out = []
    for g in d.get("games", []):
        sport = SPORT_MAP.get(g.get("sport_id"), g.get("sport_id"))
        if sport not in wanted:
            continue
        away, home = _split_title(g.get("abbreviated_title"))
        out.append({
            "sport": sport,
            "away": away, "home": home,
            "title": g.get("title") or g.get("full_team_names_title"),
            "away_score": g.get("away_team_score"),
            "home_score": g.get("home_team_score"),
            "status": g.get("status"),
            "progress": g.get("match_progress"),
            "start_time": g.get("scheduled_at"),
            "props": counts.get(g.get("id"), 0),
        })
    out.sort(key=lambda x: x.get("start_time") or "")
    log(f"Underdog slate: {len(out)} game(s)")
    return out, {}


def _american(s):
    try:
        return int(str(s).replace("+", ""))
    except (TypeError, ValueError):
        return None


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
