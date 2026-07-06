"""Central config: HTTP headers, league/sport maps, and the stat-distribution
table the projection model uses.

Everything that is likely to drift (PrizePicks league ids, stat shapes) lives
here so the source/model code stays stable.
"""

# --- HTTP ---------------------------------------------------------------

# These headers are what get PrizePicks' /projections endpoint past its
# PerimeterX bot wall. The Origin/Referer/sec-fetch trio is the important part;
# a bare User-Agent gets a 403 challenge.
PRIZEPICKS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://app.prizepicks.com",
    "Referer": "https://app.prizepicks.com/",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}

UNDERDOG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

PRIZEPICKS_PROJECTIONS_URL = "https://api.prizepicks.com/projections"
PRIZEPICKS_LEAGUES_URL = "https://api.prizepicks.com/leagues"
UNDERDOG_LINES_URL = "https://api.underdogfantasy.com/beta/v5/over_under_lines"

# Seconds to wait between PrizePicks league fetches. The endpoint 429s if you
# hammer it; ~2s between calls is comfortable.
PRIZEPICKS_THROTTLE_SEC = 2.5

# --- Leagues / sports ---------------------------------------------------

# PrizePicks league_id -> our canonical sport label. Only the "full game"
# leagues; halves/quarters/periods are derived and excluded by default.
PRIZEPICKS_LEAGUES = {
    "MLB": 2,
    "NBA": 7,
    "WNBA": 3,
    "NFL": 9,
    "NHL": 8,
    "PGA": 1,
    "TENNIS": 5,
    "SOCCER": 82,
    "UFC": 12,
}

# Underdog tags each player with a sport_id string; map the common ones.
# Filled lazily from the payload, but these are the labels we surface.
SUPPORTED_SPORTS = ["MLB", "NBA", "WNBA", "NFL", "NHL", "SOCCER", "TENNIS", "PGA", "UFC"]

# --- Stat distribution model -------------------------------------------

# How each stat behaves, so the projection model picks the right distribution.
#   kind "discrete" -> Poisson (low-count counting stats)
#   kind "normal"   -> Normal with sigma = cv * mean (high-volume continuous)
# cv is the coefficient of variation used only for normal stats.
# Matched by case-insensitive substring against the book's stat label, longest
# match wins; anything unmatched falls back to DEFAULT_STAT.
STAT_SHAPES = {
    # MLB
    "pitcher strikeouts": {"kind": "discrete"},
    "strikeouts": {"kind": "discrete"},
    "hits allowed": {"kind": "discrete"},
    "earned runs": {"kind": "discrete"},
    "walks": {"kind": "discrete"},
    "total bases": {"kind": "discrete"},
    "hits": {"kind": "discrete"},
    "runs": {"kind": "discrete"},
    "rbis": {"kind": "discrete"},
    "home runs": {"kind": "discrete"},
    "stolen bases": {"kind": "discrete"},
    "hitter fantasy score": {"kind": "normal", "cv": 0.85},
    "pitcher fantasy score": {"kind": "normal", "cv": 0.45},
    # NBA / WNBA
    "points": {"kind": "normal", "cv": 0.30},
    "rebounds": {"kind": "normal", "cv": 0.40},
    "assists": {"kind": "normal", "cv": 0.45},
    "3-pt made": {"kind": "discrete"},
    "pts+rebs+asts": {"kind": "normal", "cv": 0.25},
    "pts+rebs": {"kind": "normal", "cv": 0.27},
    "pts+asts": {"kind": "normal", "cv": 0.27},
    "rebs+asts": {"kind": "normal", "cv": 0.33},
    "blocked shots": {"kind": "discrete"},
    "steals": {"kind": "discrete"},
    "turnovers": {"kind": "discrete"},
    "fantasy score": {"kind": "normal", "cv": 0.28},
    # NFL
    "pass yards": {"kind": "normal", "cv": 0.30},
    "passing yards": {"kind": "normal", "cv": 0.30},
    "rush yards": {"kind": "normal", "cv": 0.45},
    "rushing yards": {"kind": "normal", "cv": 0.45},
    "receiving yards": {"kind": "normal", "cv": 0.55},
    "receptions": {"kind": "discrete"},
    "pass tds": {"kind": "discrete"},
    "pass completions": {"kind": "normal", "cv": 0.20},
    "pass attempts": {"kind": "normal", "cv": 0.18},
    # NHL
    "shots on goal": {"kind": "discrete"},
    "goals": {"kind": "discrete"},
    "saves": {"kind": "normal", "cv": 0.30},
    # Soccer
    "shots attempted": {"kind": "discrete"},
    "shots on target": {"kind": "discrete"},
    "passes": {"kind": "normal", "cv": 0.25},
    "goalie saves": {"kind": "normal", "cv": 0.35},
}

DEFAULT_STAT = {"kind": "normal", "cv": 0.35}

# Extra discrete/continuous shapes for the full stat_type names the books emit.
STAT_SHAPES.update({
    "hits+runs+rbis": {"kind": "discrete"},
    "singles": {"kind": "discrete"},
    "doubles": {"kind": "discrete"},
    "triples": {"kind": "discrete"},
    "plate appearances": {"kind": "discrete"},
    "batter strikeouts": {"kind": "discrete"},
    "batter walks": {"kind": "discrete"},
    "walks allowed": {"kind": "discrete"},
    "earned runs allowed": {"kind": "discrete"},
    "pitching outs": {"kind": "discrete"},
    "pitches thrown": {"kind": "normal", "cv": 0.22},
    "pitch count": {"kind": "normal", "cv": 0.22},
    "fantasy points": {"kind": "normal", "cv": 0.6},
})

# Map each book's stat label to a canonical token so PrizePicks and Underdog
# lines for the same real stat match (for cross-book joins and for sharing a
# projection mean). Keys are already whitespace-normalized + lowercased.
STAT_ALIASES = {
    "tb": "total bases",
    "hitter strikeouts": "batter strikeouts",
    "hitter ks": "batter strikeouts",
    "strikeouts": "pitcher strikeouts",   # MLB "Strikeouts" = pitcher Ks
    "walks": "batter walks",
    "earned runs": "earned runs allowed",
    "hitter fs": "hitter fantasy score",
    "pitcher fs": "pitcher fantasy score",
    # NBA/WNBA combo-stat abbreviations (screenshots show "PRA", books show "Pts+Rebs+Asts")
    "pra": "pts+rebs+asts",
    "pts+rebs+asts": "pts+rebs+asts",
    "pr": "pts+rebs",
    "pts+rebs": "pts+rebs",
    "pa": "pts+asts",
    "pts+asts": "pts+asts",
    "ra": "rebs+asts",
    "rebs+asts": "rebs+asts",
    "3-pointers made": "3-pt made",
    "3pt made": "3-pt made",
}


def canonical_stat(label: str) -> str:
    """Normalized, alias-resolved stat token used for matching across books."""
    s = (label or "").strip().lower()
    # collapse spacing around '+' so "hits + runs + rbis" == "hits+runs+rbis"
    for a, b in (((" + "), "+"), ((" +"), "+"), (("+ "), "+")):
        s = s.replace(a, b)
    return STAT_ALIASES.get(s, s)


def stat_shape(stat_label: str) -> dict:
    """Return the distribution shape for a stat label (longest substring match)."""
    if not stat_label:
        return DEFAULT_STAT
    s = canonical_stat(stat_label)
    best = None
    for key, shape in STAT_SHAPES.items():
        if key in s and (best is None or len(key) > len(best[0])):
            best = (key, shape)
    return best[1] if best else DEFAULT_STAT
