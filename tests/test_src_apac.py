"""Asia-Pacific issuer adapters: State Street APAC, Nomura NEXT FUNDS, KODEX,
Betashares, Vanguard Australia, iShares Australia and the Tracker Fund of HK.

Offline tests read the tiny synthetic fixtures in ``tests/fixtures/apac``
(real layouts, invented rows). Network tests fetch one real fund per adapter.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import pytest

from openbasket import http
from openbasket.sources.issuers import (
    FetchContext,
    discover,
    validate_holdings,
    validate_products,
)
from openbasket.sources.issuers import _apac, betashares, ishares_au, kodex, nomura, ssga_apac, trahk, vanguard_au

FIX = Path(__file__).parent / "fixtures" / "apac"


def fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def json(self):
        return json.loads(self.content.decode("utf-8-sig"))

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def fake_http(monkeypatch):
    """Route ``openbasket.http.request`` to fixtures by URL substring; record the URLs asked for."""
    routes: dict[str, bytes] = {}
    calls: list[str] = []

    def request(method, url, params=None, **kw):
        if params:
            url = f"{url}?{urlencode(params)}"
        calls.append(url)
        for key, content in routes.items():
            if key in url:
                return FakeResponse(content)
        return FakeResponse(b"not found", 404)

    monkeypatch.setattr(http, "request", request)
    monkeypatch.setattr(_apac, "parse_asx_list", lambda c, min_rows=1: _ORIG_PARSE_ASX(c, min_rows=1))
    _apac._asx.cache_clear()
    routes["asx.com.au"] = fixture("asx_isin.xls")
    yield routes, calls
    _apac._asx.cache_clear()


_ORIG_PARSE_ASX = _apac.parse_asx_list


def weights(df: pd.DataFrame) -> float:
    return round(float(df["weight_pct"].sum()), 6)


# ------------------------------------------------------------------ shared helpers


def test_registered():
    reg = discover()
    for name in ("ssga_apac", "nomura", "kodex", "betashares", "vanguard_au", "ishares_au", "trahk"):
        assert name in reg
        assert reg[name].credit and reg[name].licence
    assert reg["nomura"].frequency == "monthly"
    assert reg["vanguard_au"].frequency == "monthly"
    assert reg["kodex"].frequency == "daily"
    for name in ("ssga_apac", "nomura", "kodex", "betashares", "vanguard_au", "ishares_au", "trahk"):
        assert reg[name].redistribution == "local_only"
    assert "must not be reproduced or redistributed" in reg["ishares_au"].licence


def test_valid_isin_needs_a_country_prefix():
    from openbasket.identifiers import is_valid_isin

    assert _apac.valid_isin("KR7005930003") == "KR7005930003"
    assert _apac.valid_isin("XS2621539910") == "XS2621539910"
    # Passes the check digit, but "SW" is not a country: a KODEX swap pseudo-code.
    assert is_valid_isin("SWAP00000009")
    assert _apac.valid_isin("SWAP00000009") is None


def test_ssga_apac_reads_the_individual_investor_view():
    for site in ssga_apac.SITES:
        assert ssga_apac._finder_params(site)["role"] == "individual"


def test_korea_isin():
    assert _apac.korea_isin("005930") == "KR7005930003"
    assert _apac.korea_isin("069500") == "KR7069500007"
    assert _apac.korea_isin("0048J0") == "KR70048J0007"
    assert _apac.korea_isin("005935") is None  # preferred share
    assert _apac.korea_isin(None) is None


def test_sedol_and_asx_code():
    assert _apac.valid_sedol("6144690") == "6144690"
    assert _apac.valid_sedol("6144691") is None
    assert _apac.valid_sedol("CASH_AUD") is None
    assert _apac.asx_code("BHP AT") == "BHP"
    assert _apac.asx_code("AAPL UW") is None


def test_asx_list():
    df = _apac.parse_asx_list(fixture("asx_isin.xls"), min_rows=1)
    assert dict(zip(df["code"], df["isin"]))["BHP"] == "AU000000BHP4"
    assert "Z0000" not in set(df["code"])  # rows without an ISIN are dropped
    with pytest.raises(ValueError):
        _apac.parse_asx_list(fixture("asx_isin.xls"))  # 10 rows: looks truncated


def test_fetch_caches_only_after_parse(tmp_path, fake_http):
    routes, calls = fake_http
    routes["example.org/good"] = b"42"
    routes["example.org/bad"] = b"<html>blocked</html>"
    ctx = FetchContext(cache_dir=str(tmp_path))
    assert _apac.fetch("https://example.org/good", int, ctx=ctx, key="good", ext="txt") == 42
    assert _apac.fetch("https://example.org/good", int, ctx=ctx, key="good", ext="txt") == 42
    assert calls.count("https://example.org/good") == 1  # second read came from the cache
    with pytest.raises(ValueError):
        _apac.fetch("https://example.org/bad", int, ctx=ctx, key="bad", ext="txt")
    assert not list(tmp_path.glob("*bad*"))


# ------------------------------------------------------------------ State Street APAC


def test_ssga_apac_products(fake_http):
    routes, _ = fake_http
    routes["fundfinder?country=au"] = fixture("ssga_au_finder.json")
    routes["fundfinder"] = b'{"data": {"funds": {}}}'
    df = ssga_apac.products()
    assert validate_products(df) == []
    assert set(df["ticker"]) == {"STW", "SBHI"}  # SPY (US-domiciled) is left to the US module
    stw = df.set_index("ticker").loc["STW"]
    assert stw["isin"] == "AU000000STW9"  # from the ASX list, not in the keywords
    assert stw["holdings_url"].endswith("holdings-daily-au-en-stw.xlsx")
    assert stw["replication"] == "physical"


def test_ssga_apac_fund_page():
    isin, link = ssga_apac.parse_fund_page(fixture("ssga_fund_page.html").decode())
    assert isin == "SG1W45939194"
    assert link.endswith("holdings-daily-sg-en-es3.xlsx")


def test_ssga_apac_holdings_au():
    as_of, df = ssga_apac.parse_holdings(fixture("ssga_au_holdings.xlsx"))
    assert as_of == "2026-11-02"
    assert validate_holdings(df) == []
    assert len(df) == 5
    assert df.loc[0, "isin"] == "AU000000BHP4" and df.loc[0, "sedol"] == "6144690"
    assert df.loc[2, "isin"] is None  # bad check digit
    assert df.loc[3, "asset_class"] == "cash"
    assert df.loc[4, "asset_class"] == "derivative (futures)" and pd.isna(df.loc[4, "weight_pct"])
    assert weights(df) == 100.0


def test_ssga_apac_holdings_jp(tmp_path, fake_http):
    routes, calls = fake_http
    routes["holdings-daily-jp-ja-449a"] = fixture("ssga_jp_holdings.xlsx")
    product = {"portfolio_id": "jp:449A", "holdings_url": ssga_apac.HOLDINGS_BASE + "holdings-daily-jp-ja-449a.xlsx"}
    h = ssga_apac.holdings(product, FetchContext(cache_dir=str(tmp_path)))
    assert h.as_of == "2026-10-30"
    assert validate_holdings(h.holdings) == []
    assert h.holdings["isin"].tolist()[:2] == ["US67066G1040", "US0378331005"]
    assert h.holdings.loc[2, "asset_class"] == "derivative (foreign exchange)"
    assert weights(h.holdings) == 99.25
    assert len(list(tmp_path.iterdir())) == 1


# ------------------------------------------------------------------ Nomura


def test_nomura_products(fake_http):
    routes, _ = fake_http
    routes["monthly_holdings/"] = fixture("nomura_list.html")
    routes["lineup/1321/"] = fixture("nomura_fund.html")
    routes["lineup/1570/"] = fixture("nomura_fund.html").replace(b"JP3027650005", b"JP3047460005")
    df = nomura.products()
    assert validate_products(df) == []
    assert df["isin"].tolist() == ["JP3027650005", "JP3047460005"]
    lev = df.set_index("ticker").loc["1570"]
    assert lev["asset_class"] == "Leveraged / Inverse" and pd.isna(lev["replication"])
    assert df.set_index("ticker").loc["1321", "holdings_url"] == "https://www.nomura-am.co.jp/fund/monthly_holdings/1321_brd_data.xlsx"


def test_nomura_holdings_fraction_to_percent():
    as_of, fund_isin, df = nomura.parse_holdings(fixture("nomura_holdings.xlsx"))
    assert as_of == "2026-10-30" and fund_isin == "JP3027650005"
    assert validate_holdings(df) == []
    assert len(df) == 3  # total and net-asset rows are dropped
    assert df.loc[0, "weight_pct"] == pytest.approx(50.0)
    assert df.loc[0, "name"] == "ADVANTEST CORPORATION" and df.loc[0, "ticker"] == "6857"
    assert df.loc[2, "asset_class"] == "derivative (futures)" and df.loc[2, "isin"] is None
    assert weights(df) == pytest.approx(99.5)


# ------------------------------------------------------------------ KODEX


def test_kodex_products(fake_http):
    routes, _ = fake_http
    routes["kodex/product.do"] = fixture("kodex_list.json")
    df = kodex.products()
    assert validate_products(df) == []
    by = df.set_index("ticker")
    assert by.loc["069500", "isin"] == "KR7069500007"
    assert by.loc["459580", "replication"] == "swap"  # (합성) = synthetic
    assert pd.isna(by.loc["122630", "replication"])
    assert by.loc["069500", "portfolio_id"] == "2ETF01"


def test_kodex_holdings(tmp_path, fake_http):
    routes, _ = fake_http
    routes["product-pdf/2ETF01"] = fixture("kodex_pdf.json")
    routes["excel_pdf.do"] = fixture("kodex_pdf.xls")
    h = kodex.holdings({"portfolio_id": "2ETF01", "replication": "physical"}, FetchContext(cache_dir=str(tmp_path)))
    df = h.holdings
    assert h.as_of == "2026-11-03"
    assert h.url.endswith("excel_pdf.do?fId=2ETF01&gijunYMD=20261103")
    assert validate_holdings(df) == []
    assert df.loc[0, "asset_class"] == "cash" and df.loc[0, "isin"] is None  # KRD pseudo-code
    assert df.loc[1, "isin"] == "KR7005930003" and df.loc[1, "weight_pct"] == pytest.approx(60.0)
    assert df.loc[3, "asset_class"] == "derivative (futures)"
    assert weights(df) == pytest.approx(100.0)


def _kodex_xlsx(rows) -> bytes:
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["KODEX EXAMPLE CD RATE ACTIVE (SYNTHETIC)"])
    ws.append(["2026/11/03"])
    ws.append(["번호", "종목명", "ISIN", "종목코드", "수량", "비중(%)", "평가금액(원)"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_kodex_swap_pseudo_codes_are_not_isins(tmp_path, fake_http):
    routes, _ = fake_http
    routes["product-pdf/2ETF99"] = fixture("kodex_pdf.json")
    routes["excel_pdf.do"] = _kodex_xlsx([
        [1, "스왑(예시증권)", "SWAP00000009", None, 1, 0.0817, 1],
        [2, "스왑(예시증권2)", "SWAP00000014", None, 1, 0.05, 1],
        [3, "예시은행 CD", "KR7005930003", None, 1, 0.8183, 1],
        [4, "원화예금", "KRD010010001", None, 1, 0.05, 1],
    ])
    h = kodex.holdings({"portfolio_id": "2ETF99", "replication": "swap"}, FetchContext(cache_dir=str(tmp_path)))
    df = h.holdings
    assert validate_holdings(df) == []
    assert df.loc[0, "isin"] is None and df.loc[0, "ticker"] == "SWAP00000009"
    assert df.loc[0, "asset_class"] == "derivative (swap)"
    assert df.loc[1, "isin"] is None
    assert df.loc[2, "isin"] == "KR7005930003"
    assert df.loc[2, "asset_class"] == "collateral (substitute basket)"
    assert df.loc[3, "asset_class"] == "cash"
    assert any("substitute basket" in n for n in h.notes)


# ------------------------------------------------------------------ Betashares


def test_betashares_products(fake_http):
    routes, _ = fake_http
    routes["fund-sitemap.xml"] = fixture("betashares_sitemap.xml")
    df = betashares.products()
    assert validate_products(df) == []
    assert set(df["ticker"]) == {"A200", "BBOZ"}  # A20BSK is a creation basket, not a fund
    by = df.set_index("ticker")
    assert by.loc["A200", "product_page_url"] == "https://www.betashares.com.au/fund/australia-200-etf/"
    assert by.loc["A200", "holdings_url"].endswith("fund_holdings_csv&ticker=A200")
    assert pd.isna(by.loc["BBOZ", "replication"])  # geared bear fund


def test_betashares_holdings():
    as_of, df = betashares.parse_holdings(fixture("betashares_holdings.csv"), {"BHP": "AU000000BHP4"})
    assert as_of == "2026-11-02"
    assert validate_holdings(df) == []
    assert df["isin"].tolist()[:2] == ["AU000000BHP4", None]  # only ASX lines are resolved
    assert df.loc[1, "ticker"] == "AAPL UW"
    assert df.loc[2, "asset_class"] == "cash"
    assert df.loc[3, "asset_class"] == "derivative (futures)"
    assert weights(df) == 100.0


# ------------------------------------------------------------------ Vanguard Australia


def test_vanguard_au_products(fake_http):
    routes, _ = fake_http
    routes["funds/etf"] = fixture("vanguard_au_funds.json")
    df = vanguard_au.products()
    assert validate_products(df) == []
    by = df.set_index("ticker")
    assert set(by.index) == {"VAS", "VEU"}  # the managed fund is not an ETF
    assert by.loc["VAS", "isin"] == "AU000000VAS1" and by.loc["VAS", "domicile"] == "AU"
    assert by.loc["VEU", "domicile"] == "US"
    assert by.loc["VAS", "holdings_url"].endswith("holdings-download/8205")


def test_vanguard_au_holdings():
    as_of, df = vanguard_au.parse_holdings(fixture("vanguard_au_holdings.json"), {"BHP": "AU000000BHP4", "ACGB": "AU000000XXX0"})
    assert as_of == "2026-09-30"
    assert validate_holdings(df) == []
    assert df["isin"].tolist() == ["AU000000BHP4", None, None]  # US ticker and bond stay unresolved
    assert df.loc[2, "asset_class"] == "debt"
    assert weights(df) == 99.75


# ------------------------------------------------------------------ iShares Australia


def test_ishares_au_products(fake_http):
    routes, _ = fake_http
    routes["product-sitemap.xml"] = fixture("ishares_au_sitemap.xml")
    df = ishares_au.products()
    assert validate_products(df) == []
    by = df.set_index("ticker")
    assert by.loc["IOZ", "portfolio_id"] == "251852"
    # the hedged ASX name matches the hedged page, never the unhedged core fund
    assert by.loc["IHVV", "portfolio_id"] == "271027"


def test_ishares_au_holdings(tmp_path, fake_http):
    routes, _ = fake_http
    routes["fileType=csv"] = fixture("ishares_au_holdings.csv")
    routes["fileType=json"] = fixture("ishares_au_holdings.json")
    h = ishares_au.holdings({"portfolio_id": "251852", "ticker": "IOZ"}, FetchContext(cache_dir=str(tmp_path)))
    df = h.holdings
    assert h.as_of == "2026-11-02"
    assert validate_holdings(df) == []
    assert df.loc[0, "isin"] == "AU000000BHP4" and df.loc[0, "sedol"] == "6144690"
    assert df.loc[0, "cusip"] == "S61446902"  # a CINS number, kept: it passes the CUSIP check digit
    assert df.loc[1, "cusip"] == "464287200"
    assert df.loc[3, "cusip"] is None and df.loc[3, "asset_class"] == "cash"
    assert df.loc[0, "weight_pct"] == pytest.approx(60.00123)  # the five-decimal raw value
    assert df.loc[2, "asset_class"] == "derivative (futures)"
    assert weights(df) == pytest.approx(100.0)


def test_ishares_au_match_rejects_wrong_hedge():
    pages = ishares_au.parse_sitemap(fixture("ishares_au_sitemap.xml").decode())
    assert ishares_au.match("ISHARES S&P 500 AUD HEDGED ETF", pages) == "271027"
    assert ishares_au.match("ISHARES SOMETHING ELSE ENTIRELY", pages) is None


# ------------------------------------------------------------------ Tracker Fund of Hong Kong


def test_trahk():
    df = trahk.products()
    assert validate_products(df) == [] and df.loc[0, "isin"] == "HK2800008867"
    as_of, h = trahk.parse_holdings(fixture("trahk_holdings.csv"))
    assert as_of == "2026-11-03"
    assert validate_holdings(h) == []
    assert h["isin"].tolist() == ["KYG017191142", "GB0005405286", None]
    assert h.loc[2, "asset_class"] == "cash"
    assert weights(h) == 100.0


# ------------------------------------------------------------------ network

NETWORK_FUNDS = {
    "ssga_apac": {"portfolio_id": "au:STW", "holdings_url": ssga_apac.HOLDINGS_BASE + "holdings-daily-au-en-stw.xlsx"},
    "nomura": {"portfolio_id": "1321", "holdings_url": "https://www.nomura-am.co.jp/fund/monthly_holdings/1321_brd_data.xlsx"},
    "kodex": {"portfolio_id": "2ETF01", "replication": "physical"},
    "betashares": {"portfolio_id": "A200", "holdings_url": betashares.HOLDINGS_URL.format(ticker="A200")},
    "vanguard_au": {"portfolio_id": "8205", "holdings_url": vanguard_au.HOLDINGS_URL.format(port_id="8205")},
    "ishares_au": {"portfolio_id": "251852", "ticker": "IOZ"},
    "trahk": {"portfolio_id": "2800"},
}


@pytest.mark.network
@pytest.mark.parametrize("name", sorted(NETWORK_FUNDS))
def test_network_one_real_fund(name, tmp_path):
    adapter = discover()[name]
    h = adapter.holdings(NETWORK_FUNDS[name], FetchContext(cache_dir=str(tmp_path)))
    df = h.holdings
    assert validate_holdings(df) == []
    assert len(df) > 50
    assert h.as_of and len(h.as_of) == 10
    assert 90 < df["weight_pct"].sum() < 110
    assert df["isin"].notna().any()
