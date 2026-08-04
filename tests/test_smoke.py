"""End-to-end smoke test using an existing loaded prop.

Offline: builds a real Prop, prices it through the actual model (projections.annotate,
market de-vig — no network), then runs it through /rank and /slip/eval to prove the
services chain over genuine model output.
"""
from fastapi.testclient import TestClient
from sources.base import Prop
from model.projections import annotate
from api.main import app

client = TestClient(app)


def _priced_rows():
    # An Underdog-style MLB prop with two-way odds -> annotate prices it via de-vig,
    # no external calls (projectors=None).
    prop = Prop(book="Underdog", sport="MLB", player="Zack Wheeler",
                stat="Pitcher Strikeouts", line=6.5, flavor="standard",
                over_odds=-125, under_odds=105, team="PHI", opponent="ATL")
    rows = annotate([prop], projectors=None)
    return [r for r in rows if r.get("hit_prob") is not None]


def test_prop_gets_priced():
    rows = _priced_rows()
    assert rows, "annotate should price an Underdog prop with two-way odds"
    assert 0.0 < rows[0]["hit_prob"] <= 1.0
    assert rows[0]["mean_source"] in ("ud_devig", "pp_standard", "own_model")


def test_end_to_end_rank_then_slip():
    rows = _priced_rows()

    # RANK the real priced board
    rj = client.post("/rank", json={"rows": rows}).json()
    assert rj["status"] == "ok"
    best = rj["data"]["best"]
    assert best is not None
    assert best["verdict"] in ("SMART", "LEAN", "THIN", "TRAP", "FADE")
    assert 0.0 < best["model_prob"] <= 1.0
    assert isinstance(best["confidence"], int)

    # Build a leg from the ranked best pick and SLIP-EVAL it
    leg = {"player": best["player"], "stat": best["stat"], "line": best["line"],
           "side": best["side"], "prob": best["model_prob"],
           "flavor": best.get("flavor", "standard"), "sport": best.get("sport")}
    sj = client.post("/slip/eval", json={"legs": [leg]}).json()
    assert sj["status"] == "ok"
    assert 0.0 < sj["data"]["correlation_adjusted"] <= 1.0
    # single leg -> naive == combined, correlation not modeled
    assert sj["data"]["correlation_modeled"] is False
    assert abs(sj["data"]["naive_combined"] - best["model_prob"]) < 0.02
