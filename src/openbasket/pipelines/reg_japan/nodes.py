"""Nodes for the ``reg_japan`` pipeline."""

from __future__ import annotations

import pandas as pd

from ...sources.edinet_reports import EdinetStore


def refresh_edinet(params: dict) -> pd.DataFrame:
    """Read new days of EDINET filings and parse every listed fund's latest annual report not stored yet.

    Needs ``EDINET_API_KEY``; without it the node fails, which a run treats as soft.
    """
    return pd.DataFrame([EdinetStore(params["root"]).refresh(days=int(params.get("days", 400)))])


def edinet_reports(manifest: pd.DataFrame, root: str) -> pd.DataFrame:
    """The latest annual report of each listed fund in the store."""
    return EdinetStore(root).reports()
