"""Fund registry from GLEIF, ESMA FIRDS, ESMA fund registers, SEC and regulator fund lists."""

import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from openbasket.identifiers import is_valid_isin
from openbasket.sources import gleif_fund_registry as reg

FIX = Path(__file__).parent / "fixtures" / "gleif-fund-registry"

FUND = "54930000000000FU0105"
UMBRELLA = "54930000000000UM0257"
MANAGER = "54930000000000MG0333"
BANK = "54930000000000PB0491"
FEEDER = "54930000000000FU0590"
INDEPENDENT = "54930000000000MG0624"
PENSION = "54930000000000FU0978"
SOLE = "54930000000000SP0866"
US_TRUST = "54930000000000GN1014"


def _zip(tmp_path: Path, name: str, member: str) -> Path:
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as z:
        z.write(FIX / member, member)
    return p


@pytest.fixture
def parsed(tmp_path):
    rel = reg.parse_relationships(_zip(tmp_path, "rr.csv.zip", "rr.csv"))
    firds = reg.parse_firds(_zip(tmp_path, "FULINS_C.zip", "FULINS_C_20261031_01of01.xml"))
    esma = reg.parse_esma_docs(json.loads((FIX / "esma_cbdif.json").read_text())["response"]["docs"], "esma_registers_funds_cbdif")
    keep = set(rel["end_lei"]) | set(firds["issuer_lei"].dropna()) | set(esma["lei"].dropna()) | set(esma["manager_lei"].dropna())
    ents = reg.parse_entities(_zip(tmp_path, "lei2.csv.zip", "lei2.csv"), keep, chunksize=3)
    leis = set(ents.loc[ents["category"] == "FUND", "lei"]) | set(firds["issuer_lei"].dropna())
    isin_lei = reg.parse_isin_lei(_zip(tmp_path, "isin_lei.zip", "isin_lei.csv"), leis, chunksize=2)
    return rel, firds, esma, ents, isin_lei


def _regulators():
    return pd.DataFrame(
        {
            "adapter": ["cnmv_spain"],
            "portfolio_id": ["FI:1:0"],
            "isin": ["ES0000000044"],
            "name": ["EXAMPLE RENTA FIJA, FI CLASE A"],
            "manager_name": ["SANTANDER ASSET MANAGEMENT, S.A., SGIIC"],
            "domicile": ["ES"],
            "currency": ["EUR"],
            "as_of": ["2026-06-30"],
            "credit": ["CNMV"],
            "licence": ["CNMV legal notice"],
            "homepage": ["https://www.cnmv.es/"],
        }
    )


def _sec():
    return pd.DataFrame(
        {
            "series_id": ["S000000001", "S000000002", "S000000003"],
            "series_name": ["Example Treasury Fund", "Example No-LEI Fund", "Example Placeholder Fund"],
            "series_lei": ["54930000000000US1173", None, "00000000000000000000"],
            "report_date": pd.to_datetime(["2026-06-30", "2026-06-30", "2026-06-30"]),
            "class_id": ["C000000001", "C000000002", "C000000003"],
            "ticker": ["EXTFX", "EXNLX", None],
            "cik": ["0000000001", "0000000002", "0000000003"],
            "has_nport": [True, True, False],
        }
    )


AS_OF = {"gleif": "2026-11-03", "gleif_isin_lei": "2026-11-03", "esma_firds": "2026-10-31", "esma_funds": "2026-07-01", "sec": "2026-06-30", "cnmv_spain": "2026-06-30"}


@pytest.fixture
def built(parsed):
    rel, firds, esma, ents, isin_lei = parsed
    fe = reg.build_entities(ents, rel, firds, esma, isin_lei, _sec(), _regulators(), AS_OF)
    fr = reg.build_registry(fe, firds, isin_lei, _regulators(), AS_OF)
    return fe, fr


# -- parsing -----------------------------------------------------------------


def test_index_responses():
    gc = reg.golden_copy_files(json.loads((FIX / "golden_copy_index.json").read_text()))
    assert gc["publish_date"] == "2026-11-03"
    assert gc["lei2"].endswith("lei2-golden-copy.csv.zip") and gc["rr"].endswith("rr-golden-copy.csv.zip")
    il = reg.isin_lei_file(json.loads((FIX / "isin_lei_index.json").read_text()))
    assert il == {
        "as_of": "2026-11-03",
        "url": "https://mapping.gleif.org/api/v2/isin-lei/00000000-0000-0000-0000-000000000000/download",
        "file_name": "isin-lei-20261103T081509.zip",
    }
    files = reg.firds_files(json.loads((FIX / "firds_index.json").read_text())["response"]["docs"])
    assert [f["file_name"] for f in files] == ["FULINS_C_20261031_01of01.zip"]


def test_relationships(parsed):
    rel = parsed[0]
    assert set(rel["relation"]) == {"manager", "umbrella", "master", "ultimate_parent", "direct_parent"}
    # The inactive manager link and the branch link are dropped.
    assert not ((rel["start_lei"] == FEEDER) & (rel["end_lei"] == MANAGER)).any()
    assert len(rel) == 7


def test_entities_keep_funds_and_named_parties(parsed):
    ents = parsed[3].set_index("lei")
    assert FUND in ents.index and BANK in ents.index and US_TRUST in ents.index
    assert "54930000000000GN0723" not in ents.index  # unrelated company
    assert pd.isna(ents.at[SOLE, "name"])  # a natural person's name is dropped
    assert "legal_address" not in " ".join(ents.columns)


def test_firds_one_row_per_isin(parsed):
    firds = parsed[1].set_index("isin")
    assert firds.at["LU0000000017", "venues"] == "XETR,XMUN"
    assert firds.at["LU0000000017", "cfi"] == "CIOGES"
    assert firds.at["US0000000069", "issuer_lei"] == US_TRUST


def test_isin_lei_filtered(parsed):
    il = parsed[4]
    assert set(il["isin"]) == {"LU0000000017", "LU0000000025", "IE0000000038", "US0000000069"} - {"US0000000069"}
    assert il["isin"].map(is_valid_isin).all()


def test_lei_check_digits():
    assert reg.is_valid_lei("54930000000000FU0105")
    assert not reg.is_valid_lei("54930000000000FU0106")
    assert not reg.is_valid_lei("00000000000000000000")
    assert not reg.is_valid_lei(None)


def test_bank_rules():
    assert reg.bank_group("Zürcher Kantonalbank") == ("Zürcher Kantonalbank", "named")
    assert reg.bank_group(None, "Swisscanto Fondsleitung AG") == ("Zürcher Kantonalbank", "named")
    assert reg.bank_group("DekaBank Deutsche Girozentrale")[0] == "Deka (Sparkassen)"
    assert reg.bank_group("Skandinaviska Enskilda Banken AB (publ)")[0] == "SEB"
    assert reg.bank_group("Example Kantonalbank") == ("Example Kantonalbank", "generic_name")
    assert reg.bank_group("Independent Capital Ltd") == (None, None)
    assert reg.bank_group("AMUNDI ASSET MANAGEMENT")[0] == "Crédit Agricole (Amundi)"
    assert reg.bank_group("XP FIC FIM CIC") == (None, None)


# -- merge -------------------------------------------------------------------


def test_entities_managers_umbrellas_banks(built):
    fe = built[0].set_index("fund_id")
    sub = fe.loc[FUND]
    assert sub["umbrella_lei"] == UMBRELLA and sub["umbrella_name"] == "Example Umbrella SICAV"
    assert sub["manager_lei"] == MANAGER and sub["manager_source"] == "gleif_rr_umbrella"
    assert sub["manager_group_lei"] == BANK and sub["manager_group_source"] == "gleif_rr_ultimate_parent"
    assert sub["bank_group"] == "Example Kantonalbank" and sub["bank_group_rule"] == "generic_name"
    assert sub["domicile"] == "LU" and sub["domicile_source"] == "gleif_jurisdiction"
    assert sub["n_isins"] == 2

    feeder = fe.loc[FEEDER]
    assert feeder["manager_lei"] == INDEPENDENT and feeder["manager_source"] == "gleif_rr"
    assert feeder["master_lei"] == FUND
    assert feeder["legal_framework"] == "UCITS"
    assert pd.isna(feeder["bank_group"])
    assert feeder["listed_in"] == "gleif,esma_funds,gleif_isin_lei"

    pension = fe.loc[PENSION]
    assert pension["manager_lei"] == SOLE and pd.isna(pension["manager_name"])
    assert pension["legal_form"] == "PENSION FUND"

    trust = fe.loc[US_TRUST]  # a FIRDS fund issuer whose LEI is not category FUND
    assert trust["category"] == "GENERAL" and "esma_firds" in trust["listed_in"]
    assert trust["domicile"] == "US"


def test_entities_without_lei(built):
    fe = built[0].set_index("fund_id")
    aif = fe.loc["esma_registers_funds_cbdif:102"]
    assert aif["legal_framework"] == "AIF" and aif["manager_name"] == "Example Bank Asset Management GmbH"
    assert aif["bank_group"] == "Example Bank Asset Management GmbH" and aif["bank_group_rule"] == "generic_name"
    sec = fe.loc["sec:S000000002"]
    assert sec["open_holdings_source"] == "sec_nport" and sec["sec_tickers"] == "EXNLX"
    sec_lei = fe.loc["54930000000000US1173"]
    assert sec_lei["sec_series_id"] == "S000000001" and sec_lei["name_source"] == "sec"
    assert sec_lei["domicile"] == "US" and sec_lei["domicile_source"] == "sec"
    assert "00000000000000000000" not in fe.index  # SEC placeholder LEI
    assert pd.isna(fe.loc["sec:S000000003", "open_holdings_source"])
    vc = fe.loc["esma_registers_funds_cbdif:103"]  # invalid LEI dropped, domicile from the authority
    assert pd.isna(vc["lei"]) and vc["domicile"] == "LU" and vc["domicile_source"] == "esma_funds_authority"
    es = fe.loc["cnmv_spain:FI:1:0"]
    assert es["bank_group"] == "Santander" and es["open_holdings_source"] == "cnmv_spain"
    assert es["as_of"] == "2026-06-30" and es["credit"] == "CNMV"


def test_every_entity_is_cited(built):
    fe = built[0]
    for col in ("source", "source_url", "credit", "licence", "as_of"):
        assert fe[col].notna().all(), col
    assert list(fe.columns) == reg.FUND_ENTITY_COLUMNS


def test_registry_rows(built):
    fr = built[1]
    assert list(fr.columns) == reg.REGISTRY_COLUMNS
    assert fr["isin"].is_unique and fr["isin"].map(is_valid_isin).all()
    assert set(fr["isin"]) == {"LU0000000017", "LU0000000025", "IE0000000038", "US0000000069", "ES0000000044"}
    r = fr.set_index("isin")
    a = r.loc["LU0000000017"]
    assert a["name"] == "Example Umbrella SICAV - Equity Fund A EUR Acc" and a["name_source"] == "esma_firds"
    assert a["lei"] == FUND and a["lei_source"] == "esma_firds" and a["cfi"] == "CIOGES"
    assert a["manager_name"] == "Example Fund Management S.A." and a["umbrella_lei"] == UMBRELLA
    assert a["listed_in"] == "gleif,esma_firds,gleif_isin_lei"
    assert "CC0" not in a["credit"] and "GLEIF" in a["credit"] and "FIRDS" in a["credit"]
    b = r.loc["LU0000000025"]
    assert b["name"] == "Example Umbrella SICAV - Equity Fund" and b["name_source"] == "gleif"
    assert b["lei_source"] == "gleif_isin_lei" and pd.isna(b["cfi"])
    es = r.loc["ES0000000044"]
    assert es["open_holdings_source"] == "cnmv_spain" and es["bank_group"] == "Santander"
    assert es["name_source"] == "cnmv_spain" and es["domicile"] == "ES"
    for col in ("source", "source_url", "credit", "licence", "as_of"):
        assert fr[col].notna().all(), col


def test_route(built):
    fr = built[1]
    assert reg.route("LU0000000017", fr)["status"] == "known fund, no open holdings source"
    assert reg.route("ES0000000044", fr)["status"] == "open holdings source: cnmv_spain"
    assert reg.route("FR0000000000", fr)["status"] == "unknown"


def test_reports(built):
    fe, fr = built
    cov = reg.coverage(fe, fr)
    row = cov.set_index("group").loc["Example Kantonalbank"]
    assert row["funds"] == 2 and row["isins"] == 2  # umbrella and sub-fund
    banks = reg.bank_coverage(fe, fr).set_index("bank_group")
    assert banks.at["Santander", "funds_with_open_holdings"] == 1
    dom = reg.domicile_coverage(fe, fr).set_index("domicile")
    assert dom.at["LU", "funds"] == 3  # sub-fund, umbrella, venture fund
    s = reg.summarise(fe, fr)
    assert s["fund_isins"] == 5 and s["bank_run_funds"] >= 3


def test_licences_quote_terms():
    assert "CC0" in reg.LICENCES["gleif"][1]
    assert "Reproduction of all information" in reg.LICENCES["esma_firds"][1]
    assert "Reproduction of all information" in reg.LICENCES["esma_funds"][1]


# -- network -------------------------------------------------------------------


@pytest.mark.network
def test_network_indexes_and_esma_register():
    gc = reg.latest_golden_copy()
    assert gc["lei2"].startswith("https://goldencopy.gleif.org/") and gc["rr"].endswith(".csv.zip")
    il = reg.latest_isin_lei()
    assert il["url"].startswith("https://mapping.gleif.org/")
    files = reg.latest_firds_c()
    assert files and files[0]["file_name"].startswith("FULINS_C_")
    esma = reg.fetch_esma_register("esma_registers_funds_cbdif", rows=20, max_pages=1)
    assert len(esma) == 20 and esma["name"].notna().all()
    assert set(esma["legal_framework"].dropna()) <= {"UCITS", "AIF", "EuVECA", "ELTIF", "EuSEF"}
