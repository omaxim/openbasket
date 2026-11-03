"""US issuer adapters: Vanguard US, Avantis and First Trust.

Fixtures in ``tests/fixtures/us-issuers`` keep each issuer's real structure
and field names with invented funds, identifiers and weights.
"""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from openbasket.identifiers import cusip_to_isin
from openbasket.sources.issuers import (
    HOLDING_COLUMNS,
    PRODUCT_COLUMNS,
    FetchContext,
    discover,
    validate_holdings,
    validate_products,
)
from openbasket.sources.issuers import _us_common as common
from openbasket.sources.issuers import avantis, first_trust, vanguard_us

FIX = Path(__file__).parent / "fixtures" / "us-issuers"


def _json(name):
    return json.loads((FIX / name).read_text())


def _text(name):
    return (FIX / name).read_text()


# ---------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    "raw, expected",
    [("8.39%", 8.39), ("$1,234.50", 1234.5), ("($65.10)", -65.1), ("-0.47", -0.47), ("", None), ("-", None), (None, None), (3, 3.0)],
)
def test_number(raw, expected):
    assert common.number(raw) == expected


def test_sedol_check_digit():
    assert common.sedol_or_none("2046251") == "2046251"
    assert common.sedol_or_none("2046252") is None
    assert common.sedol_or_none("") is None


def test_cache_written_only_after_parse(tmp_path):
    calls = []

    def fetch():
        calls.append(1)
        return b"not json"

    with pytest.raises(ValueError):
        common.cached(tmp_path, "x_T", fetch, common.load_json)
    assert not list(tmp_path.iterdir())

    good = common.cached(tmp_path, "x_T", lambda: b'{"a": 1}', common.load_json)
    assert good == {"a": 1}
    files = list(tmp_path.iterdir())
    assert [f.name for f in files] == [f"x_T_{date.today().isoformat()}.json"]
    # Second call reads the cache, not the network.
    assert common.cached(tmp_path, "x_T", fetch, common.load_json) == {"a": 1}


def test_registered():
    reg = discover()
    for name, freq in (("vanguard_us", "monthly"), ("avantis", "daily"), ("first_trust", "daily")):
        a = reg[name]
        assert a.frequency == freq
        assert a.credit and a.licence and a.homepage.startswith("https://")


# ---------------------------------------------------------------- Vanguard


def test_vanguard_products():
    df = vanguard_us.parse_products(_json("vanguard_funddetail.json"))
    assert validate_products(df) == []
    assert list(df.columns[: len(PRODUCT_COLUMNS)]) == PRODUCT_COLUMNS
    assert len(df) == 3  # the external fund is dropped
    etf = df.set_index("ticker").loc["XETF"]
    adm = df.set_index("ticker").loc["XADMX"]
    assert etf.portfolio_id == adm.portfolio_id == "9002"
    assert etf["isin"] == cusip_to_isin(_json("vanguard_funddetail.json")["fund"]["entity"][0]["profile"]["cusip"])
    assert etf.replication == "physical"
    assert df.set_index("ticker").loc["XBND", "replication"] == "sampled"
    assert etf.product_page_url.endswith("/etfs/profile/xetf")
    assert adm.product_page_url.endswith("/mutual-funds/profile/xadmx")
    assert etf.asset_class == "Equity"


def test_vanguard_holdings():
    as_of, df = vanguard_us.parse_holdings(_json("vanguard_holdings.json"))
    assert as_of == "2026-09-30"
    assert validate_holdings(df) == []
    assert list(df.columns[: len(HOLDING_COLUMNS)]) == HOLDING_COLUMNS
    assert len(df) == 7
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    by_name = df.set_index("name")
    assert by_name.loc["Example Corp", "weight_pct"] == 60.12
    assert by_name.loc["Example Corp", "sedol"] == "2046251"
    assert by_name.loc["Overseas Holdings Ltd", "isin"] == "TW0002330008"
    assert pd.isna(by_name.loc["TINY RIGHTS", "weight_pct"])  # no percentage given: kept as NaN
    assert by_name.loc["TINY RIGHTS", "market_value"] == 10
    assert pd.isna(by_name.loc["MKTLIQ", "cusip"])  # internal code fails the check digit
    assert by_name.loc["MKTLIQ", "asset_class"] == "money market"
    assert by_name.loc["S&P500 EMINI FUT  Dec26", "asset_class"] == "derivative (futures)"
    assert by_name.loc["JPY/USD FWD 20261216", "asset_class"] == "derivative (foreign exchange)"
    assert by_name.loc["Example Treasury Note", "asset_class"] == "debt"
    assert by_name.loc["Example Treasury Note", "coupon_pct"] == 4.25


def test_vanguard_holdings_cached(tmp_path, monkeypatch):
    content = (FIX / "vanguard_holdings.json").read_bytes()

    class R:
        status_code = 200
        content = (FIX / "vanguard_holdings.json").read_bytes()

        def raise_for_status(self):
            pass

    seen = []
    monkeypatch.setattr(vanguard_us.http, "get", lambda url, **kw: seen.append(url) or R())
    product = {"ticker": "XETF", "holdings_url": vanguard_us.HOLDINGS_URL.format(ticker="XETF")}
    h = vanguard_us.holdings(product, FetchContext(cache_dir=str(tmp_path)))
    assert h.as_of == "2026-09-30" and h.url.endswith("XETF-AdditionalFundData")
    assert len(list(tmp_path.iterdir())) == 1
    assert list(tmp_path.iterdir())[0].read_bytes() == content
    vanguard_us.holdings(product, FetchContext(cache_dir=str(tmp_path)))
    assert len(seen) == 1


def test_vanguard_rejects_page_without_holdings():
    with pytest.raises(ValueError):
        vanguard_us.parse_holdings({"historicalPrice": {}})


# ---------------------------------------------------------------- Avantis


def test_devalue_decoding():
    values = [{"a": 1, "b": 2, "c": -1}, [3, 3], "x", {"n": 4}, 5.5]
    assert avantis.devalue(values) == {"a": [{"n": 5.5}, {"n": 5.5}], "b": "x", "c": None}


def test_avantis_products():
    df = avantis.parse_products(_json("avantis_list.json"))
    assert validate_products(df) == []
    assert len(df) == 3  # the fund with no CUSIP in its documents is left out
    t = df.set_index("ticker")
    assert t.loc["AVXX", "portfolio_id"] == "901"
    assert t.loc["AVXX", "holdings_url"].endswith("/en-us/avantis-example-equity-etf/__data.json")
    assert t.loc["AVXX", "product_page_url"].endswith("/avantis-investments/avantis-example-equity-etf/")
    assert t.loc["AVXMX", "fund_name"].endswith("Institutional Class")
    assert t.loc["AVXB", "asset_class"] == "Fixed Income"
    assert set(df["replication"]) == {"physical"}


def test_avantis_holdings():
    as_of, df, fund = avantis.parse_holdings(_json("avantis_fund.json"))
    assert as_of == "2026-11-02"
    assert validate_holdings(df) == []
    assert len(df) == 5
    assert df["weight_pct"].sum() == pytest.approx(100.05)
    by_name = df.set_index("name")
    assert by_name.loc["EXAMPLE CORP COMMON STOCK", "isin"].startswith("US")
    assert by_name.loc["EXAMPLE CORP COMMON STOCK", "country"] == "United States"
    assert by_name.loc["SSC GOVERNMENT MM GVMXX", "asset_class"] == "money market"
    assert by_name.loc["SP8L143T6 CDS USD R F 1.00000", "asset_class"] == "derivative (swap)"
    assert by_name.loc["US DOLLAR", "asset_class"] == "cash"
    assert fund["assetsInShareClass"] == "$10,000,000"


def test_avantis_redirect_is_an_error():
    with pytest.raises(ValueError, match="redirected"):
        avantis.parse_holdings(_json("avantis_redirect.json"))


# ---------------------------------------------------------------- First Trust


def test_first_trust_list_and_overview():
    funds = first_trust.parse_list(_text("first_trust_list.html"))
    assert funds["ticker"].tolist() == ["XFT", "XFB"]
    assert funds["fund_name"].tolist()[1] == "First Trust Example Bond & Income ETF"
    found = first_trust.parse_overview(_text("first_trust_summary.html"))
    assert found["isin"] == cusip_to_isin(found["cusip"])
    df = first_trust.build_products(funds, {"XFT": found})
    assert validate_products(df) == []
    assert df["ticker"].tolist() == ["XFT"]  # XFB has no ISIN yet
    assert df.loc[0, "holdings_url"].endswith("EtfHoldings.aspx?Ticker=XFT")


def test_first_trust_products_reuses_isin_file(tmp_path, monkeypatch):
    (tmp_path / first_trust.ISIN_FILE).write_text(json.dumps({"XFT": {"isin": "US0378331005", "cusip": "037833100"}}))

    class R:
        status_code = 200
        text = _text("first_trust_list.html")

        def raise_for_status(self):
            pass

    monkeypatch.setattr(first_trust.http, "get", lambda url, **kw: R())
    summary_calls = []
    monkeypatch.setattr(first_trust, "_fetch_overview", lambda t: summary_calls.append(t) or {})
    df = first_trust.products(cache_dir=tmp_path)
    assert df["ticker"].tolist() == ["XFT"]
    assert summary_calls == ["XFB"]  # only the fund not yet in the file is looked up


def test_first_trust_holdings():
    as_of, df = first_trust.parse_holdings(_text("first_trust_holdings.html"))
    assert as_of == "2026-11-02"
    assert validate_holdings(df) == []
    assert len(df) == 5
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    by_name = df.set_index("name")
    assert pd.isna(by_name.loc["Bad Code Holding", "cusip"])
    assert by_name.loc["Example Corp, Inc.", "asset_class"] == "equity"
    assert by_name.loc["Example Note, 4.65%, due 02/15/2028", "asset_class"] == "debt"
    assert by_name.loc["US Dollar", "asset_class"] == "cash"
    assert by_name.loc["US Dollar", "currency"] == "USD"
    assert by_name.loc["US Dollar", "market_value"] == -1000
    assert df["isin"].isna().all()  # First Trust gives CUSIPs only


# ---------------------------------------------------------------- live


def _check_live(adapter_mod, product, tmp_path):
    h = adapter_mod.holdings(product, FetchContext(cache_dir=str(tmp_path)))
    assert validate_holdings(h.holdings) == []
    assert len(h.holdings) > 50
    assert h.as_of and h.as_of <= date.today().isoformat()
    assert 90 < h.holdings["weight_pct"].sum() < 110
    return h


@pytest.mark.network
def test_live_vanguard_voo(tmp_path):
    df = vanguard_us.products()
    assert validate_products(df) == []
    voo = df.set_index("ticker").loc["VOO"].to_dict() | {"ticker": "VOO"}
    assert voo["isin"] == "US9229083632"
    h = _check_live(vanguard_us, voo, tmp_path)
    assert h.holdings["isin"].notna().mean() > 0.95


@pytest.mark.network
def test_live_avantis_avus(tmp_path):
    df = avantis.products()
    assert validate_products(df) == []
    avus = df.set_index("ticker").loc["AVUS"].to_dict() | {"ticker": "AVUS"}
    h = _check_live(avantis, avus, tmp_path)
    assert h.holdings["isin"].notna().mean() > 0.95


@pytest.mark.network
def test_live_first_trust_fdn(tmp_path):
    product = {"ticker": "FDN", "holdings_url": first_trust.HOLDINGS_URL.format(ticker="FDN")}
    h = first_trust.holdings(product, FetchContext(cache_dir=str(tmp_path)))
    assert validate_holdings(h.holdings) == []
    assert len(h.holdings) > 20
    assert h.holdings["cusip"].notna().mean() > 0.9
    assert 95 < h.holdings["weight_pct"].sum() < 105
    assert first_trust._fetch_overview("FDN")["isin"] == "US33733E3027"
