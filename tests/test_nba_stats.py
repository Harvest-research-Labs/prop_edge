"""Offline tests for the NBA own-projection model (model/nba_stats.py).

Rows mimic stats.nba.com's leaguedashplayerstats (PerMode=Totals); a fake
session serves them, so nothing touches the network.
"""
import datetime

import pytest
import requests

from config import canonical_stat
from model import nba_stats
from model.projections import annotate
from sources.base import Prop


def _row(name, gp, pts, reb, ast, fg3m=0, blk=0, stl=0, tov=0):
    return {"PLAYER_NAME": name, "GP": gp, "PTS": pts, "REB": reb, "AST": ast,
            "FG3M": fg3m, "FG3A": 0, "BLK": blk, "STL": stl, "TOV": tov,
            "OREB": 0, "DREB": reb, "FTM": 0, "FGM": 0, "FGA": 0}


class _Session:
    """Serves resultSets by (Season, LastNGames)."""

    def __init__(self, by_key, error_seasons=()):
        self.by_key, self.calls, self.leagues = by_key, [], set()
        self.error_seasons = error_seasons

    def get(self, url, params=None, headers=None, timeout=None):
        key = (params["Season"], params["LastNGames"])
        self.calls.append(key)
        self.leagues.add(params["LeagueID"])
        rows = self.by_key.get(key, [])
        failing = params["Season"] in self.error_seasons
        cols = list(rows[0].keys()) if rows else ["PLAYER_NAME", "GP"]

        class R:
            status_code = 200
            def raise_for_status(self):
                if failing:
                    raise requests.HTTPError("400 Client Error")
            def json(self_inner):
                return {"resultSets": [{"headers": cols,
                                        "rowSet": [[r[c] for c in cols] for r in rows]}]}
        return R()


def test_season_label_rolls_over_in_october():
    assert nba_stats.season_label(datetime.date(2026, 10, 3)) == "2026-27"
    assert nba_stats.season_label(datetime.date(2027, 4, 1)) == "2026-27"
    assert nba_stats.season_label(datetime.date(2026, 9, 30)) == "2025-26"
    assert nba_stats.previous_season("2026-27") == "2025-26"


def test_preseason_projects_from_last_season():
    # No games yet this season: the projection is last season's per-game rate.
    last = [_row("Nikola Jokić", 70, 1890, 875, 700, blk=50, stl=100, tov=210)]
    table = nba_stats.build_table([], [], last)
    r = table["nikola jokic"]                      # accent-insensitive key
    assert r["points"] == 27.0 and r["rebounds"] == 12.5 and r["assists"] == 10.0
    assert r["pts+rebs+asts"] == 49.5
    # PP/UD fantasy: 27 + 1.2*12.5 + 1.5*10 + 3*(50+100)/70 - 3
    assert abs(r["fantasy score"] - (27 + 15 + 15 + 3 * 150 / 70 - 3)) < 1e-9


def test_early_season_shrinks_toward_last_season():
    # 2 hot games (40 ppg) against a 20-ppg last season: SHRINK_K keeps it near 20.
    season = [_row("Hot Starter", 2, 80, 10, 10)]
    last = [_row("Hot Starter", 80, 1600, 400, 400)]
    r = nba_stats.build_table(season, [], last)["hot starter"]
    expect = (80 + nba_stats.SHRINK_K * 20) / (2 + nba_stats.SHRINK_K)
    assert abs(r["points"] - expect) < 1e-9 and 20 < r["points"] < 25


def test_recent_form_blends_in():
    season = [_row("Guard", 40, 800, 160, 200)]           # 20 ppg
    recent = [_row("Guard", 10, 300, 40, 50)]              # 30 ppg last 10
    base = nba_stats.build_table(season, [], [])["guard"]["points"]
    blended = nba_stats.build_table(season, recent, [])["guard"]["points"]
    assert base < blended < 30


def test_rookie_shrinks_to_league_average():
    season = [_row("Vet", 50, 1000, 250, 250), _row("Rookie", 1, 40, 0, 0)]
    r = nba_stats.build_table(season, [], [])["rookie"]["points"]
    assert r < 40 / 1 and r > 20                           # pulled toward ~20.6 league ppg


def test_load_projector_end_to_end_and_book_labels():
    s = _Session({
        ("2026-27", 0): [_row("Jalen Brunson", 5, 130, 15, 35, fg3m=12, blk=1, stl=4)],
        ("2026-27", 10): [_row("Jalen Brunson", 5, 130, 15, 35, fg3m=12, blk=1, stl=4)],
        ("2025-26", 0): [_row("Jalen Brunson", 75, 1950, 255, 525, fg3m=200, blk=15, stl=60)],
    })
    proj = nba_stats.load_projector(session=s, today=datetime.date(2026, 11, 1))
    assert s.calls == [("2026-27", 0), ("2026-27", 10), ("2025-26", 0)]
    for label in ("Points", "Pts+Rebs+Asts", "3-Pointers Made", "Blocks + Steals", "Fantasy Score"):
        m = proj("Jalen Brunson", canonical_stat(label))
        assert m and m > 0, label
    assert proj("Nobody Here", "points") is None
    assert proj("Jalen Brunson", "pitcher strikeouts") is None


def test_preseason_skips_recent_pull():
    s = _Session({("2025-26", 0): [_row("A", 10, 100, 10, 10)]})
    proj = nba_stats.load_projector(session=s, today=datetime.date(2026, 10, 3))
    assert s.calls == [("2026-27", 0), ("2025-26", 0)]
    assert proj("A", "points") == 10.0


def test_annotate_uses_nba_model_when_market_is_silent():
    # A Demon-only line has no market anchor; the NBA model must price it.
    s = _Session({("2025-26", 0): [_row("Jalen Brunson", 75, 1950, 255, 525)]})
    proj = nba_stats.load_projector(session=s, today=datetime.date(2026, 10, 3))
    prop = Prop(book="PrizePicks", sport="NBA", player="Jalen Brunson",
                stat="Points", line=30.5, flavor="demon")
    row = annotate([prop], projectors={"NBA": proj})[0]
    assert row["mean_source"] == "own_model" and row["mean"] == 26.0
    assert 0 < row["hit_prob"] < 0.5                       # 30.5 is above a 26-ppg mean
    assert row["own_prob"] == row["hit_prob"]


def test_wnba_season_is_the_calendar_year():
    assert nba_stats.season_label(datetime.date(2026, 10, 3), "WNBA") == "2026"
    assert nba_stats.season_label(datetime.date(2027, 2, 1), "WNBA") == "2027"
    assert nba_stats.previous_season("2026") == "2025"


def test_wnba_projector_queries_league_10():
    s = _Session({
        ("2026", 0): [_row("A'ja Wilson", 40, 1080, 440, 100, blk=90, stl=70)],
        ("2026", 10): [_row("A'ja Wilson", 10, 300, 110, 25)],
        ("2025", 0): [_row("A'ja Wilson", 40, 1000, 400, 90)],
    })
    proj = nba_stats.load_projector(session=s, today=datetime.date(2026, 10, 3), league="WNBA")
    assert s.leagues == {"10"}
    assert s.calls == [("2026", 0), ("2026", 10), ("2025", 0)]
    m = proj("Aja Wilson", canonical_stat("Points"))         # apostrophe-insensitive
    assert 27 < m < 30                                       # 27 ppg season, 30 last 10
    assert proj("A'ja Wilson", canonical_stat("Fantasy Score")) > m


def test_wnba_offseason_projects_from_last_season():
    # Feb 2027: the 2027 season hasn't started (empty), so 2026 is the projection.
    s = _Session({("2026", 0): [_row("Caitlin Clark", 20, 380, 100, 170, fg3m=60)]})
    proj = nba_stats.load_projector(session=s, today=datetime.date(2027, 2, 1), league="WNBA")
    assert s.calls == [("2027", 0), ("2026", 0)]
    assert proj("Caitlin Clark", "points") == 19 and proj("Caitlin Clark", "3-pt made") == 3


def test_unstarted_season_error_falls_back_but_last_season_error_raises():
    s = _Session({("2025-26", 0): [_row("A", 10, 100, 10, 10)]}, error_seasons=("2026-27",))
    proj = nba_stats.load_projector(session=s, today=datetime.date(2026, 10, 3))
    assert proj("A", "points") == 10 and s.leagues == {"00"}
    bad = _Session({}, error_seasons=("2026-27", "2025-26"))
    with pytest.raises(requests.HTTPError):
        nba_stats.load_projector(session=bad, today=datetime.date(2026, 10, 3))
