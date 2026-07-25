"""Name/team normalization for entity resolution.

Handles: accents, punctuation, suffixes (Jr./Sr./II/III/IV), initials, team
abbreviations, common nicknames, and platform truncation. Pure functions, no I/O.
"""
import re
import unicodedata

SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}

# MLB team abbreviation -> canonical team name (subset sufficient for MLB scope).
TEAM_ABBREV = {
    "ARI": "Arizona Diamondbacks", "ATL": "Atlanta Braves", "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox", "CHC": "Chicago Cubs", "CWS": "Chicago White Sox",
    "CIN": "Cincinnati Reds", "CLE": "Cleveland Guardians", "COL": "Colorado Rockies",
    "DET": "Detroit Tigers", "HOU": "Houston Astros", "KC": "Kansas City Royals",
    "LAA": "Los Angeles Angels", "LAD": "Los Angeles Dodgers", "MIA": "Miami Marlins",
    "MIL": "Milwaukee Brewers", "MIN": "Minnesota Twins", "NYM": "New York Mets",
    "NYY": "New York Yankees", "OAK": "Oakland Athletics", "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates", "SD": "San Diego Padres", "SF": "San Francisco Giants",
    "SEA": "Seattle Mariners", "STL": "St. Louis Cardinals", "TB": "Tampa Bay Rays",
    "TEX": "Texas Rangers", "TOR": "Toronto Blue Jays", "WSH": "Washington Nationals",
}
# a couple of alternate abbreviations seen on books
TEAM_ABBREV_ALT = {"CHW": "CWS", "WAS": "WSH", "SDP": "SD", "SFG": "SF", "TBR": "TB", "KCR": "KC"}

NICKNAMES = {"shotime": "shohei ohtani", "sho-time": "shohei ohtani"}


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))


def normalize_name(s):
    """Lowercase, de-accent, drop punctuation and trailing suffixes, collapse spaces."""
    s = strip_accents(s or "").lower()
    s = re.sub(r"[.'`]", "", s)               # drop periods/apostrophes (keep letters/space/hyphen)
    s = re.sub(r"[^a-z0-9\s-]", " ", s)
    parts = [p for p in re.split(r"\s+", s) if p]
    while parts and parts[-1].replace(".", "") in {x.replace(".", "") for x in SUFFIXES}:
        parts.pop()
    norm = " ".join(parts).strip()
    return NICKNAMES.get(norm, norm)


def as_initial_surname(s):
    """If a name looks like 'A. Judge' / 'A Judge', return ('a','judge') else None."""
    n = normalize_name(s)
    parts = n.split()
    if len(parts) == 2 and len(parts[0]) == 1:
        return parts[0], parts[1]
    # 'aaron judge' -> also expose (first-initial, surname) for initial matching
    if len(parts) >= 2:
        return parts[0][0], parts[-1]
    return None


def surname(s):
    n = normalize_name(s)
    parts = n.split()
    return parts[-1] if parts else ""


def canonical_team(token):
    """Team abbreviation OR name -> canonical name (or None)."""
    if not token:
        return None
    t = token.strip().upper()
    t = TEAM_ABBREV_ALT.get(t, t)
    if t in TEAM_ABBREV:
        return TEAM_ABBREV[t]
    # maybe a full name already
    low = strip_accents(token).lower().strip()
    for name in TEAM_ABBREV.values():
        if strip_accents(name).lower() == low:
            return name
    return None
