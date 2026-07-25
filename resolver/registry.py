"""Canonical entity registry + alias store + events (SQLite).

Provenance + timestamps on every record. One connection per instance (':memory:'
works for tests). The service uses a file-backed singleton. Schema self-migrates
(idempotent ALTERs) so an older entities.db upgrades in place.
"""
import os
import sqlite3

from .normalize import normalize_name, canonical_team
from . import seed_mlb

DEFAULT_DB = os.environ.get("RESOLVER_DB",
                            os.path.join(os.path.dirname(__file__), "entities.db"))

_ENTITY_COLS = {"active": "INTEGER DEFAULT 1", "roster_status": "TEXT",
                "position": "TEXT", "jersey_number": "TEXT"}
_TEAM_COLS = {"mlb_id": "INTEGER", "team_updated_at": "TEXT"}


class EntityRegistry:
    def __init__(self, db_path=DEFAULT_DB, seed=True):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()
        if seed and not self._count("entities"):
            self.seed()

    # -- schema / migration --------------------------------------------------
    def _ensure_schema(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS entities(
          entity_id TEXT PRIMARY KEY, entity_type TEXT, canonical_name TEXT,
          display_name TEXT, sport TEXT, league TEXT, team_id TEXT, team_name TEXT,
          source TEXT, source_updated_at TEXT,
          active INTEGER DEFAULT 1, roster_status TEXT, position TEXT, jersey_number TEXT);
        CREATE TABLE IF NOT EXISTS aliases(
          alias_norm TEXT, entity_id TEXT, alias_type TEXT, source TEXT);
        CREATE TABLE IF NOT EXISTS teams(
          team_id TEXT PRIMARY KEY, team_name TEXT, abbrev TEXT, sport TEXT, league TEXT,
          source TEXT, source_updated_at TEXT, mlb_id INTEGER, team_updated_at TEXT);
        CREATE TABLE IF NOT EXISTS events(
          event_id TEXT PRIMARY KEY, sport TEXT, league TEXT, season TEXT,
          event_start TEXT, event_status TEXT, home_team_id TEXT, away_team_id TEXT,
          venue TEXT, double_header TEXT, game_number INTEGER,
          source TEXT, source_updated_at TEXT);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE INDEX IF NOT EXISTS ix_ent_name ON entities(canonical_name, league);
        CREATE INDEX IF NOT EXISTS ix_ent_team ON entities(team_id);
        CREATE INDEX IF NOT EXISTS ix_ent_active ON entities(active, league);
        CREATE INDEX IF NOT EXISTS ix_alias ON aliases(alias_norm, entity_id);
        CREATE INDEX IF NOT EXISTS ix_team_mlb ON teams(mlb_id);
        CREATE INDEX IF NOT EXISTS ix_evt_home ON events(home_team_id, event_start);
        CREATE INDEX IF NOT EXISTS ix_evt_away ON events(away_team_id, event_start);
        CREATE INDEX IF NOT EXISTS ix_evt_season ON events(season);
        """)
        self._migrate("entities", _ENTITY_COLS)
        self._migrate("teams", _TEAM_COLS)
        self.conn.commit()

    def _migrate(self, table, cols):
        have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols.items():
            if name not in have:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def _count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]

    # -- seed (fixture / emergency bootstrap only) ---------------------------
    def seed(self):
        for tid, name, ab in seed_mlb.TEAMS:
            self.conn.execute(
                "INSERT OR REPLACE INTO teams(team_id,team_name,abbrev,sport,league,source,source_updated_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (tid, name, ab, "MLB", "MLB", seed_mlb.SEED_SOURCE, seed_mlb.SEED_UPDATED_AT))
        for eid, display, ab, aliases in seed_mlb.PLAYERS:
            self.add_player(eid, display, ab, aliases,
                            source=seed_mlb.SEED_SOURCE, updated=seed_mlb.SEED_UPDATED_AT)
        self.conn.commit()

    def add_player(self, entity_id, display_name, team_abbrev, aliases=(), sport="MLB",
                   league="MLB", source="seed", updated=None, active=1,
                   roster_status="seed", position=None, jersey=None):
        self.conn.execute(
            "INSERT OR REPLACE INTO entities(entity_id,entity_type,canonical_name,display_name,"
            "sport,league,team_id,team_name,source,source_updated_at,active,roster_status,"
            "position,jersey_number) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (entity_id, "player", normalize_name(display_name), display_name, sport, league,
             "mlb-t-" + team_abbrev, canonical_team(team_abbrev), source, updated,
             active, roster_status, position, jersey))
        for a in aliases:
            self.add_alias(a, entity_id, source=source)

    def add_alias(self, alias, entity_id, alias_type="alias", source="user"):
        self.conn.execute("INSERT INTO aliases VALUES(?,?,?,?)",
                          (normalize_name(alias), entity_id, alias_type, source))
        self.conn.commit()

    # -- upserts (authoritative backfill) ------------------------------------
    def upsert_team(self, mlb_id, name, abbrev, updated, source="mlbstatsapi"):
        self.conn.execute(
            "INSERT OR REPLACE INTO teams(team_id,team_name,abbrev,sport,league,source,"
            "source_updated_at,mlb_id,team_updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("mlb-t-" + abbrev, name, abbrev, "MLB", "MLB", source, updated, mlb_id, updated))

    def upsert_player(self, person_id, name, team_abbrev, position, jersey, roster_status,
                      active, updated, source="mlbstatsapi"):
        self.conn.execute(
            "INSERT OR REPLACE INTO entities(entity_id,entity_type,canonical_name,display_name,"
            "sport,league,team_id,team_name,source,source_updated_at,active,roster_status,"
            "position,jersey_number) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("mlb-p-%s" % person_id, "player", normalize_name(name), name, "MLB", "MLB",
             ("mlb-t-" + team_abbrev) if team_abbrev else None,
             canonical_team(team_abbrev) if team_abbrev else None, source, updated,
             1 if active else 0, roster_status, position, jersey))

    def upsert_event(self, event_id, season, start, status, home_team_id, away_team_id,
                     venue, double_header, game_number, updated, source="mlbstatsapi"):
        self.conn.execute(
            "INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, "MLB", "MLB", season, start, status, home_team_id, away_team_id,
             venue, double_header, game_number, source, updated))

    def mark_inactive_unseen(self, seen_ids, updated):
        """Players previously active but not in this refresh -> inactive (stale/traded/DFA)."""
        rows = self.conn.execute(
            "SELECT entity_id FROM entities WHERE league='MLB' AND active=1 "
            "AND source='mlbstatsapi'").fetchall()
        stale = [r["entity_id"] for r in rows if r["entity_id"] not in seen_ids]
        for eid in stale:
            self.conn.execute("UPDATE entities SET active=0, roster_status=?, source_updated_at=? "
                              "WHERE entity_id=?", ("Not on active roster", updated, eid))
        return len(stale)

    # -- meta ----------------------------------------------------------------
    def set_meta(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, str(value)))
        self.conn.commit()

    def get_meta(self, key):
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    # -- lookups -------------------------------------------------------------
    def get_by_id(self, entity_id):
        return self.conn.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()

    def find_exact(self, norm, league, active_only=True):
        q = "SELECT * FROM entities WHERE canonical_name=? AND league=?"
        if active_only:
            q += " AND (active=1 OR active IS NULL)"
        return self.conn.execute(q, (norm, league)).fetchall()

    def find_by_alias(self, norm, league):
        return self.conn.execute(
            "SELECT e.* FROM entities e JOIN aliases a ON a.entity_id=e.entity_id "
            "WHERE a.alias_norm=? AND e.league=?", (norm, league)).fetchall()

    def find_by_initial_surname(self, initial, sur, league):
        rows = self.conn.execute(
            "SELECT * FROM entities WHERE league=? AND canonical_name LIKE ? "
            "AND (active=1 OR active IS NULL)", (league, f"% {sur}")).fetchall()
        return [r for r in rows if r["canonical_name"].split()[0].startswith(initial)]

    def all_names(self, league):
        return self.conn.execute(
            "SELECT canonical_name, entity_id FROM entities WHERE league=? "
            "AND (active=1 OR active IS NULL)", (league,)).fetchall()

    def team_id_for(self, team_token, league="MLB"):
        name = canonical_team(team_token)
        if not name:
            return None
        r = self.conn.execute("SELECT team_id FROM teams WHERE team_name=? AND league=?",
                              (name, league)).fetchone()
        return r["team_id"] if r else None

    def abbrev_by_mlb_id(self):
        return {r["mlb_id"]: r["abbrev"] for r in
                self.conn.execute("SELECT mlb_id, abbrev FROM teams WHERE mlb_id IS NOT NULL")}

    def find_events(self, team_id, date_prefix=None):
        q = ("SELECT * FROM events WHERE (home_team_id=? OR away_team_id=?)")
        args = [team_id, team_id]
        if date_prefix:
            q += " AND event_start LIKE ?"
            args.append(date_prefix + "%")
        return self.conn.execute(q + " ORDER BY event_start", args).fetchall()


_singleton = None


def get_registry():
    global _singleton
    if _singleton is None:
        _singleton = EntityRegistry(DEFAULT_DB)
    return _singleton
