"""OpenFIGI mapping API with an on-disk cache.

OpenFIGI turns an ISIN, CUSIP or ticker into FIGIs, names, tickers and
security types. It does not return ISINs, so it is used for names, types and
tickers only; ISINs come from N-PORT or are derived from CUSIPs.

With an API key a request may carry 100 jobs and the limit is 25 requests per
6 seconds; without one it is 10 jobs and 25 requests a minute. Requests are
paced to stay inside that limit rather than waiting for a 429.

OpenFIGI is an enrichment, never a requirement: a request that still fails
after its retries (5xx, timeouts, refused connections, endless 429s) is
logged and counted, the values in it are returned as unknown (and not cached,
so a later run asks again), and after ``max_failures`` such failures in a row
the client stops asking for the rest of the run and answers from its cache.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

from ..throttle import THROTTLE

logger = logging.getLogger(__name__)

URL = "https://api.openfigi.com/v3/mapping"
FIELDS = ("name", "ticker", "exchCode", "securityType", "securityType2", "marketSector", "compositeFIGI", "figi")
RETRY_STATUS = (429, 500, 502, 503, 504)


class OpenFIGIError(RuntimeError):
    """A mapping request failed after every retry."""


class OpenFIGI:
    """Batch mapper. Results are cached in a Parquet file keyed by id type and value.

    The cache is rewritten after every call that maps something new, unless
    ``autosave`` is off; a universe run maps thousands of small batches and
    saves the cache itself, now and then. A long call also saves every
    ``checkpoint_every`` requests, so a failure late in it loses little.
    """

    def __init__(
        self,
        cache_path: str | Path,
        api_key: str | None = None,
        timeout: int = 60,
        autosave: bool = True,
        retries: int = 5,
        backoff_s: float = 2.0,
        max_failures: int = 3,
        checkpoint_every: int = 50,
    ):
        self.cache_path = Path(cache_path)
        self.autosave = autosave
        self.api_key = api_key if api_key is not None else os.environ.get("OPENFIGI_API_KEY") or None
        self.batch = 100 if self.api_key else 10
        #: Seconds between requests: 25 per 6 s with a key, 25 a minute without.
        self.interval_s = 0.25 if self.api_key else 2.4
        self.timeout = timeout
        self.retries = retries
        self.backoff_s = backoff_s
        self.max_failures = max_failures
        self.checkpoint_every = checkpoint_every
        self._cache: dict[tuple[str, str], list[dict]] = {}
        self._dirty = False
        #: Run counters, for the run record.
        self.requests = 0
        self.failures = 0
        self.errors: list[str] = []
        self._failures_in_row = 0
        self.down = False
        if self.cache_path.exists():
            df = pd.read_parquet(self.cache_path)
            self._cache = {(t, v): json.loads(r) for t, v, r in df[["id_type", "id_value", "result"]].itertuples(index=False)}

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def _post(self, jobs: list[dict]) -> list[dict]:
        """One mapping request, retried with backoff on 429, 5xx, timeouts and connection errors."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-OPENFIGI-APIKEY"] = self.api_key
        last = None
        for attempt in range(self.retries + 1):
            THROTTLE.wait(URL, min_interval=self.interval_s)
            self.requests += 1
            try:
                r = requests.post(URL, headers=headers, json=jobs, timeout=self.timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                wait = self.backoff_s * 2**attempt
            else:
                if r.status_code not in RETRY_STATUS:
                    r.raise_for_status()
                    out = r.json()
                    if not isinstance(out, list) or len(out) != len(jobs):
                        raise OpenFIGIError(f"OpenFIGI returned {type(out).__name__} for {len(jobs)} jobs")
                    return out
                last = f"HTTP {r.status_code}"
                reset = r.headers.get("ratelimit-reset") if r.status_code == 429 else None
                wait = float(reset) + 0.5 if reset else self.backoff_s * 2**attempt
            if attempt < self.retries:
                logger.info("OpenFIGI %s, retrying in %.1fs", last, wait)
                self._sleep(min(wait, 120))
        raise OpenFIGIError(f"OpenFIGI failed after {self.retries + 1} attempts: {last}")

    def map(self, id_type: str, values: Iterable[str], exch_code: str | None = None) -> dict[str, list[dict]]:
        """Map identifiers of one type. Returns ``{value: [result, ...]}``.

        ``id_type`` is an OpenFIGI type such as ``ID_ISIN``, ``ID_CUSIP`` or
        ``TICKER``. Values with no match map to an empty list, and so do
        values in a request that failed; only the former are cached. Never
        raises for a network or service failure.
        """
        key_type = id_type if exch_code is None else f"{id_type}@{exch_code}"
        values = list(dict.fromkeys(v for v in values if isinstance(v, str) and v))
        todo = [v for v in values if (key_type, v) not in self._cache]
        sent = 0
        for i in range(0, len(todo), self.batch):
            if self.down:
                logger.warning("OpenFIGI is off for this run after %d failures; %d values left unmapped", self.failures, len(todo) - i)
                break
            chunk = todo[i : i + self.batch]
            jobs = [{"idType": id_type, "idValue": v, **({"exchCode": exch_code} if exch_code else {})} for v in chunk]
            try:
                results = self._post(jobs)
            except (OpenFIGIError, requests.RequestException, ValueError) as exc:
                self.failures += 1
                self._failures_in_row += 1
                self.errors.append(str(exc)[:200])
                logger.warning("OpenFIGI %s: %s; %d values left unmapped", id_type, exc, len(chunk))
                if self._failures_in_row >= self.max_failures:
                    self.down = True
                continue
            self._failures_in_row = 0
            for v, res in zip(chunk, results):
                rows = [{k: d.get(k) for k in FIELDS} for d in (res or {}).get("data", [])]
                self._cache[(key_type, v)] = rows
            self._dirty = True
            sent += 1
            if self.checkpoint_every and sent % self.checkpoint_every == 0:
                self.save()
            logger.info("OpenFIGI %s: %d/%d mapped", id_type, min(i + self.batch, len(todo)), len(todo))
        if self._dirty and self.autosave:
            self.save()
        return {v: self._cache.get((key_type, v), []) for v in values}

    def stats(self) -> dict:
        """Requests, failures and whether the client gave up, for a run record."""
        return {"requests": self.requests, "failures": self.failures, "gave_up": self.down,
                "errors": list(dict.fromkeys(self.errors))[:5]}

    def save(self) -> None:
        if not self._dirty and self.cache_path.exists():
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(
            [{"id_type": t, "id_value": v, "result": json.dumps(r)} for (t, v), r in self._cache.items()],
            columns=["id_type", "id_value", "result"],
        )
        tmp = self.cache_path.with_suffix(".tmp.parquet")
        df.to_parquet(tmp, index=False)
        tmp.replace(self.cache_path)
        self._dirty = False


def best_listing(rows: list[dict], prefer_exch: str = "US") -> dict | None:
    """Pick one listing: the composite on ``prefer_exch`` if there is one."""
    if not rows:
        return None
    for r in rows:
        if r.get("exchCode") == prefer_exch:
            return r
    return rows[0]
