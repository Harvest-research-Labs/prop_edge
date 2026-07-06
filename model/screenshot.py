"""Parse a PrizePicks / Underdog slip screenshot into structured picks.

Uses Claude vision (claude-opus-4-8) with a constrained JSON schema so the
output is always a clean list of picks. Requires an Anthropic API key; the
caller passes it in (read from st.secrets / env). If the key or the `anthropic`
package is missing, raises a clear error the UI turns into a friendly message.
"""

import os
import json
import base64

MODEL = "claude-opus-4-8"

SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "player": {"type": "string", "description": "Player full name"},
                    "stat": {"type": "string", "description": "Stat type, e.g. 'Total Bases', 'Points'"},
                    "line": {"type": "number", "description": "The line number"},
                    "side": {"type": "string", "enum": ["more", "less"],
                             "description": "More/Over or Less/Under. Default 'more' if unclear."},
                },
                "required": ["player", "stat", "line", "side"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["picks"],
    "additionalProperties": False,
}

PROMPT = (
    "This is a screenshot from a fantasy props app (PrizePicks or Underdog). "
    "Extract every player prop pick visible. For each pick return the player's "
    "full name, the stat type as written, the numeric line, and whether the "
    "selection is More/Over ('more') or Less/Under ('less'). If the over/under "
    "direction isn't visible, use 'more'. Only include actual player prop "
    "selections, not promotional text or headers."
)


def extract_picks(image_bytes, media_type="image/png", api_key=None, model=MODEL):
    """Return a list of {player, stat, line, side} dicts from a slip image."""
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("The 'anthropic' package isn't installed (pip install anthropic).") from e

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("No Anthropic API key found.")

    client = anthropic.Anthropic(api_key=key)
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(text).get("picks", []) if text else []


# --- Post-mortem: read a settled (graded) slip that didn't hit ------------

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "legs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "player": {"type": "string", "description": "Player full name"},
                    "stat": {"type": "string", "description": "Stat type as written"},
                    "line": {"type": "number", "description": "The line number"},
                    "side": {"type": "string", "enum": ["more", "less"],
                             "description": "More/Over ('more') or Less/Under ('less')"},
                    "actual": {"type": ["number", "null"],
                               "description": "Final actual stat value if shown, else null"},
                    "result": {"type": "string", "enum": ["hit", "miss", "push", "unknown"],
                               "description": "Whether this leg won (hit), lost (miss), pushed, "
                                              "or can't be told from the image (unknown)"},
                },
                "required": ["player", "stat", "line", "side", "actual", "result"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["legs"],
    "additionalProperties": False,
}

RESULT_PROMPT = (
    "This is a screenshot of a SETTLED fantasy props slip (PrizePicks or Underdog) "
    "that has already been graded — the entry did not hit. For every leg, read the "
    "player's full name, the stat type as written, the numeric line, and whether the "
    "selection was More/Over ('more') or Less/Under ('less'). Also read the FINAL "
    "result of each leg: the actual stat value the player ended with (a number, or "
    "null if not shown), and whether that leg won ('hit'), lost ('miss'), pushed "
    "('push'), or is unclear ('unknown'). Use the visual cues the app shows — green/"
    "checkmarks usually mean a leg hit, red/x marks mean it missed. Only include real "
    "player prop legs, not headers or promo text."
)


def extract_results(image_bytes, media_type="image/png", api_key=None, model=MODEL):
    """Return a list of {player, stat, line, side, actual, result} for a graded slip."""
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("The 'anthropic' package isn't installed (pip install anthropic).") from e

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("No Anthropic API key found.")

    client = anthropic.Anthropic(api_key=key)
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        output_config={"format": {"type": "json_schema", "schema": RESULT_SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": RESULT_PROMPT},
            ],
        }],
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(text).get("legs", []) if text else []


# --- Fallback verdict when our own board can't price the legs --------------

ESTIMATE_SCHEMA = {
    "type": "object",
    "properties": {
        "avg_miss_prob": {
            "type": "number",
            "description": "Average realistic pre-game probability (0..1) that the MISSED "
                           "legs would have hit, using typical prop-line knowledge.",
        },
        "reasoning": {
            "type": "string",
            "description": "One or two sentences explaining the assessment.",
        },
    },
    "required": ["avg_miss_prob", "reasoning"],
    "additionalProperties": False,
}


def estimate_loss(legs, api_key=None, model=MODEL):
    """Fallback when no legs are on our board: ask Claude to estimate the
    pre-game hit probability of the missed legs from general sports knowledge.
    Returns {'avg_miss_prob': float, 'reasoning': str} or None if nothing to grade."""
    misses = [lg for lg in legs if lg.get("result") == "miss"]
    if not misses:
        return None
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("The 'anthropic' package isn't installed (pip install anthropic).") from e

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("No Anthropic API key found.")

    lines = []
    for lg in misses:
        actual = lg.get("actual")
        got = f", actual {actual}" if actual is not None else ""
        lines.append(f"- {lg.get('player')} {lg.get('stat')} {lg.get('side')} {lg.get('line')}{got}")
    prompt = (
        "These player-prop legs all MISSED on a settled pick'em slip that lost:\n"
        + "\n".join(lines)
        + "\n\nFrom general sports knowledge of how these lines are typically priced, "
        "estimate the average realistic pre-game probability (0 to 1) that these missed "
        "legs would have hit. ~0.5 means coin-flip lines (lost to variance); well below "
        "0.5 means they were poor picks to begin with; above ~0.58 means they were "
        "good bets that simply didn't come in. Give a one- or two-sentence reasoning."
    )
    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(
        model=model,
        max_tokens=500,
        output_config={"format": {"type": "json_schema", "schema": ESTIMATE_SCHEMA}},
        messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(text) if text else None
