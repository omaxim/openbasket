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
from openbasket.sources.issuers import vanguard

FIXTURES = Path(__file__).parent / "fixtures" / "vanguard"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


PROFILES = _load("funds_profiles.json")
US_LIST = _load("us_funddetail.json")
PAGES = [_load("bor_holdings_page1.json"), _load("bor_holdings_page2.json")]

#: Which fixture portIds each country's portIdList returns.
COUNTRY_IDS = {
    "United Kingdom": ["9679", "9505", "9232", "9398", "A358"],
    "Switzerland": ["9679", "9505", "3141"],
    "Australia": ["8205"],
    "Canada": ["1811"],
}


class FakeGpx:
    def __init__(self):
        self.calls = []

    def __call__(self, query, variables):
        self.calls.append((query, variables))
        if "portIdList" in query:
            ids = COUNTRY_IDS.get(variables["c"], [])
            return {"portIdList": {"count": len(ids), "portIds": [{"portId": i} for i in ids]}}
        if "borHoldings" in query:
            return PAGES[0] if variables["k"] is None else PAGES[1]
        if "funds(" in query:
            wanted = set(variables["p"])
            return {"funds": [f for f in PROFILES["funds"] if f is None or f["profile"]["portId"] in wanted]}
        raise AssertionError(query)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def json(self):
        return self.payload

    def raise_for_status(self):
        pass


@pytest.fixture
def fake(monkeypatch):
    gpx = FakeGpx()
    monkeypatch.setattr(vanguard, "gpx", gpx)
    monkeypatch.setattr(vanguard.http, "get", lambda url, **kw: FakeResponse(US_LIST))
    return gpx


def test_registered():
    adapter = discover()["vanguard"]
    assert adapter.frequency == "monthly"
    assert "written authorisation" in adapter.licence
    assert adapter.products is vanguard.products


def test_products(fake):
    df = vanguard.products(("us", "au", "ca", "uk", "ch"))
    assert validate_products(df) == []
    assert list(df.columns[: len(PRODUCT_COLUMNS)]) == PRODUCT_COLUMNS
    by = df.set_index("isin")
    # Deactivated share classes and rows without an ISIN are dropped.
    assert "IE00BYYR0935" not in by.index and len(df) == 7
    vwra = by.loc["IE00BK5BQT80"]
    assert vwra["site"] == "uk" and vwra["port_id"] == "9679"
    assert vwra["ticker"] == "VWRA"  # London line in the fund's own currency
    assert vwra["replication"] == "physical" and vwra["domicile"] == "IE"
    assert vwra["product_page_url"] == (
        "https://www.vanguard.co.uk/professional/product/etf/equity/9679/ftse-all-world-ucits-etf-usd-accumulating"
    )
    # Share classes of one portfolio share the smallest portId.
    assert by.loc["IE00B3RBWM25", "portfolio_id"] == vwra["portfolio_id"] == "9505"
    # A US ETF listed in Zurich keeps the US site's row; the Admiral class
    # (no ISIN in GPX) gets one derived from the US list's CUSIP.
    assert by.loc["US9220427424", "site"] == "us"
    assert by.loc["US9220427424", "product_page_url"].endswith("/etfs/profile/vt")
    assert by.loc["US9220427184", "ticker"] == "VTWAX"
    assert by.loc["US9220427184", "portfolio_id"] == by.loc["US9220427424", "portfolio_id"] == "0628"
    assert pd.isna(by.loc["US9220427424", "replication"])
    assert by.loc["AU000000VAS1", "ticker"] == "VAS"
    assert by.loc["AU000000VAS1", "product_page_url"].endswith("/invest-with-us/etf?portId=8205")
    assert by.loc["CA92211H1047", "ticker"] == "VLB"
    assert "/etf/fixed-income/1811/" in by.loc["CA92211H1047", "product_page_url"]
    assert pd.isna(by.loc["GB00B41XG308", "ticker"])


def test_parse_holdings():
    items = PAGES[0]["borHoldings"][0]["holdings"]["items"] + PAGES[1]["borHoldings"][0]["holdings"]["items"]
    as_of, df = vanguard.parse_holdings(items)
    assert as_of == "2026-09-30"
    assert validate_holdings(df) == []
    assert list(df.columns[: len(HOLDING_COLUMNS)]) == HOLDING_COLUMNS
    # Weights are percent as reported, never rescaled.
    assert df["weight_pct"].tolist() == [40.12345, 30.5, 20.0, 0.1, -0.02, 9.29655, 0.0]
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    assert df["isin"].tolist()[:3] == ["US67066G1040", "US0378331005", "US912828ZQ64"]
    cash = df.iloc[5]
    assert pd.isna(cash["isin"]) and pd.isna(cash["cusip"])  # internal codes dropped
    assert df["asset_class"].tolist() == [
        "equity", "equity", "debt", "derivative (futures)", "derivative (foreign exchange)", "cash", "cash"]
    assert df.loc[2, "coupon_pct"] == 0.625 and df.loc[2, "maturity_date"] == "2030-05-15"
    assert df.loc[6, "currency"] == "EUR"
    assert df.loc[0, "sedol"] == "2379504"


@pytest.mark.parametrize(
    "stype, label",
    [("EQ.REIT", "equity"), ("EQ.ETF", "fund"), ("MF.MF", "fund"), ("FI.MBS", "debt"), ("MM.TBILL", "debt"),
     ("MM.RE", "money market"), ("CT.PORTSWAP", "derivative (swap)"), ("XX", "other"), (None, "other")],
)
def test_asset_class(stype, label):
    assert vanguard._asset_class(stype) == label


def test_holdings_pages_and_cache(fake, tmp_path):
    product = {"port_id": "9679", "portfolio_id": "9505", "product_page_url": "https://example.invalid/vwra"}
    h = vanguard.holdings(product, FetchContext(cache_dir=str(tmp_path)))
    assert h.as_of == "2026-09-30" and len(h.holdings) == 7
    assert h.url == "https://example.invalid/vwra"
    assert len(h.notes) == 1  # no count mismatch
    pages = [v for q, v in fake.calls if "borHoldings" in q]
    assert [v["k"] for v in pages] == [None, PAGES[0]["borHoldings"][0]["holdings"]["lastItemKey"]]
    cached = list(tmp_path.glob("vanguard_9679_*.json"))
    assert len(cached) == 1
    # A second call reads the cache and sends nothing.
    fake.calls.clear()
    again = vanguard.holdings(product, FetchContext(cache_dir=str(tmp_path)))
    assert fake.calls == [] and again.holdings["weight_pct"].sum() == pytest.approx(100.0)


def test_unparseable_response_is_not_cached(monkeypatch, tmp_path):
    bad = {"borHoldings": [{"portId": "1", "holdings": {"totalHoldings": 1, "lastItemKey": None,
                                                       "items": [{"marketValuePercentage": "x", "effectiveDate": None}]}}]}
    monkeypatch.setattr(vanguard, "gpx", lambda q, v: bad)
    monkeypatch.setattr(vanguard, "parse_holdings", lambda items: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(ValueError):
        vanguard.holdings({"port_id": "1"}, FetchContext(cache_dir=str(tmp_path)))
    assert list(tmp_path.iterdir()) == []


def test_no_holdings(monkeypatch):
    monkeypatch.setattr(vanguard, "gpx", lambda q, v: {"borHoldings": []})
    with pytest.raises(LookupError):
        vanguard.holdings({"port_id": "1"})


def test_graphql_errors_raise(monkeypatch):
    class R:
        status_code = 400

        def json(self):
            return {"errors": [{"message": "Cannot query field"}]}

    monkeypatch.setattr(vanguard.http, "post", lambda *a, **k: R())
    with pytest.raises(vanguard.GraphQLError):
        vanguard.gpx("{ x }", {})


@pytest.mark.network
def test_network_one_fund(tmp_path):
    # Vanguard S&P 500 UCITS ETF (USD) Distributing.
    profiles = vanguard.fetch_profiles(["9503"])
    assert vanguard._ids(profiles["9503"])["ISIN"] == "IE00B3XXRP09"
    h = vanguard.holdings({"port_id": "9503"}, FetchContext(cache_dir=str(tmp_path)))
    df = h.holdings
    assert validate_holdings(df) == []
    assert len(df) > 400
    assert df["weight_pct"].sum() == pytest.approx(100, abs=1)
    assert df.loc[df["isin"].notna(), "weight_pct"].sum() > 98
    assert h.as_of and h.as_of < pd.Timestamp.today().date().isoformat()
