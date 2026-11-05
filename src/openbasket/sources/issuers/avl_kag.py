"""Austrian and Liechtenstein funds' holdings from their reports on AVL Investmentfonds' document library.

AVL Investmentfonds (``dl.avl-investmentfonds.de``) keeps the annual
(Rechenschaftsbericht) and semi-annual reports of most funds sold in Germany
and Austria, one PDF per share-class ISIN (see
:mod:`openbasket.sources.fund_reports`). Austrian reports list every
position in a Vermögensaufstellung with its ISIN and share of the fund's net
assets, which :mod:`openbasket.sources.kag_reports` reads.

The fund list is every Austrian (``AT``) and Liechtenstein (``LI``) ISIN with a report from the last
:data:`MAX_AGE_DAYS` days. The library gives no fund names, so names come
from OpenFIGI (``$OPENFIGI_API_KEY``; answers are cached in
``data/03_primary/openfigi_cache.parquet``, or ``$OPENBASKET_OPENFIGI_CACHE``).
German funds are read from the Bundesanzeiger instead
(:mod:`openbasket.sources.issuers.bundesanzeiger_de`).

AVL's ``robots.txt`` allows ``/fds/`` for all agents (it shuts out named AI
crawlers, which openbasket is not); its legal notice says nothing about
reuse. The reports are the management companies' documents, so this source
is ``local_only``.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ... import http
from .. import fund_reports as fr
from .. import kag_reports
from . import LOCAL_ONLY, PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

logger = logging.getLogger(__name__)

NAME = "avl_kag"
COUNTRIES = ("AT", "LI")
MAX_AGE_DAYS = 400
DEFAULT_FIGI_CACHE = "data/03_primary/openfigi_cache.parquet"


def _latest(index: pd.DataFrame, today: date | None = None) -> pd.DataFrame:
    since = ((today or date.today()) - timedelta(days=MAX_AGE_DAYS)).strftime("%Y%m%d")
    r = index[index["type"].isin(list(fr.AVL_KINDS)) & index["isin"].str[:2].isin(COUNTRIES) & (index["date"] >= since)]
    return r.sort_values("date").drop_duplicates("isin", keep="last").reset_index(drop=True)


def _names(isins: list[str]) -> dict[str, str]:
    from ..openfigi import OpenFIGI, best_listing

    cache = os.environ.get("OPENBASKET_OPENFIGI_CACHE") or DEFAULT_FIGI_CACHE
    figi = OpenFIGI(cache, autosave=False)
    try:
        found = figi.map("ID_ISIN", isins)
    finally:
        try:
            figi.save()
        except Exception as exc:  # an unsaved cache costs requests next time, nothing more
            logger.warning("OpenFIGI cache not saved: %s", exc)
    out = {}
    for isin, listings in found.items():
        b = best_listing(listings)
        if b and b.get("name"):
            out[isin] = b["name"]
    return out


def products() -> pd.DataFrame:
    latest = _latest(fr.avl_index(None))
    if latest.empty:
        return pd.DataFrame(columns=PRODUCT_COLUMNS)
    names = _names(latest["isin"].tolist())
    latest = latest[latest["isin"].isin(names)]
    return pd.DataFrame({
        "issuer": NAME, "site": "at", "portfolio_id": latest["isin"], "isin": latest["isin"], "ticker": None,
        "fund_name": latest["isin"].map(names), "domicile": latest["isin"].str[:2], "currency": None,
        "asset_class": None, "replication": None, "product_page_url": latest["url"], "holdings_url": latest["url"],
    })[PRODUCT_COLUMNS].reset_index(drop=True)


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    url = product.get("holdings_url")
    if not url:
        raise LookupError(f"no AVL report for {product['isin']}")
    cache = Path((ctx.cache_dir if ctx and ctx.cache_dir else None) or fr.DEFAULT_CACHE_DIR) / "avl_kag"
    path = cache / f"{hashlib.sha1(url.encode()).hexdigest()[:16]}.parquet"
    if path.exists():
        lines = pd.read_parquet(path)
    else:
        r = http.get(url, timeout=120)
        r.raise_for_status()
        lines, how = kag_reports.parse_pdf(r.content, fund_name=product.get("fund_name"))
        cache.mkdir(parents=True, exist_ok=True)
        lines.to_parquet(path, index=False)
    if not kag_reports.plausible(lines):
        raise LookupError(f"the report {url} gave holdings adding up to {kag_reports.total(lines):.1f}%, outside "
                          f"{kag_reports.MIN_TOTAL:.0f}-{kag_reports.MAX_TOTAL:.0f}%")
    stamp = url.rsplit("-", 1)[-1].removesuffix(".pdf")
    as_of = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if stamp.isdigit() and len(stamp) == 8 else None
    notes = [f"report: {url}",
             f"weights: percent of the fund's net assets as printed in the Vermögensaufstellung, not rescaled; "
             f"lines add up to {kag_reports.total(lines):.2f}%"]
    return IssuerHoldings(as_of=as_of, holdings=kag_reports.as_holdings(lines), url=url, notes=notes)


ADAPTER = register(
    IssuerAdapter(
        name=NAME,
        title="Austrian and Liechtenstein funds' annual and semi-annual reports (AVL Investmentfonds library)",
        homepage="https://www.avl-investmentfonds.de",
        credit=("Holdings from the fund's annual or semi-annual report, located by ISIN on AVL Investmentfonds' "
                "public document library; positions as of the report date"),
        licence=("Reports are the management companies' documents, read from AVL's ungated library "
                 "(robots.txt allows /fds/). Read locally only, never republished."),
        regions=COUNTRIES,
        products=products,
        holdings=holdings,
        frequency="semi-annual",
        redistribution=LOCAL_ONLY,
    )
)
