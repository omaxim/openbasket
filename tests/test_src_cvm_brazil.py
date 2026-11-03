"""CVM Brazil: monthly fund portfolio filings (CDA) with B3's ISIN register and instruments file."""

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from openbasket.sources import cvm_brazil, redistribution
from openbasket.sources.issuers import (
    HOLDING_COLUMNS,
    PRODUCT_COLUMNS,
    discover,
    validate_holdings,
    validate_products,
)

FIX = Path(__file__).parent / "fixtures" / "cvm-brazil"


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _cda_zip(folder: Path) -> bytes:
    # CVM publishes Latin-1; the fixtures are UTF-8 text, so re-encode them.
    return _zip({p.name: p.read_text("utf-8").encode("latin-1") for p in sorted(folder.glob("*.csv"))})


def _b3_zip(folder: Path) -> bytes:
    return _zip({p.name: p.read_bytes() for p in sorted(folder.glob("*.TXT"))})


def _store(tmp_path, monkeypatch, cda_folder, b3_folder, month, instruments=None) -> cvm_brazil.CdaStore:
    cda = tmp_path / f"cda_fi_{month}.zip"
    cda.write_bytes(_cda_zip(cda_folder))
    reg = tmp_path / "b3_isin_20261103.zip"
    reg.write_bytes(_b3_zip(b3_folder))
    monkeypatch.setattr(cvm_brazil, "list_months", lambda: {month: "202610310117"})
    monkeypatch.setattr(cvm_brazil, "download_month", lambda m, raw, stamp=None: cda)
    monkeypatch.setattr(cvm_brazil, "download_isin_register", lambda raw: reg)
    if instruments is None:
        def no_instruments(raw):
            raise ValueError("offline")
        monkeypatch.setattr(cvm_brazil, "download_instruments", no_instruments)
    else:
        ins = tmp_path / "b3_instruments_20261102.csv"
        ins.write_bytes(instruments.read_text("utf-8").encode("latin-1"))
        monkeypatch.setattr(cvm_brazil, "download_instruments", lambda raw: ins)
    store = cvm_brazil.CdaStore(tmp_path / "store", months=3)
    assert store.refresh()["month"].tolist() == [month]
    return store


@pytest.fixture
def br_store(tmp_path, monkeypatch):
    return _store(tmp_path, monkeypatch, FIX / "small", FIX / "small_b3", "202607")


@pytest.fixture
def etf_store(tmp_path, monkeypatch):
    return _store(tmp_path, monkeypatch, FIX / "cda_fi_202609", FIX / "b3", "202609", FIX / "instruments.csv")


# -- listings and registers ---------------------------------------------------


def test_month_listing():
    got = cvm_brazil.parse_month_listing((FIX / "small" / "listing.html").read_text())
    assert got == {"202606": "202610310635", "202607": "202610310117"}


def test_month_listing_as_a_table():
    got = cvm_brazil.parse_month_listing((FIX / "directory.html").read_text())
    assert got == {"202608": "202611030103", "202609": "202611030057"}


def test_isin_listing():
    payload = {"geralPt": {"id": 103415, "dataGeracao": "2026-11-03T03:00:31-0300"}, "geralEn": {"id": 1}}
    assert cvm_brazil.parse_isin_listing(payload) == (103415, "2026-11-03T03:00:31-0300")


def test_isin_register():
    df = cvm_brazil.parse_isin_register(_b3_zip(FIX / "small_b3"))
    assert set(df["isin"]) == {"BRAAAACTF008", "BRBBBBCTF000", "BRCCCCCTF002", "BRCCCCCTF010"}  # CTF only
    assert df.set_index("isin").at["BRBBBBCTF000", "cnpj"] == "22222222000122"


def test_parse_instruments():
    t = cvm_brazil.parse_instruments((FIX / "instruments.csv").read_text("utf-8").encode("latin-1")).set_index("ticker")
    assert t.at["EXAC11", "isin"] == "BRBOVACTF003" and t.at["EXAC11", "segment"] == "CASH"
    assert t.at["EXIB11", "isin"] == "BRIMABCTF004"  # fixed-income ETF, FORWARD segment
    assert "EXIBETF11H" not in t.index and "EXSPT11" not in t.index


# -- the small store: ordinary funds -----------------------------------------


def test_cda_positions_schema(br_store):
    pos = pd.read_parquet(br_store.parquet / "positions_202607.parquet")
    assert list(pos.columns) == cvm_brazil.POSITION_COLUMNS
    assert sorted(pos["block"].unique()) == ["BLC_1", "BLC_2", "BLC_4", "BLC_6", "BLC_8", "CONFID", "FIE"]
    nav = pd.read_parquet(br_store.parquet / "nav_202607.parquet").set_index("fund_cnpj")
    assert nav.at["33333333000133", "net_assets"] == 500.0  # structured fund: net assets on its rows
    # Individuals' CPF numbers and names are not kept.
    blc6 = pos[pos["block"] == "BLC_6"].iloc[0]
    assert pd.isna(blc6["issuer_cnpj"]) and blc6["name"] == "Individual issuer (name withheld)"
    assert not pos.astype(str).apply(lambda c: c.str.contains("000.000.000-00|PESSOA", regex=True)).any().any()


def test_br_products(br_store):
    p = br_store.products()
    assert validate_products(p) == []
    assert set(PRODUCT_COLUMNS) <= set(p.columns)
    assert set(p["isin"]) == {"BRAAAACTF008", "BRBBBBCTF000", "BRCCCCCTF002", "BRCCCCCTF010"}
    assert p.set_index("isin").at["BRCCCCCTF002", "portfolio_id"] == "33333333000133"
    assert p["ticker"].isna().all()  # no instruments file: no tickers, nothing else lost


def test_br_fund_of_funds(br_store):
    as_of, month, df, notes = br_store.holdings("11111111000111")
    assert (as_of, month) == ("2026-07-31", "202607")
    assert validate_holdings(df) == []
    assert set(HOLDING_COLUMNS) <= set(df.columns)
    assert df["weight_pct"].sum() == pytest.approx(100.0)  # payable is negative
    fund = df[df["asset_class"] == "fund"].iloc[0]
    assert fund["isin"] == "BRBBBBCTF000" and fund["isin_source"] == "b3_fund_cnpj"
    assert fund["weight_pct"] == pytest.approx(99.0)
    assert df.loc[df["tp_aplic"] == "Valores a pagar", "weight_pct"].iloc[0] == pytest.approx(-0.5)
    assert any("CDA" in n and "2026-07-31" in n for n in notes)


def test_br_direct_holdings(br_store):
    _, _, df, notes = br_store.holdings("22222222000122")
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    by = df.set_index("tp_aplic")
    assert by.at["Títulos Públicos", "isin"] == "BRSTNCLF1AA4"
    assert by.at["Títulos Públicos", "weight_pct"] == pytest.approx(40.0)
    repo = by.loc["Operações Compromissadas"]
    assert repo["isin"] is None and repo["asset_class"] == "repurchase agreement"
    assert "BRSTNCLF1AA4" in repo["name"]
    assert by.at["Ações", "isin"] == "BRZZZZACNOR3" and by.at["Ações", "ticker"] == "ZZZZ3"
    conf = df[df["block"] == "CONFID"].iloc[0]
    assert conf["isin"] is None and conf["weight_pct"] == pytest.approx(5.0)
    assert any("5.00% of net assets is confidential" in n for n in notes)


def test_br_index_fund_codes_map_to_isin(br_store):
    _, _, df, _ = br_store.holdings("33333333000133")
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    share = df[df["ticker"] == "ZZZZ3"].iloc[0]
    assert share["isin"] == "BRZZZZACNOR3" and share["isin_source"] == "cda_ticker"
    assert share["name"] == "ZETA SA ON NM"
    foreign = df[df["tp_aplic"] == "Investimento no Exterior"].iloc[0]
    assert foreign["isin"] is None and foreign["ticker"] is None  # internal code, not a ticker
    assert foreign["country"] == "US" and foreign["currency"] is None


def test_br_confidential_month_fallback(tmp_path):
    store = cvm_brazil.CdaStore(tmp_path, max_confidential_pct=10.0)
    store.parquet.mkdir(parents=True)
    for month, conf in (("202606", 0.0), ("202607", 60.0)):
        rows = pd.DataFrame(
            [{c: None for c in cvm_brazil.POSITION_COLUMNS} | {"month": month, "fund_cnpj": "1", "block": b,
              "tp_aplic": "Ações", "value": v} for b, v in (("BLC_4", 100.0 - conf), ("CONFID", conf)) if v]
        )
        rows.to_parquet(store.parquet / f"positions_{month}.parquet", index=False)
        pd.DataFrame([{"fund_cnpj": "1", "fund_type": "CLASSES - FIF", "fund_name": "X",
                       "as_of": pd.Timestamp(f"{month[:4]}-{month[4:]}-28"), "net_assets": 100.0}]).to_parquet(
            store.parquet / f"nav_{month}.parquet", index=False)
    assert store.choose_month("1") == ("202606", 100.0, 0.0)


# -- the ETF store: index funds, B3 tickers -----------------------------------


def test_etf_products(etf_store):
    p = etf_store.products()
    assert validate_products(p) == []
    by = p.set_index("isin")
    assert set(by.index) == {"BRBOVACTF003", "BRIMABCTF004", "BRIVVBCTF001"}
    assert by.at["BRBOVACTF003", "ticker"] == "EXAC11" and by.at["BRBOVACTF003", "asset_class"] == "ETF EQUITIES"
    assert by.at["BRBOVACTF003", "replication"] == "physical"
    assert by.at["BRIVVBCTF001", "replication"] == "swap"  # S&P 500 futures over government bonds
    assert by.at["BRIMABCTF004", "replication"] == "physical"  # bond ETF that adds up


def test_etf_equity_holdings(etf_store):
    _, _, df, _ = etf_store.holdings("11111111000111")
    assert validate_holdings(df) == []
    assert df["weight_pct"].sum() == pytest.approx(100.0)  # payables count as negative
    by_ticker = df.dropna(subset=["ticker"]).set_index("ticker")
    assert by_ticker.at["VALE3", "isin"] == "BRVALEACNOR0" and by_ticker.at["VALE3", "isin_source"] == "b3_ticker"
    assert by_ticker.at["VALE3", "name"] == "VALE S.A."
    assert by_ticker.at["VALE3", "weight_pct"] == pytest.approx(40.0)
    assert by_ticker.at["PETR4", "isin"] == "BRPETRACNPR6"
    # Ticker in DS_ATIVO, ISIN from another fund's filing.
    assert by_ticker.at["NATU3", "isin"] == "BRNATUACNOR6" and by_ticker.at["NATU3", "isin_source"] == "cda_ticker"
    fund = df[df["tp_aplic"] == "Cotas de Fundos"].iloc[0]
    assert fund["isin"] == "BRIMABCTF004" and fund["asset_class"] == "fund" and fund["fund_cnpj"] == "22222222000122"
    repo = df[df["tp_aplic"] == "Operações Compromissadas"].iloc[0]
    assert repo["isin"] is None and repo["asset_class"] == "repurchase agreement"
    fut = df[df["asset_class"] == "derivative (future)"].iloc[0]
    assert fut["weight_pct"] == pytest.approx(-0.1)  # as reported, not re-signed


def test_etf_bond_maps_selic(etf_store):
    _, _, df, _ = etf_store.holdings("22222222000122")
    bond = df[df["asset_class"] == "debt"].iloc[0]
    assert bond["isin"] == "BRSTNCNTB0O7" and bond["isin_source"] == "cda_selic"
    assert bond["weight_pct"] == pytest.approx(99.8)
    assert bond["name"].startswith("Título público SELIC 760199")


def test_offshore_line(etf_store):
    _, _, df, _ = etf_store.holdings("44444444000144")
    off = df[df["tp_aplic"] == "Investimento no Exterior"].iloc[0]
    assert off["isin"] is None and off["asset_class"] == "fund" and off["name"] == "ISHARES CORE SP500"
    assert off["country"] is None  # numeric CVM country code is not ISO


def test_offshore_ticker_and_isin_in_name():
    lines = pd.DataFrame([
        {"block": "FIE", "tp_aplic": "Investimento no Exterior", "tp_ativo": "Fundos Offshore", "code": "IAU",
         "name": "9000105", "value": 60.0},
        {"block": "FIE", "tp_aplic": "Investimento no Exterior", "tp_ativo": "Fundos Offshore", "code": "9000777",
         "name": "GALAXY BBG BITC ETF - KYG376981000 - NAV", "value": 40.0},
    ]).reindex(columns=cvm_brazil.POSITION_COLUMNS)
    df = cvm_brazil.fund_positions(lines, 100.0)
    gold, btc = df.iloc[0], df.iloc[1]
    assert gold["ticker"] == "IAU" and gold["name"] == "IAU" and gold["isin"] is None
    assert btc["ticker"] is None and btc["isin"] == "KYG376981000" and btc["isin_source"] == "name_text"


def test_adapter_registered():
    a = discover()["cvm_brazil"]
    assert a.regions == ("BR",) and "ODbL" in a.licence and "CVM" in a.credit and a.redistribution == "public"
    assert redistribution("cvm_brazil") == "public"
    assert "cvm" not in discover()


# -- network ------------------------------------------------------------------


@pytest.mark.network
def test_network_bova11(tmp_path):
    """One real Brazilian ETF (BOVA11) from CVM."""
    br = cvm_brazil.CdaStore(tmp_path / "br", months=1)
    br.refresh()
    p = br.products().set_index("isin")
    assert p.at["BRBOVACTF003", "ticker"] == "BOVA11"
    _, _, df, _ = br.holdings(p.at["BRBOVACTF003", "portfolio_id"])
    assert validate_holdings(df) == []
    assert 98 < df["weight_pct"].sum() < 102
    assert df.loc[df["isin"].notna(), "weight_pct"].sum() > 90
