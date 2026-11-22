"""SportsGameOdds (SGO) source — optional feed for PrizePicks/Underdog lines.

Why this exists: PrizePicks/Underdog have no official public API, so ProEdge
currently aggregates (scrapes) their internal endpoints. SGO is a vendor that
aggregates the same DFS lines behind a documented API with terms + support. This
adapter lets ProEdge pull those lines from SGO instead of (or alongside) the
scraper. See docs/DATA_FEED_DECISION.md.

Dormant by default: without `SGO_API_KEY` (config), fetch() returns no props and
makes no network call — the source is simply off, never an error.

SGO schema (docs, https://sportsgameodds.com/docs):
    GET {SGO_BASE_URL}/events?apiKey=KEY&leagueID=NBA,NFL&oddsAvailable=true
    -> {"success": true, "data": [ event, ... ], "nextCursor": ...}
  event.odds is keyed by oddID = "{statID}-{statEntityID}-{periodID}-{betTypeID}-{sideID}"
    - statEntityID is "home"/"away"/"all" for team odds, or a playerID for props
    - a player prop over line is e.g. "points-LEBRON_JAMES_NBA-game-ou-over"
  each odd carries .byBookmaker.{prizepicks|underdog} = {overUnder, odds, available, lastUpdatedAt}
  event.players (when present) maps playerID -> {name/firstName/lastName, teamID}

NOTE: the parser is written against the documented schema and verified by unit
tests with representative payloads. It has NOT been validated against the live
API (that needs a key). Treat live coverage/field names as "confirm on first key".
"""

import requests

from config import (
    SGO_BASE_URL,
    SGO_API_KEY,
    SGO_LEAGUE_MAP,
    SGO_LEAGUE_TO_SPORT,
    SGO_BOOKMAKERS,
    canonical_stat,
)
from sources.base import Prop


def _humanize_player(player_id):
    """'LEBRON_JAMES_NBA' -> 'Lebron James' (fallback when no players map)."""
    if not player_id:
        return "?"
    parts = [p for p in str(player_id).split("_") if p]
    # drop a trailing league token (NBA/NFL/...) if present
    if len(parts) > 1 and parts[-1].isupper() and parts[-1].isalpha() and len(parts[-1]) <= 4:
        parts = parts[:-1]
    return " ".join(w.capitalize() for w in parts) or "?"


def _humanize_stat(stat_id):
    """'passing_yards' / 'points' -> a display-ish label; canonical_stat normalizes."""
    label = str(stat_id or "").replace("_", " ").replace("-", " ").strip().title()
    return label


def _player_name(event, player_id):
    players = (event.get("players") or {}) if isinstance(event, dict) else {}
    p = players.get(player_id) or {}
    name = p.get("name") or p.get("displayName")
    if not name:
        fn, ln = p.get("firstName"), p.get("lastName")
        if fn or ln:
            name = f"{fn or ''} {ln or ''}".strip()
    return name or _humanize_player(player_id)


def _player_team(event, player_id):
    players = (event.get("players") or {}) if isinstance(event, dict) else {}
    p = players.get(player_id) or {}
    return p.get("teamID") or p.get("team")


def _event_opponent_label(event):
    """'AWAY @ HOME' from event.teams when available."""
    teams = (event.get("teams") or {}) if isinstance(event, dict) else {}
    away = (teams.get("away") or {}).get("names", {}).get("short") or (teams.get("away") or {}).get("teamID")
    home = (teams.get("home") or {}).get("names", {}).get("short") or (teams.get("home") or {}).get("teamID")
    if away and home:
        return f"{away} @ {home}"
    return None


def _parse_odd_id(odd_id):
    """'{statID}-{statEntityID}-{periodID}-{betTypeID}-{sideID}' -> dict or None.

    statEntityID may itself contain hyphens in rare ids, so split from both ends:
    the first field is statID, the last three are period/betType/side, and
    everything between is the statEntityID.
    """
    parts = str(odd_id or "").split("-")
    if len(parts) < 5:
        return None
    stat_id = parts[0]
    period_id, bet_type_id, side_id = parts[-3], parts[-2], parts[-1]
    entity_id = "-".join(parts[1:-3])
    return {
        "statID": stat_id,
        "statEntityID": entity_id,
        "periodID": period_id,
        "betTypeID": bet_type_id,
        "sideID": side_id,
    }


def parse_events(events, books):
    """Turn SGO events into Prop rows for the given book labels.

    `books` maps SGO bookmakerID -> our book label (e.g. {"prizepicks": "PrizePicks"}).
    Pure function (no network) so it is fully unit-testable.
    """
    props = []
    for event in events or []:
        league_id = event.get("leagueID")
        sport = SGO_LEAGUE_TO_SPORT.get(league_id, league_id)
        opponent = _event_opponent_label(event)
        start_time = event.get("startsAt") or (event.get("status") or {}).get("startsAt")
        odds = event.get("odds") or {}
        for odd_id, odd in odds.items():
            comp = _parse_odd_id(odd_id)
            if not comp:
                continue
            # player props only: over/under bet on a specific player
            if comp["betTypeID"] != "ou" or comp["sideID"] != "over":
                continue
            entity = comp["statEntityID"]
            if entity in ("home", "away", "all", ""):
                continue
            by_book = odd.get("byBookmaker") or {}
            for book_id, label in books.items():
                entry = by_book.get(book_id)
                if not entry or entry.get("available") is False:
                    continue
                line = entry.get("overUnder")
                if line in (None, ""):
                    continue
                try:
                    line = float(line)
                except (TypeError, ValueError):
                    continue
                props.append(
                    Prop(
                        book=label,
                        sport=sport,
                        player=_player_name(event, entity),
                        team=_player_team(event, entity),
                        opponent=opponent,
                        stat=_humanize_stat(comp["statID"]),
                        line=line,
                        flavor="standard",
                        start_time=start_time,
                        source_id=f"sgo:{odd_id}",
                    )
                )
    return props


def fetch(sports, session=None, timeout=30, log=lambda m: None, books=None):
    """Fetch PrizePicks/Underdog lines via SportsGameOdds. Returns (props, errors).

    Off (returns ([], {})) when SGO_API_KEY is unset — no network call. `books`
    defaults to all configured SGO bookmakers (PrizePicks + Underdog).
    """
    if not SGO_API_KEY:
        return [], {}
    books = books or dict(SGO_BOOKMAKERS)
    league_ids = [SGO_LEAGUE_MAP[s] for s in sports if s in SGO_LEAGUE_MAP]
    if not league_ids:
        return [], {}

    session = session or requests.Session()
    params = {
        "apiKey": SGO_API_KEY,
        "leagueID": ",".join(sorted(set(league_ids))),
        "oddsAvailable": "true",
    }
    try:
        resp = session.get(f"{SGO_BASE_URL}/events", params=params, timeout=timeout)
    except requests.RequestException as e:
        return [], {"SportsGameOdds": f"unreachable: {e}"}
    if resp.status_code != 200:
        return [], {"SportsGameOdds": f"HTTP {resp.status_code}"}
    try:
        payload = resp.json()
    except ValueError as e:
        return [], {"SportsGameOdds": f"bad JSON: {e}"}

    events = payload.get("data") or []
    props = parse_events(events, books)
    log(f"SportsGameOdds: {len(props)} props across {len(league_ids)} leagues")
    return props, {}
