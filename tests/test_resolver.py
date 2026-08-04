"""Entity-resolver tests (MLB seed registry). Covers the required cases plus one
end-to-end screenshot-text -> resolved-prop example.
"""
import pytest
from resolver.registry import EntityRegistry
from resolver.resolve import resolve_pick


@pytest.fixture()
def reg():
    return EntityRegistry(":memory:")   # fresh, seeded, isolated per test


def R(reg, **pick):
    pick.setdefault("sport", "MLB")
    return resolve_pick(pick, reg=reg)


def test_exact_match(reg):
    r = R(reg, player="Aaron Judge", stat="Hits", line=0.5)
    assert r["status"] == "resolved" and r["entity_id"] == "mlb-p-judge"
    assert r["resolution_method"] == "exact_name" and r["resolution_confidence"] >= 0.90


def test_alias_match(reg):
    r = R(reg, player="La Bestia", stat="Home Runs", line=0.5)
    assert r["status"] == "resolved" and r["entity_id"] == "mlb-p-acuna"
    assert r["resolution_method"] == "alias"


def test_initial_plus_surname(reg):
    r = R(reg, player="A. Judge", stat="Hits", line=0.5)
    assert r["entity_id"] == "mlb-p-judge" and r["resolution_method"] == "initial_surname"
    assert r["status"] == "resolved"


def test_suffix_handling(reg):
    r = R(reg, player="Ronald Acuna Jr", stat="Stolen Bases", line=0.5)
    assert r["entity_id"] == "mlb-p-acuna" and r["status"] == "resolved"


def test_accented_name(reg):
    r = R(reg, player="Jose Ramirez", stat="Total Bases", line=1.5)
    assert r["entity_id"] == "mlb-p-jramirez" and r["status"] == "resolved"


def test_duplicate_surname_is_ambiguous(reg):
    r = R(reg, player="Will Smith", stat="Strikeouts", line=5.5)
    assert r["status"] == "needs_review" and r["resolution_method"] == "ambiguous"
    assert r["entity_id"] is None and len(r["candidates"]) == 2


def test_wrong_league_unsupported(reg):
    r = R(reg, player="Aaron Judge", stat="Points", line=25.5, sport="NBA")
    assert r["status"] == "unresolved" and r["resolution_method"] == "unsupported_sport"


def test_wrong_team_flagged(reg):
    r = R(reg, player="Aaron Judge", stat="Hits", line=0.5, team="BOS")
    assert r["resolution_method"] == "name_team_mismatch" and r["needs_review"] is True
    assert r["resolution_confidence"] < 0.90


def test_event_date_disambiguation(reg):
    r = R(reg, player="Will Smith", stat="Strikeouts", line=5.5, team="LAD",
          event_start="2026-07-25T19:10:00+00:00")
    assert r["status"] == "resolved" and r["entity_id"] == "mlb-p-wsmith-lad"
    assert r["resolution_method"] in ("team_constrained", "event_constrained")


def test_ambiguous_returns_candidates(reg):
    r = R(reg, player="Will Smith", stat="Hits", line=0.5)
    assert r["needs_review"] is True and len(r["candidates"]) == 2
    assert {c["team_name"] for c in r["candidates"]} == {"Los Angeles Dodgers", "Kansas City Royals"}


def test_no_result(reg):
    r = R(reg, player="Nonexistent McGhostface", stat="Hits", line=0.5)
    assert r["status"] == "unresolved" and r["resolution_method"] == "no_match"
    assert r["entity_id"] is None


def test_user_correction_then_resolves(reg):
    first = R(reg, player="Will Smith", stat="Strikeouts", line=5.5)
    assert first["status"] == "needs_review"                      # ambiguous
    corrected = R(reg, player="Will Smith", stat="Strikeouts", line=5.5,
                  entity_id="mlb-p-wsmith-lad")                   # user picks the right one
    assert corrected["status"] == "resolved"
    assert corrected["entity_id"] == "mlb-p-wsmith-lad"
    assert corrected["resolution_method"] == "canonical_id"


def test_end_to_end_screenshot_text_to_resolved_prop(reg):
    # A pick as it would arrive from /extract (raw OCR text), then resolved for pricing.
    extracted = {"player": "J. Ramirez", "stat": "Total Bases", "line": 1.5,
                 "side": "more", "sport": "MLB", "team": "CLE"}
    r = resolve_pick(extracted, reg=reg)
    assert r["status"] == "resolved" and r["entity_id"] == "mlb-p-jramirez"
    assert r["canonical_name"] == "jose ramirez"
    # carries the prop fields + a canonical stat so /price can consume it
    assert r["line"] == 1.5 and r["side"] == "more" and r["canonical_stat"]
    assert r["source"] == "seed" and r["source_updated_at"]
