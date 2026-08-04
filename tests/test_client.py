"""API client tests — mocked HTTP. Verifies envelope passthrough + that transport
failures become uniform 'unavailable'/'error' envelopes (no crashes, no fabrication).
"""
import requests
from api import client


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._p = payload
        self.text = text

    def json(self):
        if self._p is None:
            raise ValueError("no json")
        return self._p


def test_post_returns_envelope(monkeypatch):
    monkeypatch.setattr(client.requests, "post",
                        lambda *a, **k: FakeResp(200, {"status": "ok", "data": {"x": 1}}))
    r = client.rank([{"a": 1}])
    assert r["status"] == "ok" and r["data"] == {"x": 1}


def test_validation_error_422(monkeypatch):
    monkeypatch.setattr(client.requests, "post", lambda *a, **k: FakeResp(422, None, "bad body"))
    r = client.slip_eval([])
    assert r["status"] == "error"
    assert any("422" in e or "validation" in e for e in r["errors"])


def test_transport_unavailable(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("connection refused")
    monkeypatch.setattr(client.requests, "post", boom)
    r = client.rank([])
    assert r["status"] == "unavailable" and r["errors"]


def test_timeout(monkeypatch):
    def boom(*a, **k):
        raise requests.Timeout("slow")
    monkeypatch.setattr(client.requests, "post", boom)
    r = client.explain([], "q")
    assert r["status"] == "error" and any("timed out" in e for e in r["errors"])


def test_health(monkeypatch):
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: FakeResp(200, {"status": "ok"}))
    assert client.health() is True
    monkeypatch.setattr(client.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("x")))
    assert client.health() is False


def test_routes_hit_correct_paths(monkeypatch):
    seen = {}

    def fake_post(url, json=None, timeout=None):
        seen["url"], seen["json"] = url, json
        return FakeResp(200, {"status": "ok", "data": None})

    monkeypatch.setattr(client.requests, "post", fake_post)
    client.rank([{"r": 1}], limit=5)
    assert seen["url"].endswith("/rank") and seen["json"]["limit"] == 5
    client.slip_eval([{"prob": 0.7}])
    assert seen["url"].endswith("/slip/eval")
    client.explain([], "why")
    assert seen["url"].endswith("/explain") and seen["json"]["question"] == "why"
    client.extract("abc", "image/png")
    assert seen["url"].endswith("/extract")
