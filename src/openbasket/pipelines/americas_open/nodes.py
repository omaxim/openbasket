"""Nodes for the ``americas_open`` pipeline."""

from __future__ import annotations

import pandas as pd

from ...sources.americas_open import SfcColombiaStore


def refresh_sfc(root: str) -> pd.DataFrame:
    """Download the latest SFC Formato 351 cut-offs and fund values, converted to Parquet."""
    return SfcColombiaStore(root).refresh()


def build_products(manifest: pd.DataFrame, root: str) -> pd.DataFrame:
    """Every Colombian pension and severance fund in the latest Formato 351 files."""
    return SfcColombiaStore(root).products()
