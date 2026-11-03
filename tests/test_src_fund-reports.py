"""Statutory-report holdings: locators, the schedule-of-investments parser and the adapters."""

import json
from pathlib import Path

import pandas as pd
import pytest

from openbasket.sources import fund_reports as fr
from openbasket.sources import report_parser as rp
from openbasket.sources.issuers import HOLDING_COLUMNS, discover, validate_holdings, validate_products

FIX = Path(__file__).parent / "fixtures" / "fund-reports"
REPORTS = json.loads((FIX / "reports.json").read_text())


def pages(name):
    return [rp.clean_text(p) for p in REPORTS[name]]


# --------------------------------------------------------------------------
# Numbers and lines
# --------------------------------------------------------------------------


def test_number_parsing_styles():
    en, de, ch = rp.NumberStyle(".", ","), rp.NumberStyle(",", "."), rp.NumberStyle(".", " ")
    assert rp.parse_num("1,234,567", en) == 1234567
    assert rp.parse_num("(0.32)", en) == -0.32
    assert rp.parse_num("(0.00%)", en) == 0
    assert rp.parse_num("4.169.663", de) == 4169663
    assert rp.parse_num("0,95", de) == 0.95
    assert rp.parse_num("1'234.50", en) == 1234.5
    assert rp.parse_num("-", en) is None
    assert rp.merge_space_groups("CHF 4 148 513 363 326 769 9.25".split()) == ["CHF", "4148513363326769", "9.25"]
    toks = rp._tokens("CHF 4 148 513  363 326 769 9.25", ch)
    assert toks == ["CHF", "4148513", "363326769", "9.25"]


def test_detect_style():
    assert rp.detect_style(["Alpha Inc 1,000 150,000 0.10", "Beta 2,000 2,000 0.20"]) == rp.NumberStyle(".", ",")
    assert rp.detect_style(["Alpha 57.252 4.169.663 0,03", "Beta 1.000 1.000 0,02"]) == rp.NumberStyle(",", ".")
    assert rp.detect_style(["USD ARBUTUS NPV 577 780.00 2 634 676.80 0.56", "USD XENON 125 056.00 6 844 314.88 1.46"]).thousands == " "


def test_holding_line_variants():
    en = rp.NumberStyle(".", ",")
    pl = rp.parse_holding_line("Gamma Computers Corp 10,000 30,000,000 40.00", en)
    assert (pl.name, pl.quantity, pl.market_value, pl.weight_pct) == ("Gamma Computers Corp", 10000, 30000000, 40.0)
    pl = rp.parse_holding_line("1.375% EXAMPLE AIR 20/30 -SR- EUR 100,000.00 92,675.77 0.07", en)
    assert pl.currency == "EUR" and pl.name.startswith("1.375% EXAMPLE AIR") and pl.weight_pct == 0.07
    pl = rp.parse_holding_line("3.00% EXAMPLE BTP 23/33 XS2534567891 EUR 50,000.00 51,000.00 24.77", en)
    assert pl.isin == "XS2534567891" and "XS25" not in pl.name
    pl = rp.parse_holding_line("USD 758,088 SGD (978,935) Brown Brothers 25/08/2026 (5,742) (0.00%)", en)
    assert pl.weight_pct == 0 and pl.market_value == -5742
    # not holdings: sector headers, totals, column headers, page footers
    for line in [
        "Advertising (0.12%) (31 December 2024: 0.17%)",
        "Japan: 9.15% (30 Nov 2025: 2.89%)",
        "Total Advertising 5,219,852 0.12",
        "Net financial assets at fair value through profit and loss 4,517,121,761 99.40",
        "Unrealised gain on open forward foreign currency exchange contracts 17,346,686 0.37",
        "Fair value outperformance swaps losses (23,523,243) (0.30)",
        "Semi-Annual Report for the financial period ended 30 June 2025 192",
        "% of Net Assets",
        "Transferable securities admitted to an official stock exchange listing 98.89",
    ]:
        assert rp.parse_holding_line(line, en) is None, line
    residual = rp.parse_holding_line("Other net assets 27,285,975 0.60", en)
    assert residual.residual
    # a bank is a holding, not cash
    assert not rp.parse_holding_line("Bank of America Corp 436,582 20,659,060 0.45", en).residual


def test_unspace_and_clean():
    assert rp.unspace("To t a l O i l & G a s 1 2.66") .startswith("TotalOil&Gas")
    assert rp.clean_text("T otal Advertising\n%o f") == "Total Advertising\n% of"
    assert rp.clean_text("Reliance Industries Ltd. ............ 10.639.320 1,79") == "Reliance Industries Ltd. 10.639.320 1,79"
    assert rp.text_quality(["abc def"]) == 1.0
    assert rp.text_quality([" x"]) < 0.5


def test_stray_spaces_inside_numbers_in_wide_columns():
    en = rp.NumberStyle(".", ",")
    gap = " " * 30
    pl = rp.parse_holding_line(f"Example Group Co Ltd 'H'{gap}221 , 109{gap}673,301{gap}1. 81", en)
    assert (pl.name, pl.quantity, pl.market_value, pl.weight_pct) == ("Example Group Co Ltd 'H'", 221109, 673301, 1.81)
    pl = rp.parse_holding_line(f"Example Holdings Plc{gap}1,17 8{gap}1 , 136,770{gap}3.06", en)
    assert (pl.quantity, pl.market_value) == (1178, 1136770)


def test_title_scoring():
    name = "UBS Core MSCI World UCITS ETF USD acc"
    good = rp.title_score("UBS Core MSCI World UCITS ETF", name)
    assert good[0] == 1.0
    assert rp.title_score("UBS MSCI World Socially Responsible UCITS ETF", name) == (0.0, 0)
    assert rp.title_score("UBS Core MSCI World UCITS ET F", name)[0] == 1.0       # German spacing glitch
    assert rp.title_score("UBS ETF (CH) – UBS SMI ® ETF", "UBS SMI ETF CHF dis")[0] == 1.0
    assert rp.title_score("UBS ETF (CH) – UBS SMIM ® ETF", "UBS SMI ETF CHF dis") == (0.0, 0)
    assert rp.title_score("PICTET - BIOTECH", "Pictet SICAV - Biotech EUR PC")[0] == 1.0
    assert rp.title_score("UBS (Lux) Equity Fund – Biotech (USD)", "UBS (Lux) Equity Fund FCP - Biotech")[0] == 1.0


def test_dates():
    assert rp.find_dates("Schedule of Investments as at 30 June 2025") == ["2025-06-30"]
    assert rp.find_dates("AS AT MARCH 31, 2026 (EXPRESSED IN EUR)") == ["2026-03-31"]
    assert set(rp.find_dates("Aufstellung des Wertpapierbestandes zum\n30. Juni 2026")) == {"2026-06-30"}
    assert rp.find_dates("Schedule of investments as at 31/03/2026") == ["2026-03-31"]
    assert rp.find_dates("Halbjahresbericht per 31. Mai 2026") == ["2026-05-31"]


# --------------------------------------------------------------------------
# Whole reports
# --------------------------------------------------------------------------


def test_irish_umbrella_split_and_totals():
    p = pages("irish_umbrella_en")
    r = rp.subfund_schedule(p, "Example Core World UCITS ETF USD acc", ["Example"])
    assert r.title == "Example Core World UCITS ETF"
    assert r.as_of == "2026-06-30" and r.kind == "semi-annual report"
    df = r.holdings
    assert list(df["name"])[:2] == ["Alpha Media Inc", "Beta Publicis SA"]
    assert "Gamma Computers Corp" in set(df["name"]) and "Total Technology" not in set(df["name"])
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    assert r.stats["found_total_line"]
    assert set(df["asset_class"]) == {"equity", "derivative", "other"}
    # the second sub-fund is kept apart
    usa = rp.subfund_schedule(p, "Example Core USA UCITS ETF", ["Example"])
    assert usa.title == "Example Core USA UCITS ETF" and len(usa.holdings) == 3
    assert usa.holdings["weight_pct"].sum() == pytest.approx(100.0)


def test_lux_sicav_currency_isin_and_residuals():
    r = rp.subfund_schedule(pages("lux_sicav_en"), "Example SICAV - EUR Bonds I EUR", ["Example"])
    assert r.title == "EXAMPLE - EUR BONDS"
    df = r.holdings
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    assert (df.loc[~df["residual"], "asset_class"] == "debt").all()
    assert df["isin"].dropna().tolist() == ["XS2534567891"]
    assert df.loc[df["name"] == "Cash at banks", "asset_class"].item() == "cash"
    assert r.as_of == "2026-03-31"
    bio = rp.subfund_schedule(pages("lux_sicav_en"), "Example SICAV - Biotech P USD", ["Example"])
    assert bio.title == "EXAMPLE - BIOTECH" and len(bio.holdings) == 3


def test_quantity_first_layout():
    r = rp.subfund_schedule(pages("irish_quantity_first"), "Example Global Dividend UCITS ETF - USD", ["Example"])
    df = r.holdings
    assert r.title == "Example Global Dividend UCITS ETF"
    assert df.loc[df["name"] == "Example Wendel SE", "quantity"].item() == 498
    assert "contracts" not in set(df["name"])                 # wrapped sub-total line
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    assert r.stats["found_total_line"]


def test_german_translation():
    r = rp.subfund_schedule(pages("german_translation"), "Beispiel Core World UCITS ETF USD acc", ["Beispiel"])
    df = r.holdings
    assert r.as_of == "2026-06-30"
    assert df.loc[df["name"] == "Omnicom Beispiel, Inc", "market_value"].item() == 4169663
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    assert df.loc[df["residual"], "name"].tolist() == ["Sonstiges Nettovermögen"]


def test_swiss_inventory():
    r = rp.subfund_schedule(pages("swiss_inventory"), "Example SMI ETF CHF dis", ["Example"])
    df = r.holdings
    assert r.title == "Example ETF (CH) – Example SMI ® ETF"
    assert df.loc[df["name"].str.contains("NESTLE"), "market_value"].item() == 363326769
    assert df.loc[df["name"].str.contains("ROCHE"), "weight_pct"].item() == 38.91
    assert not df["name"].str.contains("thereof", case=False).any()
    assert df["weight_pct"].sum() == pytest.approx(100.0)


def test_quantity_first_space_thousands():
    r = rp.subfund_schedule(pages("lux_quantity_first_space"), "Lombard Odier Example SICAV - Convertible Bond", ["LO Example"])
    df = r.holdings
    assert r.title == "LO Example - Convertible Bond"
    assert len(df) == 5                                        # country sub-totals dropped
    assert df.loc[df["name"].str.contains("JAZZ"), "quantity"].item() == 6566000
    assert df.loc[df["name"].str.contains("AIRWAYS"), "currency"].item() == "JPY"
    assert (df["asset_class"] == "debt").all()


def test_unknown_subfund_is_not_guessed():
    r = rp.subfund_schedule(pages("irish_umbrella_en"), "Example Gold Miners UCITS ETF", ["Example"])
    assert r.title is None and r.holdings.empty


def test_extract_pages_from_a_minimal_pdf():
    pdf = _tiny_pdf("Alpha Inc 1,000 150,000 0.10")
    assert "Alpha Inc 1,000 150,000 0.10" in rp.extract_pages(pdf)[0]


def _tiny_pdf(text: str) -> bytes:
    stream = f"BT /F1 10 Tf 50 750 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


# --------------------------------------------------------------------------
# Locators and lookup
# --------------------------------------------------------------------------

SITEMAP = (
    '<?xml version="1.0" encoding="UTF-8"?><urlset>'
    "<url><loc>https://dl.avl-investmentfonds.de/fds/IE00BD4TXV59-HALBJAHRBER-DE-20260630.pdf</loc></url>"
    "<url><loc>https://dl.avl-investmentfonds.de/fds/IE00BD4TXV59-RECHENSCHAFT-DE-20251231.pdf</loc></url>"
    "<url><loc>https://dl.avl-investmentfonds.de/fds/IE00BD4TXV59-KID-EN-20260316.pdf</loc></url>"
    "<url><loc>https://dl.avl-investmentfonds.de/fds/LU0950674332-HALBJAHRBER-EN-20260630.pdf</loc></url>"
    "</urlset>"
)


def test_avl_sitemap_and_locate():
    idx = fr.parse_avl_sitemap(SITEMAP)
    assert list(idx.columns) == ["isin", "type", "lang", "date", "url"] and len(idx) == 4
    refs = fr.rank(fr.avl_locate("IE00BD4TXV59", idx))
    assert [(r.kind, r.period_end, r.lang) for r in refs] == [
        ("semi-annual report", "2026-06-30", "DE"),
        ("annual report", "2025-12-31", "DE"),
    ]


def test_rank_prefers_newest_then_english():
    a = fr.ReportRef("X", "a", "annual report", "avl", "EN", "2025-12-31")
    b = fr.ReportRef("X", "b", "semi-annual report", "avl", "DE", "2026-06-30")
    c = fr.ReportRef("X", "c", "semi-annual report", "avl", "EN", "2026-06-30")
    assert [r.url for r in fr.rank([a, b, c])] == ["c", "b", "a"]


def test_wisdomtree_filename_and_kid_name():
    fn = "SEMI_ANNUAL_REPORT-EN-GB-IE00BZ56RN96-WisdomTree Global Quality Dividend Growth UCITS ETF - USD.pdf"
    assert fr.wisdomtree_fund_name(fn) == "WisdomTree Global Quality Dividend Growth UCITS ETF - USD"
    kid = [
        "Key Investor Information\nThis document provides you with key investor information about this fund. You are advised to read it so you can\n"
        "make an informed decision about whether to invest.\nExample Core World UCITS ETF (the \"Fund\"), a sub-fund of Example (Irl) ETF plc (the \"Company\"), class USD acc (ISIN:\nIE00BD4TXV59)"
    ]
    assert fr.kid_fund_name(kid, "IE00BD4TXV59") == "Example Core World UCITS ETF"
    assert fr.kid_fund_name(["Product: Example Fund - Biotech P USD\nISIN: LU0000000000"], "LU0000000000") == "Example Fund - Biotech P USD"


def test_portfolio_key_shared_by_share_classes():
    assert fr.portfolio_key("UBS Core S&P 500 UCITS ETF USD acc") == fr.portfolio_key("UBS Core S&P 500 UCITS ETF hEUR acc")
    assert fr.portfolio_key("Fidelity Global Quality Income UCITS ETF - Inc") == "fidelityglobalqualityincomeucitsetf"


def test_lookup_offline(monkeypatch, tmp_path):
    idx = fr.parse_avl_sitemap(SITEMAP.replace("IE00BD4TXV59", "IE0000000001"))
    seen = []

    def fake_pages(ref, cache_dir=None):
        seen.append(ref.url)
        path = fr._text_path(ref, cache_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")  # mark as cached for the reuse check
        if "HALBJAHRBER" in ref.url:
            return pages("german_translation")      # holds no matching sub-fund
        return pages("irish_umbrella_en")

    monkeypatch.setattr(fr, "report_pages", fake_pages)
    h = fr.lookup("IE0000000001", "Example Core World UCITS ETF USD acc", ["Example"], ("avl",), tmp_path, idx)
    assert validate_holdings(h.holdings) == []
    assert list(h.holdings.columns[: len(HOLDING_COLUMNS)]) == HOLDING_COLUMNS
    assert h.kind == "annual report"                 # the semi-annual report did not have the sub-fund
    assert h.notes[0] == "annual report, positions as of 2026-06-30"
    assert any("not its current daily holdings" in n for n in h.notes)
    assert any("Other reports tried" in n for n in h.notes)
    assert h.url.endswith("RECHENSCHAFT-DE-20251231.pdf")
    assert len(seen) == 2


def test_lookup_reuses_the_umbrella_report_across_share_classes(monkeypatch, tmp_path):
    import gzip

    xml = "".join(
        f"<url><loc>https://dl.avl-investmentfonds.de/fds/{i}-HALBJAHRBER-EN-20260630.pdf</loc></url>"
        for i in ("IE0000000001", "IE0000000002")
    )
    idx = fr.parse_avl_sitemap(xml)
    first = fr.avl_locate("IE0000000001", idx)[0]
    path = fr._text_path(first, tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(gzip.compress(json.dumps(REPORTS["irish_umbrella_en"]).encode()))

    def no_download(*a, **k):
        raise AssertionError("the umbrella report should not be downloaded again")

    monkeypatch.setattr(fr.http, "get", no_download)
    h1 = fr.lookup("IE0000000001", "Example Core World UCITS ETF USD acc", ["Example"], ("avl",), tmp_path, idx)
    h2 = fr.lookup("IE0000000002", "Example Core USA UCITS ETF USD dis", ["Example"], ("avl",), tmp_path, idx)
    assert h1.sub_fund == "Example Core World UCITS ETF" and h2.sub_fund == "Example Core USA UCITS ETF"
    assert "IE0000000002-HALBJAHRBER" in h2.url                    # cites its own link
    assert any("Text reused from the identical report filed under IE0000000001" in n for n in h2.notes)


def test_lookup_reports_missing_routes(monkeypatch, tmp_path):
    idx = fr.parse_avl_sitemap(SITEMAP)
    with pytest.raises(LookupError, match="No annual or semi-annual report"):
        fr.lookup("IE0000000002", "X Fund", ["X"], ("avl",), tmp_path, idx)


def test_products_from_six(tmp_path):
    six = pd.DataFrame(
        {
            "FundLongName": ["UBS Core MSCI World UCITS ETF USD acc", "UBS SMI ETF CHF dis", "PrivilEdge SICAV - Fidelity Technology", "UBS Other"],
            "ValorSymbol": ["SWDA", "SMICHA", None, None],
            "ValorNumber": ["1", "2", "3", "4"],
            "ISIN": ["IE00BD4TXV59", "CH0017142719", "LU1390458310", "LU0950674332"],
            "IssuerNameFull": ["UBS (Irl) ETF plc", "UBS Fund Management (Switzerland) AG", "Lombard Odier Funds (Europe) SA", "UBS (Lux) Fund Solutions"],
            "FundCurrency": ["USD", "CHF", "USD", "USD"],
            "AssetClassDesc": ["Equity"] * 4,
            "LegalStructureCountryDesc": ["Ireland", "Switzerland", "Luxembourg", "Luxembourg"],
            "ProductLineDesc": ["Exchange Traded Funds", "Exchange Traded Funds", "Sponsored Funds", "Sponsored Funds"],
        }
    )
    swiss_etf = lambda r: r.ISIN.startswith("CH") and r.ProductLineDesc.startswith("Exchange Traded")
    df = fr.products_from_six(six, "ubs_reports", r"\bUBS\b", {"IE00BD4TXV59"}, swiss_etf)
    assert validate_products(df) == []
    assert df["isin"].tolist() == ["IE00BD4TXV59", "CH0017142719"]       # LU0950674332 has no route here
    fid = fr.products_from_six(six, "fidelity_reports", r"\bFidelity\b", {"LU1390458310"})
    assert fid.empty                                                         # matched on the issuer, not the name


def test_adapters_registered():
    reg = discover()
    for name in ["ubs_reports", "invesco_reports", "pictet_reports", "wisdomtree_reports",
                 "fidelity_reports", "franklin_reports", "lombardodier_reports"]:
        ad = reg[name]
        assert ad.frequency == "semi-annual"
        assert "statutory" in ad.credit and ad.licence


# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------


@pytest.mark.network
def test_ubs_smi_etf_report_live(tmp_path):
    """One real report: UBS ETF (CH)'s annual report (about 1.6 MB) through extraETF's link.

    The AVL route is the same code path but needs the 50 MB sitemap index, so it is not
    rebuilt in a test.
    """
    h = fr.lookup("CH0017142719", "UBS SMI ETF CHF dis", ["UBS"], ("extraetf",), tmp_path)
    df = h.holdings
    assert validate_holdings(df) == []
    assert "SMI" in h.sub_fund
    assert 15 <= len(df) <= 40
    assert 95 < df["weight_pct"].sum() < 105
    assert h.notes[0].startswith(("semi-annual report, positions as of", "annual report, positions as of"))
    assert any("total fund assets" in n for n in h.notes)
