"""Swiss bank fund registry: bank attribution, GLEIF, FIRDS, SIX and FINMA parsers, merge and coverage."""

import io
import json
import zipfile
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from openbasket.sources import swiss_banks, swiss_registry as reg

FIX = Path(__file__).parent / "fixtures" / "swiss"


def _zip(name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, data)
    return buf.getvalue()


def _xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# -- attribution --------------------------------------------------------------


@pytest.mark.parametrize(
    "name, umbrella, manager, lei, bank",
    [
        ("Einkommen (CHF)", "BKB Anlagelösung", "UBS Fund Management (Switzerland) AG", None, "bkb"),
        ("MI BANK (CH) FONDS INST-PACIFICSTOCK", None, None, None, "migros"),
        ("Bonds USD Aggregate", "UBS (CH) Index Fund", None, None, "ubs"),
        ("Zürcher Kantonalbank Gold ETF", None, None, None, "zkb"),
        ("Glarner Kantonalbank Strategie", None, None, None, "other_kb"),
        ("Hallwyl Institutional Fund", None, "UBS Fund Management (Switzerland) AG", None, "ubs"),
        ("GLOBAL EQUITY", None, None, "529900LO1T9ADP03SQ41", "vontobel"),
        ("Some Third Party Fund", None, "Carne Global Fund Managers (Schweiz) AG", None, None),
        ("Pictet AI Enhanced World Equity", None, None, None, "pictet"),
    ],
)
def test_attribute(name, umbrella, manager, lei, bank):
    assert swiss_banks.attribute(name, umbrella, manager, lei) == bank


def test_bank_catalogue_is_consistent():
    keys = [b.key for b in swiss_banks.BANKS]
    assert len(keys) == len(set(keys))
    assert all(b.route in swiss_banks.ROUTES for b in swiss_banks.BANKS)
    leis = [lei for b in swiss_banks.BANKS for lei in b.manager_leis]
    assert len(leis) == len(set(leis)) and all(len(lei) == 20 for lei in leis)


# -- GLEIF / FIRDS ------------------------------------------------------------


def test_parse_managed_funds_and_isin_lei():
    payload = json.loads((FIX / "gleif_managed_funds.json").read_text())
    funds = reg.parse_managed_funds([payload], "529900LO1T9ADP03SQ41")
    assert [f["name"] for f in funds] == ["MTX EMERGING MARKETS LEADERS", "EINKOMMEN (CHF)", "OLD FUND"]
    csv = b"LEI,ISIN\n549300TESTFUND000A01,LU0571085413\n549300TESTFUND000A01,LU0571085330\nOTHERLEI,US0378331005\n"
    pairs = reg.parse_isin_lei(_zip("lei-isin.csv", csv), {f["fund_lei"] for f in funds})
    assert sorted(pairs["isin"]) == ["LU0571085330", "LU0571085413"]


def test_parse_firds_funds():
    ns = "urn:iso:std:iso:20022:tech:xsd:auth.017.001.02"
    xml = (f'<BizData><Pyld><Document xmlns="{ns}"><FinInstrmRptgRefDataRpt>'
           "<RefData><FinInstrmGnlAttrbts><Id>LU0571085413</Id><FullNm>Vontobel Fd.-mtx EM Leaders Actions Nom. A</FullNm>"
           "<ClssfctnTp>CIOGEU</ClssfctnTp><NtnlCcy>USD</NtnlCcy></FinInstrmGnlAttrbts><Issr>549300TESTFUND000A01</Issr></RefData>"
           "<RefData><FinInstrmGnlAttrbts><Id>IE00B5BMR087</Id><FullNm>Other</FullNm></FinInstrmGnlAttrbts><Issr>OTHER</Issr></RefData>"
           "</FinInstrmRptgRefDataRpt></Document></Pyld></BizData>").encode()
    df = reg.parse_firds_funds(_zip("FULINS_C.xml", xml), {"549300TESTFUND000A01"})
    assert df.to_dict("records") == [{"isin": "LU0571085413", "firds_name": "Vontobel Fd.-mtx EM Leaders Actions Nom. A",
                                      "cfi": "CIOGEU", "currency": "USD", "fund_lei": "549300TESTFUND000A01"}]


def test_gleif_rows_drop_inactive_and_attribute():
    payload = json.loads((FIX / "gleif_managed_funds.json").read_text())
    funds = reg.parse_managed_funds([payload], "529900LO1T9ADP03SQ41")
    isin_lei = pd.DataFrame({"fund_lei": ["549300TESTFUND000A01"], "isin": ["LU0571085413"]})
    firds = pd.DataFrame(columns=["isin", "firds_name", "cfi", "currency", "fund_lei"])
    rows = reg.gleif_rows(funds, isin_lei, firds, "2026-11-03", None)
    assert list(rows.columns) == reg.REGISTRY_COLUMNS
    assert "OLD FUND" not in set(rows["name"])
    lu = rows[rows["isin"] == "LU0571085413"].iloc[0]
    assert (lu["bank"], lu["source"], lu["redistribution"], lu["domicile"]) == ("vontobel", "gleif", "public", "LU")


# -- SIX / FINMA --------------------------------------------------------------


def test_six_rows_are_local_only():
    six, as_of = reg.parse_six((FIX / "six_funds.json").read_bytes())
    assert as_of == "2026-11-03"
    rows = reg.six_rows(six, as_of)
    assert set(rows["bank"]) == {"ubs", "pictet", "zkb"}
    assert (rows["redistribution"] == "local_only").all()
    assert rows.loc[rows["isin"] == "LU0950668870", "replication"].item() == "swap"
    assert rows["isin"].is_unique


def test_finma_parsers_and_link():
    ch = _xlsx([
        ["Approved Swiss collective investment schemes"],
        ["Open-ended collective investment schemes (Art. 8 CISA)"],
        ["Name of individual fund or sub-fund", "Name of the umbrella", "FINMA-ID", None, None, "Fund management company", "Custodian bank",
         None, None, "contractual", None, "SICAV", "SICAF", "KmGK", "Securities funds", "Real estate funds", "Other funds for traditional investments",
         "Other funds for alternative investments", "Qualified investors"],
        ["Einkommen (CHF)", "BKB Anlagelösung", "F01067296", None, None, "UBS Fund Management (Switzerland) AG", "UBS Switzerland AG",
         None, None, "X", None, " ", " ", " ", " ", " ", "X", " ", " "],
        ["Third Party Fund", None, "F00000001", None, None, "Carne Global Fund Managers (Schweiz) AG", "Bank X", None, None, "X"],
    ])
    fo = _xlsx([
        ["Foreign collective investment schemes authorised by FINMA for offering in Switzerland"],
        ["Name of individual fund or sub-fund", None, "Name of the umbrella", None, "FINMA-ID", None, "Country", None, "Representatives",
         None, None, None, None, "UCITS", "NON-UCITS", "ALT"],
        ["mtx Emerging Markets Leaders", None, "Vontobel Fund", None, "F00012345", None, "Luxemburg", None, "Vontobel Fonds Services AG",
         None, None, None, None, "X", " ", " "],
    ])
    c, f = reg.parse_finma_ch(ch), reg.parse_finma_foreign(fo)
    assert c.loc[0, ["legal_form", "fund_type", "section"]].tolist() == ["contractual", "other traditional", "open-ended"]
    assert f.loc[0, ["domicile", "fund_type", "umbrella"]].tolist() == ["LU", "UCITS", "Vontobel Fund"]
    finma = reg.finma_rows(c, f, "2026-11-03")
    assert set(finma["bank"]) == {"bkb", "vontobel"} and (finma["redistribution"] == "local_only").all()
    # The UBS-run BKB sub-fund links to its GLEIF record and is attributed to BKB through the umbrella.
    payload = json.loads((FIX / "gleif_managed_funds.json").read_text())
    funds = reg.parse_managed_funds([payload], "5493003QNP1E68GGMZ05")
    isin_lei = pd.DataFrame({"fund_lei": ["549300TESTFUND000B02"], "isin": ["CH0023406470"]})
    g = reg.gleif_rows(funds, isin_lei, pd.DataFrame(columns=["isin", "firds_name", "cfi", "currency", "fund_lei"]), "2026-11-03", None)
    six = reg.six_rows(*reg.parse_six((FIX / "six_funds.json").read_bytes()))
    merged = reg.merge_registry(g, six, finma)
    row = merged[merged["isin"] == "CH0023406470"].iloc[0]
    assert (row["bank"], row["finma_id"], row["umbrella"], row["attributed_by"]) == ("bkb", "F01067296", "BKB Anlagelösung", "brand")
    assert merged["isin"].dropna().is_unique
    # FINMA rows that did not link stay as name-only rows.
    assert "F00012345" in set(merged["finma_id"])
    cov = reg.coverage(merged)
    assert cov.set_index("bank").loc["bkb", "share_classes_with_isin"] == 1


# -- network ------------------------------------------------------------------


@pytest.mark.network
def test_network_gleif_managed_funds(tmp_path):
    funds = reg.gleif_managed_funds("5067005S078K436U6F95", tmp_path)  # LUKB Expert Fondsleitung AG
    assert len(funds) >= 5 and all(f["jurisdiction"] == "CH" for f in funds)


@pytest.mark.network
def test_network_six_and_finma(tmp_path):
    six, as_of = reg.six_funds(tmp_path)
    assert len(six) > 1000 and as_of
    ch, foreign = reg.finma_lists(tmp_path)
    assert len(ch) > 1000 and len(foreign) > 1000


@pytest.mark.parametrize("status, allowed", [(400, True), (403, True), (404, True), (429, False), (503, False)])
def test_robots_status_follows_rfc_9309(monkeypatch, status, allowed):
    import requests

    from openbasket import http
    from openbasket.sources import swiss_fetch

    def fake_get(url, **kw):
        r = requests.Response()
        r.status_code, r._content = status, b""
        return r

    monkeypatch.setattr(http, "get", fake_get)
    monkeypatch.setattr(swiss_fetch, "_robots", {})
    assert swiss_fetch.allowed(f"https://robots-{status}.example/data/file.zip") is allowed
