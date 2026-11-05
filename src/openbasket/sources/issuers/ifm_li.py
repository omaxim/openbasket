"""Liechtenstein funds administered by IFM Independent Fund Management.

IFM (``www.ifm.li``) lists about 500 Liechtenstein share classes on its
fund documents page, each with a link to its annual (``AR``) and
semi-annual (``SAR``) report. The reports' *Vermögensinventar* lists every
position with its ISIN and share of net assets in the Swiss and
Liechtenstein layout, which :mod:`openbasket.sources.kag_reports` reads.
Reports for an umbrella are split by the sub-fund's name.

``robots.txt`` disallows ``/JsonAPI/``, ``/Tools/``, ``/iFrame/`` and
``/Service/``; the documents page, ``/link/`` and ``/files/`` are allowed
and need no login or investor-type confirmation. IFM's terms forbid
reproduction without consent, so this source is ``local_only``. Funds that
AVL's library also has are read from there
(:mod:`openbasket.sources.issuers.avl_kag`), since an ISIN stays with the
first adapter that lists it.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pandas as pd

from ... import http
from .. import fund_reports as fr
from .. import kag_reports
from ..edinet_reports import tables
from . import LOCAL_ONLY, PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

NAME = "ifm_li"
HOST = "https://www.ifm.li"
DOCS_URL = HOST + "/FondsDokumente.aspx?nid=8212&groupnr=8212&lang=de&cookieCheck=true"
_PDF = re.compile(r"files/funddocuments/[^\"'\s]*?_(\d{4}-\d{2}-\d{2})\.pdf")


def parse_docs(html: str) -> pd.DataFrame:
    """``isin``, ``fund_name``, ``currency`` from the documents page's table."""
    rows = []
    for _, table in tables(html):
        for cells in table:
            cells = [c for c in cells if c]
            for i, c in enumerate(cells):
                if re.fullmatch(r"LI[0-9][0-9A-Z]{9}", c) and i > 0:
                    cur = next((x for x in cells[i + 1:] if re.fullmatch(r"[A-Z]{3}", x)), None)
                    rows.append({"isin": c, "fund_name": cells[i - 1], "currency": cur})
                    break
    return pd.DataFrame(rows, columns=["isin", "fund_name", "currency"]).drop_duplicates("isin")


def products() -> pd.DataFrame:
    r = http.get(DOCS_URL, timeout=120)
    r.raise_for_status()
    d = parse_docs(r.text)
    return pd.DataFrame({
        "issuer": NAME, "site": "li", "portfolio_id": d["isin"], "isin": d["isin"], "ticker": None,
        "fund_name": d["fund_name"], "domicile": "LI", "currency": d["currency"], "asset_class": None,
        "replication": None, "product_page_url": HOST + "/FondsDokumente.aspx?nid=8212&groupnr=8212&lang=de",
        "holdings_url": None,
    })[PRODUCT_COLUMNS].reset_index(drop=True)


def _report_url(isin: str) -> tuple[str, str] | None:
    """``(pdf_url, period_end)`` of the newer of the annual and semi-annual report."""
    found = []
    for kind in ("SAR", "AR"):
        r = http.get(f"{HOST}/link/de/{kind}/{isin}")
        m = _PDF.search(r.text) if r.ok else None
        if m:
            found.append((m.group(1), f"{HOST}/{m.group(0)}"))
    if not found:
        return None
    date, url = max(found)
    return url, date


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    isin = product["isin"]
    got = _report_url(isin)
    if got is None:
        raise LookupError(f"IFM lists no annual or semi-annual report for {isin}")
    url, as_of = got
    cache = Path((ctx.cache_dir if ctx and ctx.cache_dir else None) or fr.DEFAULT_CACHE_DIR) / "ifm_li"
    path = cache / f"{hashlib.sha1((url + '|' + str(product.get('fund_name'))).encode()).hexdigest()[:16]}.parquet"
    if path.exists():
        lines = pd.read_parquet(path)
    else:
        r = http.get(url, timeout=120)
        r.raise_for_status()
        lines, _ = kag_reports.parse_pdf(r.content, fund_name=product.get("fund_name"))
        cache.mkdir(parents=True, exist_ok=True)
        lines.to_parquet(path, index=False)
    if not kag_reports.plausible(lines):
        raise LookupError(f"the report {url} gave holdings adding up to {kag_reports.total(lines):.1f}%, outside "
                          f"{kag_reports.MIN_TOTAL:.0f}-{kag_reports.MAX_TOTAL:.0f}%")
    notes = [f"report: {url}",
             f"weights: percent of the fund's net assets as printed in the Vermögensinventar, not rescaled; "
             f"lines add up to {kag_reports.total(lines):.2f}%"]
    return IssuerHoldings(as_of=as_of, holdings=kag_reports.as_holdings(lines), url=url, notes=notes)


ADAPTER = register(
    IssuerAdapter(
        name=NAME,
        title="IFM Independent Fund Management: Liechtenstein funds' annual and semi-annual reports",
        homepage=HOST,
        credit="Holdings from the fund's annual or semi-annual report published by IFM Independent Fund Management AG",
        licence="IFM's terms forbid reproduction without consent. Read locally only, never republished.",
        regions=("LI",),
        products=products,
        holdings=holdings,
        frequency="semi-annual",
        redistribution=LOCAL_ONLY,
    )
)
