"""Entity resolution pipeline + schedule join + mandatory-gate.

Order: canonical-ID -> exact name+league -> alias -> initial+surname ->
team/opponent-constrained -> event-time-constrained -> fuzzy -> manual review.
A real schedule join sets the matched event + verified opponent. Opponents are
never inferred from a team name alone without a verified event.
"""
import difflib
import datetime

from config import canonical_stat
from .normalize import normalize_name, as_initial_surname, canonical_team
from .registry import get_registry

SPORT_LEAGUE = {"MLB": "MLB"}   # first supported scope
HIGH, MEDIUM = 0.90, 0.60


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _aliases_for(reg, entity_id):
    return [r["alias_norm"] for r in reg.conn.execute(
        "SELECT alias_norm FROM aliases WHERE entity_id=?", (entity_id,)).fetchall()]


def _core(q, reg):
    """Return (entity_row|None, confidence, method, warnings, candidates)."""
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
    return {"event_id": e["event_id"], "opponent_id": opp,
            "event_status": e["event_status"], "event_start": e["event_start"]}


def _resolve_event(reg, team_id, q):
    """Verify the event via the schedule; never infer opponent from a team alone."""
    warns = []
    out = {"event_id": None, "opponent_id": None, "event_status": None,
           "event_start": q.get("event_start")}
    if not team_id:
        return out, warns
    if q.get("event_id"):
        r = reg.conn.execute("SELECT * FROM events WHERE event_id=?", (q["event_id"],)).fetchone()
        if r:
            return _event_out(r, team_id), warns
        warns.append("provided event_id not found in schedule")
    date_prefix = (q.get("event_start") or "")[:10] or None
    evs = reg.find_events(team_id, date_prefix)
    if len(evs) == 1:
        return _event_out(evs[0], team_id), warns
    if len(evs) > 1:                       # doubleheader / multiple
        gn = q.get("game_number")
        if gn:
            for e in evs:
                if e["game_number"] == gn:
                    return _event_out(e, team_id), warns
        warns.append("multiple events for team/date (doubleheader) — provide event_id or "
                     "game_number; opponent not inferred")
        return out, warns
    if date_prefix:
        warns.append("no scheduled event for team on that date — opponent not inferred "
                     "(possible stale screenshot)")
    return out, warns


def _registry_stale(reg, now, max_age_hours=48):
    if reg.get_meta("last_refresh_status") == "failed":
        return True
    last = reg.get_meta("last_successful_refresh")
    if not last:
        return False   # seed/bootstrap only — not a stale backfill
    try:
        dt = datetime.datetime.fromisoformat(last)
        n = datetime.datetime.fromisoformat(now)
        return (n - dt).total_seconds() > max_age_hours * 3600
    except Exception:  # noqa: BLE001
        return False


def gate(sport, status, has_entity):
    """Mandatory-gate decision for pricing (only meaningful for covered leagues)."""
    league = SPORT_LEAGUE.get((sport or "").upper())
    if not league:
        return {"action": "advisory", "reason": f"'{sport or 'unknown'}' not a covered league — advisory only"}
    if status == "resolved":
        return {"action": "proceed", "reason": "covered MLB entity, high confidence"}
    if status == "needs_review":
        return {"action": "confirm", "reason": "covered MLB entity, medium confidence — confirm before pricing"}
    return {"action": "stop", "reason": "covered MLB entity, low/no confidence — do not price"}


def resolve_pick(pick, reg=None, now=None, registry_stale=None):
    reg = reg or get_registry()
    now = now or _now_iso()
    q = dict(pick)
    entity, conf, method, warns, cands = _core(q, reg)

    # stale-screenshot / roster-move signal
    if entity is not None and (entity["active"] == 0):
        warns.append("player not on the current active roster — possible stale screenshot or roster move")
        conf = min(conf, 0.75)
        method = (method or "") + "+inactive"

    # real schedule join (verified event + opponent)
    event = {"event_id": q.get("event_id"), "opponent_id": None,
             "event_status": None, "event_start": q.get("event_start")}
    if entity is not None:
        ev, ewarn = _resolve_event(reg, entity["team_id"], q)
        event.update(ev)
        warns += ewarn
    if q.get("opponent") and event["opponent_id"] is None:
        warns.append("opponent provided but no verified event — not used as authoritative")

    # upstream staleness
    if registry_stale is None:
        registry_stale = _registry_stale(reg, now)
    if registry_stale:
        warns.append("registry data may be stale (last refresh old or failed) — confidence lowered")
        conf = min(conf, 0.80)

    if conf >= HIGH:
        status = "needs_review" if method.startswith("name_team_mismatch") else "resolved"
    elif conf >= MEDIUM or method.startswith(("ambiguous", "name_team_mismatch", "fuzzy")):
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
        "position": entity["position"] if entity else None,
        "jersey_number": entity["jersey_number"] if entity else None,
        "active": bool(entity["active"]) if entity is not None else None,
        "roster_status": entity["roster_status"] if entity else None,
        "opponent_id": event["opponent_id"],
        "event_id": event["event_id"],
        "event_start": event["event_start"],
        "event_status": event["event_status"],
        "aliases": _aliases_for(reg, entity["entity_id"]) if entity else [],
        "source": entity["source"] if entity else None,
        "source_updated_at": entity["source_updated_at"] if entity else None,
        "resolution_confidence": round(conf, 3),
        "resolution_method": method,
        "needs_review": status != "resolved",
        "warnings": warns,
        "status": status,
        "registry_stale": bool(registry_stale),
        "gate": gate(q.get("sport"), status, entity is not None),
        "candidates": cands,
        # carried through for pricing (compatible with the UI)
        "player": entity["display_name"] if entity else q.get("player"),
        "stat": q.get("stat"),
        "canonical_stat": canonical_stat(q.get("stat") or ""),
        "line": q.get("line"),
        "side": q.get("side", "more"),
    }
