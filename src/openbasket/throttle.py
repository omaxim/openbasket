"""A process-wide politeness throttle for the sites openbasket reads.

Issuer sites are public web servers, not APIs with published limits. A
universe run asks each of them for thousands of files, so every request to a
host waits until at least ``min_interval`` seconds have passed since the last
request to that host, whichever client made it.
"""

from __future__ import annotations

import threading
import time
from urllib.parse import urlsplit

#: Seconds between requests to one host unless configured otherwise.
DEFAULT_INTERVAL_S = 0.5


class Throttle:
    def __init__(self, default_s: float = DEFAULT_INTERVAL_S, per_host_s: dict[str, float] | None = None):
        self.default_s = default_s
        self.per_host_s = dict(per_host_s or {})
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.waited_s = 0.0
        self.requests = 0

    def interval(self, host: str) -> float:
        return float(self.per_host_s.get(host, self.default_s))

    def wait(self, url: str, min_interval: float | None = None) -> None:
        """Block until a request to ``url``'s host is allowed, then claim the slot.

        ``min_interval`` is the host's own limit when it has one (an API's
        published rate); a configured interval for the host is used instead
        when it is longer.
        """
        host = urlsplit(url).netloc.lower()
        with self._lock:
            now = time.monotonic()
            gap = self.interval(host) if host in self.per_host_s or min_interval is None else float(min_interval)
            if min_interval is not None:
                gap = max(gap, float(min_interval))
            due = self._last.get(host, float("-inf")) + gap
            pause = max(0.0, due - now)
            self._last[host] = now + pause
            self.waited_s += pause
            self.requests += 1
        if pause:
            time.sleep(pause)


THROTTLE = Throttle()


def configure(default_s: float | None = None, per_host_s: dict[str, float] | None = None) -> Throttle:
    """Set the intervals of the shared throttle and return it."""
    if default_s is not None:
        THROTTLE.default_s = float(default_s)
    if per_host_s is not None:
        THROTTLE.per_host_s = {k.lower(): float(v) for k, v in per_host_s.items()}
    return THROTTLE
