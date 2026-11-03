"""Polite HTTP for issuer and reference sources.

One place for the User-Agent, retries and a per-host minimum interval, so a
batch over thousands of funds never hammers a single site.
"""

from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; openbasket; +https://openbasket.leithresearch.org)"
DEFAULT_MIN_INTERVAL = 1.0  # seconds between requests to one host

_last: dict[str, float] = {}
_lock = threading.Lock()
_intervals: dict[str, float] = {}


def set_min_interval(host: str, seconds: float) -> None:
    _intervals[host] = seconds


def _wait(host: str) -> None:
    with _lock:
        gap = _intervals.get(host, DEFAULT_MIN_INTERVAL)
        now = time.monotonic()
        wait = _last.get(host, 0.0) + gap - now
        _last[host] = max(now, _last.get(host, 0.0) + gap)
    if wait > 0:
        time.sleep(wait)


def request(method: str, url: str, *, retries: int = 4, timeout: int = 60, headers: dict | None = None, **kw) -> requests.Response:
    """Send a request, waiting for the host's slot and retrying 429/5xx with backoff."""
    host = urlparse(url).netloc
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    for attempt in range(retries):
        _wait(host)
        r = requests.request(method, url, headers=hdrs, timeout=timeout, **kw)
        if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
            delay = float(r.headers.get("Retry-After") or 2 ** (attempt + 1))
            logger.info("%s %s -> %s, retrying in %.0fs", method, url, r.status_code, delay)
            time.sleep(min(delay, 60))
            continue
        return r
    return r


def get(url: str, **kw) -> requests.Response:
    return request("GET", url, **kw)


def post(url: str, **kw) -> requests.Response:
    return request("POST", url, **kw)
