"""Shared helpers for the Asia-Pacific issuer adapters.

Not an adapter itself (the leading underscore keeps :func:`discover` from
importing it as one). It holds:

* :func:`fetch`, a cached GET/POST that writes the cache only after the
  response parsed, one file per fund per day;
* the ASX's free ISIN list (:func:`asx_list`, :func:`asx_isins`), which
  turns ASX codes into ISINs;
* :func:`korea_isin`, which builds a Korean ISIN from a six-digit KRX code
  for ordinary shares and ETFs;
* :func:`holdings_frame`, which turns parsed rows into
  :data:`HOLDING_COLUMNS` and drops identifiers that fail their check digit.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date
from functools import lru_cache
from hashlib import sha1
from pathlib import Path
from typing import Callable, TypeVar

import pandas as pd

from ... import http
from ...identifiers import is_valid_cusip, is_valid_isin, isin_check_digit
from . import HOLDING_COLUMNS, PRODUCT_COLUMNS, FetchContext

logger = logging.getLogger(__name__)

T = TypeVar("T")

ASX_ISIN_URL = "https://www.asx.com.au/content/dam/asx/issuers/ISIN.xls"


# --------------------------------------------------------------------------- fetching


def cache_path(ctx: FetchContext | None, prefix: str, key: str, ext: str, day: str | None = None) -> Path | None:
    """``<cache_dir>/<prefix>_<key>_<hash>_<day>.<ext>``, or ``None`` without a cache dir."""
    if ctx is None or not ctx.cache_dir:
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", key)[:60]
    digest = sha1(key.encode()).hexdigest()[:6]
    return Path(ctx.cache_dir) / f"{prefix}_{safe}_{digest}_{day or date.today().isoformat()}.{ext}"


def fetch(
    url: str,
    parse: Callable[[bytes], T],
    *,
    ctx: FetchContext | None = None,
    prefix: str = "apac",
    key: str | None = None,
    ext: str = "bin",
    method: str = "GET",
    **kw,
) -> T:
    """Fetch ``url`` (or read today's cached copy) and return ``parse(content)``.

    The response is written to the cache only after ``parse`` succeeded, so a
    block page or a half-written file is never cached.
    """
    path = cache_path(ctx, prefix, key or url, ext)
    if path is not None and path.exists():
        return parse(path.read_bytes())
    logger.info("Fetching %s", url)
    r = http.request(method, url, **kw)
    r.raise_for_status()
    content = r.content
    result = parse(content)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return result


_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def read_sheets(content: bytes) -> dict[str, pd.DataFrame]:
    """Every sheet of an ``.xls`` or ``.xlsx`` workbook, no header, values as objects.

    Legacy ``.xls`` files are read with ``xlrd`` directly (pandas' xlrd engine
    is deprecated, and calamine garbles the UTF-16 text in Samsung's files);
    ``.xlsx`` files with openpyxl through pandas.
    """
    if content[:8] == _OLE_MAGIC:
        import xlrd

        book = xlrd.open_workbook(file_contents=content)
        out = {}
        for sheet in book.sheets():
            rows = [[None if c.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK) else c.value for c in sheet.row(i)] for i in range(sheet.nrows)]
            out[sheet.name] = pd.DataFrame(rows, dtype=object)
        return out
    return pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, dtype=object, engine="openpyxl")


# --------------------------------------------------------------------------- values


def clean(v: object) -> str | None:
    """Strip a cell to text; blanks, dashes and ``nan`` become ``None``."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).replace("　", " ").strip()
    return None if s in ("", "-", "--", "nan", "NaN", "None", "Unassigned", "N/A", "n/a") else s


def number(v: object) -> float | None:
    """A cell as a float; strips ``%``, commas and spaces. ``None`` when not numeric."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def valid_isin(v: object) -> str | None:
    s = clean(v)
    return s.upper() if s and is_valid_isin(s) else None


def valid_cusip(v: object) -> str | None:
    s = clean(v)
    return s.upper() if s and is_valid_cusip(s) else None


_SEDOL_RE = re.compile(r"^[0-9B-DF-HJ-NP-TV-Z]{6}[0-9]$")


def valid_sedol(v: object) -> str | None:
    """A seven-character SEDOL whose check digit is right, else ``None``."""
    s = clean(v)
    if not s:
        return None
    s = s.upper().zfill(7) if s.isdigit() else s.upper()
    if not _SEDOL_RE.match(s):
        return None
    weights = (1, 3, 1, 7, 3, 9)
    total = sum((int(c) if c.isdigit() else ord(c) - 55) * w for c, w in zip(s[:6], weights))
    return s if (10 - total % 10) % 10 == int(s[6]) else None


def korea_isin(code: object) -> str | None:
    """ISIN of a KRX-listed ordinary share or ETF from its six-digit short code.

    Korean ISINs for ordinary shares and ETFs are ``KR7`` + the short code +
    ``00`` + a check digit (Samsung Electronics ``005930`` is ``KR7005930003``,
    KODEX 200 ``069500`` is ``KR7069500007``, and the newer alphanumeric code
    ``0048J0`` is ``KR70048J0007``). Preferred shares, whose codes do not end
    in 0, use a different national number and return ``None``.
    """
    s = clean(code)
    if not s or not re.fullmatch(r"[0-9][0-9A-Z]{4}0", s.upper()):
        return None
    payload = f"KR7{s}00"
    return payload + isin_check_digit(payload)


ASSET_CLASS_WORDS = [
    ("cash", "cash"),
    ("deposit", "cash"),
    ("margin", "cash"),
    ("money market", "fund"),
    ("future", "derivative (futures)"),
    ("forward", "derivative (foreign exchange)"),
    ("fx", "derivative (foreign exchange)"),
    ("swap", "derivative (swap)"),
    ("option", "derivative (option)"),
    ("bond", "debt"),
    ("fixed income", "debt"),
    ("debt", "debt"),
    ("government", "debt"),
    ("equit", "equity"),
    ("stock", "equity"),
    ("share", "equity"),
    ("reit", "equity"),
    ("property", "equity"),
    ("fund", "fund"),
    ("etf", "fund"),
]


def asset_class(label: object) -> str | None:
    """Map an issuer's asset-class label to the shared vocabulary."""
    s = (clean(label) or "").lower()
    if not s:
        return None
    for word, out in ASSET_CLASS_WORDS:
        if word in s:
            return out
    return "other"


def holdings_frame(rows: list[dict]) -> pd.DataFrame:
    """Rows (dicts with any of :data:`HOLDING_COLUMNS`) to a checked frame.

    ISINs, CUSIPs and SEDOLs that fail their check digit become ``None``;
    ``weight_pct`` is numeric and never rescaled.
    """
    df = pd.DataFrame(rows, columns=HOLDING_COLUMNS) if rows else pd.DataFrame(columns=HOLDING_COLUMNS)
    df["isin"] = df["isin"].map(valid_isin)
    df["cusip"] = df["cusip"].map(valid_cusip)
    df["sedol"] = df["sedol"].map(valid_sedol)
    for col in ("ticker", "name", "currency", "country", "asset_class"):
        df[col] = df[col].map(clean)
    weights = pd.to_numeric(df["weight_pct"], errors="coerce").astype(float)
    df = df.astype(object).where(df.notna(), None)
    df["weight_pct"] = weights
    return df.reset_index(drop=True)


def products_frame(rows: list[dict]) -> pd.DataFrame:
    """Rows to :data:`PRODUCT_COLUMNS`, keeping only valid, unique fund ISINs."""
    df = pd.DataFrame(rows, columns=PRODUCT_COLUMNS) if rows else pd.DataFrame(columns=PRODUCT_COLUMNS)
    df["isin"] = df["isin"].map(valid_isin)
    df = df[df["isin"].notna()].drop_duplicates("isin")
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------- code lists


def parse_asx_list(content: bytes, min_rows: int = 1000) -> pd.DataFrame:
    """ASX ``ISIN.xls`` to a frame of ``code``, ``name``, ``security_type``, ``isin``.

    Fewer than ``min_rows`` codes means a truncated or wrong file (the real list
    has about 14,000), so it is rejected rather than cached.
    """
    sheet = next(iter(read_sheets(content).values()))
    first = sheet.iloc[:, 0].map(lambda v: (clean(v) or "").upper())
    hdr = first.index[first == "ASX CODE"]
    if len(hdr) == 0:
        raise ValueError("ASX ISIN list has no 'ASX CODE' header")
    rows = []
    for _, row in sheet.iloc[hdr[0] + 1 :].iterrows():
        code, isin = clean(row.iloc[0]), valid_isin(row.iloc[3])
        if code and isin:
            rows.append({"code": code.upper(), "name": clean(row.iloc[1]), "security_type": clean(row.iloc[2]), "isin": isin})
    if len(rows) < min_rows:
        raise ValueError(f"ASX ISIN list looks truncated ({len(rows)} codes)")
    return pd.DataFrame(rows).drop_duplicates("code").reset_index(drop=True)


@lru_cache(maxsize=4)
def _asx(cache_dir: str | None, day: str) -> pd.DataFrame:
    return fetch(ASX_ISIN_URL, parse_asx_list, ctx=FetchContext(cache_dir), prefix="asx", key="isin-list", ext="xls")


def asx_list(ctx: FetchContext | None = None) -> pd.DataFrame:
    """The ASX's free ISIN list (every ASX code with its name and ISIN), fetched once a day."""
    return _asx(ctx.cache_dir if ctx else None, date.today().isoformat())


def asx_isins(ctx: FetchContext | None = None) -> dict[str, str]:
    """``{ASX code: ISIN}`` from :func:`asx_list`."""
    df = asx_list(ctx)
    return dict(zip(df["code"], df["isin"]))


def asx_code(ticker: object) -> str | None:
    """The ASX code in a Bloomberg-style ticker such as ``BHP AT`` or ``BHP AU``, else ``None``."""
    s = (clean(ticker) or "").upper().split()
    return s[0] if len(s) == 2 and s[1] in ("AT", "AU") else None
