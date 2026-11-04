"""Swedish UCITS funds from Finansinspektionen's quarterly fund holdings.

A regulatory source, not an issuer. See :mod:`openbasket.sources.sweden_fi`.
Data is kept in ``data/01_raw/sweden_fi`` (or ``$OPENBASKET_SWEDEN_FI_DIR``);
the first call to ``products`` downloads the newest quarter (about 6.5 MB).
"""

from __future__ import annotations

import os

import pandas as pd

from .. import sweden_fi as fi
from . import PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

DEFAULT_ROOT = "data/01_raw/sweden_fi"
EXTRA_COLUMNS = ["manager", "manager_lei", "bank_group", "net_assets_sek", "share_classes", "as_of"]

_stores: dict[str, fi.FiStore] = {}


def store(root: str | None = None) -> fi.FiStore:
    root = root or os.environ.get("OPENBASKET_SWEDEN_FI_DIR") or DEFAULT_ROOT
    if root not in _stores:
        _stores[root] = fi.FiStore(root)
    return _stores[root]


def products() -> pd.DataFrame:
    s = store()
    if not s.is_built():
        s.refresh()
    df = s.products()
    return df[PRODUCT_COLUMNS + EXTRA_COLUMNS] if len(df) else pd.DataFrame(columns=PRODUCT_COLUMNS)


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    s = store()
    if not s.is_built():
        s.refresh()
    as_of, frame, notes, url = s.holdings(str(product["portfolio_id"]))
    return IssuerHoldings(as_of=as_of, holdings=frame, url=url, notes=notes)


ADAPTER = register(
    IssuerAdapter(
        name="sweden_fi",
        title="Finansinspektionen Sweden: quarterly holdings of Swedish UCITS funds",
        homepage=fi.PAGE_URL,
        credit=fi.CREDIT,
        licence=fi.LICENCE,
        redistribution="public",  # Finansinspektionen publishes fondinnehav as open data
        regions=("SE",),
        products=products,
        holdings=holdings,
        frequency="quarterly, published about two to four months after quarter end",
    )
)
