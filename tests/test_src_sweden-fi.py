"""Finansinspektionen Sweden: quarterly holdings of Swedish UCITS funds."""

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from openbasket.sources import sweden_fi as fi
from openbasket.sources.issuers import (
    HOLDING_COLUMNS,
    PRODUCT_COLUMNS,
    discover,
    validate_holdings,
    validate_products,
)
from openbasket.sources.issuers import sweden_fi as adapter_module

FIX = Path(__file__).parent / "fixtures" / "sweden-fi"
NAME = "Fondinnehav_2026Q2_2026-10-14 08.12.zip"


def _zip() -> bytes:
    # FI's entries use backslashes: <company>_<id>_<ts>\<fund>_<id>_VPFO_<quarter>_<ts>.xml
    files = {
        "Exempel Fonder AB_90001_2026-07-01 10.00\\Exempel Aktiefond_10001_VPFO_2026Q2_2026-07-01 10.00.xml":
            (FIX / "fund_equity.xml").read_bytes(),
        "Exempel Fonder AB_90001_2026-07-01 10.00\\Exempel Stängd Fond_10003_VPFO_2026Q2_2026-07-01 10.00.xml":
            (FIX / "fund_closed.xml").read_bytes(),
        "Nordea Funds Ab_90002_2026-07-02 10.00\\Exempel Räntefond_10002_VPFO_2026Q2_2026-07-02 10.00.xml":
            (FIX / "fund_bonds.xml").read_bytes(),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def store(tmp_path, monkeypatch):
    calls = []

    def fake_download(name, raw_dir):
        calls.append(name)
        dest = Path(raw_dir) / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_zip())
        return dest

    monkeypatch.setattr(fi, "list_quarters", lambda: fi.parse_index((FIX / "index.html").read_text("utf-8")))
    monkeypatch.setattr(fi, "download_quarter", fake_download)
    s = fi.FiStore(tmp_path / "store")
    s.calls = calls
    return s


def test_parse_index():
    got = fi.parse_index((FIX / "index.html").read_text("utf-8"))
    assert got == {
        "2026Q2": "Fondinnehav_2026Q2_2026-10-14 08.12.zip",
        "2026Q1": "Fondinnehav_2026Q1_2026-10-14 08.12.zip",
        "2023Q3": "Fondinnehav_2023Q3_2023-12-21 10.32.zip",
    }
    assert fi.download_url(got["2026Q2"]).endswith("filnamn=Fondinnehav_2026Q2_2026-10-14%2008.12.zip")


def test_parse_fund():
    fund, rows = fi.parse_fund((FIX / "fund_equity.xml").read_bytes())
    assert fund["as_of"] == "2026-06-30" and fund["quarter"] == "2026Q2"
    assert fund["fund_isin"] == "SE0000000010" and fund["fund_id"] == "10001"
    assert fund["manager"] == "Exempel Fonder AB" and fund["manager_lei"] == "000000000000000000X1"
    assert fund["net_assets_sek"] == 1_000_000 and fund["other_sek"] == -5000
    assert fund["share_classes"] == "Exempel Aktiefond A; Exempel Aktiefond B"
    assert fund["benchmark"] == "Exempelindex Sverige"
    assert len(rows) == 5
    bad = rows[-1]
    assert bad["isin_reported"] == "SE0000000029" and bad["isin"] is None  # check digit fails
    assert rows[3]["isin"] is None and rows[3]["lvf_asset_type"].startswith("Derivat")

    fund2, _ = fi.parse_fund((FIX / "fund_bonds.xml").read_bytes())
    assert fund2["fund_isin"] == "SE0000000036"  # whitespace stripped
    assert fund2["benchmark"] is None  # "Jämförelseindex saknas"


def test_read_zip_keeps_closed_funds_without_lines():
    frames = fi.read_zip(_zip())
    funds = frames["funds"].set_index("fund_id")
    assert set(funds.index) == {"10001", "10002", "10003"}
    assert funds.at["10003", "status"] == "Ej aktiv fond"
    assert frames["positions"].groupby("fund_id").size().to_dict() == {"10001": 5, "10002": 2}
    assert "\\" not in funds.at["10001", "entry"]


def test_refresh_and_products(store):
    manifest = store.refresh()
    assert manifest["quarter"] == "2026Q2" and manifest["file"] == NAME and manifest["funds"] == 3
    assert manifest["as_of"] == "2026-06-30"
    store.refresh()  # same file name on FI: nothing downloaded again
    assert store.calls == [NAME]

    p = store.products()
    assert validate_products(p) == []
    assert list(p["isin"]) == ["SE0000000010", "SE0000000036"]  # closed fund left out
    p = p.set_index("isin")
    assert p.at["SE0000000010", "asset_class"] == "Equity"
    assert p.at["SE0000000010", "replication"] is None  # has a derivative line
    assert p.at["SE0000000036", "asset_class"] == "Fixed Income"
    assert p.at["SE0000000036", "replication"] == "physical"
    assert p.at["SE0000000036", "bank_group"] == "Nordea"
    assert p.at["SE0000000010", "bank_group"] is None
    assert p.at["SE0000000036", "holdings_url"] == fi.download_url(NAME)
    assert p.at["SE0000000036", "domicile"] == "SE"


def test_refresh_replaces_republished_quarter(store, monkeypatch):
    store.refresh()
    newer = "Fondinnehav_2026Q2_2026-11-20 09.00.zip"
    monkeypatch.setattr(fi, "list_quarters", lambda: {"2026Q2": newer})
    assert store.refresh()["file"] == newer
    assert store.calls == [NAME, newer]
    assert [f.name for f in (store.root / "raw").iterdir()] == [newer]


def test_holdings_equity(store):
    store.refresh()
    as_of, df, notes, url = store.holdings("10001")
    assert as_of == "2026-06-30" and url == fi.download_url(NAME)
    assert validate_holdings(df) == []
    assert all(c in df.columns for c in HOLDING_COLUMNS)
    rep = df[~df["weight_derived"]]
    assert list(rep["weight_pct"]) == [60.0, 30.0, 8.0, 1.0, 0.0]  # as reported, not rescaled
    assert list(rep["asset_class"]) == ["equity", "equity", "fund", "derivative", "equity"]
    assert rep["isin"].tolist()[:3] == ["SE0000000028", "US0000000010", "LU0000000017"]
    assert rep["isin"].tolist()[3:] == [None, None]
    derived = df[df["weight_derived"]].set_index("asset_class")["weight_pct"].to_dict()
    assert derived == {"cash": 1.5, "other": -0.5}
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    assert any("regulatory filing" in n for n in notes)
    assert any("Exempel Aktiefond B" in n for n in notes)


def test_holdings_bonds_and_closed(store):
    store.refresh()
    _, df, _, _ = store.holdings("10002")
    assert list(df["asset_class"]) == ["debt", "debt", "cash"]  # nominal amount marks a bond
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    with pytest.raises(LookupError):
        store.holdings("10003")
    with pytest.raises(LookupError):
        store.holdings("99999")


def test_adapter_registered(store, monkeypatch):
    store.refresh()
    monkeypatch.setitem(adapter_module._stores, str(store.root), store)
    monkeypatch.setenv("OPENBASKET_SWEDEN_FI_DIR", str(store.root))
    ad = discover()["sweden_fi"]
    assert "fritt bearbetas och vidareutnyttjas" in ad.licence
    assert "Finansinspektionen" in ad.credit
    p = ad.products()
    assert list(p.columns[: len(PRODUCT_COLUMNS)]) == PRODUCT_COLUMNS
    h = ad.holdings(p.iloc[0].to_dict(), None)
    assert h.as_of == "2026-06-30" and len(h.holdings) == 7


def test_pipeline_nodes(store, monkeypatch):
    from openbasket.pipelines.sweden_fi import create_pipeline
    from openbasket.pipelines.sweden_fi.nodes import build_products, refresh_fi

    assert {n.name for n in create_pipeline().nodes} == {"refresh_fi_fondinnehav", "build_fi_products"}
    manifest = refresh_fi(str(store.root))
    assert isinstance(manifest, pd.DataFrame) and manifest.at[0, "quarter"] == "2026Q2"
    assert len(build_products(manifest, str(store.root))) == 2


@pytest.mark.network
def test_network_fi_fund(tmp_path):
    quarters = fi.list_quarters()
    assert len(quarters) >= 30 and min(quarters) == "2018Q4"
    s = fi.FiStore(tmp_path)
    manifest = s.refresh()
    p = s.products()
    assert validate_products(p) == [] and len(p) > 500
    banks = p[p["bank_group"].notna()]
    assert set(banks["bank_group"]) >= {"Swedbank", "SEB", "Handelsbanken", "Nordea"}
    row = banks.sort_values("net_assets_sek").iloc[-1]
    as_of, df, _, url = s.holdings(row["portfolio_id"])
    assert as_of == manifest["as_of"] and url == manifest["url"]
    assert validate_holdings(df) == []
    assert 95 < df["weight_pct"].sum() < 105
    assert df["isin"].notna().mean() > 0.5
