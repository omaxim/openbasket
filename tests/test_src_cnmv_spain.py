"""CNMV Spain: quarterly portfolios of every Spanish fund (FI) and SICAV."""

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from openbasket.sources import cnmv_spain, redistribution
from openbasket.sources.issuers import (
    HOLDING_COLUMNS,
    PRODUCT_COLUMNS,
    discover,
    validate_holdings,
    validate_products,
)

SMALL = Path(__file__).parent / "fixtures" / "cnmv-spain" / "small"
FULL = Path(__file__).parent / "fixtures" / "cnmv-spain" / "full"
JUNE_URL = "https://www.cnmv.es/webservices/verdocumento/ver?e=JUNIOtoken%2fx"


def _zip(paths) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in paths:
            z.write(p, p.name)
        z.writestr("FONDCART.xsd", "<xsd/>")
    return buf.getvalue()


def _store(tmp_path, monkeypatch, files, periods=None) -> cnmv_spain.CnmvStore:
    z = tmp_path / "cnmv_iic_202606.zip"
    z.write_bytes(_zip(files))
    monkeypatch.setattr(cnmv_spain, "list_periods", lambda years=2: periods or {"202606": "JUNIOtoken%2fx", "202607": "U"})
    monkeypatch.setattr(cnmv_spain, "download_period", lambda period, token, raw: z)
    store = cnmv_spain.CnmvStore(tmp_path / "store")
    assert store.refresh()["period"] == "202606"
    return store


@pytest.fixture
def small(tmp_path, monkeypatch):
    return _store(tmp_path, monkeypatch, sorted(SMALL.glob("*.xml")))


@pytest.fixture
def full(tmp_path, monkeypatch):
    return _store(tmp_path, monkeypatch, sorted(FULL.glob("*_202606.xml")))


def _by_name(df):
    return {r["name"]: r for r in df.to_dict("records")}


# -- listing ------------------------------------------------------------------


def test_page():
    got = cnmv_spain.parse_page((SMALL / "page.html").read_text("utf-8"))
    assert got == {"202605": "TOKENMAY", "202606": "TOKENJUN%2fX", "202607": "TOKENJUL"}
    assert cnmv_spain.latest_quarter(got) == "202606"


def test_listing_with_link_ids_and_other_links():
    got = cnmv_spain.parse_page((FULL / "listing.html").read_text("utf-8"))
    assert got == {"202608": "AGOSTOtoken%2fx", "202606": "JUNIOtoken%2fx", "202603": "MARZOtoken%2fx"}
    assert cnmv_spain.quarter_periods(got) == ["202606", "202603"]


def test_rejects_non_quarter_zip():
    with pytest.raises(ValueError, match="quarter-end"):
        cnmv_spain.read_zip(_zip([FULL / "FONDREGISTRO_202606.xml"]))
    with pytest.raises(ValueError, match="not a ZIP"):
        cnmv_spain.read_zip(b"<html>error</html>")


def test_refresh_falls_back_to_the_quarter_before(tmp_path, monkeypatch):
    good = tmp_path / "good.zip"
    good.write_bytes(_zip(sorted(FULL.glob("*_202606.xml"))))
    bad = tmp_path / "bad.zip"
    bad.write_bytes(_zip([FULL / "FONDREGISTRO_202606.xml"]))
    monkeypatch.setattr(cnmv_spain, "list_periods", lambda years=2: {"202609": "S", "202606": "J"})
    monkeypatch.setattr(cnmv_spain, "download_period", lambda period, token, raw: bad if period == "202609" else good)
    store = cnmv_spain.CnmvStore(tmp_path / "store")
    assert store.refresh()["period"] == "202606"


# -- products -----------------------------------------------------------------


def test_products_small(small):
    p = small.products()
    assert validate_products(p) == []
    assert p.set_index("isin")["portfolio_id"].to_dict() == {
        "ES0100000A08": "FI:9001:0", "ES0100000B07": "FI:9001:0", "ES0200000A06": "FI:9002:0"}
    assert bool(p.set_index("isin").at["ES0200000A06", "etf"])


def test_products(full):
    p = full.products()
    assert validate_products(p) == []
    assert set(PRODUCT_COLUMNS) <= set(p.columns)
    # ES0900000030 has a bad check digit and is dropped.
    assert sorted(p["isin"]) == ["ES0900000011", "ES0900000029", "ES0900000037", "ES0900000045", "ES0900000052", "ES0900000060"]
    by = p.set_index("isin")
    assert by.loc["ES0900000029", "portfolio_id"] == by.loc["ES0900000037", "portfolio_id"] == "FI:102:0"
    assert by.loc["ES0900000029", "fund_name"] == "EJEMPLO RENTA FIJA, FI - CLASE A"
    assert by.loc["ES0900000037", "currency"] == "USD"  # share-class currency from FONDTRIM
    assert by.loc["ES0900000052", "fund_name"] == "PARAGUAS EJEMPLO / GLOBAL"
    assert by.loc["ES0900000060", "portfolio_id"] == "SICAV:201:0"
    assert by.loc["ES0900000011", "replication"] == "physical"   # ETF, 98% in securities
    assert by.loc["ES0900000045", "replication"] == "swap"       # TRS nominal 80% of net assets
    assert pd.isna(by.loc["ES0900000029", "replication"])        # interest-rate swaps do not count
    assert by.loc["ES0900000011", "asset_class"] == "IIC de Gestión Pasiva"
    assert set(p["domicile"]) == {"ES"}
    assert by.loc["ES0900000011", "product_page_url"] == "https://www.cnmv.es/portal/consultas/iic/fondo?isin=ES0900000011"
    assert set(p["holdings_url"]) == {JUNE_URL}


def test_swap_legs_are_counted_once():
    """CNMV files each swap among the rights and again among the obligations."""
    deri = pd.DataFrame([
        {"descripcion": "Otros Derechos", "subyacente": "IBOXX TRS", "instrumento": "Permutas financieras. Derechos a recibir", "importe": 40.0},
        {"descripcion": "Otras Obligaciones", "subyacente": "IBOXX TRS", "instrumento": "Permutas financieras. Obligaciones a pagar", "importe": 40.0},
    ])
    sides = cnmv_spain.derivative_sides(deri)
    assert sides == {"rights": 40.0, "obligations": 40.0, "swaps": 40.0}
    # 40% of net assets, not 80%: not a swap-based fund.
    assert cnmv_spain.replication(100.0, 95.0, deri, tracks_index=False) is None
    assert cnmv_spain.replication(100.0, 95.0, deri, tracks_index=True) == "physical"
    assert cnmv_spain.replication(70.0, 60.0, deri, tracks_index=False) == "swap"


# -- holdings -----------------------------------------------------------------


def test_holdings_small(small):
    as_of, df, notes = small.holdings("FI:9001:0")
    assert as_of == "2026-06-30"
    assert validate_holdings(df) == []
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    assert df.loc[df["isin"].notna(), "weight_pct"].sum() == pytest.approx(80.0)  # bond + share
    repo = df[df["asset_class"] == "repurchase agreement"].iloc[0]
    assert repo["isin"] is None and "ES0100000B07" in repo["name"]
    assert df[df["kind"] == "IIC"]["isin"].isna().all()  # invalid check digit dropped
    assert df[df["kind"] == "Liquidez"]["weight_pct"].iloc[0] == pytest.approx(4.5)
    assert any("quarterly portfolio" in n and "2026-06-30" in n for n in notes)
    assert any("derivatives are not included" in n for n in notes)


def test_holdings_weights_are_value_over_net_assets(full):
    as_of, df, notes = full.holdings("FI:101:0")
    assert validate_holdings(df) == []
    assert set(HOLDING_COLUMNS) <= set(df.columns)
    rows = _by_name(df)
    assert rows["ACCIONES|EMPRESA UNO SA"]["weight_pct"] == pytest.approx(60.0)
    assert rows["ACCIONES|EMPRESA UNO SA"]["isin"] == "ES01000000A9"
    assert rows["ACCIONES|EMPRESA UNO SA"]["asset_class"] == "equity"
    repo = df[df["asset_class"] == "repurchase agreement"].iloc[0]
    assert repo["isin"] is None and "ES00000000C7" in repo["name"] and repo["weight_pct"] == pytest.approx(8.0)
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    assert df.loc[df["isin"].notna(), "weight_pct"].sum() == pytest.approx(90.0)
    assert any("rights 3.00% and obligations 0.00%" in n for n in notes)


def test_holdings_bad_codes_deposits_and_interest(full):
    _, df, _ = full.holdings("FI:102:0")
    rows = _by_name(df)
    assert rows["ACCIONES|SIN CODIGO"]["isin"] is None
    assert rows["ACCIONES|SIN CODIGO"]["currency"] == "CHF"
    assert rows["DEPOSITOS|CAJA EJEMPLO|2,15|2026-10-09"]["asset_class"] == "cash"
    assert rows["BONO|ESTADO EJEMPLO|1,00|2030-04-30"]["asset_class"] == "debt"
    assert df.loc[df["kind"] == "InteresesCartera", "weight_pct"].iloc[0] == pytest.approx(1.0)
    assert df["weight_pct"].sum() == pytest.approx(100.0)


def test_holdings_fallback_to_class_net_assets(full):
    _, df, notes = full.holdings("FI:104:1")
    assert df["weight_pct"].tolist() == [pytest.approx(90.0)]
    assert df["asset_class"].tolist() == ["fund"]
    assert any("sum of the share classes" in n for n in notes)


def test_holdings_sicav(full):
    _, df, notes = full.holdings("SICAV:201:0")
    assert validate_holdings(df) == []
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    assert any("thousands of euros" in n for n in notes)


def test_holdings_unknown_compartment(full):
    with pytest.raises(LookupError):
        full.holdings("FI:999:0")


def test_adapter_registered():
    a = discover()["cnmv_spain"]
    assert a.regions == ("ES",) and "CNMV" in a.credit and a.redistribution == "public"
    assert redistribution("cnmv_spain") == "public"
    assert "cnmv-es-iic" not in discover()


# -- network ------------------------------------------------------------------


@pytest.mark.network
def test_network_cnmv(tmp_path):
    """One real Spanish ETF from CNMV."""
    es = cnmv_spain.CnmvStore(tmp_path / "es")
    es.refresh()
    p = es.products()
    assert validate_products(p) == [] and len(p) > 2000
    pid = p[p["etf"]]["portfolio_id"].iloc[0]
    as_of, df, _ = es.holdings(pid)
    assert validate_holdings(df) == []
    assert as_of.endswith(("-03-31", "-06-30", "-09-30", "-12-31"))
    assert 95 < df["weight_pct"].sum() < 105
