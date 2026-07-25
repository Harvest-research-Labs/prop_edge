"""Seed dataset for the MLB-first entity registry.

Curated, representative MLB set (source='seed'). Enough to exercise the resolver
and the required test cases. Backfillable from the MLB Stats API later; for now
provenance is 'seed' with a fixed timestamp.
"""
from .normalize import TEAM_ABBREV

SEED_SOURCE = "seed"
SEED_UPDATED_AT = "2026-07-01T00:00:00+00:00"

# (entity_id, display_name, team_abbrev, [db_aliases])
PLAYERS = [
    ("mlb-p-judge",       "Aaron Judge",        "NYY", []),
    ("mlb-p-jramirez",    "José Ramírez",       "CLE", []),
    ("mlb-p-acuna",       "Ronald Acuña Jr.",   "ATL", ["La Bestia"]),
    ("mlb-p-wsmith-lad",  "Will Smith",         "LAD", []),   # catcher
    ("mlb-p-wsmith-kc",   "Will Smith",         "KC",  []),   # pitcher — duplicate surname/name
    ("mlb-p-soto",        "Juan Soto",          "NYY", []),
    ("mlb-p-ohtani",      "Shohei Ohtani",      "LAD", ["Shotime"]),
    ("mlb-p-wheeler",     "Zack Wheeler",       "PHI", []),
    ("mlb-p-trout",       "Mike Trout",         "LAA", []),
    ("mlb-p-freeman",     "Freddie Freeman",    "LAD", []),
]

# teams straight from the abbreviation map
TEAMS = [("mlb-t-" + ab, name, ab) for ab, name in TEAM_ABBREV.items()]
