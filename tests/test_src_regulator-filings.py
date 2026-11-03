"""Regulator filings: CVM Brazil (CDA) and CNMV Spain (FONDCART/SOCCART)."""

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from openbasket.sources import cnmv_spain, cvm_brazil, edinet_japan
from openbasket.sources.issuers import (
    HOLDING_COLUMNS,
    PRODUCT_COLUMNS,
    discover,
    validate_holdings,
    validate_products,
)

FIX = Path(__file__).parent / "fixtures" / "regulator-filings"


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _cda_zip() -> bytes:
    # CVM publishes Latin-1; the fixtures are UTF-8 text, so re-encode them.
    return _zip({p.name: p.read_text("utf-8").encode("latin-1") for p in sorted((FIX / "cvm").glob("*.csv"))})


def _b3_zip() -> bytes:
    return _zip({p.name: p.read_bytes() for p in sorted((FIX / "b3").glob("*.TXT"))})


# -- Brazil -------------------------------------------------------------------


def test_month_listing():
    got = cvm_brazil.parse_month_listing((FIX / "cvm" / "listing.html").read_text())
    assert got == {"202606": "202610310635", "202607": "202610310117"}


def test_isin_listing():
    payload = {"geralPt": {"id": 103415, "dataGeracao": "2026-11-03T03:00:31-0300"}, "geralEn": {"id": 1}}
    assert cvm_brazil.parse_isin_listing(payload) == (103415, "2026-11-03T03:00:31-0300")


def test_isin_register():
    df = cvm_brazil.parse_isin_register(_b3_zip())
    assert set(df["isin"]) == {"BRAAAACTF008", "BRBBBBCTF000", "BRCCCCCTF002", "BRCCCCCTF010"}  # CTF only
    assert df.set_index("isin").at["BRBBBBCTF000", "cnpj"] == "22222222000122"


@pytest.fixture
def br_store(tmp_path, monkeypatch):
    cda = tmp_path / "cda_fi_202607.zip"
    cda.write_bytes(_cda_zip())
    reg = tmp_path / "b3_isin_20261103.zip"
    reg.write_bytes(_b3_zip())
    monkeypatch.setattr(cvm_brazil, "list_months", lambda: {"202607": "202610310117"})
    monkeypatch.setattr(cvm_brazil, "download_month", lambda month, raw, stamp=None: cda)
    monkeypatch.setattr(cvm_brazil, "download_isin_register", lambda raw: reg)
    store = cvm_brazil.CdaStore(tmp_path / "store", months=3)
    manifest = store.refresh()
    assert manifest["month"].tolist() == ["202607"]
    return store


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


def test_br_adapter_registered():
    a = discover()["cvm_brazil"]
    assert a.regions == ("BR",) and "ODbL" in a.licence and "CVM" in a.credit


# -- Spain --------------------------------------------------------------------


def _cnmv_zip() -> bytes:
    return _zip({p.name: p.read_bytes() for p in sorted((FIX / "cnmv").glob("*.xml"))})


def test_cnmv_page():
    got = cnmv_spain.parse_page((FIX / "cnmv" / "page.html").read_text("utf-8"))
    assert got == {"202605": "TOKENMAY", "202606": "TOKENJUN%2fX", "202607": "TOKENJUL"}
    assert cnmv_spain.latest_quarter(got) == "202606"


def test_cnmv_rejects_non_quarter_zip():
    with pytest.raises(ValueError, match="quarter-end"):
        cnmv_spain.read_zip(_zip({"FONDMENS_202605.xml": b"<FondMens/>"}))


@pytest.fixture
def es_store(tmp_path, monkeypatch):
    z = tmp_path / "cnmv_iic_202606.zip"
    z.write_bytes(_cnmv_zip())
    monkeypatch.setattr(cnmv_spain, "list_periods", lambda years=2: {"202606": "T", "202607": "U"})
    monkeypatch.setattr(cnmv_spain, "download_period", lambda period, token, raw: z)
    store = cnmv_spain.CnmvStore(tmp_path / "store")
    assert store.refresh()["period"] == "202606"
    return store


def test_cnmv_products(es_store):
    p = es_store.products()
    assert validate_products(p) == []
    assert p.set_index("isin")["portfolio_id"].to_dict() == {
        "ES0100000A08": "FI:9001:0", "ES0100000B07": "FI:9001:0", "ES0200000A06": "FI:9002:0"}
    assert p.set_index("isin").at["ES0200000A06", "asset_class"] == "ETF"


def test_cnmv_holdings(es_store):
    as_of, df, notes = es_store.holdings("FI:9001:0")
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


def test_cnmv_adapter_registered():
    a = discover()["cnmv_spain"]
    assert a.regions == ("ES",) and "CNMV" in a.credit


# -- network ------------------------------------------------------------------


@pytest.mark.network
def test_network_cvm_and_cnmv(tmp_path):
    """One real Brazilian ETF (BOVA11) from CVM and one real Spanish fund from CNMV."""
    br = cvm_brazil.CdaStore(tmp_path / "br", months=1)
    br.refresh()
    p = br.products().set_index("isin")
    _, _, df, _ = br.holdings(p.at["BRBOVACTF003", "portfolio_id"])
    assert validate_holdings(df) == []
    assert 98 < df["weight_pct"].sum() < 102
    assert df.loc[df["isin"].notna(), "weight_pct"].sum() > 90

    es = cnmv_spain.CnmvStore(tmp_path / "es")
    es.refresh()
    p = es.products()
    pid = p[p["asset_class"] == "ETF"]["portfolio_id"].iloc[0]
    _, df, _ = es.holdings(pid)
    assert validate_holdings(df) == []
    assert 95 < df["weight_pct"].sum() < 105


# -- Japan (EDINET, gated: client only) ---------------------------------------


def test_edinet_needs_key(monkeypatch):
    monkeypatch.delenv("EDINET_API_KEY", raising=False)
    with pytest.raises(edinet_japan.EdinetKeyMissing):
        edinet_japan.fund_reports("2026-10-30")


def test_edinet_document_list():
    payload = {
        "metadata": {"status": "200"},
        "results": [
            {"docID": "S100AAAA", "fundCode": "G00001", "docTypeCode": "120", "periodEnd": "2026-07-31",
             "filerName": "Fictive AM", "docDescription": "有価証券報告書（内国投資信託受益証券）"},
            {"docID": "S100BBBB", "fundCode": None, "docTypeCode": "120", "filerName": "Company"},
            {"docID": "S100CCCC", "fundCode": "G00002", "docTypeCode": "030", "filerName": "Fictive AM"},
        ],
    }
    df = edinet_japan.parse_document_list(payload)
    assert df["docID"].tolist() == ["S100AAAA"]
    assert df["report_kind"].iloc[0] == "securities report"
