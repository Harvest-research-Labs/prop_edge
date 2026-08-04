"""Shared helpers: versions, response envelope, board loader, projectors.

These mirror app.py's data loading but are Streamlit-free so the API can run
standalone. Nothing here changes model behavior.
"""
import os
import uuid
import datetime

from sources import prizepicks, underdog
from model.projections import annotate
from model import mlb_stats, matchup
import storage

try:
    from config import STAT_SHAPES, DEFAULT_STAT
except Exception:  # noqa: BLE001 - DEFAULT_STAT is optional
    from config import STAT_SHAPES
    DEFAULT_STAT = {"kind": "normal"}

SERVICE_VERSION = "0.1.0"          # this gateway
MODEL_VERSION = "propedge-models-0.1"  # the wrapped model layer


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def anthropic_key():
    return os.environ.get("ANTHROPIC_API_KEY")


def meta(source, status="ok", warnings=None, errors=None, confidence=None,
         data_timestamp=None):
    """The response envelope required on every service reply."""
    return {
        "request_id": uuid.uuid4().hex,
        "service_version": SERVICE_VERSION,
        "model_version": MODEL_VERSION,
        "data_timestamp": data_timestamp or now_iso(),
        "status": status,                       # ok | partial | error | unavailable
        "source": source,
        "confidence": confidence,
        "warnings": warnings or [],
        "errors": errors or [],
    }


def distribution_for(stat):
    """Which distribution the model uses for this stat (longest-substring match)."""
    s = (stat or "").lower()
    best = None
    for k, v in STAT_SHAPES.items():
        if k in s and (best is None or len(k) > len(best[0])):
            best = (k, v)
    kind = (best[1] if best else DEFAULT_STAT).get("kind", "normal")
    return "poisson" if kind == "discrete" else "normal"


def get_projectors(sports):
    """Own per-player projection models (MLB today) — mirrors app.py."""
    projectors = {}
    if "MLB" in sports:
        try:
            base = mlb_stats.load_projector()
            try:
                adj = matchup.build_adjuster(datetime.date.today().isoformat())
            except Exception:  # noqa: BLE001
                adj = None

            def mlb_proj(name, stat, team=None, opp=None, _b=base, _a=adj):
                m = _b(name, stat, team, opp)
                return _a(stat, m, team, opp) if _a else m
            projectors["MLB"] = mlb_proj
        except Exception:  # noqa: BLE001
            pass
    return projectors


def load_board(sports, use_pp=True, use_ud=True):
    """Fetch both books + price every line. Returns (rows, errors, logs).

    Live network path (needs book access). The offline smoke test does not call
    this — it prices synthetic Prop objects through annotate() directly.
    """
    props, errors, logs = [], {}, []
    if use_pp:
        pp, e = prizepicks.fetch(sports, log=logs.append); props += pp; errors.update(e)
    if use_ud:
        ud, e = underdog.fetch(sports, log=logs.append); props += ud; errors.update(e)
    rows = annotate(props, projectors=get_projectors(sports))
    try:
        storage.save_snapshot(rows)
    except Exception:  # noqa: BLE001
        pass
    return rows, errors, logs
