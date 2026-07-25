"""Endpoint contract tests for the ProEdge API gateway.

Offline: no live book fetch, no API key required. Verifies the envelope + that
unresolved/unsupported inputs are flagged, not fabricated.
"""
import base64
from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)

ENVELOPE = {"request_id", "service_version", "model_version", "data_timestamp",
            "status", "source", "warnings", "errors", "data"}


def _assert_envelope(j):
    assert ENVELOPE.issubset(j.keys()), f"missing envelope fields: {ENVELOPE - set(j)}"
    assert j["status"] in ("ok", "partial", "error", "unavailable")
    assert isinstance(j["warnings"], list) and isinstance(j["errors"], list)
    assert j["service_version"] and j["model_version"] and j["data_timestamp"]


def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_version():
    j = client.get("/version").json()
    assert len(j["endpoints"]) == 7


def test_extract_no_key_is_not_fabricated():
    tiny = base64.b64encode(b"not-a-real-image").decode()
    j = client.post("/extract", json={"image_b64": tiny}).json()
    _assert_envelope(j)
    # Without a key (or on a bad image) we must NOT invent picks.
    assert j["status"] in ("unavailable", "error")
    assert j["data"]["picks"] == []
    assert j["errors"]


def test_resolve_uses_registry():
    # Isolate from whatever the on-disk registry currently holds (live backfill, etc.)
    import resolver.registry as rreg
    rreg._singleton = rreg.EntityRegistry(":memory:", seed=True)
    j = client.post("/resolve", json={"picks": [
        {"player": "Aaron Judge", "stat": "Hits", "line": 0.5, "sport": "MLB"},
        {"player": "", "stat": "Points", "line": 25.5, "sport": "NBA"}]}).json()
    _assert_envelope(j)
    res = j["data"]["resolved"]
    assert len(res) == 1 and res[0]["entity_id"] == "mlb-p-judge"
    assert res[0]["resolution_method"] == "exact_name" and res[0]["needs_review"] is False
    assert len(j["data"]["unresolved"]) == 1        # NBA unsupported + empty player
    assert j["status"] == "partial"


def test_project_marks_missing_own_model():
    j = client.post("/project", json={"picks": [
        {"player": "Jalen Brunson", "stat": "Points", "sport": "NBA"}]}).json()
    _assert_envelope(j)
    m = j["data"]["means"][0]
    assert m["mean"] is None and m["mean_source"] == "none"   # no NBA own model yet
    assert m["distribution"] in ("poisson", "normal")
    assert j["status"] == "partial"


def test_price_prices_and_flags_unpriced():
    j = client.post("/price", json={"picks": [
        {"stat": "Pitcher Strikeouts", "line": 6.5, "side": "more", "mean": 6.9},
        {"stat": "Points", "line": 25.5, "side": "more"}]}).json()
    _assert_envelope(j)
    priced = j["data"]["priced"]
    assert 0.0 <= priced[0]["hit_prob"] <= 1.0
    assert priced[1]["hit_prob"] is None          # no mean, no market -> unpriced
    assert j["status"] == "partial"


def test_rank_empty_is_honest():
    j = client.post("/rank", json={"rows": []}).json()
    _assert_envelope(j)
    assert j["data"]["best"] is None and j["data"]["board"] == []
    assert j["status"] == "partial"


def test_slip_eval_shows_correlation_flag():
    j = client.post("/slip/eval", json={"legs": [
        {"player": "A", "stat": "Hits", "line": 0.5, "prob": 0.72, "sport": "MLB"},
        {"player": "B", "stat": "TB", "line": 1.5, "prob": 0.66, "sport": "MLB"}]}).json()
    _assert_envelope(j)
    d = j["data"]
    assert 0 < d["naive_combined"] <= 1 and 0 < d["correlation_adjusted"] <= 1
    assert d["correlation_modeled"] is True        # two MLB legs
    assert "weakest_leg" in d and d["risk"] in ("Low", "Medium", "High")


def test_slip_eval_empty_rejected():
    j = client.post("/slip/eval", json={"legs": []}).json()
    assert j["status"] == "error" and j["errors"]


def test_explain_no_board_partial():
    j = client.post("/explain", json={"rows": [], "question": "best pick?"}).json()
    _assert_envelope(j)
    assert j["status"] == "partial"
