"""Deterministic alias generation. No invented nicknames — only mechanical variants
(initial+surname, common shortened first names) derived from the authoritative name.
Accent/suffix/punctuation variants are already handled by normalize_name on both
sides, so they need no stored alias.
"""
from .normalize import normalize_name

# common short forms (deterministic, source-labeled — not nicknames)
SHORT_NAMES = {
    "michael": "mike", "matthew": "matt", "joseph": "joe", "william": "will",
    "robert": "rob", "james": "jim", "daniel": "dan", "christopher": "chris",
    "nicholas": "nick", "anthony": "tony", "benjamin": "ben", "alexander": "alex",
    "zachary": "zack", "joshua": "josh", "andrew": "andy", "edward": "ed",
    "kenneth": "ken", "nathaniel": "nate", "samuel": "sam", "jonathan": "jon",
    "timothy": "tim", "richard": "rich", "thomas": "tom", "charles": "charlie",
}


def generate_player_aliases(display_name):
    """-> [(alias, alias_type, confidence)] deterministic variants (source='deterministic')."""
    norm = normalize_name(display_name)
    parts = norm.split()
    out = []
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        out.append((f"{first[0]} {last}", "initial_surname", 0.9))
        if first in SHORT_NAMES:
            out.append((f"{SHORT_NAMES[first]} {last}", "short_first", 0.85))
    return out
