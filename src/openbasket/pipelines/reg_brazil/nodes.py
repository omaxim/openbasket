"""Nodes for the ``reg_brazil`` pipeline."""

from __future__ import annotations

import pandas as pd

from ...sources.cvm_brazil import CdaStore


def refresh_cda(root: str, months: int, max_confidential_pct: float) -> pd.DataFrame:
    """Download the newest CDA months and B3's ISIN register; convert them to Parquet."""
    return CdaStore(root, months=months, max_confidential_pct=max_confidential_pct).refresh()


def build_products(manifest: pd.DataFrame, root: str) -> pd.DataFrame:
    """Every fund-quota ISIN whose fund filed a CDA in a stored month."""
    return CdaStore(root).products()
