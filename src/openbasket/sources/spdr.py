"""SPDR (State Street) daily holdings files.

Fund list
    ``https://www.ssga.com/bin/v1/ssmp/fund/fundfinder`` returns every ETF on
    a regional site. Each entry's ``keywords`` text includes the fund's ISIN,
    and its ``documentPdf`` list links the daily holdings workbook.
Holdings
    ``holdings-daily-<region>-en-<ticker>.xlsx``: a few header lines (fund
    name, ticker, as-of date), then a table whose header row starts with
    ``Name`` (US) or ``ISIN`` (EMEA, i.e. UCITS funds).

US files identify each line by ``Identifier`` (a CUSIP, an ISIN for some
bonds, or an internal code) and ``SEDOL``; EMEA files have an ``ISIN``
column. Weights are percent of the fund (``Weight`` or ``Percent of Fund``).
"""

from __future__ import annotations

import io
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date
from hashlib import sha1
from pathlib import Path

import pandas as pd
import requests

from ..identifiers import is_valid_cusip, is_valid_isin
from ..throttle import THROTTLE

logger = logging.getLogger(__name__)

HOST = "https://www.ssga.com"
FUND_FINDER_URL = HOST + "/bin/v1/ssmp/fund/fundfinder"
USER_AGENT = "Mozilla/5.0 (compatible; openbasket)"

#: Regional fund finders and the key their ETF list sits under.
REGIONS = {
    "us": {"params": {"country": "us", "language": "en", "role": "intermediary"}, "key": "etfs"},
    "emea": {"params": {"country": "uk", "language": "en_gb", "role": "intermediary"}, "key": "uk-etfs"},
}

_ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b")
_HOLDINGS_RE = re.compile(r'"(/[^"]*holdings-daily[^"]+\.xlsx)"')
_DATE_RE = re.compile(r"(\d{2}-[A-Za-z]{3}-\d{4})")


@dataclass
class IssuerHoldings:
    as_of: str | None
    holdings: pd.DataFrame
    url: str


def _get(url: str, params: dict | None = None, timeout: int = 60) -> requests.Response:
    for attempt in range(4):
        THROTTLE.wait(url)
        r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        if r.status_code in (429, 502, 503, 504):
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


def parse_fund_finder(payload: dict, region: str) -> pd.DataFrame:
    """Fund finder JSON to one row per fund with an ISIN and a holdings file."""
    key = REGIONS[region]["key"]
    rows = []
    for d in payload["data"]["funds"].get(key, {}).get("datas", []):
        isins = [t for t in _ISIN_RE.findall(d.get("keywords") or "") if is_valid_isin(t)]
        link = _HOLDINGS_RE.search(json.dumps(d.get("documentPdf") or []))
        if not isins or not link:
            continue
        path = link.group(1)
        rows.append(
            {
                "issuer": "spdr",
                "site": region,
                "portfolio_id": Path(path).stem.removeprefix("holdings-daily-"),
                "isin": isins[0],
                "ticker": (d.get("fundFilter") or "").replace("-", " ") or None,
                "fund_name": d.get("fundName"),
                "domicile": d.get("domicile"),
                "product_page_url": HOST + d["fundUri"] if d.get("fundUri") else None,
                "holdings_url": HOST + path,
            }
        )
    return pd.DataFrame(rows).drop_duplicates("isin").reset_index(drop=True) if rows else pd.DataFrame(rows)


def products(region: str = "us") -> pd.DataFrame:
    """Every SPDR ETF on a regional site that has a daily holdings file."""
    params = REGIONS[region]["params"] | {"product": "etfs", "ui": "fund-finder"}
    return parse_fund_finder(_get(FUND_FINDER_URL, params).json(), region)


def _clean(v: object) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return None if s in ("", "-", "Unassigned", "nan") else s


def parse_holdings(content: bytes) -> tuple[str | None, pd.DataFrame]:
    """Read a holdings workbook into ``(as_of, DataFrame)``.

    Output columns: ``isin``, ``cusip``, ``sedol``, ``identifier``, ``name``,
    ``ticker``, ``weight_pct``, ``currency``, ``is_debt``. ``isin`` and
    ``cusip`` are only set when the file's value has a valid check digit.
    """
    raw = pd.read_excel(io.BytesIO(content), header=None, dtype=object)
    first = raw.iloc[:, 0].map(lambda v: str(v).strip() if v is not None else "")
    header_rows = first.index[first.isin(["Name", "ISIN"])]
    if len(header_rows) == 0:
        raise ValueError("SPDR holdings file has no header row starting with Name or ISIN")
    hdr = header_rows[0]

    as_of = None
    for _, row in raw.iloc[:hdr].iterrows():
        text = " ".join(str(v) for v in row if v is not None and not (isinstance(v, float) and pd.isna(v)))
        if "as of" in text.lower():
            m = _DATE_RE.search(text)
            if m:
                as_of = pd.to_datetime(m.group(1), format="%d-%b-%Y").date().isoformat()

    body = raw.iloc[hdr + 1 :].copy()
    body.columns = [str(c).strip() for c in raw.iloc[hdr]]
    blank = body.isna().all(axis=1)
    if blank.any():
        body = body.loc[: blank.idxmax()].iloc[:-1]

    weight_col = next(c for c in ("Weight", "Percent of Fund") if c in body.columns)
    name_col = next(c for c in ("Name", "Security Name") if c in body.columns)
    is_debt = any(c in body.columns for c in ("Coupon", "Maturity", "Maturity Date", "Interest Rate"))

    out = pd.DataFrame(index=body.index)
    out["name"] = body[name_col].map(_clean)
    out["ticker"] = body["Ticker"].map(_clean) if "Ticker" in body else None
    out["identifier"] = body["Identifier"].map(_clean) if "Identifier" in body else None
    out["sedol"] = body["SEDOL"].map(_clean) if "SEDOL" in body else None
    reported = body["ISIN"].map(_clean) if "ISIN" in body else out["identifier"]
    out["isin"] = reported.map(lambda v: v.upper() if is_valid_isin(v) else None)
    out["cusip"] = out["identifier"].map(lambda v: v.upper() if is_valid_cusip(v) else None)
    out["weight_pct"] = pd.to_numeric(body[weight_col], errors="coerce")
    cur = next((c for c in ("Local Currency", "Currency", "Currency Local") if c in body.columns), None)
    out["currency"] = body[cur].map(_clean) if cur else None
    out["is_debt"] = is_debt
    out = out[out["name"].notna() | out["weight_pct"].notna()]
    return as_of, out.reset_index(drop=True)


def holdings(url: str, cache_dir: str | Path | None = None) -> IssuerHoldings:
    """Download and parse one holdings file, cached per file per day."""
    cache = None
    if cache_dir:
        cache = Path(cache_dir) / f"spdr_{Path(url).stem}_{sha1(url.encode()).hexdigest()[:6]}_{date.today().isoformat()}.xlsx"
    if cache is not None and cache.exists():
        content = cache.read_bytes()
    else:
        logger.info("Fetching SPDR holdings %s", url)
        content = _get(url).content
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(content)
    as_of, df = parse_holdings(content)
    return IssuerHoldings(as_of, df, url)
