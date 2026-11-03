"""Spanish funds and SICAVs from CNMV's quarterly portfolio files.

A regulatory source, not an issuer. See :mod:`openbasket.sources.cnmv_spain`.
Data is kept in ``data/01_raw/reg_spain`` (or ``$OPENBASKET_REG_SPAIN_DIR``);
the first call to ``products`` downloads the latest quarter (about 9 MB).
"""

from __future__ import annotations

import os

import pandas as pd

from .. import cnmv_spain as cnmv
from . import PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

DEFAULT_ROOT = "data/01_raw/reg_spain"

_stores: dict[str, cnmv.CnmvStore] = {}


def store(root: str | None = None) -> cnmv.CnmvStore:
    root = root or os.environ.get("OPENBASKET_REG_SPAIN_DIR") or DEFAULT_ROOT
    if root not in _stores:
        _stores[root] = cnmv.CnmvStore(root)
    return _stores[root]


def products() -> pd.DataFrame:
    s = store()
    if not s.is_built():
        s.refresh()
    df = s.products()
    return df[PRODUCT_COLUMNS + ["manager"]] if len(df) else pd.DataFrame(columns=PRODUCT_COLUMNS)


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    as_of, frame, notes = store().holdings(str(product["portfolio_id"]))
    return IssuerHoldings(as_of=as_of, holdings=frame, url=cnmv.PAGE_URL, notes=notes)


ADAPTER = register(
    IssuerAdapter(
        name="cnmv_spain",
        title="CNMV Spain: quarterly fund and SICAV portfolios",
        homepage=cnmv.PAGE_URL,
        credit=cnmv.CREDIT,
        licence=(
            "Public information from the CNMV website. CNMV's legal notice (NotaLegal) authorises reuse "
            "and calculations on it if the information is reproduced faithfully, unaltered, and buyers of any "
            "paid product are told it is free on the CNMV website."
        ),
        regions=("ES",),
        products=products,
        holdings=holdings,
        frequency="quarterly, published about four months after quarter end",
    )
)
