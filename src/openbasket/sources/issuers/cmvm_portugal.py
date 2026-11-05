"""Portuguese securities funds from CMVM's quarterly portfolio pages.

A regulatory source, not an issuer. See :mod:`openbasket.sources.cmvm_portugal`.
Data is kept in ``data/01_raw/reg_portugal`` (or ``$OPENBASKET_REG_PORTUGAL_DIR``);
the ``reg_portugal`` pipeline refreshes it, and if it is empty the first call
to ``products`` reads the newest quarter (about 450 requests, eight minutes).
Share lines are matched to ISINs with the security master
(``$OPENBASKET_SECURITY_MASTER``) and FinanceDatabase's company list
(``$OPENBASKET_FINDB_EQUITIES``) when those files exist.
"""

from __future__ import annotations

import os

import pandas as pd

from .. import cmvm_portugal as cmvm
from .. import name_isin
from . import PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

DEFAULT_ROOT = "data/01_raw/reg_portugal"
DEFAULT_FINDB = "data/01_raw/financedatabase/equities.parquet"
EXTRA_PRODUCT_COLUMNS = ["manager", "fund_type", "net_assets_eur", "as_of"]

_stores: dict[str, cmvm.CmvmStore] = {}
_indexes: dict[str, object] = {}


def store(root: str | None = None) -> cmvm.CmvmStore:
    root = root or os.environ.get("OPENBASKET_REG_PORTUGAL_DIR") or DEFAULT_ROOT
    if root not in _stores:
        _stores[root] = cmvm.CmvmStore(root)
    return _stores[root]


def _master():
    path = str(os.environ.get("OPENBASKET_SECURITY_MASTER") or name_isin.DEFAULT_MASTER)
    if ("master", path) not in _indexes:
        m = name_isin.load_master(path)
        _indexes[("master", path)] = name_isin.build_index(m) if m is not None else None
    return _indexes[("master", path)]


def _findb():
    path = str(os.environ.get("OPENBASKET_FINDB_EQUITIES") or DEFAULT_FINDB)
    if ("findb", path) not in _indexes:
        _indexes[("findb", path)] = cmvm.findb_index(path)
    return _indexes[("findb", path)]


def name_indexes() -> tuple[dict | None, dict | None]:
    """The exact-name indexes share lines are matched with: N-PORT security master, FinanceDatabase."""
    return _master(), _findb()


def products() -> pd.DataFrame:
    s = store()
    if not s.is_built():
        s.refresh()
    df = s.products()
    return df[PRODUCT_COLUMNS + EXTRA_PRODUCT_COLUMNS] if len(df) else pd.DataFrame(columns=PRODUCT_COLUMNS)


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    s = store()
    if not s.is_built():
        s.refresh()
    as_of, frame, notes = s.holdings(str(product["portfolio_id"]), _master(), _findb())
    return IssuerHoldings(as_of=as_of, holdings=frame, url=cmvm.PAGE_URL, notes=notes)


ADAPTER = register(
    IssuerAdapter(
        name="cmvm_portugal",
        title="CMVM Portugal: quarterly portfolios of Portuguese securities funds",
        homepage=cmvm.PAGE_URL,
        credit=cmvm.CREDIT,
        licence=cmvm.LICENCE,
        regions=("PT",),
        products=products,
        holdings=holdings,
        frequency="quarterly, published about one month after quarter end",
        redistribution=cmvm.REDISTRIBUTION,
    )
)
