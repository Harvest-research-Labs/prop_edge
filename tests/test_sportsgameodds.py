"""Tests for the SportsGameOdds adapter (sources/sportsgameodds.py).

The parser is verified against the documented SGO schema with representative
payloads. The live API is not hit (that needs a key); fetch() must be dormant
and silent without SGO_API_KEY.
"""

import unittest
from unittest import mock

from sources import sportsgameodds as sgo


SAMPLE_EVENT = {
    "leagueID": "NBA",
    "startsAt": "2026-11-22T20:00:00Z",
    "teams": {
        "away": {"teamID": "BOS", "names": {"short": "BOS"}},
        "home": {"teamID": "LAL", "names": {"short": "LAL"}},
    },
    "players": {
        "LEBRON_JAMES_NBA": {"firstName": "LeBron", "lastName": "James", "teamID": "LAL"},
    },
    "odds": {
        # player prop, over -> kept
        "points-LEBRON_JAMES_NBA-game-ou-over": {
            "byBookmaker": {"prizepicks": {"overUnder": "25.5", "odds": "+100", "available": True}}
        },
        # same prop's under -> ignored (we take the over as the line carrier)
        "points-LEBRON_JAMES_NBA-game-ou-under": {
            "byBookmaker": {"prizepicks": {"overUnder": "25.5", "odds": "+100", "available": True}}
        },
        # team total (statEntityID 'all') -> skipped
        "points-all-game-ou-over": {
            "byBookmaker": {"draftkings": {"overUnder": "224.5", "odds": "-110", "available": True}}
        },
        # underdog prop, no players-map entry -> name humanized from the id
        "assists-STEPHEN_CURRY_NBA-game-ou-over": {
            "byBookmaker": {"underdog": {"overUnder": "6.5", "available": True}}
        },
        # unavailable -> skipped
        "rebounds-ANTHONY_DAVIS_NBA-game-ou-over": {
            "byBookmaker": {"prizepicks": {"overUnder": "10.5", "available": False}}
        },
    },
}

BOOKS = {"prizepicks": "PrizePicks", "underdog": "Underdog"}


class ParseEventsTests(unittest.TestCase):
    def setUp(self):
        self.props = sgo.parse_events([SAMPLE_EVENT], BOOKS)
        self.by_player = {p.player: p for p in self.props}

    def test_keeps_only_available_player_over_lines(self):
        # LeBron (PP) + Curry (UD); under is not double-counted; 'all' total and
        # the unavailable line are dropped.
        self.assertEqual(len(self.props), 2)

    def test_lebron_prizepicks_line(self):
        p = self.by_player["LeBron James"]
        self.assertEqual(p.book, "PrizePicks")
        self.assertEqual(p.sport, "NBA")
        self.assertEqual(p.stat, "Points")
        self.assertEqual(p.line, 25.5)
        self.assertEqual(p.team, "LAL")
        self.assertEqual(p.opponent, "BOS @ LAL")
        self.assertEqual(p.start_time, "2026-11-22T20:00:00Z")
        self.assertTrue(p.source_id.startswith("sgo:"))

    def test_underdog_name_humanized_without_players_map(self):
        p = self.by_player["Stephen Curry"]
        self.assertEqual(p.book, "Underdog")
        self.assertEqual(p.stat, "Assists")
        self.assertEqual(p.line, 6.5)

    def test_team_total_and_unavailable_are_excluded(self):
        self.assertNotIn("?", self.by_player)
        self.assertNotIn("Anthony Davis", self.by_player)  # unavailable


class OddIdParsingTests(unittest.TestCase):
    def test_parses_five_components(self):
        c = sgo._parse_odd_id("points-LEBRON_JAMES_NBA-game-ou-over")
        self.assertEqual(c["statID"], "points")
        self.assertEqual(c["statEntityID"], "LEBRON_JAMES_NBA")
        self.assertEqual(c["periodID"], "game")
        self.assertEqual(c["betTypeID"], "ou")
        self.assertEqual(c["sideID"], "over")

    def test_too_few_components_returns_none(self):
        self.assertIsNone(sgo._parse_odd_id("points-over"))


class FetchTests(unittest.TestCase):
    def test_dormant_without_key(self):
        with mock.patch.object(sgo, "SGO_API_KEY", ""):
            props, errors = sgo.fetch(["NBA"])
        self.assertEqual(props, [])
        self.assertEqual(errors, {})

    def test_no_mapped_leagues_returns_empty(self):
        with mock.patch.object(sgo, "SGO_API_KEY", "test-key"):
            props, errors = sgo.fetch(["CRICKET"])  # not in SGO_LEAGUE_MAP
        self.assertEqual(props, [])
        self.assertEqual(errors, {})

    def test_fetch_with_key_parses_payload(self):
        fake_resp = mock.Mock(status_code=200)
        fake_resp.json.return_value = {"success": True, "data": [SAMPLE_EVENT]}
        fake_session = mock.Mock()
        fake_session.get.return_value = fake_resp
        with mock.patch.object(sgo, "SGO_API_KEY", "test-key"):
            props, errors = sgo.fetch(["NBA"], session=fake_session)
        self.assertEqual(errors, {})
        self.assertEqual(len(props), 2)
        # apiKey + leagueID were sent
        _, kwargs = fake_session.get.call_args
        self.assertEqual(kwargs["params"]["apiKey"], "test-key")
        self.assertEqual(kwargs["params"]["leagueID"], "NBA")

    def test_fetch_surfaces_http_error(self):
        fake_resp = mock.Mock(status_code=403)
        fake_session = mock.Mock()
        fake_session.get.return_value = fake_resp
        with mock.patch.object(sgo, "SGO_API_KEY", "test-key"):
            props, errors = sgo.fetch(["NBA"], session=fake_session)
        self.assertEqual(props, [])
        self.assertIn("SportsGameOdds", errors)


if __name__ == "__main__":
    unittest.main()
