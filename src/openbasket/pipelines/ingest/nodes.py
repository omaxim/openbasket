"""Download raw source files."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ...datasets import connection
from ...sources import ishares, nport, sec, sec_nport_history, spdr


def download_nport(quarters: list[str], raw_dir: str, overwrite: bool = False, history: dict | None = None) -> pd.DataFrame:
    """Download the N-PORT ZIP for each quarter (``2026q3`` style) unless cached.

    ``history`` (``params:nport_history``) adds older quarters, see
    :func:`openbasket.sources.sec_nport_history.resolve_quarters`.
    """
    rows = []
    for q in sec_nport_history.resolve_quarters(quarters, history):
        q = q.lower()
        dest = Path(raw_dir) / f"{q}_nport.zip"
        sec.download(sec.NPORT_URL.format(quarter=q), dest, overwrite=overwrite)
        rows.append({"quarter": q, "zip_path": str(dest), "bytes": dest.stat().st_size})
    return pd.DataFrame(rows)


def nport_to_parquet(raw_manifest: pd.DataFrame, parquet_dir: str) -> pd.DataFrame:
    """Convert the tables the pipeline needs to Parquet, one folder per quarter."""
    rows = []
    for r in raw_manifest.itertuples(index=False):
        rows += nport.zip_to_parquet(connection(), Path(r.zip_path), r.quarter, Path(parquet_dir))
    return pd.DataFrame(rows)


def download_fund_tickers() -> pd.DataFrame:
    """SEC's fund and ETF share-class tickers with their series IDs."""
    return sec.fund_tickers()


def download_issuer_products(ishares_sites: list[str], spdr_regions: list[str]) -> pd.DataFrame:
    """Every iShares and SPDR fund on the given regional sites, keyed by share-class ISIN."""
    frames = [ishares.products(s).assign(issuer="ishares") for s in ishares_sites]
    frames += [spdr.products(r) for r in spdr_regions]
    return pd.concat(frames, ignore_index=True).drop_duplicates("isin")
