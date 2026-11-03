"""Brazilian funds from CVM's monthly portfolio filings (CDA).

A regulatory source, not an issuer: every Brazilian fund class files its full
portfolio with CVM each month and CVM publishes them as open data. See
:mod:`openbasket.sources.cvm_brazil` for the files and how they are read.

The adapter keeps its data in ``data/01_raw/reg_brazil`` (or
``$OPENBASKET_REG_BRAZIL_DIR``). The ``reg_brazil`` pipeline refreshes it;
if it is empty, the first call to ``products`` builds it (about 80 MB of
downloads for three months).
"""

from __future__ import annotations

import os

import pandas as pd

from .. import cvm_brazil as cda
from . import PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

DEFAULT_ROOT = "data/01_raw/reg_brazil"

_stores: dict[str, cda.CdaStore] = {}


def store(root: str | None = None) -> cda.CdaStore:
    root = root or os.environ.get("OPENBASKET_REG_BRAZIL_DIR") or DEFAULT_ROOT
    if root not in _stores:
        _stores[root] = cda.CdaStore(root)
    return _stores[root]


def products() -> pd.DataFrame:
    s = store()
    if not s.is_built():
        s.refresh()
    df = s.products()
    return df[PRODUCT_COLUMNS + ["share_class"]] if len(df) else pd.DataFrame(columns=PRODUCT_COLUMNS)


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    s = store()
    cnpj = str(product["portfolio_id"])
    got = s.holdings(cnpj)
    if got is None:
        raise LookupError(f"No CDA filing stored for CNPJ {cnpj}")
    as_of, month, frame, notes = got
    return IssuerHoldings(as_of=as_of, holdings=frame, url=cda.CDA_URL.format(month=month), notes=notes)


ADAPTER = register(
    IssuerAdapter(
        name="cvm_brazil",
        title="CVM Brazil: fund portfolio filings (CDA)",
        homepage=cda.CDA_DATASET_PAGE,
        credit=cda.CREDIT,
        licence=(
            "CVM open data under the Open Database License (ODbL 1.0): credit CVM and share "
            "derived databases alike. B3's ISIN register is a free public download from the "
            "numbering agency; its terms were not found on the download page."
        ),
        regions=("BR",),
        products=products,
        holdings=holdings,
        frequency="monthly (published weekly; confidential positions released up to 90 days later)",
        redistribution="public",
    )
)
