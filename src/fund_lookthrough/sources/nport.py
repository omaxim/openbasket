"""SEC Form N-PORT quarterly data sets.

Each quarterly ZIP holds tab-delimited tables. The ones used here:

``SUBMISSION``
    One row per filing: ``ACCESSION_NUMBER``, ``SUB_TYPE`` (``NPORT-P`` or the
    amendment ``NPORT-P/A``), ``REPORT_DATE`` and ``FILING_DATE``.
``FUND_REPORTED_INFO``
    The fund series a filing covers: ``SERIES_ID``, ``SERIES_NAME``,
    ``SERIES_LEI`` and ``NET_ASSETS``.
``FUND_REPORTED_HOLDING``
    One row per position. ``PERCENTAGE`` is in percent of net assets, and a
    fund's lines can add up to more or less than 100.
``IDENTIFIERS``
    Zero or more identifiers per ``HOLDING_ID``, among them ``IDENTIFIER_ISIN``
    and ``IDENTIFIER_TICKER``.

A quarter's file holds filings for several report periods, including late
amendments to old ones, so :func:`latest_filings` keeps one filing per series.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import duckdb
import pandas as pd

from ..datasets import connection

logger = logging.getLogger(__name__)

TABLES = ("SUBMISSION", "FUND_REPORTED_INFO", "FUND_REPORTED_HOLDING", "IDENTIFIERS")

#: N-PORT asset category codes, grouped into plain asset classes.
ASSET_CLASS = {
    "EC": "equity",
    "EP": "preferred equity",
    "DBT": "debt",
    "LON": "loan",
    "ABS-MBS": "asset-backed (mortgage)",
    "ABS-APCP": "asset-backed (commercial paper)",
    "ABS-CBDO": "asset-backed (CDO/CBO)",
    "ABS-O": "asset-backed (other)",
    "STIV": "short-term investment vehicle",
    "RA": "repurchase agreement",
    "SN": "structured note",
    "COMM": "commodity",
    "RE": "real estate",
    "DE": "derivative (equity)",
    "DIR": "derivative (interest rate)",
    "DCR": "derivative (credit)",
    "DCO": "derivative (commodity)",
    "DFE": "derivative (foreign exchange)",
    "DO": "derivative (other)",
    "OTHER": "other",
}

#: Issuer types that mean the holding is itself a fund.
FUND_ISSUER_TYPES = ("RF", "PF")


def _read_tsv(path: str) -> str:
    # N-PORT files are not quoted; a stray double quote inside a name must not
    # swallow the rest of the file.
    return f"read_csv('{path}', delim='\t', header=true, all_varchar=true, quote='', escape='')"


def zip_to_parquet(
    con: duckdb.DuckDBPyConnection, zip_path: Path, quarter: str, out_dir: Path, tables=TABLES
) -> list[dict]:
    """Extract ``tables`` from a quarterly ZIP and write each as Parquet.

    Every row gets a ``QUARTER`` column. Returns one manifest row per table.
    """
    out = []
    qdir = Path(out_dir) / quarter
    qdir.mkdir(parents=True, exist_ok=True)
    extract_dir = Path(zip_path).with_suffix("")
    with zipfile.ZipFile(zip_path) as zf:
        names = {Path(n).stem.upper(): n for n in zf.namelist()}
        for table in tables:
            member = names.get(table)
            if member is None:
                raise KeyError(f"{table} missing from {zip_path}")
            tsv = extract_dir / Path(member).name
            if not tsv.exists():
                zf.extract(member, extract_dir)
                (extract_dir / member).replace(tsv)
            pq = qdir / f"{table}.parquet"
            con.execute(
                f"copy (select *, '{quarter}' as QUARTER from {_read_tsv(str(tsv))}) "
                f"to '{pq}' (format parquet, compression zstd)"
            )
            rows = con.execute(f"select count(*) from '{pq}'").fetchall()[0][0]
            logger.info("%s %s: %s rows", quarter, table, rows)
            out.append({"quarter": quarter, "table": table, "path": str(pq), "rows": rows})
    return out


def table(con: duckdb.DuckDBPyConnection, manifest: pd.DataFrame, name: str) -> duckdb.DuckDBPyRelation:
    """All quarters of one table as a single relation."""
    paths = manifest.loc[manifest["table"] == name, "path"].tolist()
    if not paths:
        raise KeyError(f"No {name} files in the N-PORT manifest")
    return con.read_parquet(paths, union_by_name=True)


def latest_filings(submission: duckdb.DuckDBPyRelation, info: duckdb.DuckDBPyRelation) -> duckdb.DuckDBPyRelation:
    """One filing per fund series: the latest report period, then the latest filing.

    An amendment filed later for the same period replaces the original.
    """
    return connection().sql(
        """
        with f as (
            select
                s.ACCESSION_NUMBER as accession_number,
                s.SUB_TYPE as sub_type,
                strptime(s.REPORT_DATE, '%d-%b-%Y')::date as report_date,
                strptime(s.FILING_DATE, '%d-%b-%Y')::date as filing_date,
                i.SERIES_ID as series_id,
                i.SERIES_NAME as series_name,
                nullif(i.SERIES_LEI, 'N/A') as series_lei,
                try_cast(i.NET_ASSETS as double) as net_assets,
                s.QUARTER as quarter
            from submission s
            join info i using (ACCESSION_NUMBER, QUARTER)
            where i.SERIES_ID is not null and i.SERIES_ID <> 'N/A'
        )
        select * from f
        qualify row_number() over (
            partition by series_id
            order by report_date desc, filing_date desc, accession_number desc
        ) = 1
        """
    )


def holdings_with_identifiers(
    holding: duckdb.DuckDBPyRelation, identifiers: duckdb.DuckDBPyRelation
) -> duckdb.DuckDBPyRelation:
    """Holdings with one reported ISIN and ticker per ``HOLDING_ID``."""
    return connection().sql(
        """
        with ids as (
            select
                HOLDING_ID,
                max(case when IDENTIFIER_ISIN is not null and IDENTIFIER_ISIN not in ('', 'N/A')
                         then upper(trim(IDENTIFIER_ISIN)) end) as reported_isin,
                max(case when IDENTIFIER_TICKER is not null and IDENTIFIER_TICKER not in ('', 'N/A')
                         then upper(trim(IDENTIFIER_TICKER)) end) as reported_ticker
            from identifiers
            group by HOLDING_ID
        )
        select
            h.ACCESSION_NUMBER as accession_number,
            h.HOLDING_ID as holding_id,
            h.ISSUER_NAME as issuer_name,
            nullif(h.ISSUER_LEI, 'N/A') as issuer_lei,
            h.ISSUER_TITLE as issuer_title,
            nullif(upper(h.ISSUER_CUSIP), 'N/A') as cusip,
            try_cast(h.PERCENTAGE as double) as weight_pct,
            try_cast(h.CURRENCY_VALUE as double) as value_usd,
            h.ASSET_CAT as asset_cat,
            h.ISSUER_TYPE as issuer_type,
            h.PAYOFF_PROFILE as payoff_profile,
            h.INVESTMENT_COUNTRY as country,
            ids.reported_isin,
            ids.reported_ticker
        from holding h
        left join ids using (HOLDING_ID)
        """
    )

