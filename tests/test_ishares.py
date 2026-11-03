import pandas as pd
import pytest

from fund_lookthrough.lookthrough import Line, look_through
from fund_lookthrough.providers import DataProviders
from fund_lookthrough.sources import ishares


def _dp(value):
    return {"value": value, "formattedValue": value}


PAYLOAD = {
    "componentsByNameMap": {
        "holdings": {
            "containersByNameMap": {
                "all": {
                    "dataPointsByNameMap": {
                        "asOfDate": _dp("20261102"),
                        "dateList": _dp("[20261102, 20261030]"),
                        "isin": _dp("['US0378331005', 'IE00BK8MB266', None, '-']"),
                        "ticker": _dp("['AAPL', '-', 'USD', 'ESZ6']"),
                        "issueName": _dp("['APPLE', 'BLK ICS USD LEAF AGENCY DIST', 'USD CASH', 'S&P500 EMINI DEC 26']"),
                        "holdingPercent": _dp("[97.5, 2.0, 0.49, 0.0]"),
                        "assetClass": _dp("['Equity', 'Money Market', 'Cash', 'Futures']"),
                    }
                }
            }
        }
    }
}


def test_parse_holdings():
    as_of, df = ishares.parse_holdings(PAYLOAD)
    assert as_of == "2026-11-02"
    assert df["isin"].tolist()[:2] == ["US0378331005", "IE00BK8MB266"]
    assert df["isin"].isna().tolist()[2:] == [True, True]
    assert df["holdingPercent"].sum() == pytest.approx(99.99)


def test_parse_holdings_rejects_ragged_columns():
    bad = {"componentsByNameMap": {"holdings": {"containersByNameMap": {"all": {"dataPointsByNameMap": {
        "isin": _dp("['A', 'B']"), "holdingPercent": _dp("[1.0]")}}}}}}
    with pytest.raises(ValueError):
        ishares.parse_holdings(bad)


@pytest.fixture
def providers(monkeypatch, tmp_path):
    from fund_lookthrough.datasets import connection

    empty = connection().sql(
        "select null::varchar as isin, null::varchar as cusip, null::varchar as issuer_lei, null::varchar as series_id where false"
    )
    fund_master = pd.DataFrame(
        [{"series_id": "S1", "series_name": "ProShares Ultra Semiconductors", "series_lei": "L1", "ticker": "USD"}]
    )
    products = pd.DataFrame(
        [{"site": "uk", "portfolio_id": "253743", "isin": "IE00B5BMR087", "ticker": "CSPX",
          "fund_name": "iShares Core S&P 500 UCITS ETF"}]
    )
    monkeypatch.setattr(
        ishares, "holdings",
        lambda pid, site, cache_dir=None: ishares.IssuerHoldings(*ishares.parse_holdings(PAYLOAD), url="u"),
    )
    return DataProviders(
        empty, empty, fund_master, pd.DataFrame(columns=["ticker", "isin"]), None,
        source_order=["issuer", "nport"], issuer_products=products,
    )


def test_issuer_match_and_holdings(providers):
    r = look_through("IE00B5BMR087", providers)
    s = r.summary
    assert s["matched_via"] == "ishares" and s["source"] == "ishares" and s["as_of"] == "2026-11-02"
    assert s["resolved_pct"] == pytest.approx(99.5)
    assert s["unresolved_pct"] == pytest.approx(0.5)


def test_cash_ticker_is_not_a_fund(providers):
    # "USD" is also a US ETF ticker; on an issuer line it is a cash code.
    assert providers.resolve_line(Line(0.49, None, name="USD CASH", ticker="USD")) is None
    assert providers.resolve_line(Line(1.0, "US0000000000", ticker="USD", us_ticker=True)).series_id == "S1"


@pytest.mark.network
def test_live_ishares_uk():
    p = ishares.products("uk")
    pid = p.loc[p["isin"] == "IE00B5BMR087", "portfolio_id"].iloc[0]
    got = ishares.holdings(pid, "uk")
    assert got.holdings["holdingPercent"].sum() == pytest.approx(100, abs=1)
    assert got.holdings["isin"].notna().mean() > 0.95
