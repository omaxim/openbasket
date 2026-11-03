"""Nodes for the ``reg_spain`` pipeline."""

from __future__ import annotations

import pandas as pd

from ...sources.cnmv_spain import CnmvStore


def refresh_cnmv(root: str) -> pd.DataFrame:
    """Download the latest quarter-end ZIP from CNMV and parse it to Parquet."""
    return pd.DataFrame([CnmvStore(root).refresh()])


def build_products(manifest: pd.DataFrame, root: str) -> pd.DataFrame:
    """Every share-class ISIN of a fund or SICAV in the latest quarterly files."""
    return CnmvStore(root).products()
