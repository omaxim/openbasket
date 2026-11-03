import pandas as pd
import pytest

from openbasket.lookthrough import Line, look_through
from openbasket.providers import DataProviders
from openbasket.sources import ishares
from openbasket.sources.issuers import ishares as ishares_adapter


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


def _holdings(points):
    return {"componentsByNameMap": {"holdings": {"containersByNameMap": {"all": {"dataPointsByNameMap": points}}}}}


def test_parse_holdings_us_equity_market_weight():
    # US equity funds report marketWeight instead of holdingPercent.
    _, df = ishares.parse_holdings(_holdings({
        "asOfDate": _dp("20261102"),
        "isin": _dp("['US0378331005', None]"),
        "cusip": _dp("['037833100', None]"),
        "issueName": _dp("['APPLE', 'S&P500 EMINI DEC 26']"),
        "marketWeight": _dp("[7.59, None]"),
        "notionalWeight": _dp("[7.58, 0.15]"),
        "assetClass": _dp("['Equity', 'Futures']"),
    }))
    assert "marketWeight" not in df
    assert df["holdingPercent"].iloc[0] == pytest.approx(7.59)
    assert df["isin"].iloc[1] is None


CUSIP_ONLY = _holdings({
    "isin": _dp("['US91282CMM00', '-', None, None]"),
    "cusip": _dp("['91282CMM0', '94990LAF5', 'G0000000X', None]"),
    "issueName": _dp("['TREASURY NOTE', 'WFCM_26-5C9 AS', 'CINS BOND', 'USD CASH']"),
    "holdingPercent": _dp("[0.40285, 0.00717, 0.001, -2.9]"),
})


def test_parse_holdings_keeps_cusip_but_does_not_derive():
    _, df = ishares.parse_holdings(CUSIP_ONLY)
    assert df["isin"].tolist() == ["US91282CMM00", None, None, None]
    assert df["cusip"].tolist()[1] == "94990LAF5"


@pytest.mark.parametrize("known, expected", [(set(), None), ({"US94990LAF58"}, "US94990LAF58")])
def test_cusip_only_line_needs_openfigi(monkeypatch, known, expected):
    from openbasket.datasets import connection

    class Figi:
        def map(self, id_type, values, exch_code=None):
            return {v: ([{"name": "x"}] if v in known else []) for v in values}

    empty = connection().sql(
        "select null::varchar as isin, null::varchar as cusip, null::varchar as issuer_lei, null::varchar as series_id where false"
    )
    products = pd.DataFrame([{"issuer": "ishares", "site": "us", "portfolio_id": "1", "isin": "US4642872265",
                              "ticker": "AGG", "fund_name": "AGG"}])
    monkeypatch.setattr(ishares, "holdings", lambda pid, site, cache_dir=None, top10=False: ishares.IssuerHoldings(
        *ishares.parse_holdings(CUSIP_ONLY), url="u"))
    prov = DataProviders(empty, empty, pd.DataFrame(columns=["series_id", "series_name", "series_lei", "ticker"]),
                         pd.DataFrame(columns=["ticker", "isin"]), Figi(), source_order=["issuer"],
                         issuer_products=products, adapters={"ishares": ishares_adapter.ADAPTER})
    lines = prov.holdings(prov.resolve("US4642872265"))[0].lines
    assert lines[1].isin == expected
    assert lines[1].isin_source == ("derived_cusip" if expected else None)
    assert lines[0].isin_source == "reported"


US_ROWS = {
    "columns": [{"name": n} for n in (
        "portfolioId", "isin", "cusip", "localExchangeTicker", "fundName", "productType",
        "aladdinAssetClass", "productPageUrl", "totalNetAssets")],
    "data": [
        [239726, "US4642872000", "464287200", "IVV", "iShares Core S&P 500 ETF", "ISHARES_FUND_DATA",
         "Equity", "/us/products/239726/ishares-core-sp-500-etf", {"d": "1", "r": 7.4e11}],
        [239458, "-", "464287226", "AGG", "iShares Core U.S. Aggregate Bond ETF", "ISHARES_FUND_DATA",
         "Fixed Income", None, "-"],
        [227724, "US09253F3091", "09253F309", "MDIIX", "iShares MSCI EAFE International Index Fund",
         "BLK_MUTUAL_FUND_DATA", "Equity", None, None],
    ],
}


def test_products_us(monkeypatch):
    class R:
        def json(self):
            return {"data": {"tableData": US_ROWS}}

    seen = {}
    monkeypatch.setattr(ishares, "_get", lambda url, params: seen.update(url=url) or R())
    df = ishares.products("us")
    assert seen["url"].endswith("/us/product-screener/product-screener-v3.jsn")
    # Mutual funds are dropped; AGG's ISIN comes from its CUSIP.
    assert df["isin"].tolist() == ["US4642872000", "US4642872265"]
    assert df["portfolio_id"].tolist() == ["239726", "239458"]
    assert df["site"].unique().tolist() == ["us"]
    assert df["total_net_assets"].iloc[0] == pytest.approx(7.4e11)
    assert df["product_page_url"].iloc[0] == "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf"


def test_products_uk(monkeypatch):
    class R:
        def json(self):
            return {"253743": {"isin": "IE00B5BMR087", "localExchangeTicker": "CSPX", "fundName": "Core S&P 500",
                               "totalNetAssets": {"r": 1.0}},
                    "1": {"isin": None, "localExchangeTicker": "-"}}

    monkeypatch.setattr(ishares, "_get", lambda url, params: R())
    df = ishares.products("uk")
    assert df[["portfolio_id", "isin", "ticker"]].values.tolist() == [["253743", "IE00B5BMR087", "CSPX"]]


def test_parse_holdings_rejects_ragged_columns():
    bad = {"componentsByNameMap": {"holdings": {"containersByNameMap": {"all": {"dataPointsByNameMap": {
        "isin": _dp("['A', 'B']"), "holdingPercent": _dp("[1.0]")}}}}}}
    with pytest.raises(ValueError):
        ishares.parse_holdings(bad)


@pytest.fixture
def providers(monkeypatch, tmp_path):
    from openbasket.datasets import connection

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
        lambda pid, site, cache_dir=None, top10=False: ishares.IssuerHoldings(*ishares.parse_holdings(PAYLOAD), url="u"),
    )
    return DataProviders(
        empty, empty, fund_master, pd.DataFrame(columns=["ticker", "isin"]), None,
        source_order=["issuer", "nport"], issuer_products=products, adapters={"ishares": ishares_adapter.ADAPTER},
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


@pytest.mark.network
def test_live_ishares_us():
    p = ishares.products("us")
    pid = p.loc[p["isin"] == "US4642872000", "portfolio_id"].iloc[0]
    got = ishares.holdings(pid, "us")
    assert got.holdings["holdingPercent"].sum() == pytest.approx(100, abs=1)
    assert got.holdings["isin"].notna().mean() > 0.95
