"""Offline tests for the college football own-projection model (model/cfb_stats.py).

Rows mimic the CollegeFootballData API (/stats/player/season and /games); a
fake session serves them, so nothing touches the network.
"""
import datetime

import pytest

from config import canonical_stat
from model import cfb_stats
from model.projections import annotate
from sources.base import Prop


def _stats(player, team, **by_cat):
    """_stats("A", "Georgia", passing={"YDS": 300}) -> CFBD stat rows."""
    return [{"player": player, "team": team, "category": cat, "statType": st, "stat": str(v)}
            for cat, kv in by_cat.items() for st, v in kv.items()]


def _games(team, n, completed=True):
    return [{"homeTeam": team, "awayTeam": f"Opp{i}", "completed": completed} for i in range(n)]


class _Session:
    def __init__(self, by_key):
        self.by_key, self.calls, self.auth = by_key, [], None

    def get(self, url, params=None, headers=None, timeout=None):
        key = (url.rsplit("/", 1)[-1], params["year"])
        self.calls.append(key)
        self.auth = headers.get("Authorization")
        data = self.by_key.get(key, [])

        class R:
            def raise_for_status(self): pass
            def json(self_inner): return data
        return R()


def test_season_year():
    assert cfb_stats.season_year(datetime.date(2026, 10, 3)) == 2026
    assert cfb_stats.season_year(datetime.date(2027, 1, 5)) == 2026   # bowl season
    assert cfb_stats.season_year(datetime.date(2026, 7, 1)) == 2025


def test_per_game_uses_team_completed_games_and_combos():
    stats = _stats("Gunner Stockton", "Georgia",
                   passing={"YDS": 1250, "TD": 10, "COMPLETIONS": 100, "ATT": 150, "INT": 2},
                   rushing={"YDS": 150, "CAR": 30, "TD": 2})
    games = _games("Georgia", 5) + _games("Georgia", 2, completed=False)
    r = cfb_stats.build_table(stats, games, [], [])["gunner stockton"]
    assert r["pass yards"] == 250 and r["rush yards"] == 30 and r["pass tds"] == 2
    assert r["pass+rush yds"] == 280 and r["rush+rec tds"] == 0.4


def test_shrinks_toward_last_season_and_preseason_uses_it():
    last = _stats("Back", "Ohio State", rushing={"YDS": 1200, "CAR": 240})   # 100/g over 12
    last_g = _games("Ohio State", 12)
    pre = cfb_stats.build_table([], [], last, last_g)["back"]
    assert pre["rush yards"] == 100
    cur = _stats("Back", "Ohio State", rushing={"YDS": 300})                 # 150/g over 2
    r = cfb_stats.build_table(cur, _games("Ohio State", 2), last, last_g)["back"]
    expect = (300 + cfb_stats.SHRINK_K * 100) / (2 + cfb_stats.SHRINK_K)
    assert r["rush yards"] == pytest.approx(expect) and 100 < r["rush yards"] < 150


def test_same_name_on_two_teams_is_left_unprojected():
    stats = (_stats("Josh Smith", "Alabama", receiving={"YDS": 400})
             + _stats("Josh Smith", "Oregon", receiving={"YDS": 100})
             + _stats("Solo Guy", "Oregon", receiving={"YDS": 200}))
    games = _games("Alabama", 4) + _games("Oregon", 4)
    table = cfb_stats.build_table(stats, games, [], [])
    assert table["josh smith"] is None
    assert table["solo guy"]["receiving yards"] == 50


def test_load_projector_needs_key(monkeypatch):
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        cfb_stats.load_projector(session=_Session({}))


def test_load_projector_end_to_end_and_book_labels(monkeypatch):
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    s = _Session({
        ("season", 2026): _stats("Jeremiah Smith", "Ohio State",
                                 receiving={"YDS": 500, "REC": 30, "TD": 5}, rushing={"YDS": 20}),
        ("games", 2026): _games("Ohio State", 5),
    })
    proj = cfb_stats.load_projector(api_key="k", session=s, today=datetime.date(2026, 10, 3))
    assert s.auth == "Bearer k"
    assert s.calls == [("season", 2026), ("games", 2026), ("season", 2025), ("games", 2025)]
    for label in ("Receiving Yards", "Receptions", "Rush + Rec Yards", "Rush+Rec Yds", "Rush+Rec TDs"):
        m = proj("Jeremiah Smith", canonical_stat(label))
        assert m and m > 0, label
    assert proj("Jeremiah Smith", canonical_stat("Rush+Rec Yds")) == 104
    assert proj("Nobody", "pass yards") is None


def test_annotate_uses_cfb_model_when_market_is_silent():
    s = _Session({
        ("season", 2026): _stats("Qb One", "Texas", passing={"YDS": 1500}),
        ("games", 2026): _games("Texas", 6),
    })
    proj = cfb_stats.load_projector(api_key="k", session=s, today=datetime.date(2026, 10, 3))
    prop = Prop(book="PrizePicks", sport="CFB", player="QB One",
                stat="Pass Yards", line=300.5, flavor="demon")
    row = annotate([prop], projectors={"CFB": proj})[0]
    assert row["mean_source"] == "own_model" and row["mean"] == 250.0
    assert 0 < row["hit_prob"] < 0.5
