"""Polite HTTP for issuer and reference sources.

One place for the User-Agent, retries and a per-host minimum interval, so a
batch over thousands of funds never hammers a single site. The wait goes
through the process-wide :data:`openbasket.throttle.THROTTLE`, so requests
made here and by the older clients share one slot per host, and an interval
a run configures for a host (``universe.http``) applies here too when it is
longer.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import requests

from .throttle import THROTTLE

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; openbasket; +https://openbasket.leithresearch.org)"
DEFAULT_MIN_INTERVAL = 1.0  # seconds between requests to one host

_intervals: dict[str, float] = {}


def set_min_interval(host: str, seconds: float) -> None:
    _intervals[host] = seconds


def _wait(url: str) -> None:
    host = urlparse(url).netloc
    THROTTLE.wait(url, min_interval=_intervals.get(host, DEFAULT_MIN_INTERVAL))


def request(method: str, url: str, *, retries: int = 4, timeout: int = 60, headers: dict | None = None, **kw) -> requests.Response:
    """Send a request, waiting for the host's slot and retrying timeouts, dropped connections and 429/5xx with backoff."""
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    for attempt in range(retries):
        _wait(url)
        try:
            r = requests.request(method, url, headers=hdrs, timeout=timeout, **kw)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == retries - 1:
                raise
            delay = 2 ** (attempt + 2)
            logger.info("%s %s -> %s, retrying in %.0fs", method, url, type(exc).__name__, delay)
            time.sleep(delay)
            continue
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
