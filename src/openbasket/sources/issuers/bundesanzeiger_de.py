"""German public funds' holdings from their reports in the Bundesanzeiger.

See :mod:`openbasket.sources.bundesanzeiger` for how a report is found and
:mod:`openbasket.sources.kag_reports` for how its Vermögensaufstellung is
read. The fund list is every German ISIN in openbasket's fund registry
(GLEIF and ESMA, ``data/03_primary/fund_registry.parquet`` or
``$OPENBASKET_FUND_REGISTRY``), share classes grouped by the fund's LEI.
Special funds (Spezial-AIF) are in the registry too but publish nothing in
the Bundesanzeiger; their search finds no report and they stay without
holdings.

Weights are the report's percent of the fund's net assets, as printed, and
ISINs are as printed. Positions are as of the report's date: the fund's last
half-year or year end.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from .. import bundesanzeiger as ba
from .. import kag_reports
from . import LOCAL_ONLY, PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

NAME = "bundesanzeiger_de"
DEFAULT_REGISTRY = "data/03_primary/fund_registry.parquet"
DEFAULT_CACHE = "data/01_raw/issuers/bundesanzeiger"


def products() -> pd.DataFrame:
    path = Path(os.environ.get("OPENBASKET_FUND_REGISTRY") or DEFAULT_REGISTRY)
    if not path.is_file():
        return pd.DataFrame(columns=PRODUCT_COLUMNS)
    g = pd.read_parquet(path, columns=["isin", "lei", "name"])
    g = g[g["isin"].fillna("").str.startswith("DE") & g["name"].notna()].drop_duplicates("isin")
    return pd.DataFrame({
        "issuer": NAME, "site": "de", "portfolio_id": g["lei"].fillna(g["isin"]), "isin": g["isin"], "ticker": None,
        "fund_name": g["name"], "domicile": "DE", "currency": None, "asset_class": None, "replication": None,
        "product_page_url": ba.HOST, "holdings_url": None,
    })[PRODUCT_COLUMNS].reset_index(drop=True)


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    root = Path((ctx.cache_dir if ctx and ctx.cache_dir else None) or DEFAULT_CACHE)
    store = ba.Store(root / "bundesanzeiger" if ctx and ctx.cache_dir else root)
    isin = product["isin"]
    got = store.holdings(isin, fund_name=product.get("fund_name"))
    if got is None:
        raise LookupError(f"no annual or semi-annual report listing {isin} in the Bundesanzeiger")
    lines, meta = got
    if not kag_reports.plausible(lines):
        raise LookupError(f"the Bundesanzeiger report '{meta['title']}' for {isin} gave holdings adding up to "
                          f"{meta['total_pct']:.1f}%, outside {kag_reports.MIN_TOTAL:.0f}-{kag_reports.MAX_TOTAL:.0f}%")
    notes = [
        f"report: {meta['title']} ({meta.get('fund') or product.get('fund_name')}), published {meta['published']} "
        f"in the Bundesanzeiger; search the ISIN {isin} under Kapitalmarkt to open it",
        f"weights: percent of the fund's net assets as printed in the Vermögensaufstellung, not rescaled; "
        f"lines add up to {meta['total_pct']:.2f}%",
    ]
    return IssuerHoldings(as_of=meta["as_of"], holdings=kag_reports.as_holdings(lines), url=ba.HOST, notes=notes)


ADAPTER = register(
    IssuerAdapter(
        name=NAME,
        title="Bundesanzeiger: German public funds' annual and semi-annual reports",
        homepage=ba.HOST,
        credit=("Holdings from the fund's annual or semi-annual report as published in the Bundesanzeiger "
                "(KAGB § 107); positions as of the report date"),
        licence=("Reports are the management companies' documents, read from the Bundesanzeiger's free search "
                 "(robots.txt allows it; no reader terms stated). Read locally only, never republished."),
        regions=("DE",),
        products=products,
        holdings=holdings,
        frequency="semi-annual, published within two to four months of the period end",
        redistribution=LOCAL_ONLY,
    )
)
