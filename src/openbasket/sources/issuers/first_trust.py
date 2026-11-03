"""First Trust US ETFs from ftportfolios.com.

Fund list
    ``https://www.ftportfolios.com/Retail/etf/etflist.aspx`` lists every ETF
    (334 on 3 November 2026) by name and ticker, without identifiers. Each
    fund's ISIN and CUSIP are on its summary page
    ``/Retail/Etf/EtfSummary.aspx?Ticker=<T>`` ("Fund Overview"). Those pages
    are large (about 2 MB), so the ISIN is read from the start of the page
    and the download stopped, and the result is kept in a permanent
    ticker-to-ISIN file: a fund's ISIN never changes, so only new funds cost
    a request.
Holdings
    ``/Retail/Etf/EtfHoldings.aspx?Ticker=<T>`` is an HTML page with the full
    daily holdings table ("Holdings of the Fund as of 11/2/2026"): security
    name, identifier (ticker), CUSIP, classification (equity funds), shares,
    market value and weighting in percent to two decimals.

``/Common/`` is disallowed in robots.txt, which rules out the CUSIP list PDF
and the "Export to Excel" files; neither is used.
"""

from __future__ import annotations

import json
import logging
import os
import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

from ... import http
from ...identifiers import cusip_to_isin, is_valid_isin
from . import PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register
from ._us_common import cached, clean, cusip_or_none, holdings_frame, number, us_date

logger = logging.getLogger(__name__)

NAME = "first_trust"
HOST = "https://www.ftportfolios.com"
LIST_URL = HOST + "/Retail/etf/etflist.aspx"
SUMMARY_URL = HOST + "/Retail/Etf/EtfSummary.aspx?Ticker={ticker}"
HOLDINGS_URL = HOST + "/Retail/Etf/EtfHoldings.aspx?Ticker={ticker}"

#: Where the ticker-to-ISIN file is kept when ``products()`` is called
#: without a ``cache_dir``: ``$OPENBASKET_ISSUER_CACHE`` or the project's
#: ``data/01_raw/issuers`` (only used if that folder exists).
DEFAULT_CACHE_DIR = os.environ.get("OPENBASKET_ISSUER_CACHE", "data/01_raw/issuers")
ISIN_FILE = "first_trust_isins.json"

EXTRA_COLUMNS = ("market_value", "shares", "classification")

_LIST_RE = re.compile(r"href='/Retail/Etf/EtfSummary\.aspx\?Ticker=([A-Z0-9.]+)'>([^<]+)</a>", re.I)
_OVERVIEW_RE = {
    "cusip": re.compile(r">\s*CUSIP\s*</td>\s*<td[^>]*>\s*([0-9A-Z]{9})\s*<", re.I),
    "isin": re.compile(r">\s*ISIN\s*</td>\s*<td[^>]*>\s*([A-Z]{2}[0-9A-Z]{9}[0-9])\s*<", re.I),
}
_AS_OF_RE = re.compile(r"Holdings of the Fund as of\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})", re.I)


class _Tables(HTMLParser):
    """Collect the cells of every ``<table class="fundSilverGrid">``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._depth = 0  # nesting depth inside a wanted table
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self._depth:
                self._depth += 1
            elif "fundSilverGrid" in (dict(attrs).get("class") or "").split():
                self._depth = 1
                self.tables.append([])
        elif self._depth == 1 and tag == "tr":
            self._row = []
        elif self._depth == 1 and tag == "td" and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table" and self._depth:
            self._depth -= 1
        elif self._depth == 1 and tag == "td" and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif self._depth == 1 and tag == "tr" and self._row is not None:
            if self._row:
                self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_list(html: str) -> pd.DataFrame:
    """Tickers and names from the ETF list page."""
    seen: dict[str, str] = {}
    for ticker, name in _LIST_RE.findall(html):
        seen.setdefault(ticker.upper(), unescape(name).strip())
    return pd.DataFrame({"ticker": list(seen), "fund_name": list(seen.values())})


def parse_overview(html: str) -> dict:
    """CUSIP and ISIN from a summary page (or the first part of one)."""
    out = {}
    for key, rx in _OVERVIEW_RE.items():
        m = rx.search(html)
        if m:
            out[key] = m.group(1).upper()
    if "isin" in out and not is_valid_isin(out["isin"]):
        out.pop("isin")
    if "isin" not in out and cusip_or_none(out.get("cusip")):
        out["isin"] = cusip_to_isin(out["cusip"])
    return out


def _fetch_overview(ticker: str) -> dict:
    """Read a summary page only until its ISIN appears, then stop."""
    url = SUMMARY_URL.format(ticker=ticker)
    r = http.get(url, stream=True, timeout=120)
    try:
        r.raise_for_status()
        buf = ""
        for chunk in r.iter_content(chunk_size=1 << 16, decode_unicode=False):
            buf += chunk.decode("utf-8", errors="replace")
            found = parse_overview(buf)
            if "isin" in found and "cusip" in found:
                return found
        return parse_overview(buf)
    finally:
        r.close()


def _isin_file(cache_dir: str | Path | None) -> Path | None:
    if cache_dir:
        return Path(cache_dir) / ISIN_FILE
    if Path(DEFAULT_CACHE_DIR).is_dir():
        return Path(DEFAULT_CACHE_DIR) / ISIN_FILE
    return None


def products(cache_dir: str | Path | None = None, limit: int | None = None) -> pd.DataFrame:
    """Every First Trust ETF, one row per ISIN.

    ISINs already in the ticker-to-ISIN file are reused; others are read from
    the fund's summary page (one request each, at most ``limit`` of them).
    """
    r = http.get(LIST_URL)
    r.raise_for_status()
    funds = parse_list(r.text)
    path = _isin_file(cache_dir)
    known: dict[str, dict] = {}
    if path is not None and path.exists():
        known = json.loads(path.read_text())
    fetched = 0
    for ticker in funds["ticker"]:
        if ticker in known and known[ticker].get("isin"):
            continue
        if limit is not None and fetched >= limit:
            break
        try:
            found = _fetch_overview(ticker)
        except Exception as exc:  # one bad page must not lose the list
            logger.warning("First Trust %s summary failed: %s", ticker, exc)
            continue
        fetched += 1
        if found.get("isin"):
            known[ticker] = found
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(known, indent=0, sort_keys=True))
    return build_products(funds, known)


def build_products(funds: pd.DataFrame, known: dict[str, dict]) -> pd.DataFrame:
    """Join the list with the ticker-to-ISIN map; funds with no ISIN are left out."""
    rows = []
    for ticker, name in zip(funds["ticker"], funds["fund_name"]):
        isin = (known.get(ticker) or {}).get("isin")
        if not isin:
            continue
        rows.append(
            {
                "issuer": NAME,
                "site": "us",
                "portfolio_id": ticker,
                "isin": isin,
                "ticker": ticker,
                "fund_name": name,
                "domicile": "US",
                "currency": "USD",
                "asset_class": None,
                "replication": None,
                "product_page_url": SUMMARY_URL.format(ticker=ticker),
                "holdings_url": HOLDINGS_URL.format(ticker=ticker),
            }
        )
    return pd.DataFrame(rows, columns=PRODUCT_COLUMNS).drop_duplicates("isin").reset_index(drop=True)


#: OCC option symbol, e.g. ``4SPY 270115C00006920`` (root, expiry, C/P, strike).
_OCC_RE = re.compile(r"\d{6}[CP]\d{8}$")
_DEBT_RE = re.compile(r"\d%|\bdue\b|\bnotes?\b|\bbonds?\b|\bdebentures?\b|treasury|\bFRN\b", re.I)


def _asset_class(name: str | None, identifier: str | None, cusip: str | None, classification: str | None) -> str:
    n = name or ""
    if (identifier or "").startswith("$") or re.match(r"(?i)^(cash|us dollar|u\.s\. dollar)", n):
        return "cash"
    if re.search(r"(?i)money market|treasury obligations fund|government obligations fund", n):
        return "money market"
    if re.search(r"(?i)\bfutures?\b", n):
        return "derivative (futures)"
    if re.search(r"(?i)\bswap\b", n):
        return "derivative (swap)"
    if re.search(r"(?i)\bforward\b|\bfx\b", n):
        return "derivative (foreign exchange)"
    if _OCC_RE.search(identifier or "") or re.search(r"(?i)\boptions?\b|\bflex\b|\bcall\b|\bput\b", n):
        return "derivative (options)"
    if re.search(r"(?i)\bETF\b", n):
        return "fund"
    if classification and classification.lower() not in ("other", "cash"):
        return "equity"
    if _DEBT_RE.search(n):
        return "debt"
    if re.search(r"(?i)\bfund\b", n):
        return "fund"
    return "equity" if cusip else "other"


def parse_holdings(html: str) -> tuple[str | None, pd.DataFrame]:
    """Holdings page HTML to ``(as_of, frame)``."""
    m = _AS_OF_RE.search(html)
    as_of = us_date(m.group(1)) if m else None
    parser = _Tables()
    parser.feed(html)
    rows = []
    for table in parser.tables:
        if not table:
            continue
        header = [h.lower() for h in table[0]]
        if "weighting" not in header or "security name" not in header:
            continue
        col = {h: i for i, h in enumerate(header)}

        def get(cells, key):
            i = col.get(key)
            return clean(cells[i]) if i is not None and i < len(cells) else None

        for cells in table[1:]:
            name = get(cells, "security name")
            weight = number(get(cells, "weighting"))
            if name is None and weight is None:
                continue
            identifier = get(cells, "identifier")
            cusip = cusip_or_none(get(cells, "cusip"))
            classification = get(cells, "classification")
            rows.append(
                {
                    "isin": None,
                    "cusip": cusip,
                    "sedol": None,
                    "ticker": identifier if identifier and not identifier.startswith("$") else None,
                    "name": name,
                    "weight_pct": weight,
                    "asset_class": _asset_class(name, identifier, cusip, classification),
                    "currency": identifier[1:] if identifier and identifier.startswith("$") else None,
                    "country": None,
                    "market_value": number(get(cells, "market value")),
                    "shares": number(get(cells, "shares / quantity")),
                    "classification": classification,
                }
            )
    if not rows:
        raise ValueError("First Trust page has no holdings table")
    return as_of, holdings_frame(rows, EXTRA_COLUMNS)


def _parse_bytes(content: bytes):
    return parse_holdings(content.decode("utf-8", errors="replace"))


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    """Daily full holdings of one First Trust ETF."""
    ticker = product["ticker"]
    url = product.get("holdings_url") or HOLDINGS_URL.format(ticker=ticker)

    def fetch() -> bytes:
        logger.info("Fetching First Trust holdings %s", url)
        r = http.get(url, timeout=120)
        r.raise_for_status()
        return r.content

    as_of, df = cached(ctx.cache_dir if ctx else None, f"{NAME}_{ticker}", fetch, _parse_bytes, suffix="html")
    notes = [
        "Lines carry CUSIPs, not ISINs; weights are two-decimal percentages, "
        f"summing to {df['weight_pct'].sum():.2f}%."
    ]
    return IssuerHoldings(as_of, df, url, notes)


ADAPTER = register(
    IssuerAdapter(
        name=NAME,
        title="First Trust (US ETFs)",
        homepage="https://www.ftportfolios.com",
        credit="Holdings data: First Trust Portfolios L.P., ftportfolios.com",
        licence=(
            "Public data from First Trust's website. The holdings pages state that CUSIP "
            "identifiers are provided by CUSIP Global Services (FactSet) and may not be used "
            "as a substitute for a CUSIP service; openbasket uses them only to identify the "
            "holdings shown. Site terms have not been reviewed for automated or bulk use; "
            "each fund is fetched at most once a day."
        ),
        regions=("US",),
        products=products,
        holdings=holdings,
        frequency="daily",
    )
)
