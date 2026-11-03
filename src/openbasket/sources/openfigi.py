"""OpenFIGI mapping API with an on-disk cache.

OpenFIGI turns an ISIN, CUSIP or ticker into FIGIs, names, tickers and
security types. It does not return ISINs, so it is used for names, types and
tickers only; ISINs come from N-PORT or are derived from CUSIPs.

With an API key a request may carry 100 jobs and the limit is 25 requests per
6 seconds; without one it is 10 jobs and 25 requests a minute.
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

logger = logging.getLogger(__name__)

URL = "https://api.openfigi.com/v3/mapping"
FIELDS = ("name", "ticker", "exchCode", "securityType", "securityType2", "marketSector", "compositeFIGI", "figi")


class OpenFIGI:
    """Batch mapper. Results are cached in a Parquet file keyed by id type and value."""

    def __init__(self, cache_path: str | Path, api_key: str | None = None, timeout: int = 60):
        self.cache_path = Path(cache_path)
        self.api_key = api_key if api_key is not None else os.environ.get("OPENFIGI_API_KEY") or None
        self.batch = 100 if self.api_key else 10
        self.timeout = timeout
        self._cache: dict[tuple[str, str], list[dict]] = {}
        self._dirty = False
        if self.cache_path.exists():
            df = pd.read_parquet(self.cache_path)
            self._cache = {(t, v): json.loads(r) for t, v, r in df[["id_type", "id_value", "result"]].itertuples(index=False)}

    def _post(self, jobs: list[dict]) -> list[dict]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-OPENFIGI-APIKEY"] = self.api_key
        for attempt in range(6):
            r = requests.post(URL, headers=headers, json=jobs, timeout=self.timeout)
            if r.status_code == 429:
                wait = float(r.headers.get("ratelimit-reset", 6)) + 0.5
                logger.info("OpenFIGI rate limit, waiting %.1fs", wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("OpenFIGI kept returning 429")

    def map(self, id_type: str, values: Iterable[str], exch_code: str | None = None) -> dict[str, list[dict]]:
        """Map identifiers of one type. Returns ``{value: [result, ...]}``.

        ``id_type`` is an OpenFIGI type such as ``ID_ISIN``, ``ID_CUSIP`` or
        ``TICKER``. Values with no match map to an empty list.
        """
        key_type = id_type if exch_code is None else f"{id_type}@{exch_code}"
        values = list(dict.fromkeys(v for v in values if isinstance(v, str) and v))
        todo = [v for v in values if (key_type, v) not in self._cache]
        for i in range(0, len(todo), self.batch):
            chunk = todo[i : i + self.batch]
            jobs = [{"idType": id_type, "idValue": v, **({"exchCode": exch_code} if exch_code else {})} for v in chunk]
            for v, res in zip(chunk, self._post(jobs)):
                rows = [{k: d.get(k) for k in FIELDS} for d in res.get("data", [])]
                self._cache[(key_type, v)] = rows
            self._dirty = True
            logger.info("OpenFIGI %s: %d/%d mapped", id_type, min(i + self.batch, len(todo)), len(todo))
        if self._dirty:
            self.save()
        return {v: self._cache.get((key_type, v), []) for v in values}

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(
            [{"id_type": t, "id_value": v, "result": json.dumps(r)} for (t, v), r in self._cache.items()]
        )
        df.to_parquet(self.cache_path, index=False)
        self._dirty = False


def best_listing(rows: list[dict], prefer_exch: str = "US") -> dict | None:
    """Pick one listing: the composite on ``prefer_exch`` if there is one."""
    if not rows:
        return None
    for r in rows:
        if r.get("exchCode") == prefer_exch:
            return r
    return rows[0]
