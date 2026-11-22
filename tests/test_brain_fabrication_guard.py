"""Tests for the §8 fabrication guard in model/brain.py.

ProEdge has no injury/lineup/news/weather feed, so the guard flags those claims
in the AI's prose. It must be narrow enough never to trip on legitimate
probability/usage/matchup language.
"""

import unittest

from model import brain


class ScanUnsupportedClaimsTests(unittest.TestCase):
    def test_injury_language_flagged(self):
        for text in [
            "LeBron is questionable with an ankle injury",
            "He's a game-time decision tonight",
            "Star guard ruled out, back on a minutes restriction",
            "dealing with a hamstring issue",
        ]:
            self.assertIn("injury/availability", brain.scan_unsupported_claims(text), text)

    def test_lineup_news_flagged(self):
        self.assertIn("lineup/role news", brain.scan_unsupported_claims("He'll be benched tonight"))
        self.assertIn("lineup/role news", brain.scan_unsupported_claims("scratched from the starting lineup"))

    def test_weather_flagged(self):
        self.assertIn("weather", brain.scan_unsupported_claims("high winds expected at the venue"))

    def test_unsourced_report_flagged(self):
        self.assertIn("unsourced report", brain.scan_unsupported_claims("reports say he'll return"))
        self.assertIn("unsourced report", brain.scan_unsupported_claims("expected to play per beat writer"))

    def test_legitimate_analysis_not_flagged(self):
        # No false positives on normal, supported reasoning.
        for text in [
            "The most probable outcome given his high usage rate and team pace.",
            "Strong matchup edge; the model likes the over here.",
            "A short goblin line with a favorable role in a fast-paced game.",
            "Model edge is large, so treat it with suspicion, not excitement.",
            "",
            None,
        ]:
            self.assertEqual(brain.scan_unsupported_claims(text), [], repr(text))

    def test_multiple_categories(self):
        text = "Questionable with a knee injury, and windy conditions too."
        flags = brain.scan_unsupported_claims(text)
        self.assertIn("injury/availability", flags)
        self.assertIn("weather", flags)


class GuardTakeClaimsTests(unittest.TestCase):
    def test_flags_from_all_prose_fields(self):
        take = {
            "headline": "Clean slate",
            "strategy": "Stay disciplined.",
            "plays": [{"rationale": "He is doubtful with a groin injury."}],
            "slip": {"rationale": "Low correlation legs."},
        }
        out = brain.guard_take_claims(take)
        self.assertEqual(out["claim_flags"], ["injury/availability"])

    def test_clean_take_has_empty_flags(self):
        take = {
            "headline": "Sharp slate",
            "strategy": "Favor short, low-correlation slips.",
            "plays": [{"rationale": "High usage, strong pace, good model edge."}],
            "slip": {"rationale": "Independent legs."},
        }
        out = brain.guard_take_claims(take)
        self.assertEqual(out["claim_flags"], [])

    def test_get_take_always_carries_claim_flags(self):
        # get_take must route every returned take through the guard, whichever
        # engine produced it. Stub the heuristic so the test doesn't depend on
        # board internals, and prove an unsupported claim surfaces.
        from unittest import mock
        fake = {"headline": "He is questionable with a knee injury",
                "strategy": "", "plays": [], "slip": {}}
        with mock.patch.object(brain, "heuristic_take", return_value=fake):
            take = brain.get_take([], api_key=None)
        self.assertIn("claim_flags", take)
        self.assertEqual(take["claim_flags"], ["injury/availability"])


if __name__ == "__main__":
    unittest.main()
