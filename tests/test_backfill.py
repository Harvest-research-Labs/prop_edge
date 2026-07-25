"""Backfill + schedule-join + mandatory-gate tests.

Deterministic: a FakeApi stands in for the MLB Stats API (no network). Covers full
import, idempotency, trades, activation, aliases, schedule, doubleheaders, stale
events, timeouts, partial failure, failure-preserves-registry, the mandatory gate
(high/med/low), uncovered-sport advisory, and an end-to-end screenshot->price chain.
"""
import copy
import pytest

from resolver.registry import EntityRegistry
from resolver import backfill
from resolver.mlb_api import MlbApiError
from resolver.resolve import resolve_pick
from model.projections import over_prob

NOW = "2026-07-25T12:00:00+00:00"


class FakeApi:
    def __init__(self):
        self.teams = [
            {"mlb_id": 119, "name": "Los Angeles Dodgers", "abbrev": "LAD", "team_name": "Dodgers", "location": "Los Angeles"},
            {"mlb_id": 118, "name": "Kansas City Royals",  "abbrev": "KC",  "team_name": "Royals",  "location": "Kansas City"},
            {"mlb_id": 147, "name": "New York Yankees",    "abbrev": "NYY", "team_name": "Yankees", "location": "New York"},
        ]
        self.rosters = {
            119: [{"person_id": 660271, "name": "Shohei Ohtani", "position": "DH", "jersey": "17", "roster_status": "Active", "active": True},
                  {"person_id": 700,    "name": "Will Smith",    "position": "C",  "jersey": "16", "roster_status": "Active", "active": True}],
            118: [{"person_id": 701,    "name": "Will Smith",    "position": "P",  "jersey": "20", "roster_status": "Active", "active": True}],
            147: [{"person_id": 592450, "name": "Aaron Judge",   "position": "RF", "jersey": "99", "roster_status": "Active", "active": True}],
        }
        self.schedule = {"2026-07-25": [
            {"game_pk": 745, "game_date": "2026-07-25T19:10:00+00:00", "status": "Scheduled",
             "home_mlb_id": 119, "away_mlb_id": 118, "venue": "Dodger Stadium",
             "double_header": "N", "game_number": 1, "season": "2026"}]}
        self.fail_teams = False
        self.fail_roster = set()
        self.timeout_roster = set()

    def fetch_teams(self):
        if self.fail_teams:
            raise MlbApiError("teams endpoint down")
        return self.teams

    def fetch_active_roster(self, mlb_id):
        if mlb_id in self.timeout_roster or mlb_id in self.fail_roster:
            raise MlbApiError(f"roster {mlb_id} failed")
        return self.rosters.get(mlb_id, [])

    def fetch_schedule(self, date):
        return self.schedule.get(date, [])


@pytest.fixture()
def empty():
    return EntityRegistry(":memory:", seed=False)


@pytest.fixture()
def seeded():
    return EntityRegistry(":memory:", seed=True)


# ---- import / idempotency --------------------------------------------------
def test_full_roster_import(empty):
    rep = backfill.full_refresh(empty, api=FakeApi(), dates=["2026-07-25"], now=NOW)
    assert rep["status"] == "ok" and rep["teams"] == 3 and rep["players_active"] == 4 and rep["events"] == 1
    r = resolve_pick({"player": "Aaron Judge", "stat": "Hits", "sport": "MLB"}, reg=empty, now=NOW)
    assert r["status"] == "resolved" and r["entity_id"] == "mlb-p-592450"


def test_idempotent_second_import(empty):
    api = FakeApi()
    backfill.full_refresh(empty, api=api, dates=["2026-07-25"], now=NOW)
    n1 = empty._count("entities")
    backfill.full_refresh(empty, api=api, dates=["2026-07-25"], now=NOW)
    assert empty._count("entities") == n1          # upserts, no duplicates


def test_player_traded(empty):
    api = FakeApi()
    backfill.full_refresh(empty, api=api, now=NOW)
    # Judge traded NYY -> LAD
    api.rosters[147] = []
    api.rosters[119].append({"person_id": 592450, "name": "Aaron Judge", "position": "RF",
                             "jersey": "99", "roster_status": "Active", "active": True})
    backfill.full_refresh(empty, api=api, now=NOW)
    r = resolve_pick({"player": "Aaron Judge", "sport": "MLB", "stat": "Hits"}, reg=empty, now=NOW)
    assert r["team_id"] == "mlb-t-LAD"


def test_player_deactivated_then_activated(empty):
    api = FakeApi()
    backfill.full_refresh(empty, api=api, now=NOW)
    api.rosters[147] = []                            # Judge off the roster
    rep = backfill.full_refresh(empty, api=api, now=NOW)
    assert rep["stale_deactivated"] >= 1
    assert empty.get_by_id("mlb-p-592450")["active"] == 0
    # reactivate
    api.rosters[147] = [{"person_id": 592450, "name": "Aaron Judge", "position": "RF",
                         "jersey": "99", "roster_status": "Active", "active": True}]
    backfill.full_refresh(empty, api=api, now=NOW)
    assert empty.get_by_id("mlb-p-592450")["active"] == 1


def test_team_alias_import(empty):
    backfill.full_refresh(empty, api=FakeApi(), now=NOW)
    assert empty.team_id_for("LAD") == "mlb-t-LAD"
    assert empty.team_id_for("Los Angeles Dodgers") == "mlb-t-LAD"


# ---- schedule / events -----------------------------------------------------
def test_schedule_import_sets_event_and_opponent(empty):
    backfill.full_refresh(empty, api=FakeApi(), dates=["2026-07-25"], now=NOW)
    r = resolve_pick({"player": "Shohei Ohtani", "sport": "MLB", "stat": "Total Bases",
                      "team": "LAD", "event_start": "2026-07-25T19:10:00+00:00"}, reg=empty, now=NOW)
    assert r["event_id"] == "mlb-e-745" and r["opponent_id"] == "mlb-t-KC"


def test_doubleheader_matching(empty):
    api = FakeApi()
    api.schedule["2026-07-25"].append(
        {"game_pk": 746, "game_date": "2026-07-25T22:10:00+00:00", "status": "Scheduled",
         "home_mlb_id": 119, "away_mlb_id": 118, "venue": "Dodger Stadium",
         "double_header": "S", "game_number": 2, "season": "2026"})
    backfill.full_refresh(empty, api=api, dates=["2026-07-25"], now=NOW)
    ambiguous = resolve_pick({"player": "Shohei Ohtani", "sport": "MLB", "stat": "Hits",
                              "team": "LAD", "event_start": "2026-07-25T00:00:00+00:00"}, reg=empty, now=NOW)
    assert ambiguous["event_id"] is None and ambiguous["opponent_id"] is None
    assert any("doubleheader" in w for w in ambiguous["warnings"])
    picked = resolve_pick({"player": "Shohei Ohtani", "sport": "MLB", "stat": "Hits", "team": "LAD",
                           "event_start": "2026-07-25T00:00:00+00:00", "game_number": 2}, reg=empty, now=NOW)
    assert picked["event_id"] == "mlb-e-746"


def test_stale_screenshot_no_event(empty):
    backfill.full_refresh(empty, api=FakeApi(), dates=["2026-07-25"], now=NOW)
    r = resolve_pick({"player": "Aaron Judge", "sport": "MLB", "stat": "Hits", "team": "NYY",
                      "event_start": "2026-07-25T18:00:00+00:00"}, reg=empty, now=NOW)
    assert r["opponent_id"] is None
    assert any("no scheduled event" in w for w in r["warnings"])


# ---- resilience ------------------------------------------------------------
def test_upstream_timeout_is_partial(empty):
    api = FakeApi(); api.timeout_roster = {119}
    rep = backfill.full_refresh(empty, api=api, now=NOW)
    assert rep["status"] == "partial" and "LAD" in rep["incomplete_teams"]
    assert empty.get_by_id("mlb-p-592450") is not None       # NYY still imported


def test_partial_upstream_failure(empty):
    api = FakeApi(); api.fail_roster = {118}
    rep = backfill.full_refresh(empty, api=api, now=NOW)
    assert rep["status"] == "partial" and "KC" in rep["incomplete_teams"]
    assert empty.get_by_id("mlb-p-660271") is not None       # LAD imported
    assert empty.get_by_id("mlb-p-701") is None              # KC skipped


def test_failed_refresh_preserves_registry(seeded):
    before = seeded._count("entities")
    api = FakeApi(); api.fail_teams = True
    rep = backfill.full_refresh(seeded, api=api, now=NOW)
    assert rep["status"] == "failed"
    assert seeded._count("entities") == before               # nothing destroyed
    assert resolve_pick({"player": "Aaron Judge", "sport": "MLB"}, reg=seeded, now=NOW)["entity_id"]


# ---- mandatory gate --------------------------------------------------------
def _reg():
    reg = EntityRegistry(":memory:", seed=False)
    backfill.full_refresh(reg, api=FakeApi(), dates=["2026-07-25"], now=NOW)
    return reg


def test_gate_high_proceed():
    r = resolve_pick({"player": "Aaron Judge", "sport": "MLB", "stat": "Hits"}, reg=_reg(), now=NOW)
    assert r["gate"]["action"] == "proceed"


def test_gate_medium_confirm():
    r = resolve_pick({"player": "Will Smith", "sport": "MLB", "stat": "Strikeouts"}, reg=_reg(), now=NOW)
    assert r["status"] == "needs_review" and r["gate"]["action"] == "confirm"


def test_gate_low_stop():
    r = resolve_pick({"player": "Ghost Player", "sport": "MLB", "stat": "Hits"}, reg=_reg(), now=NOW)
    assert r["gate"]["action"] == "stop"


def test_uncovered_sport_advisory():
    r = resolve_pick({"player": "LeBron James", "sport": "NBA", "stat": "Points"}, reg=_reg(), now=NOW)
    assert r["gate"]["action"] == "advisory" and r["league"] is None


# ---- end to end ------------------------------------------------------------
def test_e2e_screenshot_text_to_price():
    reg = _reg()
    # screenshot-extracted text -> entity + event
    r = resolve_pick({"player": "A. Judge", "stat": "Hits", "line": 0.5, "side": "more",
                      "sport": "MLB", "team": "NYY"}, reg=reg, now=NOW)
    assert r["status"] == "resolved" and r["entity_id"] == "mlb-p-592450"
    assert r["gate"]["action"] == "proceed"
    # projection (mean supplied to stay offline) -> price
    prob = over_prob(r["line"], 1.15, r["canonical_stat"] or "Hits")
    assert 0.0 < prob <= 1.0
