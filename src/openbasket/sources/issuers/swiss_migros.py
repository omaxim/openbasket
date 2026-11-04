"""Migros Bank (CH) Fonds and Migros Bank (Lux) Fonds: holdings from their reports.

**Off by default** (``ENABLED_BY_DEFAULT = False``). Migros Bank's fund page
(``/de/privatpersonen/anlegen/anlageinstrumente/anlagefonds.html``) links the
annual and semi-annual reports of both umbrellas directly, and robots.txt
allows them, but the page also carries an overlay saying the information is
for private persons resident in Switzerland and asking for the visitor's
domicile. openbasket never answers it; whether to read the reports anyway is
for the person running the tool to decide (``OPENBASKET_ENABLE=swiss_migros``),
or to ask Migros Bank (legal information page linked below).

Both umbrellas are administered by UBS: the Swiss one by UBS Fund Management
(Switzerland) AG ("Inventar des Fondsvermögens" layout), the Luxembourg one by
UBS Asset Management (Europe) S.A. ("Aufstellung der Wertpapierbestände"
layout). Each report's table of contents lists every sub-fund with its
share-class ISINs, which is where :func:`products` takes them from.

``REDISTRIBUTION = "local_only"``: the reports are Migros Bank's publications.
"""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from urllib.parse import urljoin

import pandas as pd

from .. import swiss_fetch, swiss_reports
from . import HOLDING_COLUMNS, PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

logger = logging.getLogger(__name__)

NAME = "swiss_migros"
REDISTRIBUTION = "local_only"
ENABLED_BY_DEFAULT = False

PAGE = "https://www.migrosbank.ch/de/privatpersonen/anlegen/anlageinstrumente/anlagefonds.html"
LEGAL = "https://www.migrosbank.ch/de/ueber-uns/rechtliche-informationen.html"
CACHE_DIR = os.environ.get("OPENBASKET_SWISS_CACHE", "data/01_raw/swiss")
UMBRELLAS = {"CH": "Migros Bank (CH) Fonds", "LU": "Migros Bank (Lux) Fonds"}


def report_links(html: str, base: str = PAGE) -> dict[str, tuple[str, str]]:
    """Newest report per umbrella: ``{"CH": (url, kind), "LU": (url, kind)}``."""
    out: dict[str, tuple[str, str, str]] = {}
    for href in re.findall(r'href="([^"]+\.pdf)"', html):
        url = urljoin(base, href)
        name = re.sub(r"%20", " ", url.rsplit("/", 1)[-1])
        m = re.match(r"(Halbjahresbericht|Jahresbericht) Migros Bank \((CH|Lux)\) Fonds", name)
        if not m:
            continue
        dom = "CH" if m.group(2) == "CH" else "LU"
        kind = "semi-annual report" if m.group(1).startswith("Halb") else "annual report"
        stamp = swiss_reports.report_link_date(name)
        if dom not in out or stamp > out[dom][2]:
            out[dom] = (url, kind, stamp)
    return {k: (u, kind) for k, (u, kind, _) in out.items()}


@lru_cache(maxsize=4)
def _links(cache_dir: str | None) -> dict[str, tuple[str, str]]:
    body = swiss_fetch.cached(cache_dir, f"migros_pages/{swiss_fetch.today()}.html", lambda: swiss_fetch.get_bytes(PAGE, timeout=60))
    return report_links(body.decode("utf-8", "replace"))


def products(cache_dir: str | None = CACHE_DIR) -> pd.DataFrame:
    if not swiss_reports.enabled(NAME, ENABLED_BY_DEFAULT):
        logger.info("%s is off by default (Swiss-residence overlay on the site); set OPENBASKET_ENABLE=%s to use it", NAME, NAME)
        return pd.DataFrame(columns=PRODUCT_COLUMNS + ["report_url", "report_kind"])
    rows = []
    for dom, (url, kind) in _links(cache_dir).items():
        toc = swiss_reports.parse_ch_toc_isins(swiss_reports.fetch_report(url, cache_dir))
        for subfund, isins in toc.items():
            for isin in isins:
                rows.append(
                    {
                        "issuer": NAME, "site": dom.lower(), "portfolio_id": subfund, "isin": isin, "ticker": None,
                        "fund_name": subfund, "domicile": dom, "currency": None, "asset_class": None, "replication": None,
                        "product_page_url": PAGE, "holdings_url": url, "report_url": url, "report_kind": kind,
                    }
                )
    return pd.DataFrame(rows, columns=PRODUCT_COLUMNS + ["report_url", "report_kind"]).drop_duplicates("isin").reset_index(drop=True)


def holdings(product: dict, ctx: FetchContext) -> IssuerHoldings:
    if not swiss_reports.enabled(NAME, ENABLED_BY_DEFAULT):
        raise PermissionError(f"{NAME} is off by default; set OPENBASKET_ENABLE={NAME} after deciding to use it")
    cache_dir = ctx.cache_dir or CACHE_DIR
    url = product.get("report_url") or product.get("holdings_url")
    kind = product.get("report_kind") or "report"
    as_of, df, notes = swiss_reports.holdings_from_report(url, product["fund_name"], "", cache_dir, kind)
    notes.append(f"source: Migros Bank AG, {PAGE}")
    return IssuerHoldings(as_of, df[HOLDING_COLUMNS + ["isin_source"]], url, notes)


register(
    IssuerAdapter(
        name=NAME,
        title="Migros Bank funds (annual and semi-annual reports)",
        homepage=PAGE,
        credit="Holdings from the annual and semi-annual reports of Migros Bank (CH) Fonds and Migros Bank (Lux) Fonds",
        licence="local only, off by default: reports behind an unsubmitted Swiss-residence overlay; no republication licence",
        regions=("CH", "LU"),
        products=lambda: products(),
        holdings=holdings,
        frequency="semi-annual",
    )
)
