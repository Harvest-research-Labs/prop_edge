"""PropEdge Brain — the "Smarter Betting" layer.

The quant model in ``projections.py`` *prices* every line (a hit probability
and, where the market gives odds, an edge). The Brain sits on top and
*reasons* about those prices the way a disciplined bettor would:

  - which "edges" are real value vs. the market knowing something we don't,
  - which high-probability legs are genuine anchors vs. juiced traps (Demons),
  - how to build a slip that respects variance and correlation,
  - what the single smart read on the slate is.

Two engines, one output shape:

  * **AI brain** (``engine="ai"``) — used when an Anthropic key is present.
    Claude reasons in natural language over the REAL board numbers we hand it.
  * **Heuristic brain** (``engine="heuristic"``) — deterministic fallback with
    no key, so Smarter Betting always works. Rules over the same numbers.

Doctrine (consistent with the rest of the shop): the Brain only ever reasons
over the lines actually loaded from the books. It never invents a prop, a line,
or a stat line. Every take is labelled with which engine produced it.
"""

from __future__ import annotations

import json

from model import recommend
from model.recommend import _conf_side, allowed_sides, _num

MODEL = "claude-opus-4-8"

# Verdict taxonomy the whole app shares.
VERDICTS = ("SMART", "LEAN", "THIN", "TRAP", "FADE")
VERDICT_ICON = {
    "SMART": "🟢", "LEAN": "🔵", "THIN": "🟡", "TRAP": "🟠", "FADE": "🔴",
}
VERDICT_HELP = {
    "SMART": "Model likes it and the price agrees — a play worth anchoring on.",
    "LEAN":  "A defensible lean, but not a lock — size it down.",
    "THIN":  "Near a coin flip — fine as a rider, dangerous as an anchor.",
    "TRAP":  "Looks tempting (juiced line / big payout) but the math is against it.",
    "FADE":  "No value — the market already prices this side richer than our model; pass.",
}


# ----------------------------------------------------------------------------
# Shared: turn the board into a compact, decision-relevant candidate set.
# ----------------------------------------------------------------------------

def candidates(rows, limit=24):
    """The most decision-relevant lines: one per player, priced, ranked by how
    *interesting* they are (confidence away from a coin flip, plus any edge)."""
    seen = {}
    for r in rows:
        if r.get("hit_prob") is None:
            continue
        prob, side = _conf_side(r)          # confidence on the *placeable* side
        # A real *market* edge only exists when the book posts odds (Underdog).
        # PrizePicks' "edge" is just prob − 50% vs. a coin flip — not value — so
        # we drop it to avoid the inflated-goblin-edge trap.
        has_market = _num(r.get("market_over")) is not None
        edge = _num(r.get("edge")) if has_market else None
        # "interest" favors genuinely bettable plays: high makeable probability
        # plus real market disagreement. Two guards keep the board useful:
        #   - we do NOT reward lines near 0% (a 3%-to-hit Demon is extreme but
        #     useless as a rec), and
        #   - we taper ABOVE ~92% because a near-certain under ("won't homer")
        #     is safe but pays nothing — not a smart *play*, just a lock.
        # The peak sits in the ~0.75–0.92 anchor zone (where Goblins live).
        base = prob if prob <= 0.92 else 0.92 - (prob - 0.92)
        interest = base + 1.5 * (abs(edge) if edge is not None else 0.0)
        key = (r.get("player"), r.get("stat"))
        if key not in seen or interest > seen[key][0]:
            seen[key] = (interest, prob, side, r)
    ranked = sorted(seen.values(), key=lambda t: t[0], reverse=True)[:limit]
    out = []
    for _interest, prob, side, r in ranked:
        # Real market edge only when the book posted odds (see gating above).
        raw = _num(r.get("edge")) if _num(r.get("market_over")) is not None else None
        # Edge FOR the side we're actually recommending (flip sign on Unders).
        side_edge = None if raw is None else (raw if side == "more" else -raw)
        out.append({
            "player": r.get("player"), "team": r.get("team"),
            "opponent": r.get("opponent"), "sport": r.get("sport"),
            "stat": r.get("stat"), "line": r.get("line"),
            "flavor": r.get("flavor") or "standard", "book": r.get("book"),
            "side": side,
            "model_prob": round(float(prob), 4),
            "edge": (round(float(side_edge), 4) if side_edge is not None else None),
            "sides_allowed": list(allowed_sides(r)),
        })
    return out


def _side_label(side, flavor):
    if flavor in ("goblin", "demon"):
        return "More"
    return "More" if side == "more" else "Less"


# ----------------------------------------------------------------------------
# Heuristic brain — deterministic, no key required.
# ----------------------------------------------------------------------------

def _heuristic_verdict(c):
    """Rules-based verdict for one candidate dict."""
    p = c["model_prob"]
    edge = c["edge"]
    flavor = c["flavor"]

    # Demons are deliberately-juiced (hard) lines: big payout, poor probability.
    if flavor == "demon":
        if p >= 0.5:
            return "LEAN", round(p * 100), (
                f"Demon line but the model still gives it {p*100:.0f}% — a rare "
                "juiced line worth a dart, not an anchor.")
        return "TRAP", round((1 - p) * 55 + 20), (
            f"Demon at only {p*100:.0f}% to hit — the payout is bait; the math says pass.")

    # With a real market edge we can separate value from market-blindness.
    if edge is not None:
        if edge >= 0.06 and p >= 0.55:
            return "SMART", min(96, round(p * 100 + edge * 60)), (
                f"{p*100:.0f}% to hit with a +{edge*100:.0f}% edge over the price — "
                "the model and the market disagree in our favor.")
        if edge <= -0.05:
            return "FADE", round(min(90, (0.5 + abs(edge)) * 100)), (
                f"The market prices this side {abs(edge)*100:.0f}% richer than our "
                "model does — no value here; the price already reflects the news. Pass.")

    # Fall back to pure probability.
    if flavor == "goblin" and p >= 0.70:
        return "SMART", round(p * 100), (
            f"Goblin at {p*100:.0f}% — the classic safe anchor: shortened line, high hit rate.")
    if p >= 0.62:
        return "SMART", round(p * 100), (
            f"{p*100:.0f}% to hit — a clean, high-probability lean to build around.")
    if p >= 0.55:
        return "LEAN", round(p * 100), (
            f"{p*100:.0f}% — a defensible lean; size it as a rider, not the anchor.")
    if 0.45 <= p < 0.55:
        return "THIN", round(p * 100), (
            f"Basically a coin flip ({p*100:.0f}%) — thin to hang a slip on.")
    return "TRAP", round((1 - p) * 100), (
        f"Only {p*100:.0f}% the way it's leaning — the model is against this one.")


def leg_verdict(prob, flavor="standard", edge=None):
    """Verdict for a single already-chosen leg. ``prob`` is the hit probability
    of the side being recommended; ``edge`` should be side-relative and only
    passed when it reflects a real market price. Returns (verdict, confidence,
    rationale) so other tabs (e.g. Best Plays) can badge legs with the same
    brain logic the Smart Board uses."""
    return _heuristic_verdict({
        "model_prob": float(prob), "edge": edge, "flavor": flavor or "standard"})


def heuristic_take(rows, n_plays=10, slip_n=4):
    """Deterministic 'smart board' — always available, no API key needed."""
    cands = candidates(rows, limit=max(n_plays, 16))
    plays = []
    for c in cands[:n_plays]:
        verdict, conf, why = _heuristic_verdict(c)
        plays.append({**c, "side_label": _side_label(c["side"], c["flavor"]),
                      "verdict": verdict, "confidence": int(conf), "rationale": why})

    smart = [p for p in plays if p["verdict"] in ("SMART", "LEAN")]
    traps = [p for p in plays if p["verdict"] in ("TRAP", "FADE")]

    # Disciplined slip: the safest distinct-player legs the board offers.
    slip_legs = recommend.best_plays(rows, "safest", n=slip_n)
    combined = recommend.combined_prob(slip_legs) if slip_legs else None
    slip = {
        "legs": [{
            "player": l.get("player"), "stat": l.get("stat"), "line": l.get("line"),
            "side": l.get("side"), "flavor": l.get("flavor") or "standard",
            "model_prob": round(float(l.get("hit_prob") or l.get("prob") or 0), 4),
        } for l in slip_legs],
        "combined_prob": round(float(combined), 4) if combined is not None else None,
        "rationale": (
            f"A {len(slip_legs)}-leg slip built from the highest-probability, "
            "one-per-player legs on the board — fewer legs, safer lines. Every "
            "added leg multiplies risk, so the discipline is to stop early."),
    }

    strategy = (
        f"{len(smart)} plays clear the bar as SMART/LEAN and {len(traps)} are "
        "flagged as traps or fades. Smart betting on this slate means anchoring "
        "on the green legs, keeping slips short (2–4 legs), and refusing the "
        "big-payout Demons unless the model is unusually high on them.")
    headline = (
        f"{len(smart)} genuinely smart leans on the board today"
        if smart else "Thin slate — nothing the model loves; sit on your hands.")

    return {
        "engine": "heuristic",
        "engine_label": "Heuristic brain (no AI key)",
        "headline": headline,
        "strategy": strategy,
        "plays": plays,
        "slip": slip,
    }


# ----------------------------------------------------------------------------
# AI brain — Claude reasons over the same real numbers.
# ----------------------------------------------------------------------------

_SYSTEM = (
    "You are the analytical brain of PropEdge, a disciplined sports-betting "
    "assistant for PrizePicks and Underdog player props. You are given a set of "
    "REAL prop lines already priced by a quantitative model: each has the model's "
    "hit probability and, where the book posts odds, an 'edge' vs. the market.\n\n"
    "Your job is to reason about these prices like a sharp, risk-aware bettor and "
    "decide which are genuinely smart plays. Rules you MUST follow:\n"
    "1. Reason ONLY about the plays provided. Never invent a player, prop, line, "
    "or statistic. You may reference general sports knowledge (role, pace, "
    "usage, injuries, matchup difficulty) but never state a specific fabricated "
    "number as fact.\n"
    "2. A large model edge (>20%) usually means the model is blind to news the "
    "market has priced in — treat huge edges with suspicion, not excitement.\n"
    "3. Demons are deliberately hard (juiced) lines with big payouts; Goblins are "
    "shortened safe lines. Respect that in your verdicts.\n"
    "4. Favor short, low-correlation slips. Same-game / same-player legs correlate.\n"
    "5. Assign each play exactly one verdict from: SMART, LEAN, THIN, TRAP, FADE.\n"
    "Return ONLY valid JSON matching the requested schema — no prose outside it."
)

_SCHEMA_HINT = (
    '{\n'
    '  "headline": "one punchy sentence on the slate",\n'
    '  "strategy": "2-3 sentences of disciplined strategy for THIS slate",\n'
    '  "plays": [\n'
    '    {"player","stat","line","verdict","confidence"(0-100 int),\n'
    '     "rationale":"<=140 chars, grounded in the numbers"}\n'
    '  ],\n'
    '  "slip": {"legs":[{"player","stat","line","side"}],\n'
    '           "rationale":"why these legs, note any correlation"}\n'
    '}'
)


def ai_take(rows, api_key, n_plays=10, model=MODEL):
    """Claude's take over the real board. Raises on any failure so the caller
    can fall back to the heuristic brain and stay honest about which ran."""
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("The 'anthropic' package isn't installed (pip install anthropic).") from e
    if not api_key:
        raise RuntimeError("No Anthropic API key found.")

    cands = candidates(rows, limit=max(n_plays * 2, 24))
    if not cands:
        raise RuntimeError("No priced lines on the board to reason about.")

    payload = [{
        "player": c["player"], "team": c["team"], "opp": c["opponent"],
        "sport": c["sport"], "stat": c["stat"], "line": c["line"],
        "flavor": c["flavor"], "book": c["book"],
        "favored_side": c["side"], "sides_allowed": c["sides_allowed"],
        "model_hit_prob": c["model_prob"], "market_edge": c["edge"],
    } for c in cands]

    prompt = (
        f"Here are {len(payload)} real, priced prop lines from today's board:\n\n"
        f"{json.dumps(payload, indent=1)}\n\n"
        f"Pick the {n_plays} most decision-relevant and return JSON exactly in "
        f"this shape:\n{_SCHEMA_HINT}\n\n"
        "Only use players/stats/lines from the list above."
    )

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model, max_tokens=2000, system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    data = _parse_json(text)

    # Re-attach the trustworthy numbers from OUR board by (player, stat) so the
    # UI never shows an AI-typed probability — only AI reasoning + our math.
    by_key = {(c["player"], c["stat"]): c for c in cands}
    plays = []
    for p in data.get("plays", []):
        base = by_key.get((p.get("player"), p.get("stat")))
        if not base:
            continue  # drop anything not on our real board
        verdict = str(p.get("verdict", "THIN")).upper()
        if verdict not in VERDICTS:
            verdict = "THIN"
        plays.append({
            **base,
            "side_label": _side_label(base["side"], base["flavor"]),
            "verdict": verdict,
            "confidence": int(p.get("confidence") or round(base["model_prob"] * 100)),
            "rationale": str(p.get("rationale", "")).strip()[:180],
        })

    # Ground the recommended slip in real legs too.
    slip_data = data.get("slip", {}) or {}
    legs = []
    for lg in slip_data.get("legs", []):
        base = by_key.get((lg.get("player"), lg.get("stat")))
        if base:
            legs.append({
                "player": base["player"], "stat": base["stat"], "line": base["line"],
                "side": base["side"], "flavor": base["flavor"],
                "model_prob": base["model_prob"],
            })
    combined = recommend.combined_prob(
        [{"prob": l["model_prob"]} for l in legs]) if legs else None

    return {
        "engine": "ai",
        "engine_label": f"AI brain (Claude {model})",
        "headline": str(data.get("headline", "")).strip()[:200],
        "strategy": str(data.get("strategy", "")).strip()[:600],
        "plays": plays or heuristic_take(rows, n_plays)["plays"],
        "slip": {
            "legs": legs,
            "combined_prob": round(float(combined), 4) if combined is not None else None,
            "rationale": str(slip_data.get("rationale", "")).strip()[:400],
        },
    }


def _parse_json(text):
    """Best-effort JSON extraction from a model reply."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start:end + 1])
        raise


# ----------------------------------------------------------------------------
# Orchestrator — one call the app uses; always returns a take.
# ----------------------------------------------------------------------------

def get_take(rows, api_key=None, n_plays=10):
    """Return the smartest take available: AI if a key works, else heuristic.
    Never raises — a failed AI call degrades to the heuristic brain with a note."""
    if api_key:
        try:
            take = ai_take(rows, api_key, n_plays=n_plays)
            take["note"] = None
            return take
        except Exception as e:  # noqa: BLE001 — degrade, don't crash the board
            take = heuristic_take(rows, n_plays=n_plays)
            take["note"] = f"AI brain unavailable ({e}); showing the heuristic brain."
            return take
    take = heuristic_take(rows, n_plays=n_plays)
    take["note"] = ("Add an ANTHROPIC_API_KEY to unlock the AI brain — it reasons "
                    "over these same numbers in natural language.")
    return take
