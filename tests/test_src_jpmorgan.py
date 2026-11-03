import json
from pathlib import Path

import pandas as pd
import pytest

from openbasket.sources.issuers import (
    HOLDING_COLUMNS,
    PRODUCT_COLUMNS,
    FetchContext,
    discover,
    validate_holdings,
    validate_products,
)
from openbasket.sources.issuers import jpmorgan

FIXTURES = Path(__file__).parent / "fixtures" / "jpmorgan"


def _json(name):
    return json.loads((FIXTURES / name).read_text())


class FakeResponse:
    def __init__(self, content: bytes, content_type: str, status: int = 200):
        self.content = content
        self.status_code = status
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self.content)


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;charset=UTF-8"


@pytest.fixture
def fake_site(monkeypatch):
    """Serve the fixtures in place of am.jpmorgan.com and record the URLs asked for."""
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append((url, params))
        if "fund-explorer" in url:
            name = f"fund_explorer_{params['country']}.json"
            body = (FIXTURES / name).read_bytes() if (FIXTURES / name).exists() else b"[]"
            return FakeResponse(body, "application/json")
        if "product-data" in url:
            return FakeResponse((FIXTURES / "product_data_us.json").read_bytes(), "application/json")
        if "dailyETFHoldings" in url:
            name = "ucits_bond_ch.xlsx" if "country=ch" in url else "ucits_equity_gb.xlsx"
            return FakeResponse((FIXTURES / name).read_bytes(), XLSX)
        raise AssertionError(url)

    monkeypatch.setattr(jpmorgan.http, "get", fake_get)
    return calls


def test_registered():
    adapter = discover()["jpmorgan"]
    assert adapter.frequency == "daily"
    assert adapter.credit and adapter.licence
    assert adapter.redistribution == "local_only"


def test_product_page_language_follows_site():
    # The de, at, it, fr and es sites have no English product pages (/de/en/... is a 404).
    assert jpmorgan._product_page("de", "JPM Example UCITS ETF", "IE000PYFQ9I0") == (
        "https://am.jpmorgan.com/de/de/asset-management/adv/products/jpm-example-ucits-etf-ie000pyfq9i0")
    assert "/at/de/" in jpmorgan._product_page("at", "x", "IE000PYFQ9I0")
    assert "/it/it/" in jpmorgan._product_page("it", "x", "IE000PYFQ9I0")
    assert "/gb/en/" in jpmorgan._product_page("gb", "x", "IE000PYFQ9I0")
    assert "/se/en/" in jpmorgan._product_page("se", "x", "IE000PYFQ9I0")
    # The holdings file keeps its English locale.
    assert "locale=en-DE" in jpmorgan.ucits_holdings_url("IE000PYFQ9I0", "de")


def test_currency_pseudo_codes():
    assert jpmorgan.normalise_currency("USD") == "USD"
    assert jpmorgan.normalise_currency("USQ") == "USD"
    assert jpmorgan.normalise_currency("EUQ") == "EUR"
    assert jpmorgan.normalise_currency("JPQ") == "JPY"
    assert jpmorgan.normalise_currency("CCC") is None
    assert jpmorgan.normalise_currency(None) is None


def test_us_weight_sum_far_from_100_gets_a_note(monkeypatch):
    payload = {"fundData": {"dailyHoldingsAll": {"effectiveDate": "2026-11-02", "data": [
        {"securityId": "037833100", "securityDescription": "APPLE INC", "securityType": "Common Stock",
         "netAssetValuePercent": 80.8, "currencyCode": "USD"},
        {"securityId": "XX1", "securityDescription": "COMMODITY FUTURE", "securityType": "Commodity Futures",
         "netAssetValuePercent": 0.0, "currencyCode": "USQ"},
    ]}}}
    monkeypatch.setattr(jpmorgan.http, "get", lambda url, **kw: FakeResponse(json.dumps(payload).encode(), "application/json"))
    h = jpmorgan.holdings({"isin": "US46641Q3323", "site": "us", "holdings_url": None})
    assert any("sum to 80.80%" in n for n in h.notes)
    assert h.holdings["currency"].tolist() == ["USD", "USD"]


def test_products_merges_country_sites(fake_site):
    df = jpmorgan.products(("gb", "ch", "us"))
    assert validate_products(df) == []
    assert list(df.columns) == PRODUCT_COLUMNS
    # The bad check digit is dropped; the dist class listed on gb and ch keeps gb.
    assert df["isin"].tolist() == ["IE00BMDV7354", "IE000P334X90", "IE000UPAYVL7", "US46641Q3323"]
    assert df.set_index("isin")["site"].to_dict() == {
        "IE00BMDV7354": "gb", "IE000P334X90": "gb", "IE000UPAYVL7": "ch", "US46641Q3323": "us"}
    by = df.set_index("isin")
    assert by.loc["IE00BMDV7354", "portfolio_id"] == by.loc["IE000P334X90", "portfolio_id"]
    assert by.loc["IE000UPAYVL7", "currency"] == "CHF"
    assert set(df["domicile"]) == {"IE", "US"}
    assert set(df["replication"]) == {"physical"}
    assert "country=ch" in by.loc["IE000UPAYVL7", "holdings_url"]
    assert "cusip=46641Q332" in by.loc["US46641Q3323", "holdings_url"]
    assert by.loc["IE000UPAYVL7", "product_page_url"] == (
        "https://am.jpmorgan.com/ch/en/asset-management/adv/products/"
        "jpm-example-premium-income-active-ucits-etf-chf-hedged-dist-ie000upayvl7")


def test_parse_ucits_equity_workbook():
    as_of, df = jpmorgan.parse_ucits_workbook((FIXTURES / "ucits_equity_gb.xlsx").read_bytes())
    assert as_of == "2026-11-02"
    assert validate_holdings(df) == []
    assert list(df.columns) == HOLDING_COLUMNS
    assert len(df) == 5  # disclaimer rows are not holdings
    # Fractions in the file, percent out.
    assert df["weight_pct"].tolist()[:3] == [50.0, 25.0, 12.5]
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    assert df["isin"].tolist()[:3] == ["US0378331005", "CH0038863350", "US81141R1005"]
    # An option's OCC symbol in the ISIN column is not an ISIN.
    assert df["isin"].isna().tolist()[3:] == [True, True]
    assert df["asset_class"].tolist() == ["equity", "equity", "equity", "derivative (options)", "cash"]


def test_parse_ucits_bond_workbook_swiss_date():
    as_of, df = jpmorgan.parse_ucits_workbook((FIXTURES / "ucits_bond_ch.xlsx").read_bytes())
    assert as_of == "2026-11-02"  # "02.11.2026" on the Swiss site
    assert validate_holdings(df) == []
    assert df["isin"].tolist()[0] == "CH0127181193"
    assert df["isin"].iloc[1:3].isna().all()  # invalid check digits become None
    assert df["asset_class"].tolist() == ["debt", "debt", "debt", "derivative (futures)", "cash"]
    assert df["weight_pct"].sum() == pytest.approx(100.0)


def test_parse_us_product_data():
    as_of, df = jpmorgan.parse_us_product_data(_json("product_data_us.json"))
    assert as_of == "2026-11-02"
    assert validate_holdings(df) == []
    assert list(df.columns) == HOLDING_COLUMNS
    assert df["cusip"].tolist()[0] == "037833100"
    assert df["sedol"].tolist()[1] == "6900643"  # foreign shares carry a SEDOL
    assert pd.isna(df["cusip"].iloc[1])
    assert df["cusip"].tolist()[2] == "91282CQB0"
    assert pd.isna(df["ticker"].iloc[2])  # bond lines' issuer tickers are dropped
    assert df["asset_class"].tolist() == [
        "equity", "equity", "debt", "derivative (equity-linked notes)", "money market", "cash"]
    assert df["isin"].isna().all()
    assert df["weight_pct"].sum() == pytest.approx(100.13)  # as reported, not rescaled


def test_parse_us_product_data_without_holdings():
    with pytest.raises(ValueError):
        jpmorgan.parse_us_product_data({"fundData": {"dailyHoldingsAll": {"data": None}}})


def test_holdings_and_cache(fake_site, tmp_path):
    ctx = FetchContext(cache_dir=str(tmp_path))
    df = jpmorgan.parse_fund_explorer(_json("fund_explorer_ch.json"), "ch")
    product = df.set_index("isin").loc["IE000UPAYVL7"].to_dict() | {"isin": "IE000UPAYVL7"}
    h = jpmorgan.holdings(product, ctx)
    assert h.as_of == "2026-11-02"
    assert "cusip=IE000UPAYVL7&country=ch" in h.url
    assert validate_holdings(h.holdings) == []
    assert len(list(tmp_path.iterdir())) == 1
    n = len(fake_site)
    jpmorgan.holdings(product, ctx)
    assert len(fake_site) == n  # second call is served from the cache

    us = jpmorgan.parse_fund_explorer(_json("fund_explorer_us.json"), "us").iloc[0].to_dict()
    hu = jpmorgan.holdings(us, ctx)
    assert hu.as_of == "2026-11-02" and len(hu.holdings) == 6
    cached = [p for p in tmp_path.iterdir() if p.suffix == ".json"]
    assert len(cached) == 1 and "dailyHoldingsAll" in cached[0].read_text()


def test_failed_parse_is_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(jpmorgan.http, "get", lambda url, **kw: FakeResponse(b"<html>error</html>", "text/html"))
    product = {"isin": "IE00BMDV7354", "site": "gb", "holdings_url": None}
    with pytest.raises(ValueError):
        jpmorgan.holdings(product, FetchContext(cache_dir=str(tmp_path)))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.network
def test_live_one_fund():
    product = {"isin": "IE00BJK9HD13", "site": "gb", "holdings_url": None}  # BetaBuilders EUR Govt Bond
    h = jpmorgan.holdings(product)
    assert h.as_of and len(h.holdings) > 50
    assert validate_holdings(h.holdings) == []
    assert 95 < h.holdings["weight_pct"].sum() < 105
    assert h.holdings["isin"].notna().mean() > 0.9
