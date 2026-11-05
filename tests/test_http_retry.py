"""openbasket.http retries timeouts and dropped connections."""

import pytest
import requests

from openbasket import http


class _Ok:
    status_code = 200
    headers = {}


def test_timeout_is_retried(monkeypatch):
    calls = []

    def fake(method, url, **kw):
        calls.append(url)
        if len(calls) < 3:
            raise requests.ReadTimeout("slow")
        return _Ok()

    monkeypatch.setattr(http.requests, "request", fake)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    monkeypatch.setattr(http, "_wait", lambda url: None)
    assert http.get("https://example.org/x").status_code == 200 and len(calls) == 3


def test_last_timeout_is_raised(monkeypatch):
    def fake(method, url, **kw):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(http.requests, "request", fake)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    monkeypatch.setattr(http, "_wait", lambda url: None)
    with pytest.raises(requests.ConnectionError):
        http.get("https://example.org/x", retries=2)
