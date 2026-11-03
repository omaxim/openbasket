"""Pick each fund's latest filing and attach ISINs to its holdings."""

from __future__ import annotations

import duckdb
import pandas as pd

from ...datasets import connection
from ...sources import nport


def latest_filings(nport_parquet_manifest: pd.DataFrame) -> pd.DataFrame:
    """One N-PORT filing per fund series (see :func:`fund_lookthrough.sources.nport.latest_filings`)."""
    con = connection()
    submission = nport.table(con, nport_parquet_manifest, "SUBMISSION")
    info = nport.table(con, nport_parquet_manifest, "FUND_REPORTED_INFO")
    return nport.latest_filings(submission, info).df()


def fund_master(nport_latest_filings: pd.DataFrame, sec_fund_tickers: pd.DataFrame) -> pd.DataFrame:
    """Fund series with their LEI, latest filing and every share-class ticker.

    Series that file N-PORT but have no ticker keep one row with an empty
    ticker; tickers whose series has no filing in the loaded quarters are kept
    too, so TickerInside can still be tried for them.
    """
    f = nport_latest_filings[["series_id", "series_name", "series_lei", "report_date", "net_assets"]]
    t = sec_fund_tickers[["series_id", "class_id", "ticker", "cik"]]
    out = f.merge(t, on="series_id", how="outer")
    out["has_nport"] = out["report_date"].notna()
    return out.sort_values(["series_id", "ticker"]).reset_index(drop=True)


def nport_holdings(
    nport_positions: duckdb.DuckDBPyRelation,
    nport_latest_filings: pd.DataFrame,
    cusip_isin_map: duckdb.DuckDBPyRelation,
) -> duckdb.DuckDBPyRelation:
    """Holdings of each series' latest filing, with the best available ISIN."""
    con = connection()
    con.register("_latest_filings", nport_latest_filings)
    positions, cmap = nport_positions, cusip_isin_map  # noqa: F841 - read by DuckDB below
    return con.sql(
        """
        select
            l.series_id, l.series_name, l.report_date, l.filing_date, l.net_assets,
            p.*,
            coalesce(p.reported_isin, m.isin) as isin,
            case when p.reported_isin is not null then 'reported' else m.isin_source end as isin_source
        from positions p
        join _latest_filings l using (accession_number)
        left join cmap m on p.reported_isin is null and p.cusip = m.cusip
        """
    )
