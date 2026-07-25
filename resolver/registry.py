"""Canonical entity registry + alias store + events (SQLite).

Provenance + timestamps on every record. 40-man aware: identity (eligible on the
40-man) is separate from participation (active on the 26-man / IL). One connection
per instance (':memory:' for tests); the service uses a file-backed singleton.
Schema self-migrates (idempotent ALTERs).
"""
import os
import json
import sqlite3

from .normalize import normalize_name, canonical_team
from . import seed_mlb

DEFAULT_DB = os.environ.get("RESOLVER_DB",
                            os.path.join(os.path.dirname(__file__), "entities.db"))

_ENTITY_COLS = {
    "active": "INTEGER DEFAULT 1", "roster_status": "TEXT", "position": "TEXT",
    "jersey_number": "TEXT", "eligible": "INTEGER DEFAULT 1", "injury_status": "TEXT",
    "transaction_status": "TEXT", "current_team_id": "TEXT", "previous_team_id": "TEXT",
    "status_updated_at": "TEXT"}
_TEAM_COLS = {"mlb_id": "INTEGER", "team_updated_at": "TEXT"}
_ALIAS_COLS = {"confidence": "REAL DEFAULT 1.0", "updated_at": "TEXT"}


class EntityRegistry:
    def __init__(self, db_path=DEFAULT_DB, seed=True):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()
        if seed and not self._count("entities"):
            self.seed()

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
        CREATE TABLE IF NOT EXISTS events(
          event_id TEXT PRIMARY KEY, sport TEXT, league TEXT, season TEXT,
          event_start TEXT, event_status TEXT, home_team_id TEXT, away_team_id TEXT,
          venue TEXT, double_header TEXT, game_number INTEGER,
          source TEXT, source_updated_at TEXT);
        CREATE TABLE IF NOT EXISTS game_participation(
          event_id TEXT PRIMARY KEY, game_status TEXT, abstract_state TEXT,
          home_probable_id TEXT, away_probable_id TEXT,
          home_lineup TEXT, away_lineup TEXT, lineup_posted INTEGER,
          source TEXT, source_updated_at TEXT);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        """)
        self._migrate("entities", _ENTITY_COLS)
        self._migrate("teams", _TEAM_COLS)
        self._migrate("aliases", _ALIAS_COLS)
        self.conn.executescript("""
        CREATE INDEX IF NOT EXISTS ix_ent_name ON entities(canonical_name, league);
        CREATE INDEX IF NOT EXISTS ix_ent_team ON entities(team_id);
        CREATE INDEX IF NOT EXISTS ix_ent_elig ON entities(eligible, league);
        CREATE INDEX IF NOT EXISTS ix_ent_active ON entities(active, league);
        CREATE INDEX IF NOT EXISTS ix_ent_prev ON entities(previous_team_id);
        CREATE INDEX IF NOT EXISTS ix_alias ON aliases(alias_norm, entity_id);
        CREATE INDEX IF NOT EXISTS ix_team_mlb ON teams(mlb_id);
        CREATE INDEX IF NOT EXISTS ix_evt_home ON events(home_team_id, event_start);
        CREATE INDEX IF NOT EXISTS ix_evt_away ON events(away_team_id, event_start);
        CREATE INDEX IF NOT EXISTS ix_evt_season ON events(season);
        """)
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
                   league="MLB", source="seed", updated=None, active=1, roster_status="seed",
                   position=None, jersey=None):
        tid = "mlb-t-" + team_abbrev
        self.conn.execute(
            "INSERT OR REPLACE INTO entities(entity_id,entity_type,canonical_name,display_name,"
            "sport,league,team_id,team_name,source,source_updated_at,active,roster_status,"
            "position,jersey_number,eligible,current_team_id,status_updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (entity_id, "player", normalize_name(display_name), display_name, sport, league,
             tid, canonical_team(team_abbrev), source, updated, active, roster_status,
             position, jersey, 1, tid, updated))
        for a in aliases:
            self.add_alias(a, entity_id, source=source, updated=updated)

    def add_alias(self, alias, entity_id, alias_type="alias", source="user",
                  confidence=1.0, updated=None):
        self.conn.execute(
            "INSERT INTO aliases(alias_norm,entity_id,alias_type,source,confidence,updated_at)"
            " VALUES(?,?,?,?,?,?)", (normalize_name(alias), entity_id, alias_type, source,
                                     confidence, updated))

    # -- authoritative upserts ----------------------------------------------
    def upsert_team(self, mlb_id, name, abbrev, updated, source="mlbstatsapi"):
        self.conn.execute(
            "INSERT OR REPLACE INTO teams(team_id,team_name,abbrev,sport,league,source,"
            "source_updated_at,mlb_id,team_updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("mlb-t-" + abbrev, name, abbrev, "MLB", "MLB", source, updated, mlb_id, updated))

    def upsert_player_full(self, person_id, name, team_abbrev, position, jersey, roster_status,
                           active, injury_status, transaction_status, eligible, updated,
                           source="mlbstatsapi"):
        eid = "mlb-p-%s" % person_id
        new_team = ("mlb-t-" + team_abbrev) if team_abbrev else None
        prev = self.conn.execute(
            "SELECT team_id, previous_team_id FROM entities WHERE entity_id=?", (eid,)).fetchone()
        previous_team_id = prev["previous_team_id"] if prev else None
        if prev and prev["team_id"] and prev["team_id"] != new_team:
            previous_team_id = prev["team_id"]          # a transaction moved the player
        trow = self.conn.execute("SELECT team_name FROM teams WHERE team_id=?",
                                 (new_team,)).fetchone() if new_team else None
        team_name = (trow["team_name"] if trow                    # authoritative name
                     else (canonical_team(team_abbrev) if team_abbrev else None))
        self.conn.execute(
            "INSERT OR REPLACE INTO entities(entity_id,entity_type,canonical_name,display_name,"
            "sport,league,team_id,team_name,source,source_updated_at,active,roster_status,"
            "position,jersey_number,eligible,injury_status,transaction_status,current_team_id,"
            "previous_team_id,status_updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, "player", normalize_name(name), name, "MLB", "MLB", new_team,
             team_name, source, updated,
             1 if active else 0, roster_status, position, jersey, 1 if eligible else 0,
             injury_status, transaction_status, new_team, previous_team_id, updated))

    def upsert_event(self, event_id, season, start, status, home_team_id, away_team_id,
                     venue, double_header, game_number, updated, source="mlbstatsapi"):
        self.conn.execute(
            "INSERT OR REPLACE INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, "MLB", "MLB", season, start, status, home_team_id, away_team_id,
             venue, double_header, game_number, source, updated))

    def upsert_game_participation(self, event_id, game_status, abstract_state, home_probable_id,
                                  away_probable_id, home_lineup, away_lineup, lineup_posted,
                                  updated, source="mlbstatsapi"):
        """Snapshot a game's lineup / probable-pitcher state. Lineups stored as JSON
        arrays of entity_ids so we can detect scratches and pitcher changes across refreshes."""
        self.conn.execute(
            "INSERT OR REPLACE INTO game_participation(event_id,game_status,abstract_state,"
            "home_probable_id,away_probable_id,home_lineup,away_lineup,lineup_posted,"
            "source,source_updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (event_id, game_status, abstract_state, home_probable_id, away_probable_id,
             json.dumps(home_lineup or []), json.dumps(away_lineup or []),
             1 if lineup_posted else 0, source, updated))

    def get_game_participation(self, event_id):
        r = self.conn.execute("SELECT * FROM game_participation WHERE event_id=?",
                              (event_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["home_lineup"] = json.loads(d["home_lineup"] or "[]")
        d["away_lineup"] = json.loads(d["away_lineup"] or "[]")
        d["lineup_posted"] = bool(d["lineup_posted"])
        return d

    def mark_ineligible_unseen(self, seen_ids, updated):
        """40-man players not seen this refresh -> ineligible + inactive (off the org)."""
        rows = self.conn.execute(
            "SELECT entity_id FROM entities WHERE league='MLB' AND source='mlbstatsapi' "
            "AND (eligible=1 OR eligible IS NULL)").fetchall()
        stale = [r["entity_id"] for r in rows if r["entity_id"] not in seen_ids]
        for eid in stale:
            self.conn.execute("UPDATE entities SET eligible=0, active=0, "
                              "roster_status='Off 40-man', status_updated_at=? WHERE entity_id=?",
                              (updated, eid))
        return len(stale)

    # -- meta ----------------------------------------------------------------
    def set_meta(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, str(value)))
        self.conn.commit()

    def get_meta(self, key):
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    # -- lookups (identity = eligible on the 40-man) -------------------------
    def get_by_id(self, entity_id):
        return self.conn.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()

    def find_exact(self, norm, league):
        return self.conn.execute(
            "SELECT * FROM entities WHERE canonical_name=? AND league=? "
            "AND (eligible=1 OR eligible IS NULL)", (norm, league)).fetchall()

    def find_by_alias(self, norm, league):
        return self.conn.execute(
            "SELECT DISTINCT e.* FROM entities e JOIN aliases a ON a.entity_id=e.entity_id "
            "WHERE a.alias_norm=? AND e.league=? AND (e.eligible=1 OR e.eligible IS NULL)",
            (norm, league)).fetchall()

    def find_by_initial_surname(self, initial, sur, league):
        rows = self.conn.execute(
            "SELECT * FROM entities WHERE league=? AND canonical_name LIKE ? "
            "AND (eligible=1 OR eligible IS NULL)", (league, f"% {sur}")).fetchall()
        return [r for r in rows if r["canonical_name"].split()[0].startswith(initial)]

    def all_names(self, league):
        return self.conn.execute(
            "SELECT canonical_name, entity_id FROM entities WHERE league=? "
            "AND (eligible=1 OR eligible IS NULL)", (league,)).fetchall()

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

    def mlb_id_for_team(self, team_id):
        r = self.conn.execute("SELECT mlb_id FROM teams WHERE team_id=?", (team_id,)).fetchone()
        return r["mlb_id"] if r else None

    def find_events(self, team_id, date_prefix=None):
        q = "SELECT * FROM events WHERE (home_team_id=? OR away_team_id=?)"
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
