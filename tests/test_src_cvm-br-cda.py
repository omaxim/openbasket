import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from openbasket.sources.issuers import (
    FetchContext,
    discover,
    validate_holdings,
    validate_products,
)
from openbasket.sources.issuers import cvm_br_cda as cvm

FIX = Path(__file__).parent / "fixtures" / "cvm-br-cda"


def _month_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in sorted((FIX / "cda_fi_202609").glob("*.csv")):
            z.writestr(f.name, f.read_text(encoding="utf-8").encode("latin-1"))
    return buf.getvalue()


@pytest.fixture()
def month():
    lines, funds = cvm.parse_month(_month_zip())
    return cvm.Month("202609", cvm.CDA_ZIP.format(ym="202609"), lines, funds)


@pytest.fixture()
def instruments():
    return cvm.parse_instruments((FIX / "instruments.csv").read_text(encoding="utf-8").encode("latin-1"))


def test_registered():
    a = discover()["cvm"]
    assert a.frequency == "monthly" and "ODbL" in a.licence and "CVM" in a.credit


def test_parse_directory():
    months = cvm.parse_directory((FIX / "directory.html").read_text())
    assert months == [("202609", "2026-11-03"), ("202608", "2026-11-03")]


def test_parse_month(month):
    funds = month.funds
    assert set(funds.index) == {"11111111000111", "22222222000122", "33333333000133", "44444444000144", "66666666000166"}
    assert funds.at["11111111000111", "pl"] == 1_000_000.0
    assert funds.at["66666666000166", "pl"] == 110_000.0
    assert month.isin_by_selic[("760199", "2035-05-15")] == "BRSTNCNTB0O7"
    assert month.isin_by_code["NATU3"] == "BRNATUACNOR6"
    # The FIDC exists only in a confidential file without net assets: it has lines, not a fund row.
    assert len(month.fund_lines("55555555000155")) == 1


def test_parse_instruments(instruments):
    t = instruments.set_index("ticker")
    assert t.at["EXAC11", "isin"] == "BRBOVACTF003" and t.at["EXAC11", "segment"] == "CASH"
    assert t.at["EXIB11", "isin"] == "BRIMABCTF004"  # fixed-income ETF, FORWARD segment
    assert "EXIBETF11H" not in t.index and "EXSPT11" not in t.index


def test_holdings_equity_etf(month, instruments):
    lines = month.fund_lines("11111111000111")
    df = cvm.build_holdings(lines, 1_000_000.0, month, instruments, {"22222222000122": "BRIMABCTF004"})
    assert validate_holdings(df) == []
    w = df.set_index("name")["weight_pct"]
    assert df["weight_pct"].sum() == pytest.approx(100.0)  # payables count as negative
    assert w["VALE S.A."] == pytest.approx(40.0)
    assert w["Valores a Pagar"] == pytest.approx(-2.0)
    by_ticker = df.dropna(subset=["ticker"]).set_index("ticker")
    assert by_ticker.at["PETR4", "isin"] == "BRPETRACNPR6"
    assert by_ticker.at["NATU3", "isin"] == "BRNATUACNOR6"  # ticker in DS_ATIVO, ISIN from other funds
    fund = df[df["tp_aplic"] == "Cotas de Fundos"].iloc[0]
    assert fund["isin"] == "BRIMABCTF004" and fund["asset_class"] == "fund" and fund["cnpj"] == "22222222000122"
    repo = df[df["tp_aplic"] == "Operações Compromissadas"].iloc[0]
    assert pd.isna(repo["isin"]) and repo["asset_class"] == "cash"
    fut = df[df["asset_class"] == "derivative (futures)"].iloc[0]
    assert fut["weight_pct"] == pytest.approx(-0.1)  # as reported, not re-signed


def test_holdings_bond_etf_maps_selic(month, instruments):
    df = cvm.build_holdings(month.fund_lines("22222222000122"), 500_000.0, month, instruments)
    assert validate_holdings(df) == []
    bond = df[df["asset_class"] == "debt"].iloc[0]
    assert bond["isin"] == "BRSTNCNTB0O7" and bond["weight_pct"] == pytest.approx(99.8)


def test_holdings_ordinary_fund_and_confidential(month, instruments):
    df = cvm.build_holdings(month.fund_lines("66666666000166"), 110_000.0, month, instruments)
    assert validate_holdings(df) == []
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    conf = df[df["block"] == "fi_CONFID"].iloc[0]
    assert pd.isna(conf["isin"]) and "Confidential" in conf["name"] and "2026-12-29" in conf["name"]
    gov = df[df["block"] == "fi_BLC_1"].iloc[0]
    assert gov["isin"] == "BRSTNCNTB0O7" and gov["name"].startswith("NOTAS DO TESOURO")


def test_offshore_line(month, instruments):
    df = cvm.build_holdings(month.fund_lines("44444444000144"), 300_000.0, month, instruments)
    off = df[df["tp_aplic"] == "Investimento no Exterior"].iloc[0]
    assert pd.isna(off["isin"]) and off["asset_class"] == "fund" and off["name"] == "ISHARES CORE SP500"
    assert pd.isna(off["country"])  # numeric CVM country code is not ISO


def test_replication(month):
    assert cvm.replication(month.fund_lines("11111111000111"), 1_000_000.0, "ETF") == "physical"
    assert cvm.replication(month.fund_lines("33333333000133"), 200_000.0, "ETF") == "swap"
    assert cvm.replication(month.fund_lines("22222222000122"), 500_000.0, "ETF-RF") == "physical"
    assert cvm.replication(month.fund_lines("66666666000166"), 110_000.0, "FI-INFRA") is None


def test_build_products(month, instruments):
    listed = json.loads((FIX / "b3_listed_funds.json").read_text(encoding="utf-8"))
    df = cvm.build_products(listed, instruments, [month])
    assert validate_products(df) == []
    assert df["isin"].tolist() == ["BRBOVACTF003", "BRIMABCTF004", "BRIVVBCTF001"]
    row = df.set_index("ticker").loc["EXSP11"]
    assert row["replication"] == "swap" and row["portfolio_id"] == "33333333000133"
    assert row["cnpj"] == "33.333.333/0001-33" and row["domicile"] == "BR"
    assert df.set_index("ticker").at["EXIB11", "asset_class"] == "Fixed Income"


def test_holdings_end_to_end(monkeypatch, month, instruments, tmp_path):
    monkeypatch.setattr(cvm, "available_months", lambda: (("202609", "2026-11-03"),))
    monkeypatch.setattr(cvm, "load_month", lambda ym, mod, cache_dir=None: month)
    monkeypatch.setattr(cvm, "b3_instruments", lambda: instruments)
    h = cvm.holdings({"portfolio_id": "11111111000111", "isin": "BRBOVACTF003"}, FetchContext(str(tmp_path)))
    assert h.as_of == "2026-09-30"
    assert h.url.endswith("cda_fi_202609.zip")
    assert validate_holdings(h.holdings) == []
    assert h.holdings["weight_pct"].sum() == pytest.approx(100.0)


def test_month_cache_written_after_parse(monkeypatch, tmp_path):
    class R:
        content = _month_zip()

        def raise_for_status(self):
            pass

    cvm._MONTHS.clear()
    monkeypatch.setattr(cvm.http, "get", lambda url, **kw: R())
    m = cvm.load_month("202609", "2026-11-03", tmp_path)
    assert (tmp_path / "cvm_cda_202609_2026-11-03" / "lines.parquet").exists()
    cvm._MONTHS.clear()
    monkeypatch.setattr(cvm.http, "get", lambda url, **kw: pytest.fail("should read the cache"))
    again = cvm.load_month("202609", "2026-11-03", tmp_path)
    assert len(again.lines) == len(m.lines)
    cvm._MONTHS.clear()


@pytest.mark.network
def test_live_bova11(tmp_path):
    product = {"portfolio_id": "10406511000161", "isin": "BRBOVACTF003"}
    h = cvm.holdings(product, FetchContext(str(tmp_path)))
    df = h.holdings
    assert validate_holdings(df) == []
    assert len(df) > 50 and h.as_of >= "2026-01-01"
    assert df["weight_pct"].sum() == pytest.approx(100.0, abs=1.0)
    assert df.loc[df["isin"].notna(), "weight_pct"].sum() > 90
