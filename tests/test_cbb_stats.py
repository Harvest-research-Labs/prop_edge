"""Offline tests for the college basketball own-projection model (model/cbb_stats.py).

Rows mimic the CollegeBasketballData API's /stats/player/season (nested
rebounds / shooting splits); a fake session serves them.
"""
import datetime

import pytest

from config import canonical_stat
from model import cbb_stats
from model.projections import annotate
from sources.base import Prop


def _row(name, team, games, pts, reb, ast, fg3m=0, blk=0, stl=0, tov=0):
    return {"name": name, "team": team, "games": games, "points": pts,
            "rebounds": {"offensive": reb // 3, "defensive": reb - reb // 3, "total": reb},
            "assists": ast, "steals": stl, "blocks": blk, "turnovers": tov,
            "fieldGoals": {"made": 0, "attempted": 0},
            "threePointFieldGoals": {"made": fg3m, "attempted": fg3m * 3},
            "freeThrows": {"made": 0, "attempted": 0}}


class _Session:
    def __init__(self, by_season):
        self.by_season, self.calls, self.auth = by_season, [], None

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params["season"])
        self.auth = headers.get("Authorization")
        data = self.by_season.get(params["season"], [])

        class R:
            def raise_for_status(self): pass
            def json(self_inner): return data
        return R()


def test_season_year():
    assert cbb_stats.season_year(datetime.date(2026, 10, 3)) == 2027    # pre-season
    assert cbb_stats.season_year(datetime.date(2027, 3, 15)) == 2027    # March
    assert cbb_stats.season_year(datetime.date(2027, 4, 30)) == 2027


def test_per_game_from_nested_rows_and_combos():
    r = cbb_stats.build_table([_row("Cooper Flagg", "Duke", 10, 190, 85, 42, fg3m=14, blk=14, stl=15)], [])
    p = r["cooper flagg"]
    assert p["points"] == 19 and p["rebounds"] == 8.5 and p["assists"] == 4.2
    assert p["pts+rebs+asts"] == pytest.approx(31.7) and p["3-pt made"] == 1.4
    assert p["blks+stls"] == pytest.approx(2.9) and "fantasy score" not in p


def test_flat_rebounds_number_is_read_as_total():
    row = _row("Flat", "Kansas", 4, 40, 0, 8) | {"rebounds": 24}
    assert cbb_stats.build_table([row], [])["flat"]["rebounds"] == 6


def test_shrinks_toward_last_season_and_preseason_uses_it():
    last = [_row("Big Man", "Houston", 30, 300, 270, 30)]               # 10 ppg
    assert cbb_stats.build_table([], last)["big man"]["points"] == 10
    cur = [_row("Big Man", "Houston", 2, 40, 20, 4)]                     # 20 ppg
    p = cbb_stats.build_table(cur, last)["big man"]["points"]
    assert p == pytest.approx((40 + cbb_stats.SHRINK_K * 10) / (2 + cbb_stats.SHRINK_K))


def test_freshman_uses_raw_rate():
    p = cbb_stats.build_table([_row("Freshman", "UNC", 3, 60, 15, 9)], [])["freshman"]
    assert p["points"] == 20


def test_same_name_on_two_teams_is_left_unprojected():
    rows = [_row("Jalen Johnson", "Duke", 5, 50, 10, 5),
            _row("Jalen Johnson", "Iowa", 5, 100, 10, 5),
            _row("Jalen Johnson", "Duke", 5, 50, 10, 5)]
    assert cbb_stats.build_table(rows, [])["jalen johnson"] is None


def test_load_projector_needs_key(monkeypatch):
    monkeypatch.delenv("CBBD_API_KEY", raising=False)
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        cbb_stats.load_projector(session=_Session({}))


def test_load_projector_falls_back_to_cfbd_key_and_reads_book_labels(monkeypatch):
    monkeypatch.delenv("CBBD_API_KEY", raising=False)
    monkeypatch.setenv("CFBD_API_KEY", "shared")
    s = _Session({2026: [_row("Guard Two", "Gonzaga", 30, 450, 90, 150, fg3m=60, blk=6, stl=30)]})
    proj = cbb_stats.load_projector(session=s, today=datetime.date(2026, 10, 3))
    assert s.auth == "Bearer shared" and s.calls == [2027, 2026]
    for label in ("Points", "Pts+Rebs+Asts", "3-PT Made", "Blocks + Steals", "Rebs+Asts"):
        m = proj("Guard Two", canonical_stat(label))
        assert m and m > 0, label
    assert proj("Guard Two", "points") == 15
    assert proj("Nobody", "points") is None


def test_annotate_uses_cbb_model_when_market_is_silent():
    s = _Session({2026: [_row("Center One", "Purdue", 30, 600, 300, 60)]})
    proj = cbb_stats.load_projector(api_key="k", session=s, today=datetime.date(2026, 10, 3))
    prop = Prop(book="PrizePicks", sport="CBB", player="Center One",
                stat="Points", line=25.5, flavor="demon")
    row = annotate([prop], projectors={"CBB": proj})[0]
    assert row["mean_source"] == "own_model" and row["mean"] == 20.0
    assert 0 < row["hit_prob"] < 0.5
