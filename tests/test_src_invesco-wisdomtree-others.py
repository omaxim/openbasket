"""Franklin Templeton adapter (built in the invesco-wisdomtree-others survey)."""

import json
from pathlib import Path

import pandas as pd
import pytest

from openbasket.sources.issuers import (
    HOLDING_COLUMNS,
    FetchContext,
    discover,
    validate_holdings,
    validate_products,
)
from openbasket.sources.issuers import franklin

FIX = Path(__file__).parent / "fixtures" / "invesco-wisdomtree-others"


def _json(name):
    return json.loads((FIX / name).read_text())


def test_registered():
    reg = discover()
    assert reg["franklin"].frequency == "daily"
    assert reg["franklin_ca"].frequency == "quarterly"
    for name in ("franklin", "franklin_ca"):
        assert "Franklin Templeton" in reg[name].credit
        assert reg[name].licence


def test_parse_products():
    pages = franklin.parse_sitemap((FIX / "franklin_sitemap_ie.xml").read_text())
    df = franklin.parse_products(_json("franklin_ppss_ie.json"), "ie", pages)
    assert validate_products(df) == []
    # Duplicate share class rows collapse, a class without an ISIN is dropped.
    assert df["isin"].tolist() == ["IE00FT000004", "IE00FT000012", "IE00FT000020"]
    assert df["portfolio_id"].tolist() == ["90001", "90001", "90002"]
    assert df["replication"].tolist() == ["physical", "physical", "sampled"]
    assert df.loc[0, "fund_name"] == "Franklin Example World UCITS ETF (Acc)"
    assert df.loc[2, "fund_name"] == "Franklin Example Euro Bond UCITS ETF CHF Hedged (Acc)"
    assert df.loc[2, "currency"] == "CHF"
    assert df.loc[1, "product_page_url"].endswith("/90001/ETD/franklin-example-world-ucits-etf/IE00FT000012")
    assert df.loc[2, "product_page_url"] is None
    assert df.loc[0, "holdings_url"] == (
        "https://www.franklintempleton.ie/api/pds/price-and-performance?op=Holdings&fundid=90001"
    )
    assert set(df["domicile"]) == {"IE"}


def test_closed_funds_are_dropped():
    pages = {("90002", ""): "https://www.franklintempleton.com/investments/closed/90002/SINGLCLASS/x/FXEB"}
    df = franklin.parse_products(_json("franklin_ppss_ie.json"), "ie", pages)
    assert "IE00FT000020" not in set(df["isin"])


def test_parse_sitemap_us_ticker_urls():
    pages = franklin.parse_sitemap((FIX / "franklin_sitemap_ie.xml").read_text())
    assert ("90009", "SINGLCLASS") in pages
    assert ("isin", "IE00FT000004") in pages


def test_parse_daily_holdings():
    as_of, df, notes = franklin.parse_holdings(_json("franklin_holdings_ie.json"), "ie")
    assert as_of == "2026-11-02"  # DD/MM/YYYY on the IE site
    assert list(df.columns) == HOLDING_COLUMNS
    assert validate_holdings(df) == []
    assert len(df) == 5
    assert df["isin"].tolist()[:3] == ["US0378331005", "CH0038863350", None]  # bad check digit dropped
    assert df.loc[0, "cusip"] == "037833100"
    assert df.loc[1, "sedol"] == "7123870" and df.loc[1, "cusip"] is None
    assert df.loc[2, "sedol"] is None
    # Weights are percent as reported, never rescaled; "(0.25)" is negative.
    assert df["weight_pct"].tolist() == [60.25, 30.0, 9.0, -0.25, 1.0]
    assert df["asset_class"].tolist() == ["equity", "equity", "debt", "derivative (foreign exchange)", "cash"]
    assert df.loc[2, "ticker"] is None
    assert notes == ["dailyholdings, frequency daily"]


def test_us_dates_are_month_first():
    payload = _json("franklin_holdings_ie.json")
    for r in payload["data"]["Portfolio"]["portfolio"]["dailyholdings"]:
        r["asofdate"] = "11/02/2026"
    as_of, _, _ = franklin.parse_holdings(payload, "us")
    assert as_of == "2026-11-02"


def test_parse_full_holdings_keeps_latest_quarter():
    as_of, df, notes = franklin.parse_holdings(_json("franklin_holdings_ca.json"), "ca")
    assert as_of == "2026-09-30"
    assert validate_holdings(df) == []
    assert len(df) == 3
    assert df["weight_pct"].sum() == pytest.approx(99.49)
    assert df.loc[0, "isin"] == "CA7800871021"
    assert df["asset_class"].tolist() == ["equity", "debt", "derivative (futures)"]
    assert "1 lines of earlier dates dropped" in notes


def test_empty_portfolio():
    as_of, df, notes = franklin.parse_holdings({"data": {"Portfolio": {"portfolio": {}}}}, "ie")
    assert as_of is None and df.empty and list(df.columns) == HOLDING_COLUMNS


def test_sedol_check_digit():
    assert franklin.is_valid_sedol("0263494")
    assert franklin.is_valid_sedol("B0YBKJ7")
    assert not franklin.is_valid_sedol("0263495")
    assert not franklin.is_valid_sedol("BADSEDL")


def test_holdings_cache_written_after_parse(tmp_path, monkeypatch):
    calls = []

    def fake_post(site, op, query, variables, extra=""):
        calls.append((site, op, variables["fundid"]))
        return f"https://example.test/{op}{extra}", _json("franklin_holdings_ie.json")

    monkeypatch.setattr(franklin, "_post", fake_post)
    product = {"site": "ie", "portfolio_id": "90001", "isin": "IE00FT000004"}
    ctx = FetchContext(cache_dir=str(tmp_path))
    h1 = franklin.holdings_for(product, ctx)
    h2 = franklin.holdings_for(product, ctx)
    assert len(calls) == 1
    assert h1.as_of == h2.as_of == "2026-11-02"
    assert len(list((tmp_path / "franklin").glob("ie_90001_*.json"))) == 1
    assert h2.url == "https://www.franklintempleton.ie/api/pds/price-and-performance?op=Holdings&fundid=90001"


def test_error_payload_is_not_cached(tmp_path, monkeypatch):
    def fake_post(site, op, query, variables, extra=""):
        raise ValueError("Franklin PDS Holdings on ie returned errors")

    monkeypatch.setattr(franklin, "_post", fake_post)
    with pytest.raises(ValueError):
        franklin.holdings_for({"site": "ie", "portfolio_id": "1"}, FetchContext(cache_dir=str(tmp_path)))
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.network
def test_live_franklin_ucits():
    df = franklin.products()
    assert validate_products(df) == []
    assert (df["domicile"] == "IE").sum() > 30 and (df["domicile"] == "US").sum() > 50
    row = df[df["isin"] == "IE0004I037N4"]  # Franklin FTSE Emerging Markets UCITS ETF (Acc)
    assert len(row) == 1
    h = franklin.holdings_for(row.iloc[0].to_dict(), FetchContext())
    assert validate_holdings(h.holdings) == []
    assert len(h.holdings) > 500
    assert 95 < h.holdings["weight_pct"].sum() < 105
    assert h.holdings["isin"].notna().mean() > 0.9
    assert pd.Timestamp(h.as_of) > pd.Timestamp.today() - pd.Timedelta(days=10)
