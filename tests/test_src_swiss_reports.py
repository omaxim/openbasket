"""Swiss and Luxembourg fund reports: schedule-of-investments parsers and name matching."""

import json
from pathlib import Path

import pandas as pd
import pytest

from openbasket.sources import swiss_reports as sr
from openbasket.sources.issuers import HOLDING_COLUMNS, validate_holdings

FIX = Path(__file__).parent / "fixtures" / "swiss"


def _pages(name: str) -> list[str]:
    return (FIX / name).read_text("utf-8").split("=====PAGE=====\n")


def _six_equities() -> pd.DataFrame:
    d = json.loads((FIX / "six_equities.json").read_text())
    return pd.DataFrame(d["rowData"], columns=d["colNames"])


def _resolver() -> sr.NameResolver:
    sm = pd.DataFrame(
        [
            ("TW0002330008", "Taiwan Semiconductor Manufacturing Co Ltd", "TSMC", "EC", 900),
            ("US8740391003", "Taiwan Semiconductor Manufacturing Co Ltd", "TSMC ADR", "EC", 2000),
            ("CH0012005267", "Novartis AG", "Novartis AG", "EC", 500),
            ("US66987V1098", "Novartis AG", "Novartis ADR", "EC", 50),
            ("CH0126881561", "Swiss Re AG", "Swiss Re AG", "EC", 300),
        ],
        columns=["isin", "issuer_name", "title", "asset_cat", "n_positions"],
    )
    return sr.NameResolver.from_frames(_six_equities(), sm)


# -- dates and switches -------------------------------------------------------


def test_parse_date_formats():
    assert sr.parse_date("Portfolio as at February 28, 2026") == "2026-02-28"
    assert sr.parse_date("Halbjahresbericht per 31. Dezember 2025") == "2025-12-31"
    assert sr.parse_date("per 31.12.2025") == "2025-12-31"
    assert sr.parse_date("nothing here") is None


def test_enabled_switches(monkeypatch):
    monkeypatch.delenv("OPENBASKET_ENABLE", raising=False)
    monkeypatch.delenv("OPENBASKET_DISABLE", raising=False)
    assert sr.enabled("x", True) and not sr.enabled("x", False)
    monkeypatch.setenv("OPENBASKET_ENABLE", "a,x")
    assert sr.enabled("x", False)
    monkeypatch.setenv("OPENBASKET_DISABLE", "x")
    assert not sr.enabled("x", True)


# -- lux_portfolio ------------------------------------------------------------


def test_lux_portfolio_positions_wrapped_names_and_sections():
    pfs = sr.parse_report(_pages("lux_portfolio.txt"))
    names = [p.subfund for p in pfs]
    assert names == ["Example Fund – Global Leaders", "Example Fund – Sustainable Emerging Markets Local Currency Bond"]
    pf = pfs[0]
    assert pf.as_of == "2026-02-28" and pf.pages == [0, 1]
    by_name = {p.name: p for p in pf.positions}
    assert set(by_name) == {
        "Novartis", "Companhia de Saneamento Basico do Estado De Sao Paulo", "Taiwan Semiconductor Manufacturing",
        "Zorlu Enerji Elektrik Ueretimi 11.000% 23.04.30", "Example Liquidity Fund I",
    }
    tsmc = by_name["Taiwan Semiconductor Manufacturing"]
    assert (tsmc.weight_pct, tsmc.currency, tsmc.country, tsmc.asset_class, tsmc.market_value) == (9.81, "TWD", "TW", "equity", 383838231)
    assert by_name["Zorlu Enerji Elektrik Ueretimi 11.000% 23.04.30"].asset_class == "debt"
    assert by_name["Example Liquidity Fund I"].asset_class == "fund"
    # Totals are never positions, and weights are as printed.
    assert pf.weight_sum == pytest.approx(5.00 + 3.87 + 9.81 + 0.43 + 0.50)


# -- ch_inventar --------------------------------------------------------------


def test_ch_inventar_runs_over_pages_and_stops_at_cash():
    pfs = sr.parse_report(_pages("ch_inventar.txt"))
    assert [p.subfund for p in pfs] == ["Example (CH) Fonds – SwissStock"]
    pf = pfs[0]
    assert pf.as_of == "2025-12-31"
    got = {p.name: (p.weight_pct, p.kind, p.market_value) for p in pf.positions}
    assert got == {
        "SWISS RE AG CHF0.10": (2.16, "bearer", 12080051),
        "ROCHE HLDGS AG GENUSSCHEINE NPV": (13.60, "participation", 76057068),
        "NOVARTIS AG CHF0.49 (REGD)": (11.23, "registered", 62812345),
        "JULIUS BAER GRUPPE CHF0.02 (REGD)": (0.95, "registered", 5336448),
    }
    assert "NOT A POSITION AG" not in " ".join(got)


def test_ch_toc_isins_with_wrapped_names():
    toc = sr.parse_ch_toc_isins(_pages("ch_inventar.txt"))
    assert toc["Example (CH) Fonds – SwissStock"] == ["CH0023406587", "CH0023406611"]
    assert toc["Example (CH) Fonds – Bond Medium Term"] == ["CH0023406751", "CH0023406777"]
    assert toc["Example (Lux) Fonds 30"] == ["LU0261662687", "LU0261662760"]


# -- lux_aufstellung ----------------------------------------------------------


def test_lux_aufstellung_numbers_and_continuation():
    pfs = sr.parse_report(_pages("lux_aufstellung.txt"))
    assert len(pfs) == 1
    pf = pfs[0]
    assert pf.subfund == "Example (Lux) Fonds 30" and pf.as_of == "2025-12-31"
    got = {p.name: (p.quantity, p.market_value, p.weight_pct, p.currency) for p in pf.positions}
    assert got == {
        "ERSTE GROUP BK AG NPV": (607.0, 58116.69, 0.03, "EUR"),
        "ANHEUSER-BUSCH IN NPV": (1000.0, 51082.14, 0.02, "EUR"),
        "EXAMPLE MSCI USA UCITS ETF-ACC": (12500.0, 1234567.89, 4.10, "USD"),
    }


# -- names to ISINs -----------------------------------------------------------


def test_norm_security():
    assert sr.norm_security("ROCHE HLDGS AG GENUSSCHEINE NPV") == "roche holding"
    assert sr.norm_security("Roche Holding AG") == "roche holding"
    assert sr.norm_security("JULIUS BAER GRUPPE CHF0.02 (REGD)") == sr.norm_security("Julius Bär Gruppe AG")
    assert sr.norm_security("BYD 'H'") == "byd"


def test_resolver_prefers_kind_country_and_never_guesses():
    r = _resolver()
    assert r.resolve("ROCHE HLDGS AG GENUSSCHEINE NPV", "CHF", "CH", "participation") == "CH0012032048"
    assert r.resolve("ROCHE HLDGS AG", "CHF", "CH", "bearer") == "CH0012032113"
    assert r.resolve("NOVARTIS AG CHF0.49 (REGD)", "CHF", "CH", "registered") == "CH0012005267"
    # A local listing beats the ADR that more funds hold.
    assert r.resolve("Taiwan Semiconductor Manufacturing", "TWD", "TW") == "TW0002330008"
    assert r.resolve("JULIUS BAER GRUPPE CHF0.02 (REGD)", "CHF", "CH", "registered") == "CH0102484968"
    assert r.resolve("Unknown Widgets", "CHF", "CH") is None
    # A preferred line never takes the common share's ISIN.
    assert r.resolve("Novartis Pref", "CHF", "CH", "pref") is None


def test_to_holdings_frame_meets_contract():
    pf = sr.parse_report(_pages("lux_portfolio.txt"))[0]
    df = sr.to_holdings_frame(pf, _resolver())
    assert not validate_holdings(df[HOLDING_COLUMNS])
    assert df.loc[df["name"] == "Novartis", "isin"].item() == "CH0012005267"
    assert df.loc[df["name"] == "Novartis", "isin_source"].item() == "name_match"
    assert df.loc[df["asset_class"] == "debt", "isin"].isna().all()
    assert df["weight_pct"].tolist()[0] == 5.00


def test_find_portfolio_by_gleif_name():
    pfs = sr.parse_report(_pages("lux_portfolio.txt"))
    assert sr.find_portfolio(pfs, "GLOBAL LEADERS", "Example Fund").subfund == "Example Fund – Global Leaders"
    assert sr.find_portfolio(pfs, "SUSTAINABLE EMERGING MARKETS LOCAL CURRENCY BOND", "Example Fund") is pfs[1]
    assert sr.find_portfolio(pfs, "SOMETHING ELSE", "Example Fund") is None


def test_report_link_date():
    assert sr.report_link_date("Semi-Annual-Report_20260228_EN_Vontobel-Fund.pdf") == "20260228"
    assert sr.report_link_date("Halbjahresbericht Fonds.2026-03-02-10-55-51.pdf") == "20260302"
    assert sr.report_link_date("Jahresbericht.a2309ecedf.pdf") == ""


# -- adapters (offline) -------------------------------------------------------


def test_report_adapters_are_off_by_default(monkeypatch):
    from openbasket.sources.issuers import swiss_migros, swiss_vontobel

    monkeypatch.delenv("OPENBASKET_ENABLE", raising=False)
    for mod in (swiss_vontobel, swiss_migros):
        assert mod.ENABLED_BY_DEFAULT is False and mod.REDISTRIBUTION == "local_only"
        assert mod.products().empty
        with pytest.raises(PermissionError):
            mod.holdings({"isin": "LU0571085413", "fund_name": "X"}, None)


def test_migros_report_links_pick_newest_per_umbrella():
    from openbasket.sources.issuers import swiss_migros

    html = (
        '<a href="/dam/jcr:1/Halbjahresbericht%20Migros%20Bank%20(CH)%20Fonds.2026-03-02-10-55-51.pdf">'
        '<a href="/dam/jcr:2/Jahresbericht%20Migros%20Bank%20(CH)%20Fonds.553e759d4d.pdf">'
        '<a href="/dam/jcr:3/Jahresbericht%20Migros%20Bank%20(Lux)%20Fonds.2025-09-30-10-00-00.pdf">'
        '<a href="/dam/jcr:4/migros-bank-ch-fondsvertrag.pdf">'
    )
    links = swiss_migros.report_links(html)
    assert links["CH"] == ("https://www.migrosbank.ch/dam/jcr:1/Halbjahresbericht%20Migros%20Bank%20(CH)%20Fonds.2026-03-02-10-55-51.pdf",
                           "semi-annual report")
    assert links["LU"][1] == "annual report"


def test_vontobel_report_regex():
    from openbasket.sources.issuers import swiss_vontobel

    html = ('href="/en/document/aa-1/Annual-Report_20250831_EN_Vontobel-Fund.pdf" '
            'href="/en/document/bb-2/Semi-Annual-Report_20260228_EN_Vontobel-Fund.pdf" '
            'href="/en/document/cc-3/Semi-Annual-Distribution_20260423_EN_Vontobel-Fund.pdf"')
    links = swiss_vontobel.REPORT_RE.findall(html)
    assert max(links, key=lambda t: t[2])[0] == "/en/document/bb-2/Semi-Annual-Report_20260228_EN_Vontobel-Fund.pdf"


# -- network ------------------------------------------------------------------


@pytest.mark.network
def test_network_vontobel_report(tmp_path):
    """Off-by-default route: runs only when OPENBASKET_ENABLE includes swiss_vontobel."""
    from openbasket.sources.issuers import FetchContext, swiss_vontobel

    if not sr.enabled("swiss_vontobel", False):
        pytest.skip("swiss_vontobel is off by default (investor-type overlay); enable it to run this test")
    product = {"isin": "LU0035765741", "fund_name": "US EQUITY"}
    got = swiss_vontobel.holdings(product, FetchContext(cache_dir=str(tmp_path)))
    assert got.as_of and 90 < got.holdings["weight_pct"].sum() < 101
    assert not validate_holdings(got.holdings[HOLDING_COLUMNS])


@pytest.mark.network
def test_network_migros_report(tmp_path):
    """Off-by-default route: runs only when OPENBASKET_ENABLE includes swiss_migros."""
    from openbasket.sources.issuers import FetchContext, swiss_migros

    if not sr.enabled("swiss_migros", False):
        pytest.skip("swiss_migros is off by default (Swiss-residence overlay); enable it to run this test")
    products = swiss_migros.products(str(tmp_path))
    row = products[products["isin"] == "CH0023406587"].iloc[0].to_dict()
    got = swiss_migros.holdings(row, FetchContext(cache_dir=str(tmp_path)))
    assert got.as_of and 90 < got.holdings["weight_pct"].sum() < 101
