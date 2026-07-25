"""ProEdge API client — the ONLY place that speaks HTTP to the gateway.

The Command Center (and any UI) calls these functions, never raw requests.
Every call returns the gateway's envelope dict; on transport failure it returns
a synthetic envelope with status 'unavailable'/'error' so callers handle one shape.
"""
import os
import uuid
import requests

BASE_URL = os.environ.get("PROEDGE_API_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = float(os.environ.get("PROEDGE_API_TIMEOUT", "12"))


def _synthetic(status, errors, source="api-client"):
    return {"request_id": uuid.uuid4().hex, "service_version": "client",
            "model_version": "unknown", "data_timestamp": "", "status": status,
            "source": source, "confidence": None, "warnings": [], "errors": errors,
            "data": None}


def _post(path, payload):
    url = f"{BASE_URL}{path}"
    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT)
    except requests.Timeout:
        return _synthetic("error", [f"API request to {path} timed out after {TIMEOUT}s"])
    except requests.RequestException as ex:
        return _synthetic("unavailable", [f"API unavailable at {BASE_URL}: {ex}"])
    if r.status_code == 422:
        return _synthetic("error", ["validation error (422)", r.text[:300]])
    if r.status_code >= 400:
        return _synthetic("error", [f"API returned {r.status_code}", r.text[:300]])
    try:
        return r.json()
    except ValueError:
        return _synthetic("error", ["API returned non-JSON response"])


def health():
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=TIMEOUT)
        return r.status_code == 200
    except requests.RequestException:
        return False


def extract(image_b64, media_type="image/png", platform_hint=None):
    return _post("/extract", {"image_b64": image_b64, "media_type": media_type,
                              "platform_hint": platform_hint})


def resolve(picks):
    return _post("/resolve", {"picks": picks})


def participation(picks):
    return _post("/participation", {"picks": picks})


def project(picks):
    return _post("/project", {"picks": picks})


def price(picks):
    return _post("/price", {"picks": picks})


def rank(rows, limit=24):
    return _post("/rank", {"rows": rows, "limit": limit})


def slip_eval(legs):
    return _post("/slip/eval", {"legs": legs})


def explain(rows, question):
    return _post("/explain", {"rows": rows, "question": question})
