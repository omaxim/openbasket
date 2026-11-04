"""Nodes for the ``etfdb`` pipeline.

albertored/etfdb has no licence, so everything here is ``local_only``: the
tables stay in ``data/`` (never committed) and the public build does not run
this pipeline.
"""

from __future__ import annotations

import logging
from collections import Counter

import pandas as pd

from ...sources import financedatabase as findb
from ...sources.issuers import etfdb

logger = logging.getLogger(__name__)


def build_etfdb_meta(params: dict) -> pd.DataFrame:
    """The ``etfdb_meta`` table from today's ``etfs.json`` (downloaded once a day)."""
    recs = etfdb.records(params.get("root"))
    df = etfdb.meta(recs)
    logger.info(
        "albertored/etfdb: %d ETFs, %d swap-based, %d leveraged or short, %d with top holdings",
        len(df), int(df["uses_swaps"].sum()), int(df["is_leveraged"].sum()), int((df["n_top_holdings"] > 0).sum()),
    )
    return df


def build_stand_ins(meta: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Physical ETFs on the same index as each swap-based ETF, to review as proxies."""
    return etfdb.stand_in_candidates(meta, per_fund=params.get("stand_ins_per_fund", 3))


def report_etfdb(meta: pd.DataFrame, params: dict) -> tuple[dict, pd.DataFrame]:
    """Counts for ``etfdb_summary`` and the overlap with openbasket's tables."""
    top = meta[meta["n_top_holdings"] > 0]
    strategies = Counter(s.strip() for v in meta["strategies"].dropna() for s in v.split(";"))
    summary = {
        "source": etfdb.SOURCE_ID,
        "redistribution": etfdb.REDISTRIBUTION,
        "etfs": int(len(meta)),
        "with_top_holdings": int(len(top)),
        "top_holding_lines": int(top["n_top_holdings"].sum()),
        "top_holdings_pct_median": float(top["top_holdings_pct"].median()) if len(top) else None,
        "replication": {k: int(v) for k, v in meta["replication"].value_counts().items()},
        "uses_swaps": int(meta["uses_swaps"].sum()),
        "leveraged_or_short": int(meta["is_leveraged"].sum()),
        "strategies": dict(strategies),
        "with_index": int(meta["index"].notna().sum()),
        "domicile": {k: int(v) for k, v in meta["domicile_code"].fillna("?").value_counts().head(10).items()},
        "fund_provider": {k: int(v) for k, v in meta["fund_provider"].value_counts().head(15).items()},
        "update_date_max": meta["update_date"].dropna().max() if meta["update_date"].notna().any() else None,
        "holdings_update_date_range": [top["holdings_update_date"].dropna().min(), top["holdings_update_date"].dropna().max()]
        if len(top) else None,
    }
    known = findb.load_known(params.get("compare") or {})
    out = findb.overlap(meta["isin"], known)
    with_top = findb.overlap(top["isin"], known).assign(subset="with_top_holdings")
    return summary, pd.concat([out.assign(subset="all"), with_top], ignore_index=True)
