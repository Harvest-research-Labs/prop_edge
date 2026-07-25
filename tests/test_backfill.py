"""40-man backfill + participation + schedule + gate tests (deterministic, no network)."""
import pytest

from resolver.registry import EntityRegistry
from resolver import backfill
from resolver.mlb_api import MlbApiError
from resolver.resolve import resolve_pick

NOW = "2026-07-25T12:00:00+00:00"
NOW2 = "2026-07-25T13:00:00+00:00"          # 1h later (recent)
OLD = "2026-07-20T12:00:00+00:00"           # 5 days earlier (stale)


def _p(pid, name, pos="RF", active=True, injury=None, txn=None):
    return {"person_id": pid, "name": name, "position": pos, "jersey": "1",
            "roster_status": injury or txn or ("Active" if active else "Inactive"),
            "active": active, "injury_status": injury, "transaction_status": txn, "eligible": True}


class FakeApi:
    def __init__(self):
        self.teams = [
            {"mlb_id": 119, "name": "Los Angeles Dodgers", "abbrev": "LAD"},
            {"mlb_id": 118, "name": "Kansas City Royals", "abbrev": "KC"},
            {"mlb_id": 147, "name": "New York Yankees", "abbrev": "NYY"},
            {"mlb_id": 114, "name": "Cleveland Guardians", "abbrev": "CLE"},
            {"mlb_id": 144, "name": "Atlanta Braves", "abbrev": "ATL"},
        ]
        self.rosters = {
            119: [_p(660271, "Shohei Ohtani", "DH"), _p(700, "Will Smith", "C"),
                  _p(477132, "Clayton Kershaw", "P", active=False, injury="10-Day Injured List"),
                  _p(900, "Dalton Rushing", "C", active=False, txn="Optioned")],
            118: [_p(701, "Will Smith", "P"), _p(677951, "Bobby Witt Jr.", "SS")],
            147: [_p(592450, "Aaron Judge", "RF")],
            114: [_p(608070, "Jose Ramirez", "3B")],
            144: [_p(660670, "Ronald Acuna Jr.", "OF"), _p(671739, "Michael Harris II", "CF")],
        }
        self.schedule = {"2026-07-25": [
            {"game_pk": 745, "game_date": "2026-07-25T19:10:00+00:00", "status": "Scheduled",
             "home_mlb_id": 119, "away_mlb_id": 118, "venue": "Dodger Stadium",
             "double_header": "N", "game_number": 1, "season": "2026"}]}
        self.fail_teams = False
        self.fail_roster = set()

    def fetch_teams(self):
        if self.fail_teams:
            raise MlbApiError("teams down")
        return self.teams

    def fetch_40man_roster(self, mlb_id):
        if mlb_id in self.fail_roster:
            raise MlbApiError(f"roster {mlb_id} failed")
        return self.rosters.get(mlb_id, [])

    def fetch_schedule(self, date):
        return self.schedule.get(date, [])


@pytest.fixture()
def reg():
    r = EntityRegistry(":memory:", seed=False)
    backfill.full_refresh(r, api=FakeApi(), dates=["2026-07-25"], now=NOW)
    return r


def R(reg, now=NOW, **pick):
    pick.setdefault("sport", "MLB")
    return resolve_pick(pick, reg=reg, now=now)


# ---- coverage / participation ----------------------------------------------
def test_active_player(reg):
    r = R(reg, player="Aaron Judge", stat="Hits")
    assert r["status"] == "resolved" and r["active"] is True
    assert r["participation_confidence"] == 0.95 and r["gate"]["action"] == "proceed"


def test_40man_inactive_optioned(reg):
    r = R(reg, player="Dalton Rushing", stat="Hits")
    assert r["status"] == "resolved"                     # identity high (40-man)
    assert r["active"] is False and r["eligible"] is True
    assert r["participation_confidence"] == 0.35 and r["gate"]["action"] == "confirm"


def test_injured_list_player(reg):
    r = R(reg, player="Clayton Kershaw", stat="Strikeouts")
    assert r["status"] == "resolved" and r["injury_status"]     # high identity
    assert r["participation_confidence"] == 0.20 and r["gate"]["action"] == "stop"


def test_recently_moved_medium_participation():
    r = EntityRegistry(":memory:", seed=False)
    api = FakeApi(); backfill.full_refresh(r, api=api, now=NOW)
    api.rosters[118] = [_p(701, "Will Smith", "P")]                        # Witt off KC
    api.rosters[147].append(_p(677951, "Bobby Witt Jr.", "SS"))           # onto NYY
    backfill.full_refresh(r, api=api, now=NOW2)                            # recent
    x = R(r, now=NOW2, player="Bobby Witt Jr.", stat="Hits")
    assert x["participation_confidence"] == 0.70 and x["gate"]["action"] == "confirm"


def test_participation_gate_bands(reg):
    assert R(reg, player="Aaron Judge")["gate"]["action"] == "proceed"        # active
    assert R(reg, player="Clayton Kershaw")["gate"]["action"] == "stop"       # IL
    assert R(reg, player="Dalton Rushing")["gate"]["action"] == "confirm"     # optioned


# ---- transactions ----------------------------------------------------------
def test_player_traded_updates_team():
    r = EntityRegistry(":memory:", seed=False)
    api = FakeApi(); backfill.full_refresh(r, api=api, now=NOW)
    api.rosters[147] = []
    api.rosters[119].append(_p(592450, "Aaron Judge", "RF"))
    backfill.full_refresh(r, api=api, now=NOW2)
    assert R(r, now=NOW2, player="Aaron Judge")["team_id"] == "mlb-t-LAD"


def test_historical_team_screenshot():
    r = EntityRegistry(":memory:", seed=False)
    api = FakeApi(); backfill.full_refresh(r, api=api, now=NOW)
    api.rosters[118] = [_p(701, "Will Smith", "P")]
    api.rosters[147].append(_p(677951, "Bobby Witt Jr.", "SS"))
    backfill.full_refresh(r, api=api, now=NOW2)
    x = R(r, now=NOW2, player="Bobby Witt Jr.", team="KC")   # old team
    assert x["resolution_method"] == "prev_team_history" and x["gate"]["action"] == "confirm"
    assert any("recent history" in w for w in x["warnings"])


def test_conflicting_current_team(reg):
    x = R(reg, player="Aaron Judge", team="KC")              # never his team
    assert x["resolution_method"] == "name_team_mismatch" and x["needs_review"] is True


# ---- aliases ---------------------------------------------------------------
def test_alias_import(reg):
    n = reg.conn.execute("SELECT COUNT(*) c FROM aliases WHERE alias_norm='mike harris'").fetchone()["c"]
    assert n >= 1
    assert R(reg, player="Mike Harris")["entity_id"] == "mlb-p-671739"


def test_accent_and_suffix_aliases(reg):
    assert R(reg, player="Jose Ramirez")["entity_id"] == "mlb-p-608070"        # accent
    assert R(reg, player="Ronald Acuna")["entity_id"] == "mlb-p-660670"        # suffix + accent


def test_initial_plus_surname(reg):
    x = R(reg, player="A. Judge")
    assert x["entity_id"] == "mlb-p-592450"


# ---- schedule --------------------------------------------------------------
def test_duplicate_name_event_disambiguation(reg):
    amb = R(reg, player="Will Smith", stat="Strikeouts")
    assert amb["status"] == "needs_review" and amb["resolution_method"] == "ambiguous"
    ok = R(reg, player="Will Smith", team="LAD", event_start="2026-07-25T19:10:00+00:00")
    assert ok["status"] == "resolved" and ok["entity_id"] == "mlb-p-700"
    assert ok["event_id"] == "mlb-e-745" and ok["opponent_id"] == "mlb-t-KC"


def test_doubleheader_matching():
    r = EntityRegistry(":memory:", seed=False)
    api = FakeApi()
    api.schedule["2026-07-25"].append(
        {"game_pk": 746, "game_date": "2026-07-25T22:10:00+00:00", "status": "Scheduled",
         "home_mlb_id": 119, "away_mlb_id": 118, "venue": "Dodger Stadium",
         "double_header": "S", "game_number": 2, "season": "2026"})
    backfill.full_refresh(r, api=api, dates=["2026-07-25"], now=NOW)
    amb = R(r, player="Shohei Ohtani", team="LAD", event_start="2026-07-25T00:00:00+00:00")
    assert amb["event_id"] is None and any("doubleheader" in w for w in amb["warnings"])
    pick = R(r, player="Shohei Ohtani", team="LAD",
             event_start="2026-07-25T00:00:00+00:00", game_number=2)
    assert pick["event_id"] == "mlb-e-746"


def test_postponed_game():
    r = EntityRegistry(":memory:", seed=False)
    api = FakeApi()
    api.schedule["2026-07-25"][0]["status"] = "Postponed"
    backfill.full_refresh(r, api=api, dates=["2026-07-25"], now=NOW)
    x = R(r, player="Shohei Ohtani", team="LAD", event_start="2026-07-25T19:10:00+00:00")
    assert x["event_status"] == "Postponed" and any("postponed" in w.lower() for w in x["warnings"])


def test_rescheduled_or_stale_schedule(reg):
    # screenshot date has no scheduled event for the team -> start-time-change / stale
    x = R(reg, player="Aaron Judge", team="NYY", event_start="2026-07-26T18:00:00+00:00")
    assert x["opponent_id"] is None
    assert any("no scheduled event" in w for w in x["warnings"])


def test_stale_registry_lowers_confidence(reg):
    reg.set_meta("last_successful_refresh", OLD)
    x = R(reg, now="2026-07-30T12:00:00+00:00", player="Aaron Judge", stat="Hits")
    assert x["registry_stale"] is True
    assert any("stale" in w for w in x["warnings"])


# ---- resilience ------------------------------------------------------------
def test_partial_upstream_failure():
    r = EntityRegistry(":memory:", seed=False)
    api = FakeApi(); api.fail_roster = {118}
    rep = backfill.full_refresh(r, api=api, dates=["2026-07-25"], now=NOW)
    assert rep["status"] == "partial" and "KC" in rep["incomplete_teams"]
    assert r.get_by_id("mlb-p-660271") is not None          # LAD imported


def test_failed_refresh_preserves_prior_production_data():
    r = EntityRegistry(":memory:", seed=False)
    backfill.full_refresh(r, api=FakeApi(), now=NOW)         # good production data
    before = r._count("entities")
    api = FakeApi(); api.fail_teams = True
    rep = backfill.full_refresh(r, api=api, now=NOW2)
    assert rep["status"] == "failed" and r._count("entities") == before
    assert R(r, now=NOW2, player="Aaron Judge")["entity_id"] == "mlb-p-592450"


def test_report_counts(reg):
    # reg was built by the fixture; re-report via a fresh import to check fields
    r = EntityRegistry(":memory:", seed=False)
    rep = backfill.full_refresh(r, api=FakeApi(), dates=["2026-07-25"], now=NOW)
    assert rep["teams"] == 5 and rep["players_total"] == 10
    assert rep["players_injured"] == 1 and rep["players_inactive"] == 2
    assert rep["events"] == 1 and rep["aliases"] >= 1
