"""Registry backfill from the authoritative MLB Stats API.

Fetch-all-then-write: if the critical teams fetch fails, the registry is left
untouched. Roster/schedule fetch failures degrade to a 'partial' refresh (recorded,
not fatal). Writes happen inside one transaction (commit-all / rollback-all).
Idempotent upserts. Stale (unseen active) players are deactivated only when the
refresh is complete enough to trust.
"""
import datetime

from . import mlb_api
from .mlb_api import MlbApiError


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def full_refresh(reg, api=mlb_api, dates=None, now=None):
    """Refresh all teams + active rosters (+ schedule for `dates`). Returns a report."""
    now = now or _now()
    report = {"status": "ok", "teams": 0, "players_active": 0, "aliases": 0,
              "events": 0, "incomplete_teams": [], "stale_deactivated": 0,
              "api_calls": 0, "errors": []}

    # 1. teams (CRITICAL) — on failure, write nothing.
    try:
        teams = api.fetch_teams()
        report["api_calls"] += 1
    except MlbApiError as ex:
        report["status"] = "failed"
        report["errors"].append(f"teams fetch failed: {ex}")
        reg.set_meta("last_refresh_status", "failed")
        return report

    abbrev_by_id = {t["mlb_id"]: t["abbrev"] for t in teams}

    # 2. rosters (partial-tolerant)
    players, seen = [], set()
    for t in teams:
        try:
            roster = api.fetch_active_roster(t["mlb_id"])
            report["api_calls"] += 1
        except MlbApiError as ex:
            report["incomplete_teams"].append(t["abbrev"])
            report["errors"].append(f"roster {t['abbrev']} failed: {ex}")
            continue
        for p in roster:
            players.append((p, t["abbrev"]))
            seen.add("mlb-p-%s" % p["person_id"])

    # 3. schedule (optional / partial-tolerant)
    events = []
    for d in (dates or []):
        try:
            events += api.fetch_schedule(d)
            report["api_calls"] += 1
        except MlbApiError as ex:
            report["errors"].append(f"schedule {d} failed: {ex}")

    too_partial = len(report["incomplete_teams"]) > len(teams) // 2

    # 4. WRITE — one transaction: all-or-nothing (registry preserved on write error).
    try:
        with reg.conn:
            # bootstrap seed is superseded by authoritative data (same transaction)
            reg.conn.execute("DELETE FROM entities WHERE source='seed'")
            reg.conn.execute("DELETE FROM teams WHERE source='seed'")
            reg.conn.execute("DELETE FROM aliases WHERE source='seed'")
            for t in teams:
                reg.upsert_team(t["mlb_id"], t["name"], t["abbrev"], now)
            for p, ab in players:
                reg.upsert_player(p["person_id"], p["name"], ab, p["position"], p["jersey"],
                                  p["roster_status"], p["active"], now)
            if not too_partial:
                report["stale_deactivated"] = reg.mark_inactive_unseen(seen, now)
            for e in events:
                home = abbrev_by_id.get(e["home_mlb_id"])
                away = abbrev_by_id.get(e["away_mlb_id"])
                reg.upsert_event("mlb-e-%s" % e["game_pk"], e["season"], e["game_date"],
                                 e["status"], ("mlb-t-" + home) if home else None,
                                 ("mlb-t-" + away) if away else None, e["venue"],
                                 e["double_header"], e["game_number"], now)
    except Exception as ex:  # noqa: BLE001 - rollback already happened via context mgr
        report["status"] = "failed"
        report["errors"].append(f"write failed (rolled back): {ex}")
        reg.set_meta("last_refresh_status", "failed")
        return report

    report["teams"] = len(teams)
    report["players_active"] = sum(1 for p, _ in players if p["active"])
    report["events"] = len(events)
    report["aliases"] = reg.conn.execute("SELECT COUNT(*) c FROM aliases").fetchone()["c"]
    if report["incomplete_teams"]:
        report["status"] = "partial"
        if too_partial:
            report["errors"].append("too many roster failures — skipped stale deactivation")
    reg.set_meta("last_successful_refresh", now)
    reg.set_meta("last_refresh_status", report["status"])
    return report


def incremental_refresh(reg, api=mlb_api, dates=None, team_mlb_ids=None, now=None):
    """Refresh schedule for `dates` (+ re-pull rosters for `team_mlb_ids`). Idempotent."""
    now = now or _now()
    report = {"status": "ok", "events": 0, "players": 0, "api_calls": 0, "errors": []}
    abbrev_by_id = reg.abbrev_by_mlb_id()
    try:
        with reg.conn:
            for tid in (team_mlb_ids or []):
                try:
                    roster = api.fetch_active_roster(tid); report["api_calls"] += 1
                except MlbApiError as ex:
                    report["errors"].append(f"roster {tid} failed: {ex}"); continue
                ab = abbrev_by_id.get(tid)
                for p in roster:
                    reg.upsert_player(p["person_id"], p["name"], ab, p["position"], p["jersey"],
                                      p["roster_status"], p["active"], now)
                    report["players"] += 1
            for d in (dates or []):
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
    if report["errors"]:
        report["status"] = "partial"
    reg.set_meta("last_incremental_refresh", now)
    return report
