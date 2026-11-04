"""Vontobel Fund (Luxembourg SICAV): holdings from its statutory reports.

**Off by default** (``ENABLED_BY_DEFAULT = False``). The fund pages on
``am.vontobel.com`` and the report PDFs they link are served to anyone and
``robots.txt`` allows them, but every page also shows an overlay asking the
visitor to confirm a country of residence and an investor type "in order to
access tailored information". openbasket never answers such an overlay, and
reading the documents behind it is a decision for the person running the
tool. Enable it locally with ``OPENBASKET_ENABLE=swiss_vontobel`` (or the
``swiss.enable`` parameter) once you have decided, or after asking Vontobel
(imprint: vontobelgroup@vontobel.com).

Route for one share class:

1. ``https://am.vontobel.com/en/view/{ISIN}`` lists the documents of the
   share class; the newest ``Annual-Report_<date>_EN_Vontobel-Fund.pdf`` or
   ``Semi-Annual-Report_<date>_EN_Vontobel-Fund.pdf`` is taken.
2. The report's "Portfolio as at <date>" pages are parsed
   (:func:`openbasket.sources.swiss_reports.parse_lux_portfolio`) and the
   sub-fund whose heading matches the fund's GLEIF legal name is returned.

The share-class list comes from GLEIF (funds managed by Vontobel Asset
Management S.A., LEI 529900LO1T9ADP03SQ41) joined to the GLEIF ISIN-LEI file
and ESMA FIRDS. ``REDISTRIBUTION = "local_only"``: the reports are
Vontobel's publications and no licence allows republication.
"""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache

import pandas as pd

from .. import swiss_fetch, swiss_registry, swiss_reports
from . import HOLDING_COLUMNS, PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

logger = logging.getLogger(__name__)

NAME = "swiss_vontobel"
REDISTRIBUTION = "local_only"
ENABLED_BY_DEFAULT = False

MANAGER_LEI = "529900LO1T9ADP03SQ41"   # Vontobel Asset Management S.A. (Luxembourg)
HOST = "https://am.vontobel.com"
PAGE = HOST + "/en/view/{isin}"
UMBRELLA = "Vontobel Fund"
CACHE_DIR = os.environ.get("OPENBASKET_SWISS_CACHE", "data/01_raw/swiss")
REPORT_RE = re.compile(r'href="(/en/document/[0-9a-f-]+/((?:Semi-)?Annual-Report)_(\d{8})_EN_Vontobel-Fund\.pdf)"')


def products(cache_dir: str | None = CACHE_DIR) -> pd.DataFrame:
    if not swiss_reports.enabled(NAME, ENABLED_BY_DEFAULT):
        logger.info("%s is off by default (investor-type overlay on the site); set OPENBASKET_ENABLE=%s to use it", NAME, NAME)
        return pd.DataFrame(columns=PRODUCT_COLUMNS)
    funds = swiss_registry.gleif_managed_funds(MANAGER_LEI, cache_dir)
    leis = {f["fund_lei"] for f in funds}
    isins = swiss_registry.gleif_isin_lei(leis, cache_dir)
    firds, _ = swiss_registry.firds_funds(leis, cache_dir)
    pairs = pd.concat([isins, firds[["fund_lei", "isin"]]], ignore_index=True).drop_duplicates("isin")
    names = {f["fund_lei"]: f["name"] for f in funds}
    rows = [
        {
            "issuer": NAME, "site": "lu", "portfolio_id": lei, "isin": isin, "ticker": None,
            "fund_name": names.get(lei), "domicile": "LU", "currency": None, "asset_class": None, "replication": None,
            "product_page_url": PAGE.format(isin=isin), "holdings_url": None,
        }
        for lei, isin in pairs.itertuples(index=False)
        if isin.startswith("LU")
    ]
    return pd.DataFrame(rows, columns=PRODUCT_COLUMNS)


@lru_cache(maxsize=512)
def latest_report(isin: str, cache_dir: str | None = CACHE_DIR) -> tuple[str, str] | None:
    """(URL, kind) of the newest English annual or semi-annual report linked from the share class page."""
    body = swiss_fetch.cached(cache_dir, f"vontobel_pages/{swiss_fetch.today()}/{isin}.html",
                              lambda: swiss_fetch.get_bytes(PAGE.format(isin=isin), timeout=60))
    links = REPORT_RE.findall(body.decode("utf-8", "replace"))
    if not links:
        return None
    path, kind, stamp = max(links, key=lambda t: t[2])
    return HOST + path, ("semi-annual report" if kind.startswith("Semi") else "annual report")


def holdings(product: dict, ctx: FetchContext) -> IssuerHoldings:
    if not swiss_reports.enabled(NAME, ENABLED_BY_DEFAULT):
        raise PermissionError(f"{NAME} is off by default; set OPENBASKET_ENABLE={NAME} after deciding to use it")
    cache_dir = ctx.cache_dir or CACHE_DIR
    found = latest_report(product["isin"], cache_dir)
    if found is None:
        raise LookupError(f"no report linked from {PAGE.format(isin=product['isin'])}")
    url, kind = found
    as_of, df, notes = swiss_reports.holdings_from_report(url, product["fund_name"] or "", UMBRELLA, cache_dir, kind)
    notes.append(f"source: Vontobel Asset Management, {PAGE.format(isin=product['isin'])}")
    return IssuerHoldings(as_of, df[HOLDING_COLUMNS + ["isin_source"]], url, notes)


register(
    IssuerAdapter(
        name=NAME,
        title="Vontobel Fund (annual and semi-annual reports)",
        homepage=HOST,
        credit="Holdings from the annual and semi-annual reports of Vontobel Fund, Vontobel Asset Management S.A.",
        licence="local only, off by default: reports behind an unsubmitted investor-type overlay; no republication licence",
        regions=("LU",),
        products=lambda: products(),
        holdings=holdings,
        frequency="semi-annual",
    )
)
