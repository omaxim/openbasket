"""Avantis Investors (American Century) US ETFs and mutual funds.

The site is a SvelteKit app; each page's data is served next to it as
``__data.json`` in SvelteKit's "devalue" encoding (a flat array of values
whose objects and lists hold indices into that array), decoded here by
:func:`devalue`.

Fund list
    ``https://www.avantisinvestors.com/avantis-investments/en-us/__data.json?t=perf&type=Mutual+Fund&type=ETF``
    holds ``fundList`` (ticker, ``fundNumber``, product type, share class,
    asset class), ``documents`` (per fund number and share class, links whose
    ``fid=`` parameter is the share class CUSIP) and ``urlSlugs`` (fund
    number to page). The fund ISIN is the US ISIN of that CUSIP.
Holdings
    ``https://www.avantisinvestors.com/avantis-investments/en-us/<slug>/__data.json``
    has ``allProducts[].portfolio.etfHoldings``: every line with ``isin``,
    ``cusip``, ``sedol``, ``ticker``, ``securityType``, ``baseMarketValue``
    and ``weight`` (``"5.83%"``), dated ``etfHoldingsAsOfDate``. ETFs are
    updated every business day; the mutual funds' list is month-end.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

import pandas as pd

from ... import http
from ...identifiers import cusip_to_isin
from . import PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register
from ._us_common import (
    cached,
    clean,
    cusip_or_none,
    holdings_frame,
    isin_or_none,
    load_json,
    number,
    sedol_or_none,
    us_date,
)

logger = logging.getLogger(__name__)

NAME = "avantis"
HOST = "https://www.avantisinvestors.com"
LIST_URL = HOST + "/avantis-investments/en-us/__data.json?t=perf&type=Mutual+Fund&type=ETF"
DATA_URL = HOST + "/avantis-investments/en-us/{slug}/__data.json"
PAGE_URL = HOST + "/avantis-investments/{slug}/"

_ASSET_CLASS = {"Stock": "Equity", "Bond": "Fixed Income", "Asset Allocation": "Multi Asset"}

EXTRA_COLUMNS = ("market_value", "security_type", "coupon_pct", "maturity")

_SPECIAL = {-1: None, -2: None, -3: float("nan"), -4: float("inf"), -5: float("-inf"), -6: -0.0}


def devalue(values: list) -> object:
    """Decode SvelteKit's devalue array (the ``data`` of a ``__data.json`` node)."""
    memo: dict[int, object] = {}

    def h(i: int) -> object:
        if i in _SPECIAL:
            return _SPECIAL[i]
        if i in memo:
            return memo[i]
        v = values[i]
        if isinstance(v, dict):
            out: dict = {}
            memo[i] = out
            for k, j in v.items():
                out[k] = h(j)
            return out
        if isinstance(v, list):
            if v and isinstance(v[0], str):
                # Typed values: ["Date", iso], ["Set", ...], ["Map", ...] ...
                return v[1] if v[0] == "Date" and len(v) > 1 else None
            out_list: list = []
            memo[i] = out_list
            out_list.extend(h(j) for j in v)
            return out_list
        return v

    return h(0) if values else None


def _page_data(payload: dict) -> dict:
    """Merge the decoded data of every node of a ``__data.json`` response."""
    if payload.get("type") == "redirect":
        raise ValueError(f"Avantis data redirected to {payload.get('location')}")
    merged: dict = {}
    for node in payload.get("nodes") or []:
        if isinstance(node, dict) and node.get("type") == "data":
            data = devalue(node.get("data") or [])
            if isinstance(data, dict):
                merged.update(data)
    return merged


def _cusip_from_documents(doc: dict) -> str | None:
    for v in doc.values():
        if isinstance(v, str) and "fid=" in v:
            fid = parse_qs(urlparse(v).query).get("fid", [None])[0]
            if cusip_or_none(fid):
                return fid.upper()
    return None


def parse_products(payload: dict) -> pd.DataFrame:
    """Fund-list ``__data.json`` to one row per share class with an ISIN."""
    data = _page_data(payload)
    slugs = {str(s.get("fundNumber")): str(s.get("slug") or "").strip("/").split("/")[-1] for s in data.get("urlSlugs") or []}
    cusips = {}
    for doc in data.get("documents") or []:
        cusip = _cusip_from_documents(doc)
        if cusip:
            cusips[(str(doc.get("fundNumber")), clean(doc.get("shareClass")) or "")] = cusip
    rows = []
    for f in data.get("fundList") or []:
        number_ = str(f.get("fundNumber"))
        share_class = clean(f.get("shareClass")) or ""
        cusip = cusips.get((number_, share_class))
        isin = cusip_to_isin(cusip)
        slug = slugs.get(number_)
        if not isin or not slug:
            continue
        etf = f.get("productType") == "ETF"
        rows.append(
            {
                "issuer": NAME,
                "site": "us",
                "portfolio_id": number_,
                "isin": isin,
                "ticker": clean(f.get("ticker")),
                "fund_name": (clean(f.get("productName")) or "") + ("" if etf or not share_class else f" {share_class}"),
                "domicile": "US",
                "currency": "USD",
                "asset_class": _ASSET_CLASS.get(f.get("assetClass"), clean(f.get("assetClass"))),
                # Actively managed; they hold the securities, with futures for cash.
                "replication": "physical",
                "product_page_url": PAGE_URL.format(slug=slug),
                "holdings_url": DATA_URL.format(slug=slug),
                "vehicle": "ETF" if etf else "Mutual Fund",
            }
        )
    df = pd.DataFrame(rows, columns=PRODUCT_COLUMNS + ["vehicle"])
    return df.drop_duplicates("isin").reset_index(drop=True)


def products() -> pd.DataFrame:
    """Every Avantis US ETF and mutual fund share class."""
    r = http.get(LIST_URL, headers={"Accept": "application/json"})
    r.raise_for_status()
    return parse_products(r.json())


def _asset_class(security_type: str | None) -> str:
    t = (security_type or "").upper()
    if not t:
        return "cash"
    if "FUTURE" in t:
        return "derivative (futures)"
    if "SWAP" in t:
        return "derivative (swap)"
    if "FORWARD" in t or "CURRENCY CONTRACT" in t:
        return "derivative (foreign exchange)"
    if "OPTION" in t:
        return "derivative (options)"
    if "SWEEP" in t or "MONEY MARKET" in t:
        return "money market"
    if "CASH" in t:
        return "cash"
    if "INVESTMENT COMPAN" in t or "EXCHANGE TRADED FUND" in t or t in ("ETF", "MUTUAL FUND"):
        # Other funds held, e.g. Avantis ETFs inside an allocation ETF.
        return "fund"
    if any(k in t for k in ("STOCK", "COMMON", "ADR", "RIGHT", "WARRANT", "REIT", "DEPOSITARY", "EQUITY", "UNITS", "STAPLED")):
        return "equity"
    if t == "OTHER FOREIGN SECURITIES":  # stapled securities in Avantis files
        return "equity"
    if any(k in t for k in ("BOND", "NOTE", "BILL", "MBS", "AGENCY", "MUNICIPAL", "TREASURY", "DEBT", "ASSET BACKED", "CMBS", "COMMERCIAL PAPER")):
        return "debt"
    return "other"


def parse_holdings(payload: dict) -> tuple[str | None, pd.DataFrame, dict]:
    """A fund page's ``__data.json`` to ``(as_of, frame, fund)``.

    ``fund`` is the selected share class record (ticker, ISIN, net assets).
    """
    data = _page_data(payload)
    fund = data.get("selectedClass") or next(iter(data.get("allProducts") or []), None)
    if not isinstance(fund, dict) or not isinstance(fund.get("portfolio"), dict):
        raise ValueError("Avantis data has no portfolio")
    portfolio = fund["portfolio"]
    lines = portfolio.get("etfHoldings")
    if not lines:
        raise ValueError("Avantis portfolio has no etfHoldings")
    rows = []
    for r in lines:
        country = clean(r.get("country"))
        rows.append(
            {
                "isin": isin_or_none(r.get("isin")),
                "cusip": cusip_or_none(r.get("cusip")),
                "sedol": sedol_or_none(r.get("sedol")),
                "ticker": clean(r.get("ticker")),
                "name": clean(r.get("name")),
                "weight_pct": number(r.get("weight")),
                "asset_class": _asset_class(clean(r.get("securityType"))),
                "currency": None,
                "country": country.title() if country else None,
                "market_value": number(r.get("baseMarketValue")),
                "security_type": clean(r.get("securityType")),
                "coupon_pct": number(r.get("coupon")) or None,
                "maturity": clean(r.get("maturityDate")),
            }
        )
    as_of = us_date(portfolio.get("etfHoldingsAsOfDate")) or us_date(portfolio.get("asOfDate"))
    return as_of, holdings_frame(rows, EXTRA_COLUMNS), {k: v for k, v in fund.items() if not isinstance(v, (dict, list))}


def _parse_bytes(content: bytes):
    return parse_holdings(load_json(content))


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    """Full holdings of one Avantis fund from its page data."""
    url = product.get("holdings_url")
    if not url:
        raise ValueError("Avantis product has no holdings_url")

    def fetch() -> bytes:
        logger.info("Fetching Avantis holdings %s", url)
        r = http.get(url, headers={"Accept": "application/json"}, timeout=120)
        r.raise_for_status()
        return r.content

    key = f"{NAME}_{product.get('ticker') or product.get('portfolio_id')}"
    as_of, df, fund = cached(ctx.cache_dir if ctx else None, key, fetch, _parse_bytes)
    notes = [f"Weights are Avantis's two-decimal percentages, summing to {df['weight_pct'].sum():.2f}%."]
    assets = clean(fund.get("assetsInShareClass"))
    if assets and fund.get("productType") == "ETF":
        notes.append(f"Net assets {assets} as of {us_date(fund.get('navAsOfDate')) or fund.get('navAsOfDate')}.")
    if re.search(r"SWAP", " ".join(df["security_type"].dropna()), re.I):
        notes.append("Swap lines (for example credit default swaps) are weighted by Avantis on their own basis, not market value.")
    return IssuerHoldings(as_of, df, url, notes)


ADAPTER = register(
    IssuerAdapter(
        name=NAME,
        title="Avantis Investors (American Century)",
        homepage="https://www.avantisinvestors.com",
        credit="Holdings data: Avantis Investors (American Century Investment Services, Inc.), avantisinvestors.com",
        licence=(
            "Public data from the Avantis website, read from the page data its fund pages load. "
            "The site terms have not been reviewed for automated or bulk use; openbasket "
            "fetches each fund at most once a day and credits Avantis on every row."
        ),
        regions=("US",),
        products=products,
        holdings=holdings,
        frequency="daily",
    )
)
