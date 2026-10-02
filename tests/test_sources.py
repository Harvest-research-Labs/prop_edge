"""Offline tests for the book sources' sport coverage (college football/basketball).

A fake session stands in for requests.Session, so no network is touched.
"""
from config import PRIZEPICKS_LEAGUES, SUPPORTED_SPORTS
from sources import prizepicks, underdog


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


class _Session:
    """Routes PrizePicks GETs: /leagues -> `leagues`, /projections -> one prop per league."""

    def __init__(self, leagues=None, leagues_status=200):
        self.leagues, self.leagues_status = leagues, leagues_status
        self.projection_league_ids = []

    def get(self, url, params=None, headers=None, timeout=None):
        if url.endswith("/leagues"):
            return _Resp({"data": self.leagues or []}, self.leagues_status)
        lid = params["league_id"]
        self.projection_league_ids.append(lid)
        return _Resp({
            "data": [{
                "id": f"p{lid}",
                "attributes": {"stat_type": "Pass Yards", "line_score": "250.5"},
                "relationships": {"new_player": {"data": {"id": "1"}}},
            }],
            "included": [{"type": "new_player", "id": "1",
                          "attributes": {"display_name": "Some Player", "team": "UGA"}}],
        })


def test_college_sports_are_selectable():
    for sport in ("CFB", "CBB"):
        assert sport in SUPPORTED_SPORTS
        assert sport in PRIZEPICKS_LEAGUES


def test_prizepicks_fetches_college_leagues_with_fallback_ids():
    s = _Session(leagues_status=503)  # /leagues down -> hardcoded ids
    props, errors = prizepicks.fetch(["CFB", "CBB"], session=s, throttle=0)
    assert not errors
    assert s.projection_league_ids == [PRIZEPICKS_LEAGUES["CFB"], PRIZEPICKS_LEAGUES["CBB"]]
    assert {p.sport for p in props} == {"CFB", "CBB"}


def test_prizepicks_prefers_live_league_ids_and_ignores_derived_leagues():
    s = _Session(leagues=[
        {"id": "999", "attributes": {"name": "CFB"}},
        {"id": "998", "attributes": {"name": "CFB1H"}},
    ])
    props, _ = prizepicks.fetch(["CFB"], session=s, throttle=0)
    assert s.projection_league_ids == [999]
    assert props[0].sport == "CFB" and props[0].line == 250.5


def test_underdog_maps_college_sport_ids():
    for sid, label in (("CFB", "CFB"), ("NCAAF", "CFB"), ("CBB", "CBB"), ("NCAAB", "CBB")):
        assert underdog.SPORT_MAP[sid] == label
