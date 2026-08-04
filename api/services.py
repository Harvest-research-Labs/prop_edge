"""The seven services — thin wrappers over the existing models.

Each returns a plain dict {data, status, source, warnings, errors, confidence};
api.main wraps it in the ApiResponse envelope. No model logic is changed here.
Unresolved / unsupported inputs are rejected or flagged, never fabricated.
"""
import base64

from config import canonical_stat  # noqa: F401 - kept for compatibility
from sources.base import devig_two_way
from model.projections import over_prob
from model import brain, recommend, screenshot
from resolver.resolve import resolve_pick
from resolver import participation as part_svc
from resolver.registry import get_registry
from . import core


def _reply(data=None, status="ok", source="", warnings=None, errors=None, confidence=None):
    return {"data": data, "status": status, "source": source,
            "warnings": warnings or [], "errors": errors or [], "confidence": confidence}


def _fair(p):
    if not p:
        return None
    return f"-{round(p / (1 - p) * 100)}" if p >= .5 else f"+{round((1 - p) / p * 100)}"


# ---- 1. extract ------------------------------------------------------------
def svc_extract(req):
    key = core.anthropic_key()
    if not key:
        return _reply({"picks": []}, status="unavailable",
                      source="model.screenshot.extract_picks",
                      errors=["ANTHROPIC_API_KEY not configured — screenshot vision extraction is unavailable"])
    try:
        img = base64.b64decode(req.image_b64)
    except Exception as ex:  # noqa: BLE001
        return _reply({"picks": []}, status="error", source="api.extract",
                      errors=[f"invalid base64 image: {ex}"])
    try:
        picks = screenshot.extract_picks(img, req.media_type or "image/png", api_key=key)
    except Exception as ex:  # noqa: BLE001
        return _reply({"picks": []}, status="error",
                      source="model.screenshot.extract_picks", errors=[str(ex)])
    return _reply({"picks": picks, "platform_hint": req.platform_hint}, status="ok",
                  source="model.screenshot.extract_picks")


# ---- 2. resolve ------------------------------------------------------------
def svc_resolve(req):
    """Real entity resolution against the canonical registry (MLB-first).
    High confidence auto-resolves; medium -> needs_review; low/none -> unresolved.
    """
    resolved, needs_review, unresolved = [], [], []
    for p in req.picks:
        r = resolve_pick(p.model_dump())
        {"resolved": resolved, "needs_review": needs_review}.get(r["status"], unresolved).append(r)
    status = "ok" if not (needs_review or unresolved) else "partial"
    conf = round(sum(r["resolution_confidence"] for r in resolved) / len(resolved), 3) if resolved else None
    warnings = ["Only high-confidence (>=0.90) entities auto-resolve; medium -> needs_review, "
                "low/none -> unresolved. None proceed silently into pricing.",
                "Scope: MLB registry (seeded). Other sports return unresolved (unsupported_sport)."]
    return _reply({"resolved": resolved, "needs_review": needs_review, "unresolved": unresolved},
                  status=status, source="resolver (MLB seed registry)",
                  warnings=warnings, confidence=conf)


# ---- 2b. participation -----------------------------------------------------
def svc_participation(req):
    """Lineup + probable-pitcher validation for already-resolved picks. Authoritative
    MLB Stats API; a rostered player is never assumed to participate. Fetch failure
    preserves the last stored snapshot (picks fall to 'hold')."""
    picks = [p.model_dump() for p in req.picks]
    try:
        outputs, meta = part_svc.evaluate(picks, get_registry())
    except Exception as ex:  # noqa: BLE001
        return _reply({"participation": []}, status="error",
                      source="resolver.participation", errors=[str(ex)])
    proceed = sum(1 for o in outputs if o.get("gate", {}).get("action") == "proceed")
    status = "ok" if all(o.get("gate", {}).get("action") == "proceed" for o in outputs) else "partial"
    warnings = ["Participation is validated against posted lineups / probable pitchers; "
                "a rostered player is not treated as confirmed to participate.",
                f"gate mode: {part_svc.participation_gate_mode()}"]
    if meta["errors"]:
        warnings.append("source refresh had failures — affected picks held on last valid state")
    return _reply({"participation": outputs, "gate_mode": part_svc.participation_gate_mode(),
                   "games_seen": meta["games"], "proceed": proceed},
                  status=status, source=f"resolver.participation ({meta['source']})",
                  warnings=warnings + meta["errors"])


# ---- 3. project ------------------------------------------------------------
def svc_project(req):
    sports = {p.sport for p in req.picks}
    projectors = core.get_projectors(sports)
    means, warnings = [], []
    missing = False
    for p in req.picks:
        proj = projectors.get(p.sport)
        mean, src = (None, "none")
        if getattr(p, "matchup_stale", False):
            # opposing probable pitcher changed -> the matchup-dependent mean is invalid
            missing = True
            means.append({"player": p.player, "stat": p.stat, "sport": p.sport,
                          "mean": None, "mean_source": "stale_matchup",
                          "distribution": core.distribution_for(p.stat)})
            warnings.append(f"{p.player}: projection stale — probable pitcher changed; reprice")
            continue
        if proj:
            try:
                m = proj(p.player, p.stat, p.team, p.opponent)
                if m is not None:
                    mean, src = round(float(m), 3), "own_model"
            except Exception as ex:  # noqa: BLE001
                warnings.append(f"projector error for {p.player}: {ex}")
        if mean is None:
            missing = True
        means.append({"player": p.player, "stat": p.stat, "sport": p.sport,
                      "mean": mean, "mean_source": src,
                      "distribution": core.distribution_for(p.stat)})
    if missing:
        warnings.append("No own projection for some picks (only MLB has one today); "
                        "the market mean is supplied via /price.")
    return _reply({"means": means}, status="partial" if missing else "ok",
                  source="model.projections/mlb_stats/matchup", warnings=warnings)


# ---- 4. price --------------------------------------------------------------
def svc_price(req):
    priced, warnings = [], []
    unpriced = 0
    for p in req.picks:
        hp = over_prob(p.line, p.mean, p.stat) if p.mean is not None else None
        p_over, _ = devig_two_way(p.market_over_odds, p.market_under_odds)
        edge = None
        if hp is not None and p_over is not None:
            e = hp - p_over
            edge = round(e if p.side == "more" else -e, 4)
        side_prob = None if hp is None else (hp if p.side == "more" else 1 - hp)
        if side_prob is None and p_over is None:
            unpriced += 1
        priced.append({"stat": p.stat, "line": p.line, "side": p.side,
                       "hit_prob": (round(side_prob, 4) if side_prob is not None else None),
                       "fair_odds": _fair(side_prob), "edge": edge,
                       "mean_used": p.mean, "market_prob_over": (round(p_over, 4) if p_over else None)})
    if unpriced:
        warnings.append(f"{unpriced} pick(s) left unpriced (no mean and no market odds) — not fabricated.")
    return _reply({"priced": priced}, status="partial" if unpriced else "ok",
                  source="model.projections.over_prob + sources.base.devig_two_way", warnings=warnings)


# ---- 5. rank ---------------------------------------------------------------
def svc_rank(req):
    cands = brain.candidates(req.rows, limit=req.limit)
    board = []
    for c in cands:
        v, conf, why = brain.leg_verdict(c.get("model_prob"), c.get("flavor"), c.get("edge"))
        board.append({**c, "side_label": brain._side_label(c.get("side", "more"), c.get("flavor", "standard")),
                      "verdict": v, "confidence": int(conf), "rationale": why})
    best = next((b for b in board if b["verdict"] in ("SMART", "LEAN")), board[0] if board else None)
    top = board[:5]
    avoid = [b for b in board if b["verdict"] in ("TRAP", "FADE")][:3]
    status = "ok" if board else "partial"
    warns = [] if board else ["No priced candidates on the board (empty or unpriced input)."]
    return _reply({"best": best, "top": top, "avoid": avoid, "board": board},
                  status=status, source="model.brain.candidates + leg_verdict", warnings=warns)


# ---- 6. slip/eval ----------------------------------------------------------
def svc_slip(req):
    legs = [l.model_dump() for l in req.legs]
    if not legs:
        return _reply({}, status="error", source="model.recommend.combined_prob",
                      errors=["no legs provided"])
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
    data = {
        "naive_combined": round(naive, 4),
        "correlation_adjusted": round(corr_p, 4),
        "correlation_modeled": dup > 0,
        "correlation_note": (f"+{round(adj * 100)}% for {dup} same-sport leg(s)"
                             if dup else "independent assumption (no same-sport legs)"),
        "weakest_leg": {"player": weak["player"], "stat": weak["stat"], "prob": weak["prob"]},
        "concentration": "Med" if dup else "Low",
        "expected_value": None,   # requires payout data — not provided in this slice
        "risk": risk,
        "leg_probs": [{"player": l["player"], "stat": l["stat"], "prob": l["prob"]} for l in legs],
    }
    return _reply(data, status="ok", source="model.recommend.combined_prob (poisson-binomial)")


# ---- 7. explain ------------------------------------------------------------
def svc_explain(req):
    if not req.rows:
        return _reply({"answer": "No board loaded — nothing to explain.", "cited": []},
                      status="partial", source="model.brain.get_take")
    try:
        take = brain.get_take(req.rows, api_key=core.anthropic_key())
    except Exception as ex:  # noqa: BLE001
        return _reply({"answer": None, "cited": []}, status="error",
                      source="model.brain.get_take", errors=[str(ex)])
    plays = take.get("plays", [])
    answer = f"{take.get('headline', '')} {take.get('strategy', '')}".strip()
    return _reply({"question": req.question, "answer": answer,
                   "engine": take.get("engine_label"),
                   "cited": [{"player": p.get("player"), "stat": p.get("stat"),
                              "verdict": p.get("verdict")} for p in plays[:6]]},
                  status="ok", source="model.brain.get_take",
                  warnings=["Analyst explains the model's numbers; it never generates a probability."])
