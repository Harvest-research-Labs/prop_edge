"""Canonical entity registry + alias store (SQLite).

Lightweight persistence with source provenance and update timestamps. One
connection per registry instance (so ':memory:' works for tests). The service
uses a file-backed singleton seeded on first use.
"""
import os
import sqlite3

from .normalize import normalize_name, canonical_team
from . import seed_mlb

DEFAULT_DB = os.environ.get("RESOLVER_DB",
                            os.path.join(os.path.dirname(__file__), "entities.db"))


class EntityRegistry:
    def __init__(self, db_path=DEFAULT_DB, seed=True):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()
        if seed and not self._count("entities"):
            self.seed()

    # -- schema / seed -------------------------------------------------------
    def _ensure_schema(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS entities(
          entity_id TEXT PRIMARY KEY, entity_type TEXT, canonical_name TEXT,
          display_name TEXT, sport TEXT, league TEXT, team_id TEXT, team_name TEXT,
          source TEXT, source_updated_at TEXT);
        CREATE TABLE IF NOT EXISTS aliases(
          alias_norm TEXT, entity_id TEXT, alias_type TEXT, source TEXT);
        CREATE TABLE IF NOT EXISTS teams(
          team_id TEXT PRIMARY KEY, team_name TEXT, abbrev TEXT, sport TEXT, league TEXT,
          source TEXT, source_updated_at TEXT);
        CREATE INDEX IF NOT EXISTS ix_ent_name ON entities(canonical_name, league);
        CREATE INDEX IF NOT EXISTS ix_alias ON aliases(alias_norm, entity_id);
        """)
        self.conn.commit()

    def _count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]

    def seed(self):
        for tid, name, ab in seed_mlb.TEAMS:
            self.conn.execute("INSERT OR REPLACE INTO teams VALUES(?,?,?,?,?,?,?)",
                              (tid, name, ab, "MLB", "MLB", seed_mlb.SEED_SOURCE, seed_mlb.SEED_UPDATED_AT))
        for eid, display, ab, aliases in seed_mlb.PLAYERS:
            self.add_player(eid, display, ab, aliases,
                            source=seed_mlb.SEED_SOURCE, updated=seed_mlb.SEED_UPDATED_AT)
        self.conn.commit()

    def add_player(self, entity_id, display_name, team_abbrev, aliases=(),
                   sport="MLB", league="MLB", source="seed", updated=None):
        team_name = canonical_team(team_abbrev)
        self.conn.execute("INSERT OR REPLACE INTO entities VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (entity_id, "player", normalize_name(display_name), display_name,
                           sport, league, "mlb-t-" + team_abbrev, team_name, source, updated))
        for a in aliases:
            self.add_alias(a, entity_id, source=source)
        self.conn.commit()

    def add_alias(self, alias, entity_id, alias_type="alias", source="user"):
        self.conn.execute("INSERT INTO aliases VALUES(?,?,?,?)",
                          (normalize_name(alias), entity_id, alias_type, source))
        self.conn.commit()

    # -- lookups -------------------------------------------------------------
    def get_by_id(self, entity_id):
        return self.conn.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()

    def find_exact(self, norm, league):
        return self.conn.execute(
            "SELECT * FROM entities WHERE canonical_name=? AND league=?", (norm, league)).fetchall()

    def find_by_alias(self, norm, league):
        return self.conn.execute(
            "SELECT e.* FROM entities e JOIN aliases a ON a.entity_id=e.entity_id "
            "WHERE a.alias_norm=? AND e.league=?", (norm, league)).fetchall()

    def find_by_initial_surname(self, initial, sur, league):
        rows = self.conn.execute(
            "SELECT * FROM entities WHERE league=? AND canonical_name LIKE ?",
            (league, f"% {sur}")).fetchall()
        return [r for r in rows if r["canonical_name"].split()[0].startswith(initial)]

    def all_names(self, league):
        return self.conn.execute(
            "SELECT canonical_name, entity_id FROM entities WHERE league=?", (league,)).fetchall()

    def team_id_for(self, team_token, league="MLB"):
        name = canonical_team(team_token)
        if not name:
            return None
        r = self.conn.execute("SELECT team_id FROM teams WHERE team_name=? AND league=?",
                              (name, league)).fetchone()
        return r["team_id"] if r else None


_singleton = None


def get_registry():
    global _singleton
    if _singleton is None:
        _singleton = EntityRegistry(DEFAULT_DB)
    return _singleton
