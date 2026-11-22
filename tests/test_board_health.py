"""Tests for model/board.py source-health helpers."""

import unittest

import pandas as pd

from model.board import source_health, enabled_books


def _df(books):
    return pd.DataFrame([{"book": b, "player": "x", "stat": "Points", "line": 1.0} for b in books])


class SourceHealthTests(unittest.TestCase):
    def test_ok_counts(self):
        df = _df(["PrizePicks"] * 3 + ["Underdog"] * 2)
        rows = {r["book"]: r for r in source_health(df, ["PrizePicks", "Underdog"])}
        self.assertEqual(rows["PrizePicks"]["status"], "ok")
        self.assertEqual(rows["PrizePicks"]["lines"], 3)
        self.assertEqual(rows["Underdog"]["lines"], 2)

    def test_empty_when_enabled_but_no_lines(self):
        df = _df(["PrizePicks"] * 3)
        rows = {r["book"]: r for r in source_health(df, ["PrizePicks", "Underdog"])}
        self.assertEqual(rows["Underdog"]["status"], "empty")
        self.assertEqual(rows["Underdog"]["lines"], 0)

    def test_error_surfaces_from_errors_map(self):
        df = _df(["Underdog"])
        errors = {"PrizePicks/NFL": "HTTP 403 (partner-api + api both refused)"}
        rows = {r["book"]: r for r in source_health(df, ["PrizePicks", "Underdog"], errors)}
        self.assertEqual(rows["PrizePicks"]["status"], "error")
        self.assertIn("403", rows["PrizePicks"]["detail"])
        self.assertEqual(rows["Underdog"]["status"], "ok")

    def test_empty_df(self):
        rows = source_health(pd.DataFrame(), ["PrizePicks"])
        self.assertEqual(rows[0]["status"], "empty")

    def test_none_df(self):
        rows = source_health(None, ["PrizePicks"])
        self.assertEqual(rows[0]["lines"], 0)


class EnabledBooksTests(unittest.TestCase):
    def test_scrapers(self):
        self.assertEqual(enabled_books(True, True, False), ["PrizePicks", "Underdog"])

    def test_only_prizepicks(self):
        self.assertEqual(enabled_books(True, False, False), ["PrizePicks"])

    def test_sgo_covers_both(self):
        self.assertEqual(enabled_books(False, False, True), ["PrizePicks", "Underdog"])

    def test_none(self):
        self.assertEqual(enabled_books(False, False, False), [])


if __name__ == "__main__":
    unittest.main()
