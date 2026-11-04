"""Build the ISIN-keyed security master from every N-PORT position.

ISINs come from what funds report. Where a holding has a CUSIP but no ISIN,
the ISIN another filing reported for that CUSIP is used, and failing that it
is derived from the CUSIP (US and Canadian numbers only). ``isin_source``
records which.

Placeholder CUSIPs (``000000000``, nine equal characters, ``N/A``) are not
CUSIPs: filers use them for cash sweeps and private funds alike, so they are
never mapped to an ISIN and never stand for a security.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from ...datasets import connection
from ...identifiers import PLACEHOLDER_CUSIP_SQL, cusip_to_isin, is_placeholder_cusip, is_valid_isin
from ...sources import nport


def nport_positions(nport_parquet_manifest: pd.DataFrame) -> duckdb.DuckDBPyRelation:
    """Every N-PORT holding across the loaded quarters, with its reported ISIN and ticker."""
    con = connection()
    holding = nport.table(con, nport_parquet_manifest, "FUND_REPORTED_HOLDING")
    identifiers = nport.table(con, nport_parquet_manifest, "IDENTIFIERS")
    desc_ref = (
        nport.table(con, nport_parquet_manifest, "DESC_REF_OTHER")
        if (nport_parquet_manifest["table"] == "DESC_REF_OTHER").any()
        else None
    )
    return nport.holdings_with_identifiers(holding, identifiers, desc_ref)


def cusip_isin_map(nport_positions: duckdb.DuckDBPyRelation) -> duckdb.DuckDBPyRelation:
    """One ISIN per CUSIP: the one most often reported, else derived."""
    con = connection()
    positions = nport_positions  # noqa: F841 - read by DuckDB below
    placeholder = PLACEHOLDER_CUSIP_SQL
    reported = con.sql(  # noqa: F841 - read by DuckDB below
        f"""
        select cusip, reported_isin as isin, 'reported_for_cusip' as isin_source
        from (select cusip, reported_isin, count(*) n from positions
              where cusip is not null and reported_isin is not null and not {placeholder} group by all)
        qualify row_number() over (partition by cusip order by n desc, reported_isin) = 1
        """
    )
    missing = con.sql(
        f"""
        select cusip, mode(country) as country from positions
        where cusip is not null and reported_isin is null and not {placeholder}
          and cusip not in (select cusip from reported)
        group by cusip
        """
    ).df()
    missing["isin"] = [
        cusip_to_isin(c, "CA" if k == "CA" else "US") if not is_placeholder_cusip(c) else None
        for c, k in zip(missing["cusip"], missing["country"])
    ]
    derived = missing.dropna(subset=["isin"])[["cusip", "isin"]].assign(isin_source="derived_cusip")
    derived = derived[derived["isin"].map(is_valid_isin).astype(bool)]
    derived = pd.DataFrame(
        {c: pd.Series(derived[c].tolist(), dtype=object) for c in ("cusip", "isin", "isin_source")}
    )
    # Registered by name: the returned relation is evaluated later, when Kedro saves it.
    con.register("_derived_cusip_isin", derived)
    return con.sql("select * from reported union all select cusip, isin, isin_source from _derived_cusip_isin")


def security_master(
    nport_positions: duckdb.DuckDBPyRelation, cusip_isin_map: duckdb.DuckDBPyRelation
) -> duckdb.DuckDBPyRelation:
    """One row per ISIN with the names, LEI, CUSIP and asset class funds report for it."""
    con = connection()
    positions, cmap = nport_positions, cusip_isin_map  # noqa: F841 - read by DuckDB below
    placeholder = PLACEHOLDER_CUSIP_SQL
    return con.sql(
        f"""
        with p as (
            select p.*,
                   coalesce(p.reported_isin, m.isin) as isin,
                   case when p.reported_isin is not null then 'reported' else m.isin_source end as isin_source
            from positions p
            left join cmap m on p.reported_isin is null and p.cusip = m.cusip
        )
        select
            isin,
            mode(issuer_name) as issuer_name,
            mode(issuer_title) as title,
            mode(issuer_lei) as issuer_lei,
            mode(case when not {placeholder} then cusip end) as cusip,
            mode(reported_ticker) as ticker,
            mode(asset_cat) as asset_cat,
            mode(issuer_type) as issuer_type,
            mode(country) as country,
            min(isin_source) as isin_source,
            count(*) as n_positions
        from p
        where isin is not null
        group by isin
        """
    )


def security_terms(
    nport_positions: duckdb.DuckDBPyRelation, cusip_isin_map: duckdb.DuckDBPyRelation, nport_parquet_manifest: pd.DataFrame
) -> duckdb.DuckDBPyRelation:
    """Currency, and for bonds coupon and maturity, per ISIN, as N-PORT filings report them.

    Statutory reports name bonds by issuer, coupon and maturity and give no
    ISIN; :mod:`openbasket.matching` matches them against these terms. One row
    per ISIN: the values most filings give. Without the ``DEBT_SECURITY``
    table (a quarter converted before it was read) coupon and maturity are
    null.
    """
    con = connection()
    positions, cmap = nport_positions, cusip_isin_map  # noqa: F841 - read by DuckDB below
    holding = nport.table(con, nport_parquet_manifest, "FUND_REPORTED_HOLDING")  # noqa: F841 - read by DuckDB below
    if (nport_parquet_manifest["table"] == "DEBT_SECURITY").any():
        debt = nport.table(con, nport_parquet_manifest, "DEBT_SECURITY")  # noqa: F841 - read by DuckDB below
    else:
        debt = con.sql(  # noqa: F841 - read by DuckDB below
            "select null::varchar as HOLDING_ID, null::varchar as MATURITY_DATE, null::varchar as COUPON_TYPE, "
            "null::varchar as ANNUALIZED_RATE where false"
        )
    placeholder = PLACEHOLDER_CUSIP_SQL
    return con.sql(
        f"""
        with pos as (
            select holding_id, reported_isin, case when not {placeholder} then cusip end as cusip from positions
        ),
        p as (
            select pos.holding_id, coalesce(pos.reported_isin, m.isin) as isin
            from pos
            left join cmap m on pos.reported_isin is null and pos.cusip = m.cusip
        ),
        h as (select HOLDING_ID, nullif(upper(trim(CURRENCY_CODE)), '') as currency from holding),
        d as (
            select HOLDING_ID,
                   try_strptime(MATURITY_DATE, '%d-%b-%Y')::date as maturity,
                   try_cast(ANNUALIZED_RATE as double) as coupon,
                   nullif(COUPON_TYPE, '') as coupon_type
            from debt
        )
        select p.isin,
               mode(h.currency) as currency,
               mode(d.coupon) as coupon,
               mode(d.maturity) as maturity,
               mode(d.coupon_type) as coupon_type,
               count(*) as n_positions
        from p
        left join h on h.HOLDING_ID = p.holding_id
        left join d on d.HOLDING_ID = p.holding_id
        where p.isin is not null
        group by p.isin
        """
    )


def ticker_isin_map(security_master: duckdb.DuckDBPyRelation) -> pd.DataFrame:
    """Ticker to ISIN for equities whose filings report a ticker."""
    con = connection()
    sm = security_master  # noqa: F841 - read by DuckDB below
    return con.sql(
        """
        select ticker, isin from (
            select ticker, isin, n_positions from sm
            where ticker is not null and asset_cat in ('EC', 'EP')
        )
        qualify row_number() over (partition by ticker order by n_positions desc) = 1
        """
    ).df()


def sedol_isin_map(nport_parquet_manifest: pd.DataFrame) -> pd.DataFrame:
    """SEDOL to ISIN, from holdings whose identifiers report both.

    Issuer files for US funds identify foreign stocks by SEDOL, not ISIN.
    """
    con = connection()
    identifiers = nport.table(con, nport_parquet_manifest, "IDENTIFIERS")  # noqa: F841 - read by DuckDB below
    return con.sql(
        """
        with s as (
            select HOLDING_ID, upper(trim(OTHER_IDENTIFIER)) as sedol from identifiers
            where upper(OTHER_IDENTIFIER_DESC) like '%SEDOL%' and length(trim(OTHER_IDENTIFIER)) = 7
        ),
        i as (
            select HOLDING_ID, max(upper(trim(IDENTIFIER_ISIN))) as isin from identifiers
            where IDENTIFIER_ISIN is not null and IDENTIFIER_ISIN not in ('', 'N/A') group by 1
        )
        select sedol, isin from (select sedol, isin, count(*) n from s join i using (HOLDING_ID) group by all)
        qualify row_number() over (partition by sedol order by n desc, isin) = 1
        """
    ).df()
