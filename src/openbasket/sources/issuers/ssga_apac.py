"""State Street (SPDR) ETFs domiciled in Australia, Singapore, Hong Kong and Japan.

The US and UCITS SPDR ranges are read by :mod:`openbasket.sources.spdr`;
this adapter covers the funds State Street lists only on its Asia-Pacific
sites, such as STW (S&P/ASX 200), ES3 (Straits Times Index), the ABF Pan Asia
Bond Index Fund (2821) and the Japanese-listed SPDR S&P 500 ETFs.

Fund lists
    ``/bin/v1/ssmp/fund/fundfinder?country=<au|sg|hk|jp>&role=institutional``
    (the ``intermediary`` role returns nothing outside Australia). Only funds
    domiciled in the site's own country are kept; cross-listed US funds such
    as SPY are left to the US module.
Fund ISIN
    The ISIN in the entry's ``keywords`` text; for Australian funds without
    one, the ASX ISIN list; otherwise the ISIN printed on the fund page.
Holdings
    ``/library-content/products/fund-data/etfs/apac/holdings-daily-<cc>-<lang>-<ticker>.xlsx``
    for Australia, Singapore and Japan, and the
    ``...-fullholdings-download-hk-en-daily.xlsx`` file linked from the 2821
    page. A few header lines (name, ticker, as-of date), then a table with an
    ``ISIN`` column and the weight in percent of the fund.
"""

from __future__ import annotations

import collections
import re

import pandas as pd

from ... import http

from . import FetchContext, IssuerAdapter, IssuerHoldings, register
from ._apac import asset_class, asx_isins, clean, fetch, holdings_frame, number, products_frame, read_sheets, valid_isin

HOST = "https://www.ssga.com"
FINDER_URL = HOST + "/bin/v1/ssmp/fund/fundfinder"
HOLDINGS_BASE = HOST + "/library-content/products/fund-data/etfs/apac/"

#: Regional sites: fund-finder parameters, the domicile label of local funds
#: and the language part of the holdings file name.
SITES = {
    "au": {"country": "au", "language": "en_gb", "domicile": "AU", "file_lang": "en"},
    "sg": {"country": "sg", "language": "en", "domicile": "SG", "file_lang": "en"},
    "hk": {"country": "hk", "language": "en", "domicile": "HK", "file_lang": "en"},
    "jp": {"country": "jp", "language": "ja", "domicile": "日本", "file_lang": "ja"},
}

_ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b")
_LINK_RE = re.compile(r'"(/library-content/products/fund-data/etfs/apac/[^"]*(?:holdings-daily|fullholdings-download)[^"]*\.xlsx)"')
_DATE_EN = re.compile(r"(\d{1,2}-[A-Za-z]{3}-\d{4})")
_DATE_JA = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")

ASSET_CLASSES = {"Equity": "Equity", "Fixed Income": "Fixed Income", "alternative": "Real Estate"}


def _finder_params(site: str) -> dict:
    s = SITES[site]
    return {"country": s["country"], "language": s["language"], "role": "institutional", "product": "etfs", "ui": "fund-finder"}


def parse_finder(payload: dict, site: str, asx: dict[str, str] | None = None) -> list[dict]:
    """Fund-finder JSON to product rows (no network). Rows may still lack an ISIN or holdings URL."""
    s = SITES[site]
    rows = []
    for d in payload.get("data", {}).get("funds", {}).get("etfs", {}).get("datas", []):
        if (d.get("domicile") or "").strip() != s["domicile"]:
            continue
        ticker = (d.get("fundTicker") or d.get("fundFilter") or "").strip()
        keywords = d.get("keywords") or ""
        isins = [t for t in _ISIN_RE.findall(keywords) if valid_isin(t)]
        isin = isins[0] if isins else None
        if isin is None and site == "au" and asx:
            isin = asx.get(ticker.upper())
        words = [w.strip().lower() for w in keywords.split(",")]
        label = next((v for k, v in ASSET_CLASSES.items() if k.lower() in words), None)
        holdings_url = None
        if site in ("au", "sg", "jp") and re.fullmatch(r"[A-Za-z0-9]+", ticker):
            holdings_url = f"{HOLDINGS_BASE}holdings-daily-{s['country']}-{s['file_lang']}-{ticker.lower()}.xlsx"
        rows.append(
            {
                "issuer": "ssga_apac",
                "site": site,
                "portfolio_id": f"{site}:{ticker}",
                "isin": isin,
                "ticker": ticker or None,
                "fund_name": d.get("fundName"),
                "domicile": {"日本": "JP"}.get(s["domicile"], s["domicile"]),
                "currency": None,
                "asset_class": label,
                # State Street's Asia-Pacific ETFs hold their securities (or, for
                # the Japanese gold ETFs, a US gold ETF); none is swap-based.
                "replication": "physical",
                "product_page_url": HOST + d["fundUri"] if d.get("fundUri") else None,
                "holdings_url": holdings_url,
            }
        )
    return rows


def parse_fund_page(html: str) -> tuple[str | None, str | None]:
    """``(most frequent ISIN on the page, holdings file URL)`` from a fund page."""
    isins = collections.Counter(t for t in _ISIN_RE.findall(html) if valid_isin(t))
    links = _LINK_RE.findall(html)
    link = next((l for l in links if "holdings-daily" in l), None) or next((l for l in links if "daily" in l), None)
    return (isins.most_common(1)[0][0] if isins else None), (HOST + link if link else None)


def products() -> pd.DataFrame:
    """Every ETF domiciled in Australia, Singapore, Hong Kong or Japan on State Street's sites."""

    asx = asx_isins()
    rows: list[dict] = []
    for site in SITES:
        payload = http.get(FINDER_URL, params=_finder_params(site)).json()
        for row in parse_finder(payload, site, asx):
            if (row["isin"] is None or row["holdings_url"] is None) and row["product_page_url"]:
                isin, link = parse_fund_page(http.get(row["product_page_url"]).text)
                row["isin"] = row["isin"] or isin
                row["holdings_url"] = row["holdings_url"] or link
            if row["holdings_url"]:
                rows.append(row)
    return products_frame(rows)


def _as_of(texts: list[str]) -> str | None:
    for t in texts:
        m = _DATE_EN.search(t)
        if m and ("as of" in t.lower() or "holdings" in t.lower()):
            return pd.to_datetime(m.group(1), format="%d-%b-%Y").date().isoformat()
        m = _DATE_JA.search(t)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def _pick(cols: list[str], *names: str) -> str | None:
    return next((n for n in names if n in cols), None)


def parse_holdings(content: bytes) -> tuple[str | None, pd.DataFrame]:
    """A State Street Asia-Pacific holdings workbook to ``(as_of, HOLDING_COLUMNS frame)``."""
    raw = next(iter(read_sheets(content).values()))
    hdr = None
    for i, row in raw.iterrows():
        cells = [clean(v) for v in row]
        if "ISIN" in cells or "ISIN Number" in cells:
            hdr = i
            break
    if hdr is None:
        raise ValueError("State Street holdings file has no ISIN header row")
    head = [" ".join(clean(v) or "" for v in r) for _, r in raw.iloc[:hdr].iterrows()]
    as_of = _as_of(head)

    cols = [clean(c) or f"col{j}" for j, c in enumerate(raw.iloc[hdr])]
    body = raw.iloc[hdr + 1 :].copy()
    body.columns = cols
    blank = body.isna().all(axis=1)
    if blank.any():
        body = body.loc[: blank.idxmax()].iloc[:-1]

    isin_c = _pick(cols, "ISIN", "ISIN Number")
    weight_c = _pick(cols, "Weight (%)", "Weight %", "Weight", "Percent of Fund", "組入比率")
    name_c = _pick(cols, "Name", "Security Name", "Issue Name", "銘柄名")
    if weight_c is None or name_c is None:
        raise ValueError(f"State Street holdings file has unexpected columns {cols}")
    cur_c = _pick(cols, "Local Currency", "ISO Currency", "Currency", "現地通貨")
    country_c = _pick(cols, "Trade Country Name", "Country of Issue Name")
    is_debt = any(c in cols for c in ("Coupon", "Maturity", "Maturity Date")) or "Issue Name" in cols

    rows = []
    for _, r in body.iterrows():
        name = clean(r[name_c])
        ticker = clean(r["Ticker"]) if "Ticker" in cols else None
        code = (ticker or clean(r.get("SEDOL")) or name or "").upper()
        if code.startswith("CASH") or (name or "").lower().startswith("cash_"):
            label = "cash"
        elif re.search(r"\b(fut|futr|futures?)\b", (name or "").lower()):
            label = "derivative (futures)"
        elif "fx forward" in (name or "").lower():
            label = "derivative (foreign exchange)"
        elif "fund" in (name or "").lower() and "sicav" in (name or "").lower():
            label = "fund"
        else:
            label = "debt" if is_debt else "equity"
        rows.append(
            {
                "isin": r[isin_c],
                "sedol": r.get("SEDOL"),
                "ticker": ticker,
                "name": name,
                "weight_pct": number(r[weight_c]),
                "asset_class": label,
                "currency": r[cur_c] if cur_c else None,
                "country": r[country_c] if country_c else None,
            }
        )
    df = holdings_frame(rows)
    return as_of, df[df["name"].notna() | df["isin"].notna()].reset_index(drop=True)


def holdings(product: dict, ctx: FetchContext) -> IssuerHoldings:
    url = product["holdings_url"]
    as_of, df = fetch(url, parse_holdings, ctx=ctx, prefix="ssga_apac", key=str(product["portfolio_id"]), ext="xlsx")
    notes = []
    if df["weight_pct"].isna().any():
        notes.append("Futures and some cash lines carry no weight in the file.")
    return IssuerHoldings(as_of, df, url, notes)


register(
    IssuerAdapter(
        name="ssga_apac",
        title="State Street SPDR ETFs (Australia, Singapore, Hong Kong, Japan)",
        homepage="https://www.ssga.com/au/en_gb/intermediary/etfs",
        credit="Holdings: State Street Global Advisors, ssga.com (daily fund holdings files).",
        licence=(
            "Public files on State Street's website, free to download without a log-in. "
            "The site terms have not been reviewed for automated reuse; files are fetched "
            "at most once per fund per day."
        ),
        regions=("AU", "SG", "HK", "JP"),
        products=products,
        holdings=holdings,
        frequency="daily",
    )
)
