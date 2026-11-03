"""The Tracker Fund of Hong Kong (2800), Hong Kong's largest ETF, daily holdings.

Fund
    One fund, ISIN ``HK2800008867`` (from the HKEX list of securities), managed
    by Hang Seng Investment Management. Its site is https://www.trahk.com.hk.
Holdings
    The "Download holdings" link on
    ``https://www.trahk.com.hk/en-hk/trahk-fund/fund-information/`` points at
    ``https://rbwm-api.hsbc.com.hk/pws-hk-hase-hsvm2-papi-prod-proxy/v1/hsvm/csv/trahkfund/holdings?mode=daily``:
    ``Fund Name``, ``Ticker Symbol``, ``Holdings as of:,DDMMYYYY``, then a
    table with ``ISIN``, ``SEDOL``, ``Ticker``, ``Security Name``,
    ``Local Currency`` and ``Weight (%)`` (percent of the fund, two decimals).
"""

from __future__ import annotations

import csv
import io
import re

import pandas as pd

from . import LOCAL_ONLY, FetchContext, IssuerAdapter, IssuerHoldings, register
from ._apac import clean, fetch, holdings_frame, number, products_frame

FUND_ISIN = "HK2800008867"
PAGE_URL = "https://www.trahk.com.hk/en-hk/trahk-fund/fund-information/"
HOLDINGS_URL = "https://rbwm-api.hsbc.com.hk/pws-hk-hase-hsvm2-papi-prod-proxy/v1/hsvm/csv/trahkfund/holdings?mode=daily"


def products() -> pd.DataFrame:
    """The Tracker Fund of Hong Kong (a single fund, so no request is needed)."""
    return products_frame(
        [
            {
                "issuer": "trahk",
                "site": "hk",
                "portfolio_id": "2800",
                "isin": FUND_ISIN,
                "ticker": "2800",
                "fund_name": "The Tracker Fund of Hong Kong",
                "domicile": "HK",
                "currency": "HKD",
                "asset_class": "Equity",
                "replication": "physical",
                "product_page_url": PAGE_URL,
                "holdings_url": HOLDINGS_URL,
            }
        ]
    )


def parse_holdings(content: bytes) -> tuple[str | None, pd.DataFrame]:
    """The daily holdings CSV to ``(as_of, HOLDING_COLUMNS frame)``."""
    lines = list(csv.reader(io.StringIO(content.decode("utf-8-sig", "replace"))))
    as_of = hdr = None
    for i, row in enumerate(lines):
        cells = [c.strip() for c in row]
        if cells and cells[0].lower().startswith("holdings as of") and len(cells) > 1:
            m = re.fullmatch(r"(\d{2})(\d{2})(\d{4})", cells[1])
            if m:
                as_of = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        if cells and cells[0] == "ISIN" and "Weight (%)" in cells:
            hdr = i
            break
    if hdr is None:
        raise ValueError("TraHK holdings CSV has no ISIN header row")
    cols = [c.strip() for c in lines[hdr]]
    rows = []
    for row in lines[hdr + 1 :]:
        if not row or not any(c.strip() for c in row):
            break
        r = dict(zip(cols, (c.strip() for c in row)))
        name = clean(r.get("Security Name"))
        ticker = clean(r.get("Ticker"))
        label = "cash" if (ticker or "").upper().startswith("CASH") or "cash" in (name or "").lower() else (
            "derivative (futures)" if "fut" in (name or "").lower() else "equity"
        )
        rows.append(
            {
                "isin": r.get("ISIN"),
                "sedol": r.get("SEDOL"),
                "ticker": ticker,
                "name": name,
                "weight_pct": number(r.get("Weight (%)")),
                "asset_class": label,
                "currency": r.get("Local Currency"),
            }
        )
    return as_of, holdings_frame(rows)


def holdings(product: dict, ctx: FetchContext) -> IssuerHoldings:
    as_of, df = fetch(HOLDINGS_URL, parse_holdings, ctx=ctx, prefix="trahk", key="2800", ext="csv")
    return IssuerHoldings(as_of, df, HOLDINGS_URL, ["Weights are rounded to two decimals in the file."])


register(
    IssuerAdapter(
        name="trahk",
        title="Tracker Fund of Hong Kong (Hang Seng Investment Management)",
        homepage="https://www.trahk.com.hk/",
        credit="Holdings: Hang Seng Investment Management Limited, Tracker Fund of Hong Kong (trahk.com.hk).",
        licence=(
            "Public daily holdings download linked from the fund's website, free without a "
            "log-in. Terms of reuse have not been reviewed; the file is fetched at most once a day."
            " Read locally only: the holdings are never republished."
        ),
        regions=("HK",),
        products=products,
        holdings=holdings,
        frequency="daily",
        redistribution=LOCAL_ONLY,
    )
)
