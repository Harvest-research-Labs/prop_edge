"""Participation Service tests — lineup + probable-pitcher validation (no network)."""
import pytest

from resolver.registry import EntityRegistry
from resolver.mlb_api import MlbApiError
from resolver import participation as P

NOW = "2026-07-25T18:00:00+00:00"
START = "2026-07-25T19:00:00+00:00"          # posting (start-3h)=16:00 -> overdue at NOW
START_LATE = "2026-07-25T23:00:00+00:00"     # posting 20:00 -> pending at NOW
OLD = "2026-07-25T15:00:00+00:00"            # 3h before NOW -> stale


def game(**kw):
    g = {"game_pk": 745, "game_date": START, "status": "Scheduled", "abstract_state": "Preview",
         "home_mlb_id": 119, "away_mlb_id": 118, "home_probable_id": 500, "away_probable_id": 600,
         "home_lineup": [], "away_lineup": [], "lineup_posted": False,
         "source": "mlbstatsapi", "source_updated_at": NOW}
    g.update(kw)
    return g


def C(pid, side="home", **kw):
    return P.classify(pid, side, game(**kw), NOW)


# ---- hitters ---------------------------------------------------------------
def test_confirmed_hitter():
    r = C(12, home_lineup=[10, 11, 12, 13], lineup_posted=True)
    assert r["participation_status"] == "confirmed_starting" and r["batting_order"] == 3
    assert r["confirmed_starter"] and r["gate"]["action"] == "proceed"


def test_expected_hitter():
    r = C(12, expected_home=[12], game_date=START_LATE)
    assert r["participation_status"] == "expected_starting"
    assert r["gate"]["action"] == "proceed" and r["warnings"]


def test_bench_player():
    r = C(12, home_lineup=[10, 11], lineup_posted=True)
    assert r["participation_status"] == "bench" and r["gate"]["action"] == "stop"


def test_scratched_player():
    r = C(12, home_lineup=[10, 11], lineup_posted=True, prev_home_lineup=[10, 11, 12])
    assert r["participation_status"] == "scratched" and r["scratched"] is True
    assert r["gate"]["action"] == "stop"


def test_lineup_not_yet_posted():
    r = C(12, game_date=START_LATE)                     # NOW before posting time
    assert r["participation_status"] == "unknown" and r["lineup_status"] == "pending"
    assert r["participation_confidence"] == 0.55 and r["gate"]["action"] == "confirm"


def test_lineup_overdue():
    r = C(12, game_date=START)                          # NOW past posting, still unposted
    assert r["lineup_status"] == "overdue" and r["participation_confidence"] == 0.45
    assert r["gate"]["action"] == "confirm"


# ---- pitchers --------------------------------------------------------------
def test_confirmed_starting_pitcher():
    r = C(500, status="In Progress", abstract_state="Live")
    assert r["participation_status"] == "confirmed_pitcher" and r["confirmed_starter"]
    assert r["gate"]["action"] == "proceed"


def test_probable_pitcher():
    r = C(500)                                          # Preview, person == probable
    assert r["participation_status"] == "probable_pitcher"
    assert r["pitcher_role"] == "probable_pitcher" and r["gate"]["action"] == "proceed"


def test_probable_pitcher_changed():
    r = C(500, home_probable_id=999, prev_home_probable_id=500)
    assert r["participation_status"] == "scratched" and r["matchup_stale"] is True
    assert r["gate"]["action"] == "stop"


def test_opener():
    r = C(500, home_pitcher_role="opener")
    assert r["participation_status"] == "opener" and r["gate"]["action"] == "proceed"


def test_bulk_reliever():
    r = C(500, home_pitcher_role="bulk_relief")
    assert r["participation_status"] == "bulk_relief"


# ---- game state ------------------------------------------------------------
def test_postponed_game():
    r = C(12, home_lineup=[12], lineup_posted=True, status="Postponed")
    assert r["participation_status"] == "inactive" and r["gate"]["action"] == "stop"


def test_delayed_game():
    r = C(12, status="Delayed")
    assert r["gate"]["action"] == "confirm" and any("delay" in w.lower() for w in r["warnings"])


# ---- source quality --------------------------------------------------------
def test_conflicting_sources():
    r = C(12, home_lineup=[10, 11], lineup_posted=True, expected_home=[12])
    assert r["conflicting"] is True and r["participation_status"] == "bench"  # posted wins
    assert any("disagree" in w for w in r["warnings"])


def test_stale_source():
    r = C(12, home_lineup=[12], lineup_posted=True, source_updated_at=OLD)
    assert r["participation_confidence"] <= 0.60
    assert any("stale" in w for w in r["warnings"])


def test_matchup_invalidated_by_pitcher_change():
    r = C(12, home_lineup=[12], lineup_posted=True,
          prev_home_probable_id=500, prev_away_probable_id=555)   # opp SP changed 555->600
    assert r["participation_status"] == "confirmed_starting" and r["matchup_stale"] is True


# ---- gate mode -------------------------------------------------------------
def test_gate_mode_env(monkeypatch):
    monkeypatch.setenv("PROEDGE_MLB_PARTICIPATION_GATE", "required")
    assert P.participation_gate_mode() == "required"
    monkeypatch.setenv("PROEDGE_MLB_PARTICIPATION_GATE", "bogus")
    assert P.participation_gate_mode() == "advisory"             # falls back


# ---- evaluate() orchestration ----------------------------------------------
class FakeSource:
    name = "fake"
    priority = 1

    def __init__(self, by_date, fail=False):
        self.by_date = by_date
        self.fail = fail

    def fetch(self, date):
        if self.fail:
            raise MlbApiError("source down")
        return [dict(g) for g in self.by_date.get(date, [])]


def _reg():
    r = EntityRegistry(":memory:", seed=False)
    r.upsert_team(119, "Los Angeles Dodgers", "LAD", NOW)
    r.upsert_team(118, "Kansas City Royals", "KC", NOW)
    return r


def test_evaluate_matches_and_persists():
    reg = _reg()
    src = FakeSource({"2026-07-25": [game(home_lineup=[12], lineup_posted=True)]})
    picks = [{"entity_id": "mlb-p-12", "player": "X", "sport": "MLB",
              "team_id": "mlb-t-LAD", "event_start": START}]
    outs, meta = P.evaluate(picks, reg, source=src, now=NOW)
    assert outs[0]["participation_status"] == "confirmed_starting"
    assert reg.get_game_participation("mlb-e-745") is not None     # snapshot persisted
    assert meta["fetched_ok"] is True


def test_evaluate_refresh_failure_preserves_snapshot():
    reg = _reg()
    reg.upsert_game_participation("mlb-e-745", "Scheduled", "Preview", "500", "600",
                                 [10, 11, 12], [], True, OLD)      # last valid state
    src = FakeSource({}, fail=True)
    picks = [{"entity_id": "mlb-p-12", "player": "X", "sport": "MLB",
              "team_id": "mlb-t-LAD", "event_start": START}]
    outs, meta = P.evaluate(picks, reg, source=src, now=NOW)
    assert meta["fetched_ok"] is False and meta["errors"]
    assert outs[0]["gate"]["action"] == "confirm"                 # held, not proceeded
    assert reg.get_game_participation("mlb-e-745")["home_lineup"] == [10, 11, 12]  # preserved


def test_evaluate_non_mlb_pick_not_evaluated():
    reg = _reg()
    src = FakeSource({})
    outs, _ = P.evaluate([{"entity_id": None, "player": "Y", "sport": "NBA"}], reg,
                         source=src, now=NOW)
    assert outs[0]["participation_confidence"] is None
    assert outs[0]["gate"]["action"] == "advisory"
