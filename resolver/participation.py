"""Participation Service — MLB lineup + probable-pitcher validation.

Deliberately SEPARATE from identity resolution (resolver/resolve.py). Identity answers
"who is this"; participation answers "will they actually take the field". A rostered
player is never treated as confirmed to participate.

Primary authoritative source: MLB Stats API (posted lineups + probable pitchers). A
secondary source can supply *expected* (projected) lineups when licensing/ToS permit;
none is bundled. We never scrape protected sportsbook endpoints. Higher-priority
(confirmed) sources win conflicts; disagreements are flagged, not silently resolved.

Statuses: confirmed_starting, expected_starting, bench, probable_pitcher,
confirmed_pitcher, opener, bulk_relief, scratched, inactive, unknown.
"""
import os
import datetime

from config import canonical_stat as _canon
from . import mlb_api
from .mlb_api import MlbApiError

# Game states
_DEAD = {"Postponed", "Cancelled", "Canceled", "Suspended"}          # -> stop
_DELAYED = {"Delayed", "Delayed Start"}                               # -> hold + warn
_STARTED = {"Live", "Final"}                                         # abstractGameState


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def participation_gate_mode():
    """PROEDGE_MLB_PARTICIPATION_GATE = off | advisory | required (default advisory)."""
    m = os.environ.get("PROEDGE_MLB_PARTICIPATION_GATE", "advisory").lower()
    return m if m in ("off", "advisory", "required") else "advisory"


def _post_hours():
    return float(os.environ.get("PROEDGE_LINEUP_POST_HOURS", "3"))


def _fresh_minutes():
    return float(os.environ.get("PROEDGE_PARTICIPATION_FRESH_MIN", "45"))


def _person_id(entity_id):
    if entity_id and entity_id.startswith("mlb-p-"):
        try:
            return int(entity_id.rsplit("-", 1)[-1])
        except ValueError:
            return None
    return None


def _dt(ts):
    try:
        return datetime.datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _age_minutes(ts, now):
    a, b = _dt(ts), _dt(now)
    return None if not (a and b) else (b - a).total_seconds() / 60.0


def classify(person_id, side, game, now, canonical_stat=None):
    """Pure classifier. `side` in {home, away}. `game` is a merged record (primary +
    optional secondary + prior snapshot). Returns the participation output dict."""
    opp = "away" if side == "home" else "home"
    my_prob = game.get(f"{side}_probable_id")
    opp_prob = game.get(f"{opp}_probable_id")
    my_lineup = game.get(f"{side}_lineup") or []
    my_expected = game.get(f"expected_{side}") or []
    prev_prob = game.get(f"prev_{side}_probable_id")
    prev_lineup = game.get(f"prev_{side}_lineup") or []
    posted = bool(my_lineup)
    status_state = game.get("status")
    abstract = game.get("abstract_state")
    src_ts = game.get("source_updated_at")

    out = {"participation_status": "unknown", "participation_confidence": 0.5,
           "lineup_status": "pending", "batting_order": None, "pitcher_role": None,
           "probable_pitcher_id": (f"mlb-p-{my_prob}" if my_prob else None),
           "confirmed_starter": False, "scratched": False, "matchup_stale": False,
           "game_status": status_state, "conflicting": False,
           "source": game.get("source"), "source_updated_at": src_ts, "warnings": []}

    # opposing probable pitcher changed -> matchup-dependent projections are invalid
    if prev_prob is not None and opp_prob is not None:
        prev_opp = game.get(f"prev_{opp}_probable_id")
        if prev_opp is not None and prev_opp != opp_prob:
            out["matchup_stale"] = True
            out["warnings"].append("opposing probable pitcher changed — matchup projection "
                                   "invalidated; reprice")

    # 1. dead game overrides everything
    if status_state in _DEAD:
        out.update(participation_status="inactive", participation_confidence=0.02,
                   lineup_status="not_applicable")
        out["warnings"].append(f"game {status_state.lower()} — no participation")
        return _finalize(out, now, src_ts)

    is_pitcher = person_id is not None and person_id == my_prob
    pitcher_replaced = (person_id is not None and person_id == prev_prob
                        and person_id != my_prob)

    # 2. probable / confirmed starting pitcher
    if is_pitcher:
        out["pitcher_role"] = game.get(f"{side}_pitcher_role") or (
            "confirmed_pitcher" if abstract in _STARTED else "probable_pitcher")
        out["lineup_status"] = "not_applicable"
        if out["pitcher_role"] in ("opener", "bulk_relief"):
            out.update(participation_status=out["pitcher_role"],
                       participation_confidence=0.80)
            out["warnings"].append(f"{out['pitcher_role']} designation from source")
        elif abstract in _STARTED:
            out.update(participation_status="confirmed_pitcher",
                       participation_confidence=0.97, confirmed_starter=True)
        else:
            out.update(participation_status="probable_pitcher", participation_confidence=0.85)
            out["warnings"].append("probable (not confirmed) starting pitcher")
        if game.get(f"prev_{side}_probable_id") not in (None, my_prob):
            out["warnings"].append("probable pitcher changed for this side — reprice")
            out["matchup_stale"] = True
        if status_state in _DELAYED:
            out["warnings"].append(f"game {status_state.lower()}")
        return _finalize(out, now, src_ts)

    # a pitcher the user bet who has been replaced as the probable
    if pitcher_replaced:
        out.update(participation_status="scratched", participation_confidence=0.05,
                   scratched=True, matchup_stale=True, lineup_status="not_applicable",
                   pitcher_role="probable_pitcher")
        out["warnings"].append("probable pitcher changed — this pitcher is no longer listed "
                               "to start; matchup invalidated")
        return _finalize(out, now, src_ts)

    # 3. hitter — lineup logic
    conflict = posted and (person_id in my_expected) != (person_id in my_lineup) and bool(my_expected)
    if conflict:
        out["conflicting"] = True
        out["warnings"].append("sources disagree (confirmed lineup vs projected) — "
                               "authoritative posted lineup used")

    if posted and person_id in my_lineup:
        out.update(participation_status="confirmed_starting", participation_confidence=0.97,
                   lineup_status="posted", confirmed_starter=True,
                   batting_order=my_lineup.index(person_id) + 1)
    elif posted:                                              # lineup posted, player absent
        if person_id in prev_lineup:
            out.update(participation_status="scratched", participation_confidence=0.05,
                       scratched=True, lineup_status="posted")
            out["warnings"].append("was in a prior posted lineup, now absent — scratched")
        else:
            out.update(participation_status="bench", participation_confidence=0.15,
                       lineup_status="posted")
            out["warnings"].append("not in the posted lineup — benched / not starting")
    elif person_id in my_expected:                            # secondary projected starter
        out.update(participation_status="expected_starting", participation_confidence=0.80,
                   lineup_status="projected")
        out["warnings"].append("expected (projected) starter — lineup not yet officially posted")
    elif status_state in _DELAYED:
        out.update(participation_status="unknown", participation_confidence=0.5,
                   lineup_status="pending")
        out["warnings"].append(f"game {status_state.lower()} — lineup pending")
    else:                                                     # lineup not posted
        start = _dt(game.get("game_date"))
        overdue = start is not None and _dt(now) is not None and \
            _dt(now) >= start - datetime.timedelta(hours=_post_hours())
        if overdue:
            out.update(participation_status="unknown", participation_confidence=0.45,
                       lineup_status="overdue")
            out["warnings"].append("lineup overdue (past normal posting time, still unposted)")
        else:
            out.update(participation_status="unknown", participation_confidence=0.55,
                       lineup_status="pending")
            out["warnings"].append("lineup not yet posted (before normal posting time)")

    if status_state in _DELAYED:
        out["warnings"].append(f"game {status_state.lower()}")
    return _finalize(out, now, src_ts)


def _finalize(out, now, src_ts):
    age = _age_minutes(src_ts, now)
    if age is not None and age > _fresh_minutes():
        out["participation_confidence"] = round(min(out["participation_confidence"], 0.60), 3)
        out["warnings"].append(f"participation source is stale ({int(age)}m old > "
                               f"{int(_fresh_minutes())}m threshold) — treat cautiously")
    out["participation_confidence"] = round(out["participation_confidence"], 3)
    out["gate"] = participation_gate(out)
    return out


def participation_gate(part):
    st = part["participation_status"]
    gstatus = part.get("game_status")
    if gstatus in _DEAD:
        return {"action": "stop", "reason": f"game {gstatus.lower()}"}
    if gstatus in _DELAYED:
        return {"action": "confirm", "reason": "game delayed — hold and reconfirm before pricing"}
    if st in ("confirmed_starting", "confirmed_pitcher"):
        return {"action": "proceed", "reason": "confirmed to start"}
    if st in ("probable_pitcher", "expected_starting", "opener", "bulk_relief"):
        r = "expected/probable starter — proceed with visible warning; reconfirm near lock"
        return {"action": "proceed", "reason": r}
    if st in ("bench", "scratched", "inactive"):
        return {"action": "stop", "reason": f"{st} — not participating"}
    # unknown
    if part.get("lineup_status") == "overdue":
        return {"action": "confirm", "reason": "lineup overdue — hold until posted"}
    return {"action": "confirm", "reason": "lineup not yet posted — hold or reduce confidence"}


# --- source adapters --------------------------------------------------------
class MlbParticipationSource:
    """Primary, authoritative. Posted lineups + probable pitchers => confirmed."""
    name = "mlbstatsapi"
    priority = 1

    def __init__(self, api=mlb_api):
        self.api = api

    def fetch(self, date):
        return self.api.fetch_game_participation(date)


# --- orchestration ----------------------------------------------------------
def _pick_date(pick, now):
    return (pick.get("event_start") or now or "")[:10] or (now or "")[:10]


def evaluate(resolved_picks, reg, source=None, secondary=None, now=None):
    """Resolve participation for each already-identity-resolved pick.

    `resolved_picks`: dicts with entity_id, team_id, event_id?, event_start?, canonical_stat?.
    Fetches each needed date once from the primary (and optional secondary) source,
    persists a snapshot for scratch / pitcher-change detection, returns one output per pick
    aligned by index. Fetch failure preserves the last stored snapshot.
    """
    now = now or _now_iso()
    source = source or MlbParticipationSource()
    dates = sorted({_pick_date(p, now) for p in resolved_picks if p.get("entity_id")})

    by_pk, by_team_date, fetch_errors, fetched_ok = {}, {}, [], False
    for d in dates:
        try:
            recs = source.fetch(d)
            fetched_ok = True
        except MlbApiError as ex:
            fetch_errors.append(f"{source.name} {d}: {ex}")
            recs = []
        for r in recs:
            r["source"] = source.name
            r["source_updated_at"] = now
            by_pk[r["game_pk"]] = r
            for mlbid in (r.get("home_mlb_id"), r.get("away_mlb_id")):
                by_team_date.setdefault((mlbid, d), []).append(r)

    # secondary (expected/projected) overlay
    sec_by_pk = {}
    if secondary is not None:
        for d in dates:
            try:
                for r in secondary.fetch(d):
                    sec_by_pk[r["game_pk"]] = r
            except MlbApiError as ex:
                fetch_errors.append(f"{secondary.name} {d}: {ex}")

    outputs = []
    snapshots = {}
    for p in resolved_picks:
        eid = p.get("entity_id")
        pid = _person_id(eid)
        base = {"entity_id": eid, "player": p.get("player"),
                "stat": p.get("stat"), "sport": p.get("sport")}
        if not eid or p.get("sport") != "MLB":
            outputs.append({**base, "participation_status": "unknown",
                            "participation_confidence": None, "lineup_status": "not_applicable",
                            "batting_order": None, "pitcher_role": None,
                            "probable_pitcher_id": None, "confirmed_starter": False,
                            "scratched": False, "matchup_stale": False, "conflicting": False,
                            "source": None, "source_updated_at": None,
                            "gate": {"action": "advisory",
                                     "reason": "participation not evaluated (unresolved / non-MLB)"},
                            "warnings": ["participation not evaluated for this pick"]})
            continue

        team_mlb = reg.mlb_id_for_team(p.get("team_id")) if p.get("team_id") else None
        rec = _match_game(p, pid, team_mlb, by_pk, by_team_date, now)
        if rec is None:
            outputs.append({**base, "participation_status": "unknown",
                            "participation_confidence": 0.5, "lineup_status": "pending",
                            "batting_order": None, "pitcher_role": None,
                            "probable_pitcher_id": None, "confirmed_starter": False,
                            "scratched": False, "matchup_stale": False, "conflicting": False,
                            "source": source.name, "source_updated_at": now,
                            "gate": {"action": "confirm",
                                     "reason": "no matching game found for this pick — hold"},
                            "warnings": (["no scheduled game matched (doubleheader or missing "
                                          "event) — supply event_id"] if not fetch_errors else
                                         ["participation source refresh failed — last state "
                                          "unavailable; hold"])})
            continue

        side = "home" if team_mlb == rec.get("home_mlb_id") else "away"
        merged = dict(rec)
        prev = reg.get_game_participation("mlb-e-%s" % rec["game_pk"])
        if prev:
            merged["prev_home_probable_id"] = _int(prev.get("home_probable_id"))
            merged["prev_away_probable_id"] = _int(prev.get("away_probable_id"))
            merged["prev_home_lineup"] = [_int(x) for x in prev.get("home_lineup") or []]
            merged["prev_away_lineup"] = [_int(x) for x in prev.get("away_lineup") or []]
        sec = sec_by_pk.get(rec["game_pk"])
        if sec:
            merged["expected_home"] = sec.get("home_lineup") or sec.get("expected_home") or []
            merged["expected_away"] = sec.get("away_lineup") or sec.get("expected_away") or []
            merged["secondary_source"] = getattr(secondary, "name", "secondary")
        out = classify(pid, side, merged, now, canonical_stat=p.get("canonical_stat"))
        outputs.append({**base, **out})
        snapshots["mlb-e-%s" % rec["game_pk"]] = rec

    # persist fresh snapshots (only when we actually fetched) for next-refresh diffing
    if fetched_ok and snapshots:
        with reg.conn:
            for eid, rec in snapshots.items():
                reg.upsert_game_participation(
                    eid, rec.get("status"), rec.get("abstract_state"),
                    _s(rec.get("home_probable_id")), _s(rec.get("away_probable_id")),
                    rec.get("home_lineup"), rec.get("away_lineup"),
                    rec.get("lineup_posted"), now, source=source.name)
        reg.set_meta("last_participation_refresh", now)
    return outputs, {"dates": dates, "games": len(by_pk), "errors": fetch_errors,
                     "source": source.name, "fetched_ok": fetched_ok}


def _match_game(pick, pid, team_mlb, by_pk, by_team_date, now):
    if pick.get("event_id"):
        try:
            return by_pk.get(int(pick["event_id"].rsplit("-", 1)[-1]))
        except (ValueError, AttributeError):
            pass
    if team_mlb is not None:
        games = by_team_date.get((team_mlb, _pick_date(pick, now)), [])
        if len(games) == 1:
            return games[0]
        if len(games) > 1:                    # doubleheader without event_id
            return None
    return None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _s(v):
    return None if v is None else str(v)
