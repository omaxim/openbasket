import json
import re
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
from openbasket.sources.issuers import vanguard_ca as vg

FIX = Path(__file__).parent / "fixtures" / "canada"
PAGE = (FIX / "product_page.html").read_text()
FINDER = json.loads((FIX / "fund_finder.json").read_text())
HOLDINGS = json.loads((FIX / "holdings.json").read_text())


class _Resp:
    def __init__(self, text):
        self.text = text


@pytest.fixture
def offline(monkeypatch):
    """Serve the fixtures in place of vanguard.ca and record every GraphQL call."""
    calls = []

    def fake_graphql(query, variables):
        calls.append((query, variables))
        if "funds(portIds" in query and "borHoldings" not in query:
            wanted = set(variables["portIds"])
            return {"funds": [f for f in FINDER["funds"] if f["portId"] in wanted]}
        layer = re.search(r"borHoldings\(portIds: \$portIds\) \{\s*(\w+)\(", query).group(1)
        pid = variables["portIds"][0]
        page = HOLDINGS.get(pid, {}).get(layer, {}).get(variables.get("lastItemKey") or "first")
        page = page or {"items": [], "totalHoldings": 0, "lastItemKey": None}
        return {"borHoldings": [{layer: page}]}

    monkeypatch.setattr(vg, "_graphql", fake_graphql)
    monkeypatch.setattr(vg.http, "get", lambda url, **kw: _Resp(PAGE))
    vg._own_sedol_isin.cache_clear()
    yield calls
    vg._own_sedol_isin.cache_clear()


def test_registered():
    # The Canadian reader is the vanguard adapter's fallback, not an adapter of its own.
    assert "vanguard_ca" not in discover() and "vanguard" in discover()
    assert vg.REDISTRIBUTION == "local_only"


def test_parse_port_ids_dedupes():
    assert vg.parse_port_ids(PAGE) == ["9001", "9002", "9003", "D101", "D102"]
    with pytest.raises(ValueError):
        vg.parse_port_ids("<html></html>")


def test_products(offline):
    df = vg.products()
    assert validate_products(df) == []
    assert list(df.columns[: len(PRODUCT_COLUMNS)]) == PRODUCT_COLUMNS
    # the bad-ISIN product is dropped; the space inside "CA 9229Z00246" is removed
    assert df["isin"].tolist() == ["CA9229Z00162", "CA9229Z00246", "CA9229Z00329", "CA9229Z00402"]
    row = df.set_index("isin").loc["CA9229Z00162"]
    assert row["ticker"] == "VXEQ"
    assert row["domicile"] == "CA"
    assert row["replication"] == "physical"
    assert row["listing_sedols"] == "B0000B0"
    assert row["product_page_url"] == "https://www.vanguard.ca/en/product/etf/equity/9001/vanguard-example-equity-index-etf"
    alloc = df.set_index("isin").loc["CA9229Z00246"]
    assert "/etf/asset-allocation/9002/" in alloc["product_page_url"]
    # Series F and Series M of one mutual fund share a portfolio
    f, m = df.set_index("isin").loc[["CA9229Z00329", "CA9229Z00402"], "portfolio_id"]
    assert f == m == "vanguard-example-value-fund"
    assert df.set_index("isin").loc["CA9229Z00329", "ticker"] == "VIC900"
    assert "/mf/equity/D101/" in df.set_index("isin").loc["CA9229Z00329", "product_page_url"]


def test_sedol_check_digit():
    assert vg.is_valid_sedol("2000008")
    assert vg.is_valid_sedol("B0000B0")
    assert not vg.is_valid_sedol("2000009")
    assert not vg.is_valid_sedol("99X4640")  # internal "temp line" code
    assert not vg.is_valid_sedol("B0000A9")  # vowels are never used
    assert not vg.is_valid_sedol(None)


def test_asset_class_codes():
    assert vg.asset_class("EQ.STOCK") == "equity"
    assert vg.asset_class("EQ.ETF") == "fund"
    assert vg.asset_class("FI.CORP") == "debt"
    assert vg.asset_class("CRNY") == "cash"
    assert vg.asset_class("CT.FOREX") == "derivative (foreign exchange)"
    assert vg.asset_class("DE.IND") == "derivative (futures)"
    assert vg.asset_class("CT.PORTSWAP") == "derivative (swap)"


def _product(port_id):
    return {"port_id": port_id, "product_page_url": f"https://www.vanguard.ca/en/product/etf/equity/{port_id}/x"}


def test_direct_holdings(offline, tmp_path):
    h = vg.holdings(_product("9001"), FetchContext(cache_dir=str(tmp_path)))
    df = h.holdings
    assert validate_holdings(df) == []
    assert list(df.columns) == HOLDING_COLUMNS
    assert h.as_of == "2026-09-30"
    assert h.url.endswith("/9001/x")
    assert "Direct month-end" in h.notes[0]
    # weights are percent, as reported, never rescaled
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    assert df["sedol"].tolist()[:2] == ["2000008", "3000007"]
    assert df["isin"].isna().all()
    assert df["asset_class"].tolist() == ["equity", "equity", "derivative (futures)", "cash"]
    assert df.loc[3, "currency"] == "CAD"
    # cached only after a successful parse, one file per fund per day
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and files[0].name.startswith("vanguard_ca_9001_")
    n = len(offline)
    again = vg.holdings(_product("9001"), FetchContext(cache_dir=str(tmp_path)))
    assert len(offline) == n
    pd.testing.assert_frame_equal(again.holdings, df)


def test_delayered_holdings_paginate_and_win_over_direct(offline):
    h = vg.holdings(_product("9002"))
    df = h.holdings
    assert validate_holdings(df) == []
    assert len(df) == 5  # two pages
    assert "look-through" in h.notes[0]
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    assert df.loc[1, "name"] == "Province of Example 3.25% 2031-06-02"
    assert df.loc[1, "asset_class"] == "debt"
    assert pd.isna(df.loc[2, "sedol"])  # 99X4640 fails the SEDOL check
    pages = [v.get("lastItemKey") for q, v in offline if "delayeredHoldings(" in q]
    assert pages == [None, "page-2"]
    assert not any("holdings(limit" in q and "delayered" not in q and "daily" not in q for q, _ in offline)


def test_own_etf_line_gets_isin_from_listing_sedol(offline):
    items = HOLDINGS["9002"]["holdings"]["first"]["items"]
    as_of, df = vg.parse_holdings(items, {"B0000B0": "CA9229Z00162"})
    assert df.loc[0, "isin"] == "CA9229Z00162"
    assert df.loc[0, "asset_class"] == "fund"
    # holdings() looks the SEDOL up from products() when a fund line has no ISIN
    pid_9002_direct = {"9002x": {"holdings": HOLDINGS["9002"]["holdings"]}}
    HOLDINGS.update(pid_9002_direct)
    try:
        h = vg.holdings(_product("9002x"))
    finally:
        HOLDINGS.pop("9002x")
    assert h.holdings.loc[0, "isin"] == "CA9229Z00162"


def test_daily_holdings_carry_isins(offline):
    h = vg.holdings(_product("D101"))
    df = h.holdings
    assert validate_holdings(df) == []
    assert h.as_of == "2026-11-02"
    assert "Daily" in h.notes[0]
    assert df["isin"].tolist()[0] == "US0378331005"
    assert pd.isna(df.loc[1, "isin"])  # bad check digit
    assert df["weight_pct"].sum() == pytest.approx(99.5)


def test_no_holdings(offline, tmp_path):
    h = vg.holdings(_product("9003"), FetchContext(cache_dir=str(tmp_path)))
    assert h.holdings.empty and h.as_of is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.network
def test_live_vanguard_canada():
    df = vg.products()
    assert validate_products(df) == []
    assert len(df) > 40
    vcn = df[df["ticker"] == "VCN"].iloc[0].to_dict()
    h = vg.holdings(vcn)
    assert validate_holdings(h.holdings) == []
    assert len(h.holdings) > 150
    assert 99 < h.holdings["weight_pct"].sum() < 101
    assert h.as_of and h.as_of[:2] == "20"
    assert h.holdings["sedol"].notna().mean() > 0.9
