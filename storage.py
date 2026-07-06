"""SQLite persistence.

Two jobs:
  1. Snapshot every fetch so we accumulate a history of lines over time —
     the raw material for future hit-rate backtesting (did the goblin actually
     hit?).
  2. Cheap local cache so repeated runs don't re-hit the books.
"""

import os
import json
import sqlite3
import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "props.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT NOT NULL,
    book TEXT, sport TEXT, player TEXT, team TEXT, opponent TEXT,
    stat TEXT, line REAL, flavor TEXT,
    over_odds INTEGER, under_odds INTEGER,
    hit_prob REAL, edge REAL, mean REAL, mean_source TEXT,
    start_time TEXT, source_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_snap_time ON snapshots(fetched_at);
CREATE INDEX IF NOT EXISTS ix_snap_sport ON snapshots(sport);

CREATE TABLE IF NOT EXISTS postmortems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    n_legs INTEGER, n_hit INTEGER,
    avg_miss_prob REAL, verdict TEXT,
    legs_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_pm_time ON postmortems(created_at);
"""


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.executescript(SCHEMA)
    return c


def save_snapshot(rows):
    """Persist annotated rows (dicts) as a timestamped snapshot."""
    if not rows:
        return 0
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cols = [
        "book", "sport", "player", "team", "opponent", "stat", "line", "flavor",
        "over_odds", "under_odds", "hit_prob", "edge", "mean", "mean_source",
        "start_time", "source_id",
    ]
    with _conn() as c:
        c.executemany(
            f"INSERT INTO snapshots (fetched_at,{','.join(cols)}) "
            f"VALUES (?,{','.join('?' * len(cols))})",
            [[ts] + [r.get(k) for k in cols] for r in rows],
        )
    return len(rows)


def last_fetch_time():
    with _conn() as c:
        row = c.execute("SELECT MAX(fetched_at) FROM snapshots").fetchone()
    return row[0] if row else None


def snapshots_by_game_date(date_iso):
    """Distinct lines whose game started on `date_iso` (YYYY-MM-DD), keeping the
    most recent snapshot of each unique line. Returns a list of dict rows."""
    sql = """
        SELECT book, sport, player, team, stat, line, flavor, hit_prob, mean_source,
               MAX(fetched_at) AS fetched_at
        FROM snapshots
        WHERE substr(start_time, 1, 10) = ?
        GROUP BY book, player, stat, line, flavor
    """
    with _conn() as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(sql, (date_iso,)).fetchall()]


def save_postmortem(legs, verdict, avg_miss_prob=None):
    """Persist one settled-slip post-mortem. `legs` is the analyzed list of
    dicts (player/stat/line/side/actual/result/prob). Returns the new row id."""
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    n_legs = len(legs)
    n_hit = sum(1 for lg in legs if lg.get("result") == "hit")
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO postmortems (created_at,n_legs,n_hit,avg_miss_prob,verdict,legs_json) "
            "VALUES (?,?,?,?,?,?)",
            (ts, n_legs, n_hit, avg_miss_prob, verdict, json.dumps(legs)),
        )
        return cur.lastrowid


def list_postmortems(limit=50):
    """Recent post-mortems (newest first), each a dict with parsed `legs`."""
    with _conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT * FROM postmortems ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["legs"] = json.loads(d.pop("legs_json") or "[]")
        except (TypeError, ValueError):
            d["legs"] = []
        out.append(d)
    return out


def delete_postmortem(pm_id):
    with _conn() as c:
        c.execute("DELETE FROM postmortems WHERE id = ?", (pm_id,))


def game_dates_available():
    """Distinct game dates we have snapshots for (most recent first)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT DISTINCT substr(start_time,1,10) d FROM snapshots "
            "WHERE start_time IS NOT NULL ORDER BY d DESC"
        ).fetchall()
    return [r[0] for r in rows if r[0]]
