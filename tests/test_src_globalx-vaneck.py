"""Global X ETFs Europe and VanEck (Europe) adapters.

Fixtures in ``tests/fixtures/globalx-vaneck`` keep the issuers' real field
names and layout with invented funds, rows and values.
"""

import io
import json
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from openbasket.sources.issuers import (
    HOLDING_COLUMNS,
    PRODUCT_COLUMNS,
    FetchContext,
    discover,
    globalx,
    validate_holdings,
    validate_products,
    vaneck,
)

FIX = Path(__file__).parent / "fixtures" / "globalx-vaneck"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _xlsx(sheet: str, rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


VANECK_EQUITY = _xlsx(
    "EXSM_asof_20261102",
    [
        ["All Holdings  11/02/2026"],
        [""],
        ["Number", "Holding Name", "Ticker", "ISIN", "Shares", "Market Value", "% of Net Assets"],
        [1, "Example Semiconductor A", "EXA US", "US8740391003", "1,000", "$ 1,000.00", "60.25%"],
        [2, "Example Semiconductor B", "EXB US", "US67066G1040", "500", "$ 500.00", "29.50%"],
        [3, "Example Korea Co", "005380 KS", " -- ", "10", "$ 10.00", "10.20%"],
        [4, "Other/Cash", " -- ", " -- ", " -- ", "$ 1.00", "0.05%"],
        [""],
    ],
)

VANECK_BOND = _xlsx(
    "EXCB_asof_20261102",
    [
        ["All Fund Holdings 11/02/2026"],
        ["Composite Rating. An evenly weighted blend of ratings."],
        ["Number", "Holding Name", "ISIN", "Coupon", "Maturity", "Composite Rating", "% of Net Assets", "State", "Market Value"],
        [1, "Example Bank Plc", "XS2621539910", "4.856%", "23/05/2033", "A-", "60.1025", "United Kingdom", "€ 1.00"],
        [2, "Example Brewer Sa", "BE6350704175", "3.950%", "22/03/2044", "A-", "39.3233", "Belgium", "€ 1.00"],
        [3, "Other/Cash", " -- ", " -- ", " -- ", " -- ", "0.5742", "", "€ 1.00"],
        ["             "],
    ],
)

VANECK_COIN = _xlsx(
    "EXCN_asof_20261102",
    [
        ["All Holdings  11/02/2026"],
        [""],
        ["Number", "Holding Name", "Ticker", "ISIN", "Shares", "Market Value", "% of Net Assets"],
        [1, "Example Coin", "EXC", " -- ", "1", "$ 1.00", "100.00%"],
        [""],
    ],
)


# ------------------------------------------------------------------ registry


def test_both_adapters_registered():
    reg = discover()
    for name in ("globalx", "vaneck"):
        a = reg[name]
        assert a.credit and a.licence and a.homepage.startswith("https://")
        assert a.frequency == "daily"
    assert globalx.FOOTER_NOTICE in reg["globalx"].licence


# ------------------------------------------------------------------ Global X


def _globalx_products() -> pd.DataFrame:
    explore = globalx.parse_explore(_read("globalx_explore.html"))
    pages = {
        "exdv": globalx.parse_fund_page(_read("globalx_fund_exdv.html")),
        "excc": globalx.parse_fund_page(_read("globalx_fund_excc.html")),
    }
    return globalx.product_rows(explore, pages)


def test_globalx_explore_and_fund_pages():
    explore = globalx.parse_explore(_read("globalx_explore.html"))
    assert [f["PRIMARY_TICKER"] for f in explore] == ["EXDV", "EXCC", "EXC0"]
    page = globalx.parse_fund_page(_read("globalx_fund_exdv.html"))
    assert page["classes"] == [("IE000YICM5P9", "acc", "EXDA LN"), ("IE00077FRP95", "dist", "EXDV LN")]
    assert page["details"]["domicile"] == "Ireland"


def test_globalx_products_contract():
    df = _globalx_products()
    assert list(df.columns) == PRODUCT_COLUMNS
    assert validate_products(df) == []
    # Two share classes per UCITS fund from its page; the ETP has no page in
    # the fixture and keeps its primary ISIN from the explore page.
    assert len(df) == 5
    exdv = df[df["portfolio_id"] == "exdv"]
    assert set(exdv["isin"]) == {"IE000YICM5P9", "IE00077FRP95"}
    assert set(exdv["replication"]) == {"physical"}
    assert set(df.loc[df["portfolio_id"] == "excc", "replication"]) == {"swap"}
    etp = df[df["portfolio_id"] == "exc0"].iloc[0]
    assert etp["isin"] == "GB00BLBDZV05" and etp["asset_class"] == "Digital Asset"
    assert exdv.iloc[0]["holdings_url"] == "https://globalxetfs.eu/api/funds/exdv/topholdingscsv"
    assert exdv.iloc[0]["product_page_url"] == "https://globalxetfs.eu/funds/exdv"
    assert exdv.iloc[0]["fund_name"].startswith("Global X Example Dividend")


def test_globalx_replication_labels():
    assert globalx.replication("Physical - Full Replication – Passively Managed") == "physical"
    assert globalx.replication("Physical - Sampled - Passively Managed") == "sampled"
    assert globalx.replication("Synthetic – Passively Managed – Index Tracking") == "swap"
    assert globalx.replication("Physical - Backed by Uniswap (UNI)") == "physical"
    assert globalx.replication("Total return swap") == "swap"
    assert globalx.replication(None) is None


def test_globalx_holdings_parse():
    as_of, df = globalx.parse_holdings((FIX / "globalx_holdings.csv").read_bytes(), "Equity")
    assert as_of == "2026-11-02"
    assert list(df.columns) == HOLDING_COLUMNS
    assert validate_holdings(df) == []
    assert len(df) == 7  # the disclaimer footer is dropped
    assert df["weight_pct"].sum() == pytest.approx(100.0)  # NET_ASSETS is already percent
    assert df["isin"].tolist()[1:3] == ["US0378331005", "IE000S9YS762"]
    bad = df[df["name"] == "BAD IDENTIFIERS CO"].iloc[0]
    assert pd.isna(bad["isin"]) and pd.isna(bad["sedol"])  # check digits fail
    assert df.loc[1, "sedol"] == "2046251"
    classes = dict(zip(df["name"], df["asset_class"]))
    assert classes["CURRENCY CONTRACT - BRL"] == "derivative (foreign exchange)"
    assert classes["TRS CITI 000001"] == "derivative (swap)"
    assert classes["CASH"] == "cash" and classes["OTHER PAYABLE & RECEIVABLES"] == "cash"
    assert classes["EXAMPLE APPLE INC"] == "equity"
    assert pd.isna(df.loc[0, "weight_pct"])


def test_globalx_empty_file_for_synthetic_fund():
    as_of, df = globalx.parse_holdings((FIX / "globalx_holdings_empty.csv").read_bytes())
    assert as_of is None and df.empty and list(df.columns) == HOLDING_COLUMNS


def test_globalx_holdings_uses_cache(tmp_path, monkeypatch):
    product = _globalx_products().iloc[0].to_dict()
    calls = []

    def fake_get(url):
        calls.append(url)
        return (FIX / "globalx_holdings.csv").read_bytes()

    monkeypatch.setattr(globalx, "_get", fake_get)
    ctx = FetchContext(cache_dir=str(tmp_path))
    first = globalx.holdings(product, ctx)
    second = globalx.holdings(product, ctx)
    assert len(calls) == 1 and len(list(tmp_path.iterdir())) == 1
    assert first.as_of == second.as_of == "2026-11-02"
    assert first.url == product["holdings_url"]
    assert "total return swap" in " ".join(first.notes)


def test_globalx_bad_file_is_not_cached(tmp_path, monkeypatch):
    product = _globalx_products().iloc[0].to_dict()
    monkeypatch.setattr(globalx, "_get", lambda url: b"<html>error</html>")
    with pytest.raises(ValueError):
        globalx.holdings(product, FetchContext(cache_dir=str(tmp_path)))
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------------ VanEck


def _vaneck_products() -> pd.DataFrame:
    panel = json.loads(_read("vaneck_panel.json"))
    fund_data = vaneck.parse_fund_data(json.loads(_read("vaneck_fund_data.json")))
    return vaneck.product_rows(panel, fund_data)


def test_vaneck_page_ids():
    assert vaneck.parse_panel_block_id(_read("vaneck_home.html")) == "281068"
    page_id, codes = vaneck.parse_listing_page(_read("vaneck_listing.html"))
    assert page_id == "166588" and codes == ["th", "cob", "dia"]


def test_vaneck_products_contract():
    df = _vaneck_products()
    assert list(df.columns) == PRODUCT_COLUMNS
    assert validate_products(df) == []
    assert len(df) == 5  # the row with an invalid ISIN is dropped
    by_isin = df.set_index("isin")
    smh = by_isin.loc["IE00BMC38736"]
    assert smh["ticker"] == "EXSM" and smh["currency"] == "USD" and smh["asset_class"] == "Equity"
    assert smh["holdings_url"] == "https://www.vaneck.com/DE/en/investments/example-semiconductor-etf/downloads/holdings/"
    assert by_isin.loc["NL0009690247", "currency"] == "EUR"
    # Mutual-fund share classes share a portfolio and its asset class, but have no holdings file.
    mf = df[df["portfolio_id"] == "example-bond"]
    assert len(mf) == 2 and mf["holdings_url"].isna().all()
    assert set(mf["asset_class"]) == {"Income"}
    assert by_isin.loc["IE00BYXQSD13", "currency"] == "EUR"  # "UCITS-EUR Hedged"
    assert by_isin.loc["DE000A28M8D0", "domicile"] == "DE"
    assert df["replication"].isna().all()  # not published in any list this adapter reads


def test_vaneck_equity_holdings():
    as_of, df = vaneck.parse_holdings(VANECK_EQUITY, "Equity")
    assert as_of == "2026-11-02"
    assert list(df.columns) == HOLDING_COLUMNS
    assert validate_holdings(df) == []
    assert df["weight_pct"].tolist() == [60.25, 29.50, 10.20, 0.05]  # "60.25%" -> 60.25, not rescaled
    assert df["isin"].tolist()[:2] == ["US8740391003", "US67066G1040"]
    assert pd.isna(df.loc[2, "isin"]) and df.loc[2, "asset_class"] == "equity"
    assert df.loc[3, "asset_class"] == "cash"


def test_vaneck_bond_holdings():
    as_of, df = vaneck.parse_holdings(VANECK_BOND, "Income")
    assert as_of == "2026-11-02"
    assert validate_holdings(df) == []
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    assert df["asset_class"].tolist() == ["debt", "debt", "cash"]
    assert df.loc[0, "country"] == "United Kingdom"


def test_vaneck_coin_etn_holdings():
    as_of, df = vaneck.parse_holdings(VANECK_COIN, "Digital Assets")
    assert df["weight_pct"].tolist() == [100.0]
    assert df.loc[0, "asset_class"] == "other" and pd.isna(df.loc[0, "isin"])


def test_vaneck_as_of_from_title_when_sheet_has_no_date():
    content = _xlsx("Sheet", [["All Holdings  11/02/2026"], [""], ["Number", "Holding Name", "ISIN", "% of Net Assets"],
                              [1, "X", "US8740391003", "100%"]])
    as_of, _ = vaneck.parse_holdings(content)
    assert as_of == "2026-11-02"  # MM/DD/YYYY


def test_vaneck_holdings_cache_and_missing_file(tmp_path, monkeypatch):
    products = _vaneck_products().set_index("isin", drop=False)
    calls = []

    class Resp:
        content = VANECK_EQUITY

    def fake_request(method, url, **kw):
        calls.append(url)
        return Resp()

    monkeypatch.setattr(vaneck, "_request", fake_request)
    ctx = FetchContext(cache_dir=str(tmp_path))
    product = products.loc["IE00BMC38736"].to_dict()
    got = vaneck.holdings(product, ctx)
    vaneck.holdings(product, ctx)
    assert len(calls) == 1 and got.url == product["holdings_url"] and got.as_of == "2026-11-02"
    with pytest.raises(LookupError):
        vaneck.holdings(products.loc["IE00BYXQSD13"].to_dict(), ctx)


# ------------------------------------------------------------------ live


@pytest.mark.network
def test_globalx_live_one_fund():
    product = {"portfolio_id": "sdiv", "asset_class": "Equity",
               "holdings_url": globalx.HOLDINGS_URL.format(slug="sdiv")}
    got = globalx.holdings(product)
    assert validate_holdings(got.holdings) == []
    assert got.as_of and len(got.holdings) > 50
    assert 95 < got.holdings["weight_pct"].sum() < 105


@pytest.mark.network
def test_vaneck_live_one_fund():
    url = vaneck.HOST + "/DE/en/investments/semiconductor-etf/downloads/holdings/"
    got = vaneck.holdings({"portfolio_id": "semiconductor-etf", "asset_class": "Equity", "holdings_url": url})
    assert validate_holdings(got.holdings) == []
    assert got.as_of and len(got.holdings) > 10
    assert 95 < got.holdings["weight_pct"].sum() < 105
