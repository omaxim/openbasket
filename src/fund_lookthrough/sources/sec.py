"""Downloads from sec.gov.

The SEC asks automated clients to send a User-Agent naming the requester and
an email address, and to stay under ten requests a second. Set
``SEC_USER_AGENT`` in ``.env``.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

NPORT_URL = "https://www.sec.gov/files/dera/data/form-n-port-data-sets/{quarter}_nport.zip"
FUND_TICKERS_URL = "https://www.sec.gov/files/company_tickers_mf.json"


def user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        raise RuntimeError("Set SEC_USER_AGENT in .env, e.g. 'fund-lookthrough research you@example.org'.")
    return ua


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": user_agent(), "Accept-Encoding": "gzip, deflate"})
    return s


def download(url: str, dest: Path, overwrite: bool = False, timeout: int = 1800) -> Path:
    """Stream ``url`` to ``dest`` unless it is already there."""
    dest = Path(dest)
    if dest.exists() and not overwrite:
        logger.info("Using cached %s", dest)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    logger.info("Downloading %s", url)
    with _session().get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    tmp.replace(dest)
    time.sleep(0.2)
    return dest


def fund_tickers() -> pd.DataFrame:
    """SEC's list of fund share classes with tickers.

    Columns: ``cik``, ``series_id``, ``class_id``, ``ticker``. It includes
    ETFs; unit investment trusts such as SPY are absent because they do not
    file N-PORT.
    """
    r = _session().get(FUND_TICKERS_URL, timeout=60)
    r.raise_for_status()
    doc = r.json()
    df = pd.DataFrame(doc["data"], columns=doc["fields"])
    df = df.rename(columns={"seriesId": "series_id", "classId": "class_id", "symbol": "ticker"})
    df["cik"] = df["cik"].astype(str).str.zfill(10)
    df["ticker"] = df["ticker"].str.upper().str.strip()
    return df[["cik", "series_id", "class_id", "ticker"]]
