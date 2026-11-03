"""Vanguard US funds and ETFs from investor.vanguard.com.

Fund list
    ``https://investor.vanguard.com/investment-products/list/funddetail/all``
    is the JSON behind the site's "all products" list: one entity per share
    class (ETF, Investor, Admiral, Institutional ...) with its ticker, CUSIP,
    category and ``associatedFundIds`` (the share classes of one portfolio).
    The fund ISIN is the US ISIN of that CUSIP.
Holdings
    ``https://investor.vanguard.com/irr/funds/profile/<TICKER>-AdditionalFundData``
    is what the profile page's portfolio-composition tab loads. Its
    ``holdingDetails`` has the month-end portfolio split into
    ``equityHoldings``, ``fixedIncomeHoldings``, ``shortTermReservesHoldings``
    and ``derivativeHoldings``, each line with ``isin``, ``cusip``, ``sedol``,
    ``ticker``, ``marketValuePercentage`` (``"8.39%"``) and
    ``marketValueBaseCurrency`` (``"$145,149,632,888"``), plus ``asOfDate``.

Vanguard publishes full holdings monthly, as of month end, about a month
later. Percentages are rounded to two decimals; lines below 0.005% show
``0.00%`` and a few tiny lines carry no percentage at all. Both are kept as
reported (the latter as NaN) with their market value in ``market_value``.
"""

from __future__ import annotations

import logging
import time

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

NAME = "vanguard_us"
HOST = "https://investor.vanguard.com"
LIST_URL = HOST + "/investment-products/list/funddetail/all"
HOLDINGS_URL = HOST + "/irr/funds/profile/{ticker}-AdditionalFundData"
ETF_PAGE = HOST + "/investment-products/etfs/profile/{ticker}"
MF_PAGE = HOST + "/investment-products/mutual-funds/profile/{ticker}"

#: Order in which a share class id names the portfolio, so every class of
#: one portfolio gets the same ``portfolio_id``.
_PORTFOLIO_ID_ORDER = ("etfFundId", "admiralFundId", "investorFundId", "institutionalFundId", "institutionalPlusFundId")

_CATEGORY = {
    "Stock Funds": "Equity",
    "Bond Funds": "Fixed Income",
    "Balanced Funds": "Multi Asset",
    "Money Market Funds": "Money Market",
}

#: ``securityMainType`` / ``securityType`` to the normalised asset classes.
_MAIN_TYPE = {
    "EQ": "equity",
    "FI": "debt",
    "MF": "fund",
    "MM": "money market",
    "CRNY": "cash",
    "DE": "derivative (futures)",
}
_CT_TYPE = {
    "CT.FOREX": "derivative (foreign exchange)",
    "CT.SPOT": "derivative (foreign exchange)",
    "CT.PORTSWAP": "derivative (swap)",
    "CT.TOTRETSWAP": "derivative (swap)",
}

EXTRA_COLUMNS = ("market_value", "security_type", "coupon_pct", "maturity", "section")


def _replication(profile: dict) -> str:
    """Vanguard US funds do not use swaps for their exposure. Bond index funds
    track their index by sampling; equity index and active funds hold the
    securities."""
    facts = profile.get("fundFact") or {}
    if facts.get("isIndex") and facts.get("isBond"):
        return "sampled"
    return "physical"


def parse_products(payload: dict) -> pd.DataFrame:
    """Fund-list JSON to one row per share class with a US ISIN."""
    rows = []
    for entity in (payload.get("fund") or {}).get("entity") or []:
        p = entity.get("profile") or {}
        ticker = clean(p.get("ticker"))
        isin = cusip_to_isin(clean(p.get("cusip")))
        if not ticker or not isin or p.get("isExternalFund"):
            continue
        assoc = p.get("associatedFundIds") or {}
        portfolio = next((assoc[k] for k in _PORTFOLIO_ID_ORDER if assoc.get(k)), p.get("fundId"))
        high = ((p.get("fundCategory") or {}).get("high") or {}).get("name")
        page = (ETF_PAGE if p.get("isETF") else MF_PAGE).format(ticker=ticker.lower())
        rows.append(
            {
                "issuer": NAME,
                "site": "us",
                "portfolio_id": str(portfolio),
                "isin": isin,
                "ticker": ticker,
                "fund_name": clean(p.get("longName")),
                "domicile": "US",
                "currency": "USD",
                "asset_class": _CATEGORY.get(high, high),
                "replication": _replication(p),
                "product_page_url": page,
                "holdings_url": HOLDINGS_URL.format(ticker=ticker),
                "vehicle": "ETF" if p.get("isETF") else "Mutual Fund",
            }
        )
    df = pd.DataFrame(rows, columns=PRODUCT_COLUMNS + ["vehicle"])
    return df.drop_duplicates("isin").reset_index(drop=True)


def products() -> pd.DataFrame:
    """Every Vanguard US mutual fund and ETF share class."""
    r = http.get(LIST_URL, headers={"Accept": "application/json"})
    r.raise_for_status()
    return parse_products(r.json())


def _asset_class(row: dict) -> str:
    stype = (row.get("securityType") or "").upper()
    if stype in _CT_TYPE:
        return _CT_TYPE[stype]
    if stype == "EQ.ETF":
        return "fund"
    main = (row.get("securityMainType") or stype.split(".")[0]).upper()
    if main == "CT":
        return "derivative (other)"
    return _MAIN_TYPE.get(main, "other")


def parse_holdings(payload: dict) -> tuple[str | None, pd.DataFrame]:
    """``AdditionalFundData`` JSON to ``(as_of, frame)``."""
    details = payload.get("holdingDetails")
    if not isinstance(details, dict):
        raise ValueError("Vanguard response has no holdingDetails")
    rows = []
    for section, lines in details.items():
        if not isinstance(lines, list):
            continue
        for r in lines:
            if not isinstance(r, dict):
                continue
            coupon = number(r.get("couponRate"))
            rows.append(
                {
                    "isin": isin_or_none(r.get("isin")),
                    "cusip": cusip_or_none(r.get("cusip")),
                    "sedol": sedol_or_none(r.get("sedol")),
                    "ticker": clean(r.get("ticker")),
                    "name": clean(r.get("securityLongDescription")) or clean(r.get("securityShortDescription")),
                    "weight_pct": number(r.get("marketValuePercentage")),
                    "asset_class": _asset_class(r),
                    "currency": None,
                    "country": None,
                    "market_value": number(r.get("marketValueBaseCurrency")),
                    "security_type": clean(r.get("securityType")),
                    "coupon_pct": coupon if section == "fixedIncomeHoldings" else None,
                    "maturity": clean(r.get("maturityDate")),
                    "section": section,
                }
            )
    if not rows:
        raise ValueError("Vanguard holdingDetails has no holdings")
    return us_date(details.get("asOfDate")), holdings_frame(rows, EXTRA_COLUMNS)


def _parse_bytes(content: bytes) -> tuple[str | None, pd.DataFrame]:
    return parse_holdings(load_json(content))


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    """Month-end holdings of one Vanguard fund, by its ticker."""
    ticker = product["ticker"]
    url = product.get("holdings_url") or HOLDINGS_URL.format(ticker=ticker)

    def fetch() -> bytes:
        logger.info("Fetching Vanguard holdings %s", url)
        r = http.get(url, headers={"Accept": "application/json"}, timeout=120)
        r.raise_for_status()
        return r.content

    t0 = time.monotonic()
    as_of, df = cached(ctx.cache_dir if ctx else None, f"{NAME}_{ticker}", fetch, _parse_bytes)
    notes = [
        "Month-end holdings; weights are Vanguard's two-decimal percentages, "
        f"summing to {df['weight_pct'].sum():.2f}%."
    ]
    missing = int(df["weight_pct"].isna().sum())
    if missing:
        notes.append(f"{missing} lines carry a market value but no percentage.")
    logger.debug("Vanguard %s parsed in %.1fs", ticker, time.monotonic() - t0)
    return IssuerHoldings(as_of, df, url, notes)


ADAPTER = register(
    IssuerAdapter(
        name=NAME,
        title="Vanguard (US)",
        homepage="https://investor.vanguard.com",
        credit="Holdings data: The Vanguard Group, Inc., investor.vanguard.com",
        licence=(
            "Public data from Vanguard's website, read from the JSON its fund pages load. "
            "Vanguard's site terms have not been reviewed for automated or bulk use; "
            "openbasket fetches each fund at most once a day and credits Vanguard on every row."
        ),
        regions=("US",),
        products=products,
        holdings=holdings,
        frequency="monthly",
    )
)
