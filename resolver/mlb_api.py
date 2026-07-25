"""MLB Stats API client (statsapi.mlb.com) — the authoritative source for the
registry backfill. Normalizes teams, active rosters, and schedule/events.

Network concerns live here: timeout, retry/backoff, and rate-limit (429) handling.
Tests monkeypatch ``_get`` so the suite stays deterministic and offline.
"""
import time

import requests

BASE = "https://statsapi.mlb.com/api/v1"
SPORT_ID = 1  # MLB


class MlbApiError(RuntimeError):
    pass


def _get(url, params=None, retries=3, timeout=15, backoff=0.5):
    """GET with exponential backoff + Retry-After honoring. Raises MlbApiError."""
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout,
                             headers={"User-Agent": "ProEdge/0.1 (registry backfill)"})
        except requests.RequestException as ex:
            last = ex
        else:
            if r.status_code == 429:                      # rate limited
                wait = float(r.headers.get("Retry-After", backoff * (2 ** attempt)))
                time.sleep(min(wait, 10)); continue
            if r.status_code >= 500:                       # transient server error
                last = MlbApiError(f"{r.status_code} from {url}")
            else:
                try:
                    return r.json()
                except ValueError as ex:
                    raise MlbApiError(f"non-JSON from {url}: {ex}")
        time.sleep(backoff * (2 ** attempt))
    raise MlbApiError(f"GET {url} failed after {retries} attempts: {last}")


# --- normalized fetchers ----------------------------------------------------
def fetch_teams():
    """-> [{mlb_id, name, abbrev, team_name, location}] for the 30 MLB teams."""
    data = _get(f"{BASE}/teams", params={"sportId": SPORT_ID})
    out = []
    for t in data.get("teams", []):
        if t.get("sport", {}).get("id") not in (None, SPORT_ID):
            continue
        out.append({"mlb_id": t["id"], "name": t.get("name"),
                    "abbrev": (t.get("abbreviation") or "").upper(),
                    "team_name": t.get("teamName"), "location": t.get("locationName")})
    return [t for t in out if t["abbrev"]]


def fetch_active_roster(mlb_team_id):
    """-> [{person_id, name, position, jersey, roster_status, active}] active roster."""
    data = _get(f"{BASE}/teams/{mlb_team_id}/roster", params={"rosterType": "active"})
    out = []
    for r in data.get("roster", []):
        person = r.get("person", {})
        status = r.get("status", {})
        out.append({
            "person_id": person.get("id"), "name": person.get("fullName"),
            "position": (r.get("position", {}) or {}).get("abbreviation"),
            "jersey": r.get("jerseyNumber"),
            "roster_status": status.get("description") or status.get("code"),
            "active": (status.get("code") == "A"),
        })
    return [p for p in out if p["person_id"] and p["name"]]


def _classify_status(code, desc):
    """-> (active, injury_status, transaction_status, eligible)."""
    d = (desc or "").lower()
    active = code == "A" or d == "active"
    injury = desc if ("injured" in d or "disabled" in d) else None
    txn = None
    for k in ("optioned", "designated", "restricted", "suspended", "reassigned",
              "released", "paternity", "bereavement", "outrighted"):
        if k in d:
            txn = desc
            break
    return active, injury, txn, True   # on the 40-man => eligible


def fetch_40man_roster(mlb_team_id):
    """-> [{person_id, name, position, jersey, roster_status, active, injury_status,
    transaction_status, eligible}] for the full 40-man roster (incl. IL / optioned)."""
    data = _get(f"{BASE}/teams/{mlb_team_id}/roster", params={"rosterType": "40Man"})
    out = []
    for r in data.get("roster", []):
        person = r.get("person", {})
        status = r.get("status", {})
        code, desc = status.get("code"), status.get("description") or status.get("code")
        active, injury, txn, eligible = _classify_status(code, desc)
        out.append({
            "person_id": person.get("id"), "name": person.get("fullName"),
            "position": (r.get("position", {}) or {}).get("abbreviation"),
            "jersey": r.get("jerseyNumber"), "roster_status": desc,
            "active": active, "injury_status": injury,
            "transaction_status": txn, "eligible": eligible,
        })
    return [p for p in out if p["person_id"] and p["name"]]


def fetch_schedule(date):
    """date 'YYYY-MM-DD' -> [{game_pk, game_date, status, home_mlb_id, away_mlb_id,
    venue, double_header, game_number, season}]."""
    data = _get(f"{BASE}/schedule", params={"sportId": SPORT_ID, "date": date})
    out = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            teams = g.get("teams", {})
            out.append({
                "game_pk": g.get("gamePk"), "game_date": g.get("gameDate"),
                "status": (g.get("status", {}) or {}).get("detailedState"),
                "home_mlb_id": teams.get("home", {}).get("team", {}).get("id"),
                "away_mlb_id": teams.get("away", {}).get("team", {}).get("id"),
                "venue": (g.get("venue", {}) or {}).get("name"),
                "double_header": g.get("doubleHeader", "N"),
                "game_number": g.get("gameNumber", 1),
                "season": str(g.get("season") or (date or "")[:4]),
            })
    return [e for e in out if e["game_pk"]]
