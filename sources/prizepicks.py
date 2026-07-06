"""PrizePicks source.

Fetches /projections per league and joins each projection to its player and
game out of the JSON:API `included` array. Emits one Prop per projection,
carrying the goblin/demon/standard flavor.
"""

import time
import requests

from config import (
    PRIZEPICKS_PROJECTIONS_URL,
    PRIZEPICKS_HEADERS,
    PRIZEPICKS_LEAGUES,
    PRIZEPICKS_THROTTLE_SEC,
)
from sources.base import Prop


def _index_included(included):
    """Build {type: {id: attributes}} plus a game->teams lookup."""
    by_type = {}
    for item in included:
        by_type.setdefault(item["type"], {})[item["id"]] = item.get("attributes", {})
    return by_type


def fetch_league(sport: str, league_id: int, session: requests.Session, timeout=20):
    """Fetch and normalize all projections for one PrizePicks league."""
    resp = session.get(
        PRIZEPICKS_PROJECTIONS_URL,
        params={"league_id": league_id, "per_page": 1000, "single_stat": "true"},
        headers=PRIZEPICKS_HEADERS,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"PrizePicks {sport} HTTP {resp.status_code}")
    data = resp.json()
    included = _index_included(data.get("included", []))
    players = included.get("new_player", {})
    games = included.get("game", {})
    teams = included.get("team", {})

    props = []
    for proj in data.get("data", []):
        a = proj.get("attributes", {})
        rel = proj.get("relationships", {})
        pid = (rel.get("new_player", {}).get("data") or {}).get("id")
        player = players.get(pid, {})

        # opponent: PrizePicks puts "vs/@ OPP" in the description sometimes,
        # otherwise derive from the game's metadata if present.
        opponent = a.get("description") or None
        gid = (rel.get("game", {}).get("data") or {}).get("id")
        if gid and gid in games and not opponent:
            opponent = games[gid].get("metadata", {}).get("game_info")

        props.append(
            Prop(
                book="PrizePicks",
                sport=sport,
                player=player.get("display_name") or player.get("name") or "?",
                team=player.get("team") or player.get("team_name"),
                opponent=opponent,
                stat=a.get("stat_type") or a.get("stat_display_name") or "",
                line=_as_float(a.get("line_score")),
                flavor=(a.get("odds_type") or "standard"),
                start_time=a.get("start_time") or a.get("board_time"),
                combo=bool(player.get("combo")),
                image_url=player.get("image_url"),
                source_id=proj.get("id"),
            )
        )
    return props


def fetch(sports, session=None, throttle=PRIZEPICKS_THROTTLE_SEC, log=lambda m: None):
    """Fetch multiple sports. Returns (props, errors)."""
    session = session or requests.Session()
    out, errors = [], {}
    first = True
    for sport in sports:
        lid = PRIZEPICKS_LEAGUES.get(sport)
        if lid is None:
            continue
        if not first:
            time.sleep(throttle)  # avoid 429
        first = False
        try:
            rows = fetch_league(sport, lid, session)
            out.extend(rows)
            log(f"PrizePicks {sport}: {len(rows)} props")
        except Exception as e:  # noqa: BLE001 - surface per-sport, keep going
            errors[f"PrizePicks/{sport}"] = str(e)
            log(f"PrizePicks {sport}: ERROR {e}")
    return out, errors


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
