"""Command Center dispatch layer (Streamlit-free, importable, testable).

Each function routes through the API gateway when ``use_api`` is True, and falls
back to the existing direct model calls otherwise (or if the gateway is down).
Both paths return the SAME normalized shape so the UI renders identically.
Returns (payload, meta) where meta = {source, status, warnings, errors}.

`source` is one of: "api", "local", "local-fallback".
"""
import os

from config import canonical_stat
from model import brain, recommend, screenshot
from api import client
from resolver.resolve import gate_mode


def key():
    return os.environ.get("ANTHROPIC_API_KEY")


def api_available(use_api):
    return bool(use_api) and client.health()


def _meta(source, status="ok", warnings=None, errors=None):
    return {"source": source, "status": status, "warnings": warnings or [], "errors": errors or []}


# ---- ranked board ----------------------------------------------------------
def _local_rank(rows):
    take = brain.get_take(rows, api_key=key()) if rows else {"plays": []}
    plays = take.get("plays", [])
    best = next((p for p in plays if p["verdict"] in ("SMART", "LEAN")), plays[0] if plays else None)
    avoid = [p for p in plays if p["verdict"] in ("TRAP", "FADE")][:3]
    return best, plays[:5], avoid, plays


def ranked_board(rows, use_api):
    if use_api:
        r = client.rank(rows)
        if r["status"] in ("ok", "partial") and r.get("data"):
            d = r["data"]
            return (d.get("best"), d.get("top", []), d.get("avoid", []), d.get("board", []),
                    _meta("api", r["status"], r.get("warnings")))
        b, t, a, brd = _local_rank(rows)
        return b, t, a, brd, _meta("local-fallback", "ok",
                                   warnings=["Gateway rank unavailable — using local model."],
                                   errors=r.get("errors"))
    b, t, a, brd = _local_rank(rows)
    return b, t, a, brd, _meta("local")


# ---- slip evaluation -------------------------------------------------------
def _local_slip(legs):
    ps = [l["prob"] for l in legs if l.get("prob") is not None]
    naive = 1.0
    for p in ps:
        naive *= p
    combined = recommend.combined_prob([{"prob": p} for p in ps]) if ps else 0.0
    sports = [l.get("sport") for l in legs]
    dup = len(sports) - len(set(sports))
    adj = min(0.16, dup * 0.06)
    corr_p = combined * (1 + adj * 0.55)
    weak = min(legs, key=lambda l: l["prob"] if l.get("prob") is not None else 1)
    risk = "Low" if corr_p > .45 else "Medium" if corr_p > .28 else "High"
    return {"naive_combined": round(naive, 4), "correlation_adjusted": round(corr_p, 4),
            "correlation_modeled": dup > 0,
            "correlation_note": (f"+{round(adj*100)}% for {dup} same-sport leg(s)" if dup
                                 else "independent assumption (no same-sport legs)"),
            "weakest_leg": {"player": weak["player"], "stat": weak["stat"], "prob": weak["prob"]},
            "concentration": "Med" if dup else "Low", "risk": risk, "expected_value": None}


def evaluate_slip(legs, use_api):
    if not legs:
        return None, _meta("local", "error", errors=["no legs"])
    if use_api:
        r = client.slip_eval(legs)
        if r["status"] == "ok" and r.get("data"):
            return r["data"], _meta("api", "ok", r.get("warnings"))
        return _local_slip(legs), _meta("local-fallback", "ok",
                                        warnings=["Gateway slip eval unavailable — using local model."],
                                        errors=r.get("errors"))
    return _local_slip(legs), _meta("local")


# ---- explain ---------------------------------------------------------------
def explain_board(rows, question, use_api):
    if use_api:
        r = client.explain(rows, question)
        if r["status"] in ("ok", "partial") and r.get("data"):
            return r["data"], _meta("api", r["status"], r.get("warnings"))
        # fall back to a local grounded take
    if not rows:
        return {"answer": "No board loaded — nothing to explain.", "cited": []}, _meta("local", "partial")
    try:
        take = brain.get_take(rows, api_key=key())
        answer = f"{take.get('headline','')} {take.get('strategy','')}".strip()
        return {"answer": answer, "engine": take.get("engine_label"),
                "cited": [{"player": p.get("player"), "stat": p.get("stat")} for p in take.get("plays", [])[:6]]}, \
            _meta("local")
    except Exception as ex:  # noqa: BLE001
        return {"answer": None, "cited": []}, _meta("local", "error", errors=[str(ex)])


# ---- screenshot extract ----------------------------------------------------
def extract_image(image_bytes, media_type, use_api):
    if use_api:
        import base64
        r = client.extract(base64.b64encode(image_bytes).decode(), media_type)
        picks = (r.get("data") or {}).get("picks", [])
        return picks, _meta("api", r["status"], r.get("warnings"), r.get("errors"))
    k = key()
    if not k:
        return [], _meta("local", "unavailable",
                         errors=["ANTHROPIC_API_KEY not set — screenshot vision extraction is unavailable."])
    try:
        return screenshot.extract_picks(image_bytes, media_type or "image/png", api_key=k), _meta("local")
    except Exception as ex:  # noqa: BLE001
        return [], _meta("local", "error", errors=[str(ex)])


# ---- confirm → price (uses /resolve, /project, /price when API on) ----------
def _match_board(board_rows, player, stat):
    cs = canonical_stat(stat or "")
    for r in board_rows:
        if r.get("player") == player and canonical_stat(r.get("stat", "")) == cs:
            return r
    return None


def _price_from_board(p, stat, board_rows):
    m = _match_board(board_rows, p.get("player"), stat)
    if m and m.get("hit_prob") is not None:
        return {"player": p.get("player"), "stat": m.get("stat"), "line": p.get("line"),
                "side": p.get("side", "more"), "prob": m["hit_prob"],
                "flavor": m.get("flavor", "standard"), "sport": m.get("sport")}
    return None


def price_confirmed(picks, board_rows, use_api):
    """picks: [{player,stat,line,side,sport?}] -> (priced_legs, needs_review, notes, meta).

    /resolve is a MANDATORY gate for covered MLB entities: proceed -> price,
    confirm -> held for review, stop -> not priced. Uncovered sports keep the
    advisory board-match behavior.
    """
    mode = gate_mode()                                  # off | advisory | required
    priced, needs_review, notes = [], [], []
    gate_by_key = {}
    if use_api and mode != "off":
        d = (client.resolve(picks).get("data") or {})
        for r in (d.get("resolved", []) + d.get("needs_review", []) + d.get("unresolved", [])):
            gate_by_key[(r.get("player"), r.get("stat"))] = r
    for p in picks:
        r = gate_by_key.get((p.get("player"), p.get("stat")))
        g = (r or {}).get("gate") or {}
        action = g.get("action", "advisory")
        covered = bool(r and r.get("league"))           # gate only bites covered MLB
        stat = (r.get("canonical_stat") if r else None) or p.get("stat")
        if mode == "required" and covered:
            if action == "confirm":
                needs_review.append(r); continue
            if action == "stop":
                notes.append(f"{p.get('player')} · {stat} — {g.get('reason')}"); continue
        elif mode == "advisory" and covered and action in ("confirm", "stop"):
            notes.append(f"advisory · {p.get('player')} · {stat} — {g.get('reason')}")  # warn, still price
        # price via board, else project+price
        leg = _price_from_board(p, stat, board_rows)
        if leg:
            priced.append(leg); continue
        if use_api and p.get("sport"):
            pj = client.project([{"player": p.get("player"), "stat": stat, "sport": p["sport"]}])
            mean = ((pj.get("data") or {}).get("means") or [{}])[0].get("mean")
            pr = client.price([{"stat": stat, "line": p.get("line", 0),
                                "side": p.get("side", "more"), "mean": mean}])
            hp = ((pr.get("data") or {}).get("priced") or [{}])[0].get("hit_prob")
            if hp is not None:
                priced.append({"player": p.get("player"), "stat": stat, "line": p.get("line"),
                               "side": p.get("side", "more"), "prob": hp, "sport": p.get("sport")})
                continue
        notes.append(f"{p.get('player')} · {stat} — unpriced (no live line matched)")
    status = "partial" if (needs_review or notes) else "ok"
    meta = _meta("api" if use_api else "local", status)
    meta["gate_mode"] = mode
    return priced, needs_review, notes, meta
