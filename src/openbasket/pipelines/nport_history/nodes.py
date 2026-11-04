"""Measure what the older N-PORT quarters add."""

from __future__ import annotations

import logging

import pandas as pd

from ...sources import sec_nport_history

logger = logging.getLogger(__name__)


def nport_quarter_coverage(nport_parquet_manifest: pd.DataFrame, sec_fund_tickers: pd.DataFrame) -> pd.DataFrame:
    """Series each loaded quarter adds (see :func:`openbasket.sources.sec_nport_history.quarter_coverage`)."""
    dupes = sec_nport_history.duplicate_accessions(nport_parquet_manifest)
    if dupes:
        logger.warning("%d N-PORT filings appear in more than one loaded quarter", dupes)
    ids = sec_nport_history.duplicate_holding_ids(nport_parquet_manifest)
    if ids:
        logger.warning("%d N-PORT HOLDING_IDs repeat across loaded quarters; identifiers may mix", ids)
    cov = sec_nport_history.quarter_coverage(nport_parquet_manifest, sec_fund_tickers)
    for r in cov.itertuples(index=False):
        logger.info(
            "N-PORT %s: +%d series (%d still with a ticker, %d final filings), %d in total",
            r.quarter, r.series_added, r.added_with_ticker or 0, r.added_final_filing, r.series_total,
        )
    return cov
