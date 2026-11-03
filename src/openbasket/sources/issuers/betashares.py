"""Betashares ETFs (Australia), daily full holdings.

Fund list
    The ASX's free ISIN list (``https://www.asx.com.au/content/dam/asx/issuers/ISIN.xls``)
    gives every ASX code with its name and ISIN; Betashares funds are the rows
    named ``BETASHARES ...``. Fund pages are matched by name to the slugs in
    ``https://www.betashares.com.au/fund-sitemap.xml``.
Holdings
    ``https://www.betashares.com.au/wp-admin/admin-ajax.php?action=fund_holdings_csv&ticker=<ASX code>``,
    the "Download holdings" link on each fund page: a few header lines
    (``Fund Name``, ``Fund ASX Code``, ``Date,YYYY-MM-DD``), then a table with
    ``Ticker``, ``Name``, ``Weight (%)`` and, depending on the fund, asset
    class, sector, country, currency and bond terms. Weights are percent of
    the fund.

Betashares identifies holdings by Bloomberg ticker (``BHP AT``, ``AAPL UW``),
not ISIN. ASX-listed lines (``AT``/``AU``) get their ISIN from the ASX list;
other lines keep only the ticker and stay unresolved unless matched elsewhere.
"""

from __future__ import annotations

import csv
import difflib
import io
import re

import pandas as pd

from ... import http
from . import FetchContext, IssuerAdapter, IssuerHoldings, register
from ._apac import asx_code, asx_isins, asx_list, clean, fetch, holdings_frame, number, products_frame

HOST = "https://www.betashares.com.au"
HOLDINGS_URL = HOST + "/wp-admin/admin-ajax.php?action=fund_holdings_csv&ticker={ticker}"
SITEMAP_URL = HOST + "/fund-sitemap.xml"

_GEARED = re.compile(r"\b(BEAR|GEARED|LEVERAGED|COMPLEX|SHORT|STRONG)\b")
_CURRENCY_OR_COMMODITY = re.compile(r"\b(DOLLAR|GOLD|CRUDE|OIL|BITCOIN|ETHEREUM|COMMODIT)")
_DEBT_WORDS = ("BOND", "CREDIT", "HYBRID", "TREASURY", "CASH", "LOAN", "INCOME", "SUBORDINATED", "FLOATING")


#: Abbreviations in ASX security names, expanded to match the fund page slugs.
_ABBREV = {"AUS": "AUSTRALIAN", "AUST": "AUSTRALIAN", "EQY": "EQUITY", "CORP": "CORPORATE", "INTL": "INTERNATIONAL", "GBL": "GLOBAL"}


def _slug(text: str) -> str:
    text = re.sub(r"^BETASHARES\s+", "", text.upper())
    text = text.replace("S&P", "SP").replace("&", "AND")
    text = " ".join(_ABBREV.get(w, w) for w in text.split())
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_sitemap(xml: str) -> dict[str, str]:
    """``{slug: fund page URL}`` from the fund sitemap."""
    urls = re.findall(r"<loc>(https://www\.betashares\.com\.au/fund/([^/<]+)/)</loc>", xml)
    return {slug: url for url, slug in urls}


def product_rows(asx: pd.DataFrame, pages: dict[str, str]) -> list[dict]:
    """Betashares rows of the ASX list to product rows."""
    funds = asx[asx["name"].fillna("").str.upper().str.startswith("BETASHARES")]
    # "... BSK" codes are in-specie basket securities used for creations, not fund units.
    funds = funds[~funds["security_type"].fillna("").str.upper().str.contains(r"\bBSK\b")]
    rows = []
    for _, f in funds.iterrows():
        name = f["name"]
        upper = name.upper()
        slug = _slug(name)
        page = pages.get(slug) or next((u for s, u in pages.items() if s.startswith(slug) or slug.startswith(s)), None)
        if page is None:
            close = difflib.get_close_matches(slug, list(pages), n=1, cutoff=0.85)
            page = pages[close[0]] if close else None
        if _GEARED.search(upper):
            replication, label = None, "Geared / Short"
        elif _CURRENCY_OR_COMMODITY.search(upper):
            replication, label = "physical", "Commodity / Currency"
        else:
            replication = "physical"
            label = "Fixed Income" if any(w in upper for w in _DEBT_WORDS) else "Equity"
        rows.append(
            {
                "issuer": "betashares",
                "site": "au",
                "portfolio_id": f["code"],
                "isin": f["isin"],
                "ticker": f["code"],
                "fund_name": re.sub(r"\bEtf\b", "ETF", name.title()),
                "domicile": "AU",
                "currency": "AUD",
                "asset_class": label,
                "replication": replication,
                "product_page_url": page or f"{HOST}/?s={f['code']}",
                "holdings_url": HOLDINGS_URL.format(ticker=f["code"]),
            }
        )
    return rows


def products() -> pd.DataFrame:
    """Every Betashares fund quoted on the ASX."""
    pages = parse_sitemap(http.get(SITEMAP_URL).text)
    return products_frame(product_rows(asx_list(), pages))


def parse_holdings(content: bytes, asx: dict[str, str] | None = None) -> tuple[str | None, pd.DataFrame]:
    """A Betashares holdings CSV to ``(as_of, HOLDING_COLUMNS frame)``."""
    text = content.decode("utf-8-sig", "replace")
    lines = list(csv.reader(io.StringIO(text)))
    as_of = None
    hdr = None
    for i, row in enumerate(lines):
        cells = [c.strip() for c in row]
        if cells and cells[0] == "Date" and len(cells) > 1 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[1]):
            as_of = cells[1]
        if cells and cells[0] == "Ticker" and "Name" in cells:
            hdr = i
            break
    if hdr is None:
        raise ValueError("Betashares holdings CSV has no 'Ticker,Name' header")
    cols = [c.strip() for c in lines[hdr]]
    i_weight = next(j for j, c in enumerate(cols) if c.startswith("Weight"))
    is_debt = "Maturity Date" in cols or "Coupon (%)" in cols
    rows = []
    for row in lines[hdr + 1 :]:
        if not row or not any(c.strip() for c in row) or len(row) < len(cols) - 1:
            break
        rec = dict(zip(cols, [c.strip() for c in row]))
        ticker = clean(rec.get("Ticker"))
        label_in = (rec.get("Asset Class") or "").lower()
        if "cash" in label_in or (ticker and re.fullmatch(r"[A-Z]{3}", ticker)):
            label = "cash"
        elif "future" in label_in or (ticker or "").upper().endswith(" INDEX") or (ticker or "").upper().endswith(" COMDTY"):
            label = "derivative (futures)"
        elif "forward" in label_in or "fx" in label_in:
            label = "derivative (foreign exchange)"
        elif is_debt:
            label = "debt"
        elif "fund" in label_in or re.search(r"\bETF\b|\bFUND\b", (rec.get("Name") or "").upper()):
            label = "fund"
        else:
            label = "equity" if not label_in or "equit" in label_in else "other"
        code = asx_code(ticker)
        rows.append(
            {
                "isin": (asx or {}).get(code) if code else None,
                "ticker": ticker,
                "name": rec.get("Name"),
                "weight_pct": number(row[i_weight]) if i_weight < len(row) else None,
                "asset_class": label,
                "currency": rec.get("Currency"),
                "country": rec.get("Country"),
            }
        )
    return as_of, holdings_frame(rows)


def holdings(product: dict, ctx: FetchContext) -> IssuerHoldings:
    url = product["holdings_url"]
    asx = asx_isins(ctx)
    as_of, df = fetch(url, lambda c: parse_holdings(c, asx), ctx=ctx, prefix="betashares", key=str(product["portfolio_id"]), ext="csv")
    notes = ["Betashares lists holdings by Bloomberg ticker; only ASX-listed lines get an ISIN (from the ASX ISIN list)."]
    return IssuerHoldings(as_of, df, url, notes)


register(
    IssuerAdapter(
        name="betashares",
        title="Betashares",
        homepage="https://www.betashares.com.au/",
        credit="Holdings: Betashares Capital Ltd (betashares.com.au); ISINs: ASX Limited ISIN list.",
        licence=(
            "Public holdings downloads on Betashares' website, free without a log-in; the file "
            "states Betashares makes no warranty about the data. ASX ISIN list is a free public "
            "download. Terms of reuse have not been reviewed; files are fetched at most once per "
            "fund per day."
        ),
        regions=("AU",),
        products=products,
        holdings=holdings,
        frequency="daily",
    )
)
