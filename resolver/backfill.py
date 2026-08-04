"""Registry backfill from the authoritative MLB Stats API (40-man aware).

Fetch-all-then-write: if the critical teams fetch fails, the registry is untouched.
Roster/schedule fetch failures degrade to 'partial' (recorded, not fatal). Writes
happen in one transaction (commit-all / rollback-all). Idempotent upserts. Bootstrap
seed is superseded by authoritative data. Stale (unseen 40-man) players are marked
ineligible. Rolling schedule window (env-configurable).
"""
import os
import datetime

from . import mlb_api
from .mlb_api import MlbApiError
from .aliases import generate_player_aliases


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def schedule_window(ref=None, past=None, future=None):
    ref = ref or datetime.date.today()
    past = int(os.environ.get("PROEDGE_SCHEDULE_PAST_DAYS", 3 if past is None else past))
    future = int(os.environ.get("PROEDGE_SCHEDULE_FUTURE_DAYS", 7 if future is None else future))
    return [(ref + datetime.timedelta(days=d)).isoformat() for d in range(-past, future + 1)]


def full_refresh(reg, api=mlb_api, dates=None, now=None):
    """40-man teams + rosters (+ rolling schedule window). Returns a report."""
    now = now or _now()
    if dates is None:
        dates = schedule_window()
    report = {"status": "ok", "teams": 0, "players_total": 0, "players_active": 0,
              "players_injured": 0, "players_inactive": 0, "aliases": 0, "events": 0,
              "transactions": 0, "incomplete_teams": [], "stale_ineligible": 0,
              "api_calls": 0, "errors": [], "missing_fields": []}

    # 1. teams (CRITICAL) — on failure, nothing is written.
    try:
        teams = api.fetch_teams(); report["api_calls"] += 1
    except MlbApiError as ex:
        report["status"] = "failed"; report["errors"].append(f"teams fetch failed: {ex}")
        reg.set_meta("last_refresh_status", "failed")
        return report
    abbrev_by_id = {t["mlb_id"]: t["abbrev"] for t in teams}

    # 2. 40-man rosters (partial-tolerant)
    players, seen = [], set()
    for t in teams:
        try:
            roster = api.fetch_40man_roster(t["mlb_id"]); report["api_calls"] += 1
        except MlbApiError as ex:
            report["incomplete_teams"].append(t["abbrev"])
            report["errors"].append(f"roster {t['abbrev']} failed: {ex}")
            continue
        for p in roster:
            players.append((p, t["abbrev"]))
            seen.add("mlb-p-%s" % p["person_id"])
            if not p.get("position"):
                report["missing_fields"].append(f"{p['name']}: position")

    # 3. schedule window (partial-tolerant)
    events = []
    for d in dates:
        try:
            events += api.fetch_schedule(d); report["api_calls"] += 1
        except MlbApiError as ex:
            report["errors"].append(f"schedule {d} failed: {ex}")

    too_partial = len(report["incomplete_teams"]) > len(teams) // 2

    # 4. WRITE — one transaction (registry preserved on any write error).
    try:
        with reg.conn:
            reg.conn.execute("DELETE FROM entities WHERE source='seed'")
            reg.conn.execute("DELETE FROM teams WHERE source='seed'")
            reg.conn.execute("DELETE FROM aliases WHERE source='seed'")
            for t in teams:
                reg.upsert_team(t["mlb_id"], t["name"], t["abbrev"], now)
            for p, ab in players:
                reg.upsert_player_full(p["person_id"], p["name"], ab, p.get("position"),
                                       p.get("jersey"), p.get("roster_status"), p.get("active"),
                                       p.get("injury_status"), p.get("transaction_status"),
                                       p.get("eligible", True), now)
                eid = "mlb-p-%s" % p["person_id"]
                for alias, atype, conf in generate_player_aliases(p["name"]):
                    reg.add_alias(alias, eid, alias_type=atype, source="deterministic",
                                  confidence=conf, updated=now)
            if not too_partial:
                report["stale_ineligible"] = reg.mark_ineligible_unseen(seen, now)
            for e in events:
                home = abbrev_by_id.get(e["home_mlb_id"])
                away = abbrev_by_id.get(e["away_mlb_id"])
                reg.upsert_event("mlb-e-%s" % e["game_pk"], e["season"], e["game_date"],
                                 e["status"], ("mlb-t-" + home) if home else None,
                                 ("mlb-t-" + away) if away else None, e["venue"],
                                 e["double_header"], e["game_number"], now)
    except Exception as ex:  # noqa: BLE001
        report["status"] = "failed"; report["errors"].append(f"write failed (rolled back): {ex}")
        reg.set_meta("last_refresh_status", "failed")
        return report

    report["teams"] = len(teams)
    report["players_total"] = len(players)
    report["players_active"] = sum(1 for p, _ in players if p.get("active"))
    report["players_injured"] = sum(1 for p, _ in players if p.get("injury_status"))
    report["players_inactive"] = sum(1 for p, _ in players if not p.get("active"))
    report["events"] = len(events)
    report["aliases"] = reg._count("aliases")
    report["transactions"] = reg.conn.execute(
        "SELECT COUNT(*) c FROM entities WHERE previous_team_id IS NOT NULL "
        "AND status_updated_at=?", (now,)).fetchone()["c"]
    if report["incomplete_teams"]:
        report["status"] = "partial"
        if too_partial:
            report["errors"].append("too many roster failures — skipped stale marking")
    reg.set_meta("last_successful_refresh", now)
    reg.set_meta("last_refresh_status", report["status"])
    return report


def incremental_refresh(reg, api=mlb_api, dates=None, team_mlb_ids=None, now=None):
    """Transaction/schedule refresh without a full rebuild. Re-pulls the given team
    40-man rosters (trades/call-ups/IL moves) + schedule for `dates`. Idempotent."""
    now = now or _now()
    if dates is None:
        dates = schedule_window()
    report = {"status": "ok", "events": 0, "players": 0, "transactions": 0,
              "api_calls": 0, "errors": []}
    abbrev_by_id = reg.abbrev_by_mlb_id()
    try:
        with reg.conn:
            for tid in (team_mlb_ids or []):
                try:
                    roster = api.fetch_40man_roster(tid); report["api_calls"] += 1
                except MlbApiError as ex:
                    report["errors"].append(f"roster {tid} failed: {ex}"); continue
                ab = abbrev_by_id.get(tid)
                for p in roster:
                    reg.upsert_player_full(p["person_id"], p["name"], ab, p.get("position"),
                                           p.get("jersey"), p.get("roster_status"), p.get("active"),
                                           p.get("injury_status"), p.get("transaction_status"),
                                           p.get("eligible", True), now)
                    report["players"] += 1
            for d in dates:
                try:
                    for e in api.fetch_schedule(d):
                        report["api_calls"] += 1
                        home = abbrev_by_id.get(e["home_mlb_id"])
                        away = abbrev_by_id.get(e["away_mlb_id"])
                        reg.upsert_event("mlb-e-%s" % e["game_pk"], e["season"], e["game_date"],
                                         e["status"], ("mlb-t-" + home) if home else None,
                                         ("mlb-t-" + away) if away else None, e["venue"],
                                         e["double_header"], e["game_number"], now)
                        report["events"] += 1
                except MlbApiError as ex:
                    report["errors"].append(f"schedule {d} failed: {ex}")
    except Exception as ex:  # noqa: BLE001
        report["status"] = "failed"; report["errors"].append(str(ex))
        return report
    report["transactions"] = reg.conn.execute(
        "SELECT COUNT(*) c FROM entities WHERE previous_team_id IS NOT NULL "
        "AND status_updated_at=?", (now,)).fetchone()["c"]
    if report["errors"]:
        report["status"] = "partial"
    reg.set_meta("last_incremental_refresh", now)
    return report
