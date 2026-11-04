"""Nodes for the ``sweden_fi`` pipeline."""

from __future__ import annotations

import pandas as pd

from ...sources.sweden_fi import FiStore


def refresh_fi(root: str) -> pd.DataFrame:
    """Download FI's newest quarterly fund holdings ZIP (if changed) and parse it to Parquet."""
    return pd.DataFrame([FiStore(root).refresh()])


def build_products(manifest: pd.DataFrame, root: str) -> pd.DataFrame:
    """Every active Swedish UCITS fund with an ISIN in the newest quarter."""
    return FiStore(root).products(str(manifest["quarter"].iloc[0]))
