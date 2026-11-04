"""Nodes for the ``financedatabase`` pipeline."""

from __future__ import annotations

import logging

import pandas as pd

from ...sources import financedatabase as findb

logger = logging.getLogger(__name__)


def fetch_financedatabase(params: dict) -> pd.DataFrame:
    """Download each kind in ``params.kinds`` (reused when fetched today); one manifest row per kind."""
    rows = [
        findb.fetch(k, params["root"], fmt=params.get("fmt", "parquet"), overwrite=params.get("overwrite", False))
        for k in params.get("kinds") or list(findb.KINDS)
    ]
    return pd.DataFrame(rows)


def build_findb_funds(manifest: pd.DataFrame, params: dict) -> tuple[pd.DataFrame, dict]:
    """The ``findb_funds`` table (one row per ISIN, no summary text) and its counts."""
    frames = {r.kind: findb.read(r.path) for r in manifest.itertuples(index=False)}
    listing, stats = findb.listings(frames, isin_from_text=params.get("isin_from_text", False))
    as_of = dict(zip(manifest["kind"], manifest["as_of"]))
    table = findb.build_table(listing, as_of)
    summary = findb.summarise(table, stats)
    logger.info(
        "FinanceDatabase: %d distinct ISINs (%s); %d rows dropped for an invalid ISIN",
        summary["distinct_isins"], summary["isins_by_kind"], summary["invalid_isin_rows_dropped"],
    )
    return table, summary


def compare_findb(table: pd.DataFrame, params: dict) -> pd.DataFrame:
    """How many ``findb_funds`` ISINs openbasket's own tables already know (``params.compare``)."""
    out = findb.overlap(table["isin"], findb.load_known(params.get("compare") or {}))
    for r in out.itertuples(index=False):
        logger.info("FinanceDatabase ISINs known to %s: %d of %d", r.table, r.known, r.isins)
    return out
