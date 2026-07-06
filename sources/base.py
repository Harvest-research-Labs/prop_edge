"""Normalized prop schema shared by every book source.

A `Prop` is one player+stat line from one book. PrizePicks emits up to three
flavors of the same line (standard / goblin / demon); Underdog gives American
odds per side. Both collapse into this one shape so the model and UI never need
to know which book a row came from.
"""

from dataclasses import dataclass, asdict
from typing import Optional

from config import canonical_stat


@dataclass
class Prop:
    book: str                 # "PrizePicks" | "Underdog"
    sport: str                # canonical label, e.g. "MLB"
    player: str
    team: Optional[str] = None
    opponent: Optional[str] = None
    stat: str = ""            # normalized-ish display label, e.g. "Pitcher Strikeouts"
    line: float = 0.0

    # PrizePicks flavor: standard | goblin | demon
    # Underdog flavor:   standard | boosted | discounted
    flavor: str = "standard"

    # American odds per side (Underdog has these; PrizePicks does not).
    over_odds: Optional[int] = None
    under_odds: Optional[int] = None
    # Payout multiplier per side (both books expose some form of this).
    over_payout: Optional[float] = None
    under_payout: Optional[float] = None

    start_time: Optional[str] = None
    combo: bool = False        # PrizePicks combo props (two players in one)
    image_url: Optional[str] = None
    source_id: Optional[str] = None

    # join key for matching the same player+stat across flavors/books
    def group_key(self) -> str:
        return f"{self.sport}|{_norm(self.player)}|{canonical_stat(self.stat)}"

    def to_row(self) -> dict:
        return asdict(self)


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def implied_prob(american: Optional[int]) -> Optional[float]:
    """Convert American odds to implied probability (with vig)."""
    if american is None:
        return None
    if american < 0:
        return (-american) / (-american + 100.0)
    return 100.0 / (american + 100.0)


def devig_two_way(over: Optional[int], under: Optional[int]):
    """Return (p_over, p_under) with the vig removed, or (None, None)."""
    po, pu = implied_prob(over), implied_prob(under)
    if po is None or pu is None or (po + pu) == 0:
        return None, None
    total = po + pu
    return po / total, pu / total
