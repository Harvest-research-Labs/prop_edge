"""Command Center integration tests — the dispatch layer (proedge_ui) with a
MOCKED gateway. Verifies API routing when up and graceful local fallback when down,
so the UI renders identically either way. (command_center.py itself runs Streamlit
at import; its business logic lives in proedge_ui, which is what we exercise here.)
"""
import proedge_ui


def _env(status="ok", data=None, warnings=None, errors=None):
    return {"status": status, "data": data, "warnings": warnings or [], "errors": errors or []}


BOARD_ITEM = {"player": "A", "stat": "Points", "line": 25.5, "side": "more",
              "model_prob": 0.74, "flavor": "standard", "edge": None, "verdict": "SMART",
              "confidence": 90, "rationale": "x", "side_label": "More", "sport": "NBA"}


def test_ranked_board_uses_api_when_up(monkeypatch):
    monkeypatch.setattr(proedge_ui.client, "rank",
                        lambda rows, limit=24: _env("ok", {"best": BOARD_ITEM, "top": [BOARD_ITEM],
                                                           "avoid": [], "board": [BOARD_ITEM]}))
    best, top, avoid, board, meta = proedge_ui.ranked_board([{"any": 1}], use_api=True)
    assert meta["source"] == "api"
    assert best["player"] == "A" and len(top) == 1


def test_ranked_board_falls_back_when_gateway_down(monkeypatch):
    monkeypatch.setattr(proedge_ui.client, "rank",
                        lambda rows, limit=24: _env("unavailable", None, errors=["API unavailable"]))
    # empty rows -> local path returns Nones without any network/model call
    best, top, avoid, board, meta = proedge_ui.ranked_board([], use_api=True)
    assert meta["source"] == "local-fallback"
    assert best is None and top == [] and meta["warnings"]


def test_evaluate_slip_uses_api(monkeypatch):
    data = {"naive_combined": 0.50, "correlation_adjusted": 0.53, "correlation_modeled": True,
            "correlation_note": "+6% for 1 same-sport leg(s)",
            "weakest_leg": {"player": "B", "stat": "TB", "prob": 0.66},
            "concentration": "Med", "risk": "Medium"}
    monkeypatch.setattr(proedge_ui.client, "slip_eval", lambda legs: _env("ok", data))
    out, meta = proedge_ui.evaluate_slip(
        [{"player": "A", "stat": "Points", "prob": 0.74, "sport": "NBA"}], use_api=True)
    assert meta["source"] == "api" and out["correlation_modeled"] is True


def test_extract_image_uses_api(monkeypatch):
    monkeypatch.setattr(proedge_ui.client, "extract",
                        lambda image_b64, media_type="image/png", platform_hint=None:
                        _env("ok", {"picks": [{"player": "A", "stat": "Points", "line": 25.5, "side": "more"}]}))
    picks, meta = proedge_ui.extract_image(b"fake-image-bytes", "image/png", use_api=True)
    assert meta["source"] == "api" and picks[0]["player"] == "A"


def test_local_path_no_api(monkeypatch):
    # use_api=False must NOT touch the client at all
    def fail(*a, **k):
        raise AssertionError("client should not be called on the local path")
    monkeypatch.setattr(proedge_ui.client, "rank", fail)
    best, top, avoid, board, meta = proedge_ui.ranked_board([], use_api=False)
    assert meta["source"] == "local" and best is None


# ---- resolution gate modes (off | advisory | required) ---------------------
MLB_STOP = {"player": "Hurt Guy", "stat": "Hits", "canonical_stat": "hits", "line": 0.5,
            "side": "more", "league": "MLB", "sport": "MLB",
            "gate": {"action": "stop", "reason": "verified IL/inactive"}}
BOARD_ROW = {"player": "Hurt Guy", "stat": "Hits", "hit_prob": 0.61, "sport": "MLB",
             "flavor": "standard"}


def _resolve_stub(_picks):
    return _env("partial", {"resolved": [], "needs_review": [], "unresolved": [MLB_STOP]})


def test_gate_required_blocks_stop(monkeypatch):
    monkeypatch.setenv("PROEDGE_MLB_RESOLUTION_GATE", "required")
    monkeypatch.setattr(proedge_ui.client, "resolve", _resolve_stub)
    picks = [{"player": "Hurt Guy", "stat": "Hits", "line": 0.5, "side": "more", "sport": "MLB"}]
    priced, needs_review, notes, meta = proedge_ui.price_confirmed(picks, [BOARD_ROW], use_api=True)
    assert priced == [] and meta["gate_mode"] == "required"
    assert any("IL/inactive" in n for n in notes)


def test_gate_advisory_prices_with_warning(monkeypatch):
    monkeypatch.setenv("PROEDGE_MLB_RESOLUTION_GATE", "advisory")
    monkeypatch.setattr(proedge_ui.client, "resolve", _resolve_stub)
    picks = [{"player": "Hurt Guy", "stat": "Hits", "line": 0.5, "side": "more", "sport": "MLB"}]
    priced, needs_review, notes, meta = proedge_ui.price_confirmed(picks, [BOARD_ROW], use_api=True)
    assert len(priced) == 1 and meta["gate_mode"] == "advisory"       # priced anyway
    assert any(n.startswith("advisory") for n in notes)              # but warned


def test_gate_off_skips_resolve(monkeypatch):
    monkeypatch.setenv("PROEDGE_MLB_RESOLUTION_GATE", "off")

    def fail(_picks):
        raise AssertionError("resolve must not be called when gate is off")
    monkeypatch.setattr(proedge_ui.client, "resolve", fail)
    picks = [{"player": "Hurt Guy", "stat": "Hits", "line": 0.5, "side": "more", "sport": "MLB"}]
    priced, needs_review, notes, meta = proedge_ui.price_confirmed(picks, [BOARD_ROW], use_api=True)
    assert len(priced) == 1 and meta["gate_mode"] == "off" and notes == []
