"""L&G (Legal & General) funds through their annual and semi-annual reports.

``https://fundcentres.landg.com/robots.txt`` disallows ``/srp/api/``,
``/srp/documents/`` and ``/srp/lit/``, which is where L&G's fund data,
holdings files and documents are served, so openbasket requests none of
them. The fund centre's sitemaps are allowed and list one page per share
class, ``/{lang}/{country}/{audience}/fund-centre/{ETF|ICAV|SICAV}/{Name}/{ISIN}/``,
which gives the fund list. Holdings come from the fund's statutory report in
the AVL Investmentfonds library, read the same way as for Xtrackers
(:mod:`openbasket.sources.report_route`).

L&G's report contents list "Schedule of Investments" with one page number,
so sub-funds are found from the page headers, where each schedule page
repeats the sub-fund name. The fund-centre name is shorter than the report
title ("SP-100" for "L&G S&P 100 UCITS ETF"), so a fund matches the title
that equals ``L&G <name> UCITS ETF`` or, failing that, the only title that
contains the name.

What a row is: the fund's holdings on the report date (L&G UCITS ETF Plc's
year ends 30 June, its half-year 31 December), not today's holdings.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

import pandas as pd

from .. import avl_documents as avl
from .. import report_route as route
from .. import robots
from .. import schedule_tables as st
from . import PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

#: Statutory reports are L&G's copyright documents with no open licence: local use only.
REDISTRIBUTION = "local_only"

NAME = "lgim_reports"
BRAND = "L&G"
SITEMAPS = (
    "https://fundcentres.landg.com/en/uk/private-investors/sitemap.xml",
    "https://fundcentres.landg.com/en/ch/private-investors/sitemap.xml",
    "https://fundcentres.landg.com/en/de/private-investors/sitemap.xml",
    "https://fundcentres.landg.com/en/ie/institutional/sitemap.xml",
)
_PAGE = re.compile(r"/fund-centre/(ETF|ICAV|SICAV)/([^/]+)/([A-Z]{2}[A-Z0-9]{9}\d)/?$")

CREDIT = "Holdings: L&G (LGIM Managers (Europe) Limited), annual and semi-annual reports"
FAMILY = route.Family(
    brand=BRAND,
    credit=CREDIT,
    blocked="fundcentres.landg.com serves fund data and holdings only from /srp/api/ and /srp/documents/, "
            "which its robots.txt disallows",
    loose=True,
)


def fund_key(kind: str, slug: str) -> str:
    words = slug.replace("-", " ")
    return st.compact(f"{words} UCITS ETF") if kind == "ETF" else st.compact(words)


def parse_sitemap(xml: str) -> list[dict]:
    rows = []
    for url in avl.parse_sitemap_urls(xml):
        m = _PAGE.search(url)
        if not m:
            continue
        kind, slug, isin = m.groups()
        rows.append({
            "issuer": NAME,
            "site": kind,
            "portfolio_id": fund_key(kind, slug),
            "isin": isin,
            "ticker": None,
            "fund_name": f"L&G {slug.replace('-', ' ')}" + (" UCITS ETF" if kind == "ETF" else ""),
            "domicile": isin[:2],
            "currency": None,
            "asset_class": None,
            "replication": None,
            "product_page_url": url,
            "holdings_url": None,
        })
    return rows


#: Fund types read. ICAV and SICAV pages are L&G's index and active mutual funds,
#: whose reports are separate umbrellas; set OPENBASKET_LGIM_TYPES=ETF,ICAV,SICAV to add them.
TYPES = tuple(os.environ.get("OPENBASKET_LGIM_TYPES", "ETF").split(","))


def products(cache_dir: str | Path | None = None, fetch=None) -> pd.DataFrame:
    """L&G share classes with an ISIN in the fund-centre sitemaps (ETF pages by default)."""
    cache = avl.cache_dir(cache_dir) / f"lgim_products_{date.today().isoformat()}.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
        return df[df["site"].isin(TYPES)].reset_index(drop=True)
    get = fetch or (lambda u: robots.get(u, timeout=60))
    rows: list[dict] = []
    for sm in SITEMAPS:
        r = get(sm)
        if r.status_code == 200:
            rows.extend(parse_sitemap(r.text))
    df = pd.DataFrame(rows, columns=PRODUCT_COLUMNS).drop_duplicates("isin").reset_index(drop=True)
    df.to_parquet(cache, index=False)
    return df[df["site"].isin(TYPES)].reset_index(drop=True)


def holdings(product: dict, ctx: FetchContext | None = None, *, fetch=None, catalogue: pd.DataFrame | None = None,
             name_index: dict | None = None) -> IssuerHoldings:
    cache = ctx.cache_dir if ctx and ctx.cache_dir else None
    cat = catalogue if catalogue is not None else products(cache, fetch=fetch)
    return route.holdings(product, FAMILY, cat, cache, fetch=fetch, index=name_index)


ADAPTER = register(IssuerAdapter(
    name=NAME,
    title="L&G (Legal & General), statutory reports",
    homepage="https://fundcentres.landg.com",
    credit=CREDIT,
    licence=(
        "Statutory annual and semi-annual reports published by L&G for investors; L&G's copyright. "
        "Read from the AVL Investmentfonds library, whose robots.txt allows /fds/. No open licence: "
        "local use only (REDISTRIBUTION = local_only)."
    ),
    regions=("IE", "LU"),
    products=products,
    holdings=holdings,
    frequency="semi-annual",
))
