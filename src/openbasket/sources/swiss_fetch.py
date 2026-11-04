"""Polite, robots.txt-aware fetching for the Swiss bank sources.

Every request goes through :mod:`openbasket.http` (User-Agent, retries, one
request per second per host). Before the first request to a host its
``robots.txt`` is read and parsed with Protego, an RFC 9309 parser; a URL the
file disallows for our User-Agent raises :class:`RobotsDisallowed` and is
never requested. A missing ``robots.txt`` (404/410) allows everything; any
other failure to read it disallows everything, as RFC 9309 asks.

Downloads are cached under ``cache_dir`` and only written once they are
complete, so a rerun on the same day reads the disk instead of the site.
"""

from __future__ import annotations

import logging
import threading
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from protego import Protego

from .. import http

logger = logging.getLogger(__name__)

#: Code only; what a fetched file may be used for is decided per source.
REDISTRIBUTION = "public"

#: The product token matched against ``User-agent`` lines.
ROBOTS_AGENT = "openbasket"

_robots: dict[str, Protego] = {}
_lock = threading.Lock()


class RobotsDisallowed(PermissionError):
    """The site's robots.txt does not allow this URL."""


def robots_for(url: str) -> Protego:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    with _lock:
        if origin in _robots:
            return _robots[origin]
    try:
        r = http.get(origin + "/robots.txt", timeout=30, retries=2)
        if r.status_code == 200:
            text = r.text
        elif 400 <= r.status_code < 500 and r.status_code != 429:
            # RFC 9309 2.3.1.3: an "unavailable" robots.txt (4xx) means no rules apply.
            text = ""
        else:  # 429 or 5xx: the site is unreachable for now, so do not crawl
            text = "User-agent: *\nDisallow: /\n"
    except Exception as exc:  # unreachable robots.txt: do not crawl
        logger.warning("robots.txt of %s unreadable (%s); treating the site as disallowed", origin, exc)
        text = "User-agent: *\nDisallow: /\n"
    parsed = Protego.parse(text)
    with _lock:
        _robots[origin] = parsed
    return parsed


def allowed(url: str) -> bool:
    return robots_for(url).can_fetch(url, ROBOTS_AGENT)


def get(url: str, **kw):
    """``openbasket.http.get`` after a robots.txt check (also of every redirect target)."""
    if not allowed(url):
        raise RobotsDisallowed(url)
    r = http.get(url, **kw)
    for hop in [*r.history, r]:
        if hop.url != url and not allowed(hop.url):
            raise RobotsDisallowed(f"{url} redirected to {hop.url}")
    return r


def get_bytes(url: str, timeout: int = 120, **kw) -> bytes:
    r = get(url, timeout=timeout, **kw)
    r.raise_for_status()
    return r.content


def cached(cache_dir: str | Path | None, relpath: str, fetch: Callable[[], bytes]) -> bytes:
    """Bytes of ``cache_dir/relpath``, fetching and writing them first if missing."""
    if cache_dir is None:
        return fetch()
    path = Path(cache_dir) / relpath
    if path.exists():
        return path.read_bytes()
    body = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(body)
    tmp.replace(path)
    return body


def today() -> str:
    return date.today().isoformat()
