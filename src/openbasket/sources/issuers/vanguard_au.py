"""Vanguard Australia ETFs, month-end full holdings.

Fund list
    ``https://www.vanguard.com.au/personal/api/products/personal/funds/etf``
    (the JSON API behind vanguard.com.au/personal) lists Vanguard Australia's
    ETFs and managed funds with ``portId``, ticker and asset class. The
    ASX-quoted ETFs are kept, with ISINs from the ASX ISIN list. VEU and VTS
    are US Vanguard ETFs quoted on the ASX under their own AU ISINs; they
    keep Vanguard's US fund numbers as ``portId`` (``0991``, ``0970``, where
    Australian funds are ``8xxx`` or ``Fxxx``) and are kept with domicile US.
Holdings
    ``https://www.vanguard.com.au/personal/api/data/products/holdings-download/<portId>``
    (the "Download holdings" button) returns every position with ``ticker``,
    ``name``, ``marketValPercent`` (percent of net assets), ``countryCode``,
    bond terms and ``effectiveDate``, the month-end the holdings are as of.

Vanguard gives no ISIN, CUSIP or SEDOL. Lines with ``countryCode`` AU are
looked up by ticker in the ASX ISIN list (which also resolves the Vanguard
ETFs held by the diversified funds); other lines keep their ticker only.
Vanguard's unlisted wholesale funds held by the diversified ETFs (for example
``VANAUST``) have no ISIN in any free source and stay unresolved.
"""

from __future__ import annotations

import html
import json

import pandas as pd

from ... import http
from . import FetchContext, IssuerAdapter, IssuerHoldings, register
from ._apac import asx_isins, clean, fetch, holdings_frame, number, products_frame

API = "https://www.vanguard.com.au/personal/api"
LIST_URL = API + "/products/personal/funds/etf"
HOLDINGS_URL = API + "/data/products/holdings-download/{port_id}"
PAGE_URL = "https://www.vanguard.com.au/personal/invest-with-us/etf?portId={port_id}"

ASSET_CLASSES = {"equity": "Equity", "fixed income": "Fixed Income", "diversified": "Multi-asset", "cash": "Cash"}


def parse_list(payload: dict, asx: dict[str, str]) -> list[dict]:
    """Fund-list JSON to product rows."""
    rows = []
    for d in payload.get("data", []):
        if d.get("productType") != "etf":
            continue
        ticker = clean((d.get("tradingSymbols") or {}).get("TICKER"))
        isin = asx.get((ticker or "").upper())
        if not isin:
            continue  # not quoted on the ASX
        rows.append(
            {
                "issuer": "vanguard_au",
                "site": "au",
                "portfolio_id": d.get("portId"),
                "isin": isin,
                "ticker": ticker,
                "fund_name": html.unescape(d.get("fundName") or ""),
                # US Vanguard funds keep their US fund number (0xxx) as portId.
                "domicile": "US" if str(d.get("portId") or "").startswith("0") else "AU",
                "currency": "AUD",
                "asset_class": ASSET_CLASSES.get((d.get("assetClass") or "").lower(), d.get("assetClass")),
                # Vanguard's Australian ETFs hold securities or other Vanguard funds; none uses swaps.
                "replication": "physical",
                "product_page_url": PAGE_URL.format(port_id=d.get("portId")),
                "holdings_url": HOLDINGS_URL.format(port_id=d.get("portId")),
            }
        )
    return rows


def products() -> pd.DataFrame:
    """Every Vanguard ETF quoted on the ASX."""
    return products_frame(parse_list(http.get(LIST_URL).json(), asx_isins()))


def parse_holdings(content: bytes, asx: dict[str, str] | None = None) -> tuple[str | None, pd.DataFrame]:
    """The holdings-download JSON to ``(as_of, HOLDING_COLUMNS frame)``."""
    payload = json.loads(content)
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError(f"Vanguard holdings response has no data list: {payload.get('message')}")
    dates = sorted({d.get("effectiveDate") for d in data if d.get("effectiveDate")})
    rows = []
    for d in data:
        ticker = clean(d.get("ticker"))
        name = clean(d.get("longName")) or clean(d.get("name"))
        country = clean(d.get("countryCode"))
        if d.get("maturityDate") or (d.get("couponYield") or 0) > 0:
            label = "debt"
        elif "cash" in (name or "").lower():
            label = "cash"
        elif "fund" in (name or "").lower() or " etf" in (name or "").lower():
            label = "fund"
        else:
            label = "equity"
        isin = (asx or {}).get(ticker.upper()) if ticker and country == "AU" and label != "debt" else None
        rows.append(
            {
                "isin": isin,
                "ticker": ticker,
                "name": name,
                "weight_pct": number(d.get("marketValPercent")),
                "asset_class": label,
                "country": country,
            }
        )
    return (dates[-1] if dates else None), holdings_frame(rows)


def holdings(product: dict, ctx: FetchContext) -> IssuerHoldings:
    url = product["holdings_url"]
    asx = asx_isins(ctx)
    as_of, df = fetch(url, lambda c: parse_holdings(c, asx), ctx=ctx, prefix="vanguard_au", key=str(product["portfolio_id"]), ext="json")
    notes = [
        "Month-end holdings by ticker; Australian lines get an ISIN from the ASX ISIN list, others stay unresolved.",
    ]
    return IssuerHoldings(as_of, df, url, notes)


register(
    IssuerAdapter(
        name="vanguard_au",
        title="Vanguard Australia",
        homepage="https://www.vanguard.com.au/personal/invest-with-us/etf",
        credit="Holdings: Vanguard Investments Australia Ltd (vanguard.com.au); ISINs: ASX Limited ISIN list.",
        licence=(
            "Public month-end holdings published on Vanguard Australia's website, free without a "
            "log-in. Terms of reuse have not been reviewed; files are fetched at most once per fund "
            "per day."
        ),
        regions=("AU", "US"),
        products=products,
        holdings=holdings,
        frequency="monthly",
    )
)
