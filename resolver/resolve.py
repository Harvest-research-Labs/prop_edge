"""Entity resolution pipeline.

Order: canonical-ID → exact name+league → alias → team/opponent-constrained →
event-time-constrained → fuzzy → manual review. Medium/low-confidence entities
carry needs_review=True and must NOT proceed silently into pricing.
"""
import difflib

from config import canonical_stat
from .normalize import normalize_name, as_initial_surname, canonical_team
from .registry import get_registry

SPORT_LEAGUE = {"MLB": "MLB"}   # first supported scope

HIGH, MEDIUM = 0.90, 0.60       # confidence bands


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

    exact = reg.find_exact(norm, league)          # 2. exact name + league
    if len(exact) == 1:
        candidate, base, method = exact[0], 0.97, "exact_name"
    elif len(exact) > 1:
        cands = exact

    if candidate is None and not cands:           # 3. alias
        al = reg.find_by_alias(norm, league)
        if len(al) == 1:
            candidate, base, method = al[0], 0.90, "alias"
        elif len(al) > 1:
            cands = al

    if candidate is None and not cands:           # initials (e.g. "A. Judge")
        isur = as_initial_surname(player)
        if isur:
            ini = reg.find_by_initial_surname(isur[0], isur[1], league)
            if len(ini) == 1:
                candidate, base, method = ini[0], 0.90, "initial_surname"
            elif len(ini) > 1:
                cands = ini

    if candidate is None and cands:               # 4/5. team + event constrained
        tid = reg.team_id_for(q.get("team")) if q.get("team") else None
        narrowed = [c for c in cands if tid and c["team_id"] == tid] if tid else []
        if len(narrowed) == 1:
            candidate, base, method = narrowed[0], 0.92, "team_constrained"
            if q.get("event_id") or q.get("event_start"):
                base, method = 0.94, "event_constrained"

    if candidate is not None:                      # single candidate: verify team
        conf = base
        if q.get("team"):
            tid = reg.team_id_for(q["team"])
            if tid and candidate["team_id"] != tid:
                conf, method = 0.55, "name_team_mismatch"
                warnings.append(f"name matched but team '{q['team']}' != registry "
                                f"team '{candidate['team_name']}'")
        return candidate, conf, method, warnings, []

    # 6. fuzzy (only when there wasn't a hard multi-candidate tie)
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

    if cands:                                      # 7. manual review — ambiguous
        return None, 0.5, "ambiguous", \
            warnings + ["multiple active matches; disambiguate by team or event"], \
            [dict(c) for c in cands]
    return None, 0.0, "no_match", warnings + ["no registry match"], []


def resolve_pick(pick, reg=None):
    """pick: dict with player/stat/line/side/sport[/team/opponent/event_*/entity_id].
    Returns the full resolved-entity record + a status band."""
    reg = reg or get_registry()
    q = dict(pick)
    entity, conf, method, warns, cands = _core(q, reg)
    if conf >= HIGH:
        status = "needs_review" if method == "name_team_mismatch" else "resolved"
    elif conf >= MEDIUM or method in ("ambiguous", "name_team_mismatch", "fuzzy"):
        status = "needs_review"   # flag for a human, never silently drop or auto-price
    else:
        status = "unresolved"     # no match / unsupported sport

    return {
        "entity_id": entity["entity_id"] if entity else None,
        "entity_type": entity["entity_type"] if entity else "player",
        "canonical_name": entity["canonical_name"] if entity else normalize_name(q.get("player") or ""),
        "display_name": entity["display_name"] if entity else q.get("player"),
        "sport": q.get("sport"),
        "league": SPORT_LEAGUE.get((q.get("sport") or "").upper()),
        "team_id": entity["team_id"] if entity else (reg.team_id_for(q.get("team")) if q.get("team") else None),
        "team_name": entity["team_name"] if entity else canonical_team(q.get("team")),
        "opponent_id": reg.team_id_for(q.get("opponent")) if q.get("opponent") else None,
        "event_id": q.get("event_id"),
        "event_start": q.get("event_start"),
        "aliases": _aliases_for(reg, entity["entity_id"]) if entity else [],
        "source": entity["source"] if entity else None,
        "source_updated_at": entity["source_updated_at"] if entity else None,
        "resolution_confidence": round(conf, 3),
        "resolution_method": method,
        "needs_review": status != "resolved",
        "warnings": warns,
        "status": status,
        "candidates": cands,
        # carried through for pricing (kept compatible with the current UI)
        "player": entity["display_name"] if entity else q.get("player"),
        "stat": q.get("stat"),
        "canonical_stat": canonical_stat(q.get("stat") or ""),
        "line": q.get("line"),
        "side": q.get("side", "more"),
    }
