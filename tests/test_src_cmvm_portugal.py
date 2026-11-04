"""CMVM Portugal: quarterly portfolios of Portuguese securities funds."""

import pandas as pd
import pytest

from openbasket.sources import cmvm_portugal as cmvm
from openbasket.sources import redistribution
from openbasket.sources.issuers import HOLDING_COLUMNS, PRODUCT_COLUMNS, discover, validate_holdings, validate_products

AS_OF = "2026-09-30"


def _row(rub, section, name, value, *, qty="", price="", cur="EUR", nav="1000000", cpa="0"):
    return {"num_cpa": cpa, "nom_cpa": "", "dat_mov": "30-09-2026 00:00:00", "tip_car": "FUN5", "num_rub": rub,
            "DSC_RUB": section, "des_tit": name, "qtd_tit": qty, "cot_tit": price, "cod_moe": cur, "jur_cor": "",
            "val_tot": value, "num_ups": "50000", "vlg_fun": nav, "tip_car2": "FUN5"}


PORTFOLIOS = {
    ("100", "0"): [
        _row("CC04", "Ações", "AC.NOVARTIS AG-REG", "300000", qty="3000", price="100"),
        _row("CC04", "Ações", "GALP Energia SGPS SA-B Shrs-PTGAL0AM0009", "200000"),
        _row("CC04", "Ações", "Obscure Holdings", "100000"),
        _row("CC01", "Títulos de dívida pública", "BTPS 0.95 06/01/32", "250000", qty="300000", price="84.44"),
        _row("CC05", "Unidades de participação/ações de OIC que não OII (ETFs)", "PART. ISHRS AI INFRASTRC UCITS ETF",
             "100000"),
        _row("CC02", "Depósitos à Ordem", "CC (EUR) en BANKINTER PORTUGAL", "60000"),
        _row("CC07", "Valores ativos", "CC07 Valores Activos", "0"),
        _row("CC08", "Valores passivos", "CC08 Valores Passivos", "-10000"),
    ],
    ("200", "1"): [_row("CC03", "Obrigações diversas", "EDP 1 7/8 10/13/81", "500000", nav="500000", cpa="1")],
    ("200", "2"): [_row("CC31", "Unidades de participação/ações de OIC que não OII", "Amundi Funds - EM Bond",
                        "250000", nav="250000", cpa="2")],
}
DETAILS = {
    "100": {"Out_FundoDetail": {
        "Fundo_SimpleStructure": {"num_fun": "100", "cod_isi": "PTYPIFLM0019", "descom_fun": "Exemplo Ações Mundiais",
                                  "ent_gestora": "Exemplo Gestão de Ativos, SGOIC, S.A.",
                                  "tipo": "Fundos de Investimento de Ações", "situacao": "Em actividade"},
        "FundUP_ISINCategoryList": {"List": []}},
        "Fundo_Compartimentos_Ativo": {"List": []}},
    "200": {"Out_FundoDetail": {
        "Fundo_SimpleStructure": {"num_fun": "200", "cod_isi": "PTAFIUHM0016", "descom_fun": "Exemplo Ciclo de Vida",
                                  "ent_gestora": "Outra SGOIC, S.A.", "tipo": "Fundos de Poupança Reforma",
                                  "situacao": "Em actividade"},
        "FundUP_ISINCategoryList": {"List": []}},
        "Fundo_Compartimentos_Ativo": {"List": [
            {"nom_cpa": "Exemplo Ciclo de Vida -34", "cod_isi": "PTAFIUHM0016", "num_cpa": "1",
             "t_gr_tab_dsc_plus": "Fundos de Poupança Reforma", "s_gr_tab_dsc_plus": "Em actividade",
             "FundUP_ISINCategoryList": {"List": [{"CAT_UPS": "A", "COD_ISIN": "PTAFIVHM0015"},
                                                  {"CAT_UPS": "B", "COD_ISIN": "PTBAD0000000"}]}},
            {"nom_cpa": "Exemplo Ciclo de Vida +55", "cod_isi": "PTAFIYHM0012", "num_cpa": "2",
             "t_gr_tab_dsc_plus": "Fundos de Poupança Reforma", "s_gr_tab_dsc_plus": "Em actividade",
             "FundUP_ISINCategoryList": {"List": []}},
        ]}},
}


class FakeClient:
    def __init__(self):
        self.calls = []

    def funds(self, date=cmvm.LATEST):
        self.calls.append("list")
        return AS_OF, [{"descom_fun": "Exemplo", "num_fun": f, "num_cpa": c, "nom_cpa": ""} for f, c in PORTFOLIOS]

    def portfolio(self, num_fun, num_cpa, name, date=cmvm.LATEST):
        self.calls.append(f"portfolio {num_fun}:{num_cpa}")
        return PORTFOLIOS[(num_fun, num_cpa)]

    def fund(self, num_fun):
        self.calls.append(f"fund {num_fun}")
        return DETAILS[num_fun]


@pytest.fixture
def store(tmp_path):
    s = cmvm.CmvmStore(tmp_path / "store", client=FakeClient())
    s.refresh()
    return s


def test_api_version_is_read_from_the_page_script():
    script = ('controller.callDataAction("DataActionFetchData", "screenservices/CMVM_SDI_Fun_GestAtivos_CW/'
              'ComposicaoDescriminadaCarteiras/FimList/DataActionFetchData", "gyxk+dEplQj1Oese6FoNOg", function (b) {')
    assert cmvm.parse_api_version(script, cmvm.ACTIONS["list"][1]) == "gyxk+dEplQj1Oese6FoNOg"
    with pytest.raises(ValueError):
        cmvm.parse_api_version(script, cmvm.ACTIONS["portfolio"][1])


def test_sections_are_classified_by_name_not_code():
    assert cmvm.section_class("Ações") == "equity"
    assert cmvm.section_class("Obrigações diversas ") == "debt"
    assert cmvm.section_class("Outros instrumentos financeiros  ") == "other"
    assert cmvm.section_class("Unidades de participação/ações de OIC que não OII (ETFs)") == "fund"
    assert cmvm.section_class("Something new") == "other"


def test_company_names_and_embedded_isins():
    assert cmvm.company_name("AC.NOVARTIS AG-REG") == "NOVARTIS AG"
    assert cmvm.company_name("EDP-Nom.") == "EDP"
    assert cmvm.company_name("Acao Mota Engil SGPS") == "Mota Engil SGPS"
    assert cmvm.company_name("PERSHING SQUARE USA LTD (1)") == "PERSHING SQUARE USA LTD"
    assert cmvm.embedded_isin("GALP Energia SGPS SA-B Shrs-PTGAL0AM0009") == "PTGAL0AM0009"
    assert cmvm.embedded_isin("BTPS 0.95 06/01/32") is None


def test_refresh_stores_the_quarter_once(store):
    calls = store.client.calls
    assert calls.count("fund 200") == 1 and calls.count("list") == 1
    store.refresh()
    assert store.client.calls.count("list") == 2 and store.client.calls.count("fund 100") == 1  # stored: not re-read
    assert store.quarters() == [AS_OF]


def test_products_use_compartment_and_share_class_isins(store):
    p = store.products()
    assert validate_products(p) == []
    assert set(PRODUCT_COLUMNS) <= set(p.columns)
    got = dict(zip(p["isin"], p["portfolio_id"]))
    # Compartment 1 has its own ISIN and a share class; the invalid class ISIN is dropped.
    assert got == {"PTYPIFLM0019": "100:0", "PTAFIUHM0016": "200:1", "PTAFIVHM0015": "200:1",
                   "PTAFIYHM0012": "200:2"}
    row = p.set_index("isin").loc["PTAFIYHM0012"]
    assert row["fund_name"] == "Exemplo Ciclo de Vida +55" and row["domicile"] == "PT"
    assert row["asset_class"] == "Fund of funds"
    assert p.set_index("isin").loc["PTYPIFLM0019", "fund_name"] == "Exemplo Ações Mundiais"
    assert p.set_index("isin").loc["PTAFIUHM0016", "asset_class"] == "Fixed Income"


def test_holdings_weights_add_up_and_isins_are_matched(store):
    master = {"novartis": [("CH0012005267", 10, "")]}
    findb = {"obscure holdings": "GB00B03MLX29"}
    as_of, lines, notes = store.holdings("100:0", master, findb)
    assert as_of == AS_OF
    assert validate_holdings(lines) == []
    assert set(HOLDING_COLUMNS) <= set(lines.columns)
    assert sum(lines["weight_pct"]) == pytest.approx(100.0)
    by = lines.set_index("name")
    assert by.loc["AC.NOVARTIS AG-REG", "isin"] == "CH0012005267"
    assert by.loc["AC.NOVARTIS AG-REG", "isin_source"] == "name_match"
    assert by.loc["AC.NOVARTIS AG-REG", "weight_pct"] == pytest.approx(30.0)
    assert by.loc["GALP Energia SGPS SA-B Shrs-PTGAL0AM0009", "isin_source"] == "reported"
    assert by.loc["Obscure Holdings", "isin"] == "GB00B03MLX29"
    assert by.loc["BTPS 0.95 06/01/32", "isin"] is None and by.loc["BTPS 0.95 06/01/32", "asset_class"] == "debt"
    assert by.loc["CC08 Valores Passivos", "weight_pct"] == pytest.approx(-1.0)
    assert any("not rescaled" in n for n in notes)
    assert any("3 of 3 share lines" in n or "2 of 2 share lines" in n for n in notes)


def test_holdings_without_name_indexes_leave_shares_unresolved(store):
    _, lines, _ = store.holdings("100:0")
    assert lines.set_index("name").loc["AC.NOVARTIS AG-REG", "isin"] is None


def test_findb_index_keeps_only_unambiguous_names(tmp_path):
    path = tmp_path / "equities.parquet"
    pd.DataFrame({"name": ["Novartis AG", "Novartis AG", "Shell plc", "Shell plc", "Bad"],
                  "isin": ["CH0012005267", "CH0012005267", "GB00BP6MXD84", "US7802593050", "XX0000000000"]}
                 ).to_parquet(path)
    assert cmvm.findb_index(path) == {"novartis": "CH0012005267"}
    assert cmvm.findb_index(tmp_path / "missing.parquet") is None


def test_adapter_is_registered_and_public():
    adapters = discover()
    assert "cmvm_portugal" in adapters
    assert adapters["cmvm_portugal"].redistribution == "public"
    assert redistribution("cmvm_portugal") == "public"
