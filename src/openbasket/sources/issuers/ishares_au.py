"""iShares ETFs domiciled in Australia (BlackRock Australia), daily full holdings.

The Australian BlackRock site still runs the older platform whose ``.ajax``
endpoints the UK and US sites dropped in 2026 (see :mod:`openbasket.sources.ishares`).

Fund list
    ``https://www.blackrock.com/au/product-sitemap.xml`` lists every product
    page as ``/au/products/<product id>/<slug>``. The fund list page itself
    (``/au/products/investment-funds``) answers 403 to scripts and is not used.
    ISINs come from the ASX ISIN list: each ``ISHARES ...`` ASX name is turned
    into a slug and matched to a sitemap slug (ASX abbreviations such as
    ``GBL`` or ``AUD HDG`` are expanded first; a hedged fund is never matched
    to an unhedged page or the other way round).
Holdings
    ``/au/products/<id>/fund/1478358644060.ajax?tab=all&fileType=json``
    returns every position as a row of cells, including ``CUSIP``, ``ISIN``
    and ``SEDOL`` and the weight to five decimals (``raw``), but no date and
    no column names. The CSV variant
    ``...1478358644060.ajax?fileType=csv&fileName=<ticker>_holdings&dataType=fund``
    starts with ``Fund Holdings as of,"02-Nov-2026"`` and a header row; it has
    the same columns minus the three identifiers, which the JSON inserts just
    before ``Price``. Both are read: the CSV for the date and column names,
    the JSON for the rows.
"""

from __future__ import annotations

import csv
import difflib
import io
import json
import re

import pandas as pd

from ... import http
from . import LOCAL_ONLY, FetchContext, IssuerAdapter, IssuerHoldings, register
from ._apac import asx_list, clean, fetch, holdings_frame, number, products_frame

HOST = "https://www.blackrock.com"
SITEMAP_URL = HOST + "/au/product-sitemap.xml"
COMPONENT = "1478358644060"
JSON_URL = HOST + "/au/products/{pid}/fund/" + COMPONENT + ".ajax?tab=all&fileType=json"
CSV_URL = HOST + "/au/products/{pid}/fund/" + COMPONENT + ".ajax?fileType=csv&fileName={ticker}_holdings&dataType=fund"

_ABBREV = {
    "gbl": "global", "aus": "australia", "aust": "australia", "australian": "australian", "hdg": "hedged",
    "hed": "hedged", "audhdg": "aud-hedged", "div": "dividend", "govt": "government", "intl": "international",
}

ASSET_CLASSES = {
    "equity": "equity", "fixed income": "debt", "cash": "cash", "money market": "fund", "futures": "derivative (futures)",
    "fx": "derivative (foreign exchange)", "cash collateral and margins": "cash", "fund": "fund",
}


def _slug(name: str) -> str:
    s = re.sub(r"%20", "", name.lower())
    words = [w for w in re.split(r"[^a-z0-9]+", s) if w]
    return "-".join(_ABBREV.get(w, w) for w in words)


def parse_sitemap(xml: str) -> dict[str, str]:
    """``{normalised slug: product id}`` from the AU product sitemap."""
    return {_slug(slug): pid for pid, slug in re.findall(r"<loc>https://www\.blackrock\.com/au/products/(\d+)/([^<]+)</loc>", xml)}


def match(name: str, pages: dict[str, str]) -> str | None:
    """Product id for an ASX fund name, or ``None`` when no page matches safely."""
    s = _slug(name)
    if s in pages:
        return pages[s]
    hedged = "hedged" in s
    same_kind = [p for p in pages if ("hedged" in p) == hedged]
    close = difflib.get_close_matches(s, same_kind, n=1, cutoff=0.85)
    return pages[close[0]] if close else None


def product_rows(asx: pd.DataFrame, pages: dict[str, str]) -> list[dict]:
    funds = asx[asx["name"].fillna("").str.upper().str.startswith("ISHARES")]
    funds = funds[~funds["security_type"].fillna("").str.upper().str.contains(r"\bBSK\b")]
    rows = []
    for _, f in funds.iterrows():
        pid = match(f["name"], pages)
        if pid is None:
            continue
        name = f["name"]
        upper = name.upper()
        rows.append(
            {
                "issuer": "ishares_au",
                "site": "au",
                "portfolio_id": pid,
                "isin": f["isin"],
                "ticker": f["code"],
                "fund_name": re.sub(r"\bEtf\b", "ETF", name.title()).replace("Ishares", "iShares"),
                "domicile": "AU",
                "currency": "AUD",
                "asset_class": (
                    "Commodity / Crypto" if any(w in upper for w in ("GOLD", "BITCOIN"))
                    else "Fixed Income" if any(w in upper for w in ("BOND", "TREASURY", "CASH", "CREDIT", "YIELD PLUS", "INFLATION"))
                    else "Equity"
                ),
                # iShares Australia ETFs hold securities, or a US/UCITS iShares ETF; none is swap-based.
                "replication": "physical",
                "product_page_url": f"{HOST}/au/products/{pid}/",
                "holdings_url": JSON_URL.format(pid=pid),
            }
        )
    return rows


def products() -> pd.DataFrame:
    """Every iShares ETF quoted on the ASX that has a product page on blackrock.com/au."""
    return products_frame(product_rows(asx_list(), parse_sitemap(http.get(SITEMAP_URL).text)))


def parse_csv_header(content: bytes) -> tuple[str | None, list[str]]:
    """``(as_of, column names)`` from the holdings CSV."""
    lines = list(csv.reader(io.StringIO(content.decode("utf-8-sig", "replace"))))
    as_of = None
    for row in lines:
        if row and row[0].strip().startswith("Fund Holdings as of") and len(row) > 1:
            as_of = pd.to_datetime(row[1].strip(), format="%d-%b-%Y").date().isoformat()
        if row and row[0].strip() == "Ticker":
            return as_of, [c.strip() for c in row]
    raise ValueError("iShares AU holdings CSV has no Ticker header row")


def _raw(cell: object) -> object:
    return cell.get("raw") if isinstance(cell, dict) else cell


def parse_holdings(content: bytes, csv_cols: list[str]) -> pd.DataFrame:
    """The ``tab=all`` JSON rows to HOLDING_COLUMNS, named from the CSV header."""
    data = json.loads(content.decode("utf-8-sig"))["aaData"]
    i_price = csv_cols.index("Price")
    cols = csv_cols[:i_price] + ["CUSIP", "ISIN", "SEDOL"] + csv_cols[i_price:]
    rows = []
    for cells in data:
        if len(cells) != len(cols):
            raise ValueError(f"iShares AU row has {len(cells)} cells, expected {len(cols)}")
        r = dict(zip(cols, (_raw(c) for c in cells)))
        rows.append(
            {
                "isin": r.get("ISIN"),
                "cusip": r.get("CUSIP"),
                "sedol": r.get("SEDOL"),
                "ticker": r.get("Ticker"),
                "name": r.get("Name"),
                "weight_pct": number(r.get("Weight (%)")),
                "asset_class": ASSET_CLASSES.get((clean(r.get("Asset Class")) or "").lower(), "other"),
                "currency": r.get("Market Currency") or r.get("Currency"),
                "country": r.get("Location"),
            }
        )
    return holdings_frame(rows)


def holdings(product: dict, ctx: FetchContext) -> IssuerHoldings:
    pid, ticker = product["portfolio_id"], product["ticker"]
    as_of, cols = fetch(CSV_URL.format(pid=pid, ticker=ticker), parse_csv_header, ctx=ctx, prefix="ishares_au", key=f"{pid}-csv", ext="csv")
    url = JSON_URL.format(pid=pid)
    df = fetch(url, lambda c: parse_holdings(c, cols), ctx=ctx, prefix="ishares_au", key=str(pid), ext="json")
    return IssuerHoldings(as_of, df, url, [])


register(
    IssuerAdapter(
        name="ishares_au",
        title="iShares Australia (BlackRock)",
        homepage="https://www.blackrock.com/au",
        credit="Holdings: BlackRock Investment Management (Australia) Limited, iShares (blackrock.com/au); fund ISINs: ASX ISIN list.",
        licence=(
            "Public holdings data on BlackRock's Australian website, free without a log-in. No open "
            "licence: BlackRock's site terms (read on its regional sites on 2026-11-03) say the content "
            "is copyright with all rights reserved, for personal, non-commercial use, and must not be "
            "reproduced or redistributed. Each fund's two holdings files are fetched at most once per day."
            " Read locally only: the holdings are never republished."
        ),
        regions=("AU",),
        products=products,
        holdings=holdings,
        frequency="daily",
        redistribution=LOCAL_ONLY,
    )
)
