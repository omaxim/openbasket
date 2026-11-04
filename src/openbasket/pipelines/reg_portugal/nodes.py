"""Nodes for the ``reg_portugal`` pipeline."""

from __future__ import annotations

import pandas as pd

from ...sources.cmvm_portugal import CmvmStore


def refresh_cmvm(root: str) -> pd.DataFrame:
    """Read CMVM's newest quarter of fund portfolios (if not yet stored) to Parquet."""
    return pd.DataFrame([CmvmStore(root).refresh()])


def build_products(manifest: pd.DataFrame, root: str) -> pd.DataFrame:
    """Every fund, compartment and share-class ISIN with a portfolio in the newest quarter."""
    return CmvmStore(root).products(str(manifest["as_of"].iloc[0]))
