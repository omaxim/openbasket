"""robots.txt checks that follow RFC 9309.

Python's ``urllib.robotparser`` stops reading a group at the first blank line
and does not understand ``*`` and ``$`` in paths, so it reports paths such as
``/etfdata/*`` on etf.dws.com as allowed. This module uses ``protego`` (the
parser Scrapy uses), which implements RFC 9309, and treats the outcomes of a
fetch the way the RFC says:

- 2xx: parse the file;
- 4xx (no file): everything is allowed;
- 5xx or a network error: everything is disallowed until a later check
  succeeds.

Every check is recorded in :data:`LOG`, so a run can report which hosts were
checked, when, and with what result.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from protego import Protego

from .. import http

#: Code only; it holds no third-party data.
REDISTRIBUTION = "public"

logger = logging.getLogger(__name__)

#: Product token matched against robots.txt groups. The full User-Agent header
#: sent by :mod:`openbasket.http` starts with ``Mozilla/5.0 (compatible; openbasket``.
AGENT = "openbasket"
MAX_AGE = 24 * 3600  # seconds a fetched robots.txt is trusted


class RobotsDisallowed(RuntimeError):
    """Raised when robots.txt does not allow a URL."""


@dataclass
class RobotsStatus:
    host: str
    status: int | None          # HTTP status of /robots.txt, None on a network error
    fetched_at: float
    parser: Protego | None = None
    error: str | None = None
    checked: list[tuple[str, bool]] = field(default_factory=list)

    def allows(self, url: str) -> bool:
        if self.status is None or self.status >= 500:
            return False
        if self.parser is None:          # 4xx: no robots.txt
            return True
        return bool(self.parser.can_fetch(url, AGENT))

    def summary(self) -> str:
        if self.status is None:
            return f"{self.host}: robots.txt unreachable ({self.error}); treated as disallow-all"
        if self.parser is None and self.status < 500:
            return f"{self.host}: robots.txt HTTP {self.status}; no file, so all paths are allowed"
        if self.status >= 500:
            return f"{self.host}: robots.txt HTTP {self.status}; treated as disallow-all"
        return f"{self.host}: robots.txt HTTP {self.status}, parsed with protego (RFC 9309)"


_CACHE: dict[str, RobotsStatus] = {}
#: Every URL checked in this process, in order: (url, allowed).
LOG: list[tuple[str, bool]] = []


def parse(text: str, host: str = "example.org", status: int = 200) -> RobotsStatus:
    """A status object from robots.txt text (used by tests and fixtures)."""
    return RobotsStatus(host=host, status=status, fetched_at=time.time(), parser=Protego.parse(text))


def status(host: str, *, fetch=None) -> RobotsStatus:
    """The robots.txt status of ``host`` (``scheme://netloc`` or bare netloc), cached for a day."""
    base = host if "://" in host else f"https://{host}"
    netloc = urlparse(base).netloc
    cached = _CACHE.get(netloc)
    if cached and time.time() - cached.fetched_at < MAX_AGE:
        return cached
    url = f"{urlparse(base).scheme or 'https'}://{netloc}/robots.txt"
    get = fetch or (lambda u: http.get(u, timeout=30, retries=2, allow_redirects=True))
    try:
        r = get(url)
    except Exception as exc:  # network error: RFC 9309 says assume complete disallow
        st = RobotsStatus(host=netloc, status=None, fetched_at=time.time(), error=type(exc).__name__)
    else:
        code = r.status_code
        parser = Protego.parse(r.text) if 200 <= code < 300 else None
        st = RobotsStatus(host=netloc, status=code, fetched_at=time.time(), parser=parser)
    logger.info(st.summary())
    _CACHE[netloc] = st
    return st


def allowed(url: str, *, fetch=None) -> bool:
    """True when robots.txt on the URL's host allows the openbasket agent to fetch it."""
    st = status(f"{urlparse(url).scheme}://{urlparse(url).netloc}", fetch=fetch)
    ok = st.allows(url)
    st.checked.append((url, ok))
    LOG.append((url, ok))
    return ok


def require(url: str, *, fetch=None) -> None:
    """Raise :class:`RobotsDisallowed` unless robots.txt allows ``url``."""
    if not allowed(url, fetch=fetch):
        raise RobotsDisallowed(f"robots.txt on {urlparse(url).netloc} disallows {url}")


def get(url: str, **kw):
    """``openbasket.http.get`` behind a robots.txt check."""
    require(url)
    return http.get(url, **kw)


def head(url: str, **kw):
    require(url)
    return http.request("HEAD", url, **kw)
