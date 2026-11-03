"""N-PORT SQL against a tiny hand-made quarter."""

import zipfile

import pandas as pd
import pytest

from fund_lookthrough.datasets import DuckDBParquetDataset, connection
from fund_lookthrough.pipelines.consolidate.nodes import latest_filings, nport_holdings
from fund_lookthrough.pipelines.security_master.nodes import cusip_isin_map, nport_positions, security_master
from fund_lookthrough.sources.nport import zip_to_parquet

TABLES = {
    "SUBMISSION": [
        ["ACCESSION_NUMBER", "FILING_DATE", "FILE_NUM", "SUB_TYPE", "REPORT_ENDING_PERIOD", "REPORT_DATE", "IS_LAST_FILING"],
        ["A-OLD", "28-MAY-2026", "", "NPORT-P", "31-DEC-2026", "31-MAR-2026", "N"],
        ["A-NEW", "28-AUG-2026", "", "NPORT-P", "31-DEC-2026", "30-JUN-2026", "N"],
        ["A-AMEND", "15-SEP-2026", "", "NPORT-P/A", "31-DEC-2026", "30-JUN-2026", "N"],
        ["A-LATEAMEND", "20-SEP-2026", "", "NPORT-P/A", "31-DEC-2025", "30-SEP-2025", "N"],
    ],
    "FUND_REPORTED_INFO": [
        ["ACCESSION_NUMBER", "SERIES_NAME", "SERIES_ID", "SERIES_LEI", "NET_ASSETS"],
        ["A-OLD", "Fund One", "S1", "LEI1", "100"],
        ["A-NEW", "Fund One", "S1", "LEI1", "110"],
        ["A-AMEND", "Fund One", "S1", "LEI1", "111"],
        ["A-LATEAMEND", "Fund One", "S1", "LEI1", "90"],
    ],
    "FUND_REPORTED_HOLDING": [
        ["ACCESSION_NUMBER", "HOLDING_ID", "ISSUER_NAME", "ISSUER_LEI", "ISSUER_TITLE", "ISSUER_CUSIP", "BALANCE",
         "UNIT", "OTHER_UNIT_DESC", "CURRENCY_CODE", "CURRENCY_VALUE", "EXCHANGE_RATE", "PERCENTAGE",
         "PAYOFF_PROFILE", "ASSET_CAT", "OTHER_ASSET", "ISSUER_TYPE", "OTHER_ISSUER", "INVESTMENT_COUNTRY",
         "IS_RESTRICTED_SECURITY", "FAIR_VALUE_LEVEL", "DERIVATIVE_CAT"],
        ["A-OLD", "1", "Apple", "L-AAPL", "Apple", "037833100", "", "NS", "", "USD", "1", "", "50", "Long", "EC", "", "CORP", "", "US", "N", "1", ""],
        ["A-AMEND", "2", "Apple", "L-AAPL", "Apple", "037833100", "", "NS", "", "USD", "1", "", "60", "Long", "EC", "", "CORP", "", "US", "N", "1", ""],
        ["A-AMEND", "3", "Microsoft", "L-MSFT", "Microsoft", "594918104", "", "NS", "", "USD", "1", "", "39.5", "Long", "EC", "", "CORP", "", "US", "N", "1", ""],
        ["A-AMEND", "4", "Cash", "N/A", "Cash", "N/A", "", "NS", "", "USD", "1", "", "0.7", "Long", "STIV", "", "RF", "", "US", "N", "1", ""],
    ],
    "IDENTIFIERS": [
        ["HOLDING_ID", "IDENTIFIERS_ID", "IDENTIFIER_ISIN", "IDENTIFIER_TICKER", "OTHER_IDENTIFIER", "OTHER_IDENTIFIER_DESC"],
        ["1", "11", "US0378331005", "", "", ""],
        ["2", "21", "", "", "X1", "Inhouse Asset ID"],
        ["2", "22", "", "AAPL", "", ""],
    ],
}


@pytest.fixture(scope="module")
def manifest(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("nport")
    zpath = tmp / "2026q3_nport.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for name, rows in TABLES.items():
            zf.writestr(f"{name}.tsv", "\n".join("\t".join(r) for r in rows) + "\n")
    return pd.DataFrame(zip_to_parquet(connection(), zpath, "2026q3", tmp / "pq"))


def test_latest_filing_takes_latest_period_then_amendment(manifest):
    lf = latest_filings(manifest)
    assert lf["accession_number"].tolist() == ["A-AMEND"]
    assert lf["net_assets"].iloc[0] == 111


def test_holdings_get_reported_and_derived_isins(manifest, tmp_path):
    pos = nport_positions(manifest)
    cmap = cusip_isin_map(pos)
    lf = latest_filings(manifest)
    ds = DuckDBParquetDataset(str(tmp_path / "h.parquet"), order_by="series_id")
    ds.save(nport_holdings(pos, lf, cmap))
    h = ds.load().df().set_index("holding_id")
    assert set(h.index) == {"2", "3", "4"}
    assert h.at["2", "isin"] == "US0378331005"  # reported for the same CUSIP in another filing
    assert h.at["2", "isin_source"] == "reported_for_cusip"
    assert h.at["3", "isin"] == "US5949181045"  # derived from the CUSIP
    assert h.at["3", "isin_source"] == "derived_cusip"
    assert pd.isna(h.at["4", "isin"])
    assert h["weight_pct"].sum() == pytest.approx(100.2)

    sm = security_master(pos, cmap).df().set_index("isin")
    assert sm.at["US0378331005", "isin_source"] == "reported"
    assert sm.at["US0378331005", "ticker"] == "AAPL"
