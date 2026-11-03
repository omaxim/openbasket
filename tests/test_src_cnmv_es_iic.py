import io
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from openbasket import http
from openbasket.sources.issuers import (
    HOLDING_COLUMNS,
    PRODUCT_COLUMNS,
    FetchContext,
    discover,
    validate_holdings,
    validate_products,
)
from openbasket.sources.issuers import cnmv_es_iic as cnmv

FIXTURES = Path(__file__).parent / "fixtures" / "cnmv-es-iic"
QUARTER_FILES = [p for p in FIXTURES.glob("*_202606.xml")]
JUNE_URL = "https://www.cnmv.es/webservices/verdocumento/ver?e=JUNIOtoken%2fx"


def _zip(paths) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in paths:
            zf.write(p, p.name)
        zf.writestr("FONDCART.xsd", "<xsd/>")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _clear_memo():
    cnmv._MEMO.clear()
    yield
    cnmv._MEMO.clear()


@pytest.fixture
def quarter():
    return cnmv.parse_zip(_zip(QUARTER_FILES), JUNE_URL)


def _by_name(df):
    return {r["name"]: r for r in df.to_dict("records")}


def test_registered():
    adapter = discover()["cnmv-es-iic"]
    assert adapter.frequency == "quarterly"
    assert "CNMV" in adapter.credit
    assert adapter.regions == ("ES",)


def test_parse_listing():
    docs = cnmv.parse_listing((FIXTURES / "listing.html").read_text(encoding="utf-8"))
    assert [(d["year"], d["month"]) for d in docs] == [(2026, 8), (2026, 6), (2026, 3)]
    assert docs[1]["url"] == "https://www.cnmv.es/webservices/verdocumento/ver?e=JUNIOtoken%2fx"


def test_products(quarter):
    p = cnmv.build_products(quarter)
    assert validate_products(p) == []
    assert list(p.columns) == PRODUCT_COLUMNS
    # ES0900000030 has a bad check digit and is dropped.
    assert sorted(p["isin"]) == ["ES0900000011", "ES0900000029", "ES0900000037", "ES0900000045", "ES0900000052", "ES0900000060"]
    by = p.set_index("isin")
    assert by.loc["ES0900000029", "portfolio_id"] == by.loc["ES0900000037", "portfolio_id"] == "FI-102-0"
    assert by.loc["ES0900000029", "fund_name"] == "EJEMPLO RENTA FIJA, FI - CLASE A"
    assert by.loc["ES0900000037", "currency"] == "USD"
    assert by.loc["ES0900000052", "fund_name"] == "PARAGUAS EJEMPLO / GLOBAL"
    assert by.loc["ES0900000060", "portfolio_id"] == "SICAV-201-0"
    assert by.loc["ES0900000011", "replication"] == "physical"   # ETF, 98% in securities
    assert by.loc["ES0900000045", "replication"] == "swap"       # TRS nominal 80% of net assets
    assert pd.isna(by.loc["ES0900000029", "replication"])        # interest-rate swaps do not count
    assert set(p["domicile"]) == {"ES"}
    assert by.loc["ES0900000011", "product_page_url"] == "https://www.cnmv.es/portal/consultas/iic/fondo?isin=ES0900000011"
    assert set(p["holdings_url"]) == {JUNE_URL}


def test_holdings_weights_are_value_over_net_assets(quarter):
    df, notes = cnmv.build_holdings(quarter, "FI-101-0")
    assert validate_holdings(df) == []
    assert all(c in df.columns for c in HOLDING_COLUMNS)
    rows = _by_name(df)
    assert rows["ACCIONES|EMPRESA UNO SA"]["weight_pct"] == pytest.approx(60.0)
    assert rows["ACCIONES|EMPRESA UNO SA"]["isin"] == "ES01000000A9"
    assert rows["ACCIONES|EMPRESA UNO SA"]["asset_class"] == "equity"
    # Reverse repo: the code is the collateral's, so it is not a holding of that bond.
    repo = rows["REPO|BANCO EJEMPLO|2,00|2026-07-01 (colateral ES00000000C7)"]
    assert repo["isin"] is None and repo["asset_class"] == "cash" and repo["weight_pct"] == pytest.approx(8.0)
    assert rows["Liquidez (tesorería)"]["weight_pct"] == pytest.approx(1.5)
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    assert df.loc[df["isin"].notna(), "weight_pct"].sum() == pytest.approx(90.0)
    assert any("derivative positions" in n for n in notes)


def test_holdings_bad_codes_deposits_and_interest(quarter):
    df, _ = cnmv.build_holdings(quarter, "FI-102-0")
    rows = _by_name(df)
    assert rows["ACCIONES|SIN CODIGO"]["isin"] is None
    assert rows["ACCIONES|SIN CODIGO"]["currency"] == "CHF"
    assert rows["DEPOSITOS|CAJA EJEMPLO|2,15|2026-10-09"]["asset_class"] == "cash"
    assert rows["BONO|ESTADO EJEMPLO|1,00|2030-04-30"]["asset_class"] == "debt"
    assert rows["Intereses de la cartera de inversión"]["weight_pct"] == pytest.approx(1.0)
    assert df["weight_pct"].sum() == pytest.approx(100.0)


def test_holdings_fallback_to_class_net_assets(quarter):
    df, notes = cnmv.build_holdings(quarter, "FI-104-1")
    assert df["weight_pct"].tolist() == [pytest.approx(90.0)]
    assert df["asset_class"].tolist() == ["fund"]
    assert any("sum of the share classes" in n for n in notes)


def test_holdings_sicav(quarter):
    df, notes = cnmv.build_holdings(quarter, "SICAV-201-0")
    assert validate_holdings(df) == []
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    assert any("thousands of euros" in n for n in notes)


def test_holdings_unknown_compartment(quarter):
    df, notes = cnmv.build_holdings(quarter, "FI-999-0")
    assert df.empty and list(df.columns)[: len(HOLDING_COLUMNS)] == HOLDING_COLUMNS
    assert any("No net assets" in n for n in notes)


def test_non_quarter_zip_rejected():
    with pytest.raises(ValueError, match="FONDCART"):
        cnmv.parse_zip(_zip([FIXTURES / "FONDREGISTRO_202606.xml"]), "u")
    with pytest.raises(ValueError, match="not a ZIP"):
        cnmv.parse_zip(b"<html>error</html>", "u")


class _Resp:
    def __init__(self, content: bytes):
        self.content = content
        self.text = content.decode("utf-8", "replace")
        self.status_code = 200

    def raise_for_status(self):
        pass


def test_adapter_end_to_end_with_cache(monkeypatch, tmp_path):
    listing = (FIXTURES / "listing.html").read_bytes()
    june = _zip(QUARTER_FILES)
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if "descarga-informacion-individual" in url:
            return _Resp(listing)
        if "JUNIO" in url:
            return _Resp(june)
        raise AssertionError(f"unexpected request {url}")

    monkeypatch.setattr(http, "get", fake_get)
    p = cnmv.products(cache_dir=tmp_path)
    assert len(p) == 6
    cached = list(tmp_path.glob("cnmv-es-iic_*.zip"))
    assert len(cached) == 1 and date.today().isoformat() in cached[0].name

    cnmv._MEMO.clear()
    calls.clear()
    row = p[p["isin"] == "ES0900000029"].iloc[0].to_dict()
    got = cnmv.holdings(row, FetchContext(cache_dir=str(tmp_path)))
    assert calls == []  # served from the day's cache
    assert got.as_of == "2026-06-30"
    assert got.url == JUNE_URL
    assert got.holdings["weight_pct"].sum() == pytest.approx(100.0)
    assert validate_holdings(got.holdings) == []


def test_failed_parse_is_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(http, "get", lambda url, **kw: _Resp(b"not a zip"))
    with pytest.raises(ValueError):
        cnmv.load_quarter(JUNE_URL, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.network
def test_live_cnmv():
    p = cnmv.products()
    assert validate_products(p) == []
    assert len(p) > 2000
    row = p[p["isin"] == "ES0105336038"].iloc[0].to_dict()  # ACCION IBEX 35 ETF
    got = cnmv.holdings(row, FetchContext())
    assert validate_holdings(got.holdings) == []
    assert got.as_of and got.as_of.endswith(("-03-31", "-06-30", "-09-30", "-12-31"))
    assert got.holdings["weight_pct"].sum() == pytest.approx(100, abs=2)
    assert got.holdings.loc[got.holdings["isin"].notna(), "weight_pct"].sum() > 90
