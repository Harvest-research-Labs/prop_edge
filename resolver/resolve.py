"""Entity resolution + schedule join + participation + mandatory gate.

IDENTITY (who is this) is separate from PARTICIPATION (will they play). Pricing must
never collapse the two. Covered = MLB (40-man). Opponents are only returned from a
verified schedule event, never inferred from a team name alone.
"""
import os
import difflib
import datetime

from config import canonical_stat
from .normalize import normalize_name, as_initial_surname, canonical_team
from .registry import get_registry

SPORT_LEAGUE = {"MLB": "MLB"}
HIGH, MEDIUM = 0.90, 0.60


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def gate_mode():
    """PROEDGE_MLB_RESOLUTION_GATE = off | advisory | required (default advisory)."""
    m = os.environ.get("PROEDGE_MLB_RESOLUTION_GATE", "advisory").lower()
    return m if m in ("off", "advisory", "required") else "advisory"


def _recent(ts, now, hours=48):
    try:
        a = datetime.datetime.fromisoformat(ts)
        b = datetime.datetime.fromisoformat(now)
        return abs((b - a).total_seconds()) <= hours * 3600
    except Exception:  # noqa: BLE001
        return False


def _aliases_for(reg, entity_id):
    return [r["alias_norm"] for r in reg.conn.execute(
        "SELECT alias_norm FROM aliases WHERE entity_id=?", (entity_id,)).fetchall()]


def _core(q, reg):
    warnings = []
    league = SPORT_LEAGUE.get((q.get("sport") or "").upper())
    if not league:
        return None, 0.0, "unsupported_sport", \
            [f"sport '{q.get('sport')}' not supported in registry yet (MLB-first scope)"], []

    if q.get("entity_id"):
        e = reg.get_by_id(q["entity_id"])
        if e:
            return e, 1.0, "canonical_id", [], []
        warnings.append("provided entity_id not found; falling back to name")

    player = q.get("player") or ""
    norm = normalize_name(player)
    candidate, base, method, cands = None, 0.0, None, []

    exact = reg.find_exact(norm, league)
    if len(exact) == 1:
        candidate, base, method = exact[0], 0.97, "exact_name"
    elif len(exact) > 1:
        cands = exact
    if candidate is None and not cands:
        al = reg.find_by_alias(norm, league)
        if len(al) == 1:
            candidate, base, method = al[0], 0.90, "alias"
        elif len(al) > 1:
            cands = al
    if candidate is None and not cands:
        isur = as_initial_surname(player)
        if isur:
            ini = reg.find_by_initial_surname(isur[0], isur[1], league)
            if len(ini) == 1:
                candidate, base, method = ini[0], 0.90, "initial_surname"
            elif len(ini) > 1:
                cands = ini
    if candidate is None and cands:
        tid = reg.team_id_for(q.get("team")) if q.get("team") else None
        narrowed = [c for c in cands if tid and c["team_id"] == tid] if tid else []
        if len(narrowed) == 1:
            candidate, base, method = narrowed[0], 0.92, "team_constrained"
            if q.get("event_id") or q.get("event_start"):
                base, method = 0.94, "event_constrained"

    if candidate is not None:
        conf = base
        if q.get("team"):
            tid = reg.team_id_for(q["team"])
            if tid and candidate["team_id"] != tid:
                if candidate["previous_team_id"] == tid:
                    conf, method = 0.85, "prev_team_history"
                    warnings.append("screenshot team matches recent history, not the current "
                                    "roster — confirm timing before pricing")
                else:
                    conf, method = 0.55, "name_team_mismatch"
                    warnings.append(f"name matched but team '{q['team']}' != registry "
                                    f"team '{candidate['team_name']}'")
        return candidate, conf, method, warnings, []

    if not cands:
        best = None
        for row in reg.all_names(league):
            r = difflib.SequenceMatcher(None, norm, row["canonical_name"]).ratio()
            if best is None or r > best[0]:
                best = (r, row)
        if best and best[0] >= 0.85:
            e = reg.get_by_id(best[1]["entity_id"])
            conf = min(0.89, round(0.60 + (best[0] - 0.85) * 2, 3))
            return e, conf, "fuzzy", warnings + ["fuzzy match — review"], []

    if cands:
        return None, 0.5, "ambiguous", \
            warnings + ["multiple active matches; disambiguate by team or event"], \
            [dict(c) for c in cands]
    return None, 0.0, "no_match", warnings + ["no registry match"], []


# --- schedule join ----------------------------------------------------------
def _event_out(e, team_id):
    opp = e["away_team_id"] if e["home_team_id"] == team_id else e["home_team_id"]
    warns = []
    st = (e["event_status"] or "")
    if st and st not in ("Scheduled", "Pre-Game", "Warmup", "In Progress", "Final", "Game Over"):
        warns.append(f"event status '{st}' (postponed/suspended/rescheduled/cancelled) — verify")
    return {"event_id": e["event_id"], "opponent_id": opp,
            "event_status": st or None, "event_start": e["event_start"]}, warns


def _resolve_event(reg, team_id, q):
    warns = []
    out = {"event_id": None, "opponent_id": None, "event_status": None,
           "event_start": q.get("event_start")}
    if not team_id:
        return out, warns
    if q.get("event_id"):
        r = reg.conn.execute("SELECT * FROM events WHERE event_id=?", (q["event_id"],)).fetchone()
        if r:
            o, w = _event_out(r, team_id); return o, w
        warns.append("provided event_id not found in schedule")
    date_prefix = (q.get("event_start") or "")[:10] or None
    if not date_prefix:
        return out, warns          # no date supplied -> don't guess an event / opponent
    evs = reg.find_events(team_id, date_prefix)
    if len(evs) == 1:
        o, w = _event_out(evs[0], team_id); return o, warns + w
    if len(evs) > 1:
        gn = q.get("game_number")
        if gn:
            for e in evs:
                if e["game_number"] == gn:
                    o, w = _event_out(e, team_id); return o, warns + w
        warns.append("multiple events for team/date (doubleheader) — provide event_id or "
                     "game_number; opponent not inferred")
        return out, warns
    if date_prefix:
        warns.append("no scheduled event for team on that date — opponent not inferred "
                     "(possible stale screenshot or start-time change)")
    return out, warns


def _registry_stale(reg, now, max_age_hours=48):
    if reg.get_meta("last_refresh_status") == "failed":
        return True
    last = reg.get_meta("last_successful_refresh")
    if not last:
        return False
    try:
        return (datetime.datetime.fromisoformat(now)
                - datetime.datetime.fromisoformat(last)).total_seconds() > max_age_hours * 3600
    except Exception:  # noqa: BLE001
        return False


def _participation(entity, now):
    """-> (participation_confidence, participation_status). Identity-independent."""
    if entity is None:
        return None, None
    if entity["injury_status"]:
        return 0.20, "injured_list"
    if not entity["active"]:
        return 0.35, "not_on_active_roster"
    if entity["previous_team_id"] and entity["status_updated_at"] \
            and _recent(entity["status_updated_at"], now):
        return 0.70, "recently_activated_or_moved"
    return 0.95, "active"


def gate(sport, status, participation_conf, injured):
    league = SPORT_LEAGUE.get((sport or "").upper())
    if not league:
        return {"action": "advisory", "reason": f"'{sport or 'unknown'}' not a covered league — advisory only"}
    if status != "resolved":
        return {"action": "confirm" if status == "needs_review" else "stop",
                "reason": f"identity {status}"}
    if injured or (participation_conf is not None and participation_conf < 0.30):
        return {"action": "stop",
                "reason": "verified IL/inactive — do not price unless a current participation source confirms availability"}
    if participation_conf is not None and participation_conf < 0.90:
        return {"action": "confirm",
                "reason": "uncertain participation (recent move / not on active roster) — confirm before pricing"}
    return {"action": "proceed", "reason": "high identity + acceptable participation"}


def resolve_pick(pick, reg=None, now=None, registry_stale=None):
    reg = reg or get_registry()
    now = now or _now_iso()
    q = dict(pick)
    entity, conf, method, warns, cands = _core(q, reg)

    part_conf, part_status = _participation(entity, now)
    injured = bool(entity and entity["injury_status"]) if entity is not None else False
    if injured:
        warns.append(f"participation: {entity['injury_status']} — high identity, low participation")

    event = {"event_id": q.get("event_id"), "opponent_id": None,
             "event_status": None, "event_start": q.get("event_start")}
    if entity is not None:
        ev, ewarn = _resolve_event(reg, entity["team_id"], q)
        event.update(ev); warns += ewarn
    if q.get("opponent") and event["opponent_id"] is None:
        warns.append("opponent provided but no verified event — not used as authoritative")

    if registry_stale is None:
        registry_stale = _registry_stale(reg, now)
    if registry_stale:
        warns.append("registry data may be stale (last refresh old or failed) — confidence lowered")
        conf = min(conf, 0.80)

    if conf >= HIGH:
        status = "needs_review" if method.startswith("name_team_mismatch") else "resolved"
    elif conf >= MEDIUM or method.startswith(("ambiguous", "name_team_mismatch", "fuzzy",
                                              "prev_team_history")):
        status = "needs_review"
    else:
        status = "unresolved"

    return {
        "entity_id": entity["entity_id"] if entity else None,
        "entity_type": entity["entity_type"] if entity else "player",
        "canonical_name": entity["canonical_name"] if entity else normalize_name(q.get("player") or ""),
        "display_name": entity["display_name"] if entity else q.get("player"),
        "sport": q.get("sport"),
        "league": SPORT_LEAGUE.get((q.get("sport") or "").upper()),
        "team_id": entity["team_id"] if entity else (reg.team_id_for(q.get("team")) if q.get("team") else None),
        "team_name": entity["team_name"] if entity else canonical_team(q.get("team")),
        "current_team_id": entity["current_team_id"] if entity else None,
        "previous_team_id": entity["previous_team_id"] if entity else None,
        "position": entity["position"] if entity else None,
        "jersey_number": entity["jersey_number"] if entity else None,
        "active": bool(entity["active"]) if entity is not None else None,
        "eligible": bool(entity["eligible"]) if entity is not None else None,
        "roster_status": entity["roster_status"] if entity else None,
        "injury_status": entity["injury_status"] if entity else None,
        "transaction_status": entity["transaction_status"] if entity else None,
        "opponent_id": event["opponent_id"],
        "event_id": event["event_id"],
        "event_start": event["event_start"],
        "event_status": event["event_status"],
        "aliases": _aliases_for(reg, entity["entity_id"]) if entity else [],
        "source": entity["source"] if entity else None,
        "source_updated_at": entity["source_updated_at"] if entity else None,
        "resolution_confidence": round(conf, 3),
        "participation_confidence": (round(part_conf, 3) if part_conf is not None else None),
        "participation_status": part_status,
        "resolution_method": method,
        "needs_review": status != "resolved",
        "warnings": warns,
        "status": status,
        "registry_stale": bool(registry_stale),
        "gate": gate(q.get("sport"), status, part_conf, injured),
        "candidates": cands,
        "player": entity["display_name"] if entity else q.get("player"),
        "stat": q.get("stat"),
        "canonical_stat": canonical_stat(q.get("stat") or ""),
        "line": q.get("line"),
        "side": q.get("side", "more"),
    }
