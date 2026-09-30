"""Manual post-mortem grading: line + pick + actual -> hit/miss/push."""

from model.evaluate import grade_leg


def test_more_hit_and_miss():
    assert grade_leg(5.5, "more", 7) == "hit"
    assert grade_leg(5.5, "more", 4) == "miss"


def test_less_hit_and_miss():
    assert grade_leg(1.5, "less", 1) == "hit"
    assert grade_leg(1.5, "less", 2) == "miss"


def test_push_and_unknown():
    assert grade_leg(2, "more", 2) == "push"
    assert grade_leg(1.5, "more", None) == "unknown"
    assert grade_leg(None, "more", 3) == "unknown"
