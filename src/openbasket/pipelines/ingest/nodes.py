"""Download raw source files."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from ...datasets import connection
from ...identifiers import is_valid_isin
from ...sources import issuers, ishares, nport, sec, spdr

logger = logging.getLogger(__name__)


def download_nport(quarters: list[str], raw_dir: str, overwrite: bool = False) -> pd.DataFrame:
    """Download the N-PORT ZIP for each quarter (``2026q3`` style) unless cached."""
    rows = []
    for q in quarters:
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


def download_issuer_products(ishares_sites: list[str], spdr_regions: list[str], issuers_params: dict | None = None) -> pd.DataFrame:
    """Every fund on the issuer lists, keyed by share-class ISIN.

    iShares and SPDR come first, then every adapter registered in
    :mod:`openbasket.sources.issuers` (``issuers.enabled`` limits them,
    ``issuers.disabled`` drops some), each row with ``issuer`` set to the
    adapter's name. An adapter that fails, or returns rows that break the
    product contract, is logged and skipped or cleaned; it never stops the
    others. An ISIN two lists share stays with the first; stand-in adapters
    (a peer's holdings for a fund, :attr:`IssuerAdapter.stand_in`) come last,
    so they never take a fund a direct adapter lists. ``listed_on`` is the
    date the list was read.
    """
    from ...pipelines.lookthrough.nodes import issuer_adapters

    frames = [ishares.products(s).assign(issuer="ishares") for s in ishares_sites]
    frames += [spdr.products(r) for r in spdr_regions]
    for name, adapter in sorted(issuer_adapters(issuers_params).items(), key=lambda kv: (kv[1].stand_in, kv[0])):
        try:
            df = adapter.products()
        except Exception as exc:  # one issuer's site must not stop the others
            logger.warning("Issuer %s: product list failed: %s: %s", name, type(exc).__name__, exc)
            continue
        problems = issuers.validate_products(df)
        if problems:
            logger.warning("Issuer %s: %s", name, "; ".join(problems))
        if "isin" not in df or df.empty:
            continue
        df = df.assign(issuer=name)
        df = df[df["isin"].map(is_valid_isin)].drop_duplicates("isin")
        logger.info("Issuer %s: %d share classes", name, len(df))
        frames.append(df)
    out = pd.concat([f for f in frames if len(f)], ignore_index=True) if frames else pd.DataFrame(columns=issuers.PRODUCT_COLUMNS)
    out = out.drop_duplicates("isin").reset_index(drop=True)
    out["listed_on"] = date.today().isoformat()
    return parquet_safe(out)


def _kind(v: object) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    return "str" if isinstance(v, str) else "other"


def parquet_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Columns that mix value types (adapters add their own) become text, so the frame saves as Parquet."""
    df = df.copy()
    for col in df.columns:
        if df[col].dtype == object:
            kinds = {_kind(v) for v in df[col] if v is not None and not (isinstance(v, float) and pd.isna(v))}
            if kinds == {"number"}:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            elif len(kinds) > 1 or "other" in kinds:
                df[col] = [None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v) for v in df[col]]
    if "portfolio_id" in df:
        df["portfolio_id"] = df["portfolio_id"].map(lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    return df
