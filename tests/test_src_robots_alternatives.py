"""Alternative routes for funds whose issuer serves holdings only from robots.txt-disallowed paths.

Everything here is offline: the PDFs are built in the test from a few
synthetic lines with the real layouts (bold headings, regular position lines).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pandas as pd
import pytest

from openbasket.sources import avl_documents as avl
from openbasket.sources import name_isin, report_route, robots
from openbasket.sources import schedule_tables as st
from openbasket.sources.issuers import (
    FetchContext,
    discover,
    validate_holdings,
    validate_products,
)
from openbasket.sources.issuers import lgim_reports as lg
from openbasket.sources.issuers import xtrackers_reports as xr

FIX = Path(__file__).parent / "fixtures" / "robots-alternatives"
AVL = "https://dl.avl-investmentfonds.de"


# -- a tiny PDF writer (two standard fonts, one text line per row) -------------------

def _esc(text: str) -> str:
    out = []
    for b in text.encode("cp1252"):
        c = chr(b)
        if c in "()\\":
            out.append("\\" + c)
        elif b > 126 or b < 32:
            out.append("\\%03o" % b)
        else:
            out.append(c)
    return "".join(out)


def make_pdf(pages: list[list[tuple[str, bool]]]) -> bytes:
    objs: dict[int, str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(f"{5 + 2 * i} 0 R" for i in range(len(pages))), len(pages)),
        3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        4: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    }
    for i, lines in enumerate(pages):
        y, parts = 800, []
        for text, bold in lines:
            parts.append(f"BT /{'F2' if bold else 'F1'} 8 Tf 30 {y} Td ({_esc(text)}) Tj ET")
            y -= 13
        stream = "\n".join(parts)
        objs[5 + 2 * i] = ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                           f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {6 + 2 * i} 0 R >>")
        objs[6 + 2 * i] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
    out, offsets = b"%PDF-1.4\n", {}
    for k in sorted(objs):
        offsets[k] = len(out)
        out += f"{k} 0 obj\n{objs[k]}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for k in sorted(objs):
        out += f"{offsets[k]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


B, R = True, False

IE_REPORT = [
    [("1", R), ("Xtrackers (IE) plc", B), ("Ungeprüfter Halbjahresbericht zum 30. Juni 2026", R)],
    [("2", R), ("Inhalt", B), ("Seite", R), ("Allgemeine Informationen 1", R), ("Anlageportfolio", R),
     ("Xtrackers Alpha World UCITS ETF 3", R), ("Xtrackers Gamma SW AP UCITS ETF* 4", R),
     ("Anhang zum Halbjahresabschluss 5", R)],
    [("3", R), ("XTRACKERS (IE) PLC", B), ("Xtrackers Alpha World UCITS ETF", B),
     ("Anlageportfolio zum 30. Juni 2026", B), ("Aktien - 99,50 % (31. Dezember 2025 - 99,00 %)", B),
     ("Anzahl der Anteile Wertpapier Wert in USD Anteil in % des Fonds", B), ("Software", B),
     ("1.000 Alpha Software Inc. 500.000 50,00", R), ("2.000 Beta Systems AG 300.000 30,00", R),
     ("500 Gamma Holdings PLC 195.000 19,50", R), ("995.000 99,50", B),
     ("Offene Devisentermingeschäfte - (0,10) %", B), ("01.07.2026 USD 1.000 EUR 900 (10) 0,00", R),
     ("Nicht realisierter Nettoverlust aus offenen Devisentermingeschäften (1.000) (0,10)", B),
     ("Sonstiges Nettovermögen - 0,60 % (31. Dezember 2025 - 1,00 %) 6.000 0,60", B),
     ("Inhabern rücknahmefähiger, gewinnberechtigter Anteile zuzuordnendes", B),
     ("Nettovermögen 1.000.000 100,00", B), ("Analyse des Gesamtvermögens", B),
     ("Übertragbare Wertpapiere 99,00", R)],
    [("4", R), ("XTRACKERS (IE) PLC", B), ("Xtrackers Gamma SW AP UCITS ETF*", B),
     ("Anlageportfolio zum 30. Juni 2026", B), ("Aktien - 98,00 %", B),
     ("100 Alpha Software Inc. 98.000 98,00", R), ("Sonstiges Nettovermögen - 2,00 % 2.000 2,00", B),
     ("Inhabern rücknahmefähiger, gewinnberechtigter Anteile zuzuordnendes Nettovermögen 100.000 100,00", B)],
    [("5", R), ("Anhang zum Halbjahresabschluss", B)],
]

LU_REPORT = [
    [("1", R), ("Xtrackers II", B), ("Société d'investissement à Capital Variable", R)],
    [("2", R), ("Inhaltsverzeichnis", B), ("Anlagebestand zum 30. Juni 2026 3", R),
     ("Xtrackers II BETA BOND UCITS ETF 3", R), ("Anhang zum Halbjahresabschluss 4", R)],
    [("Xtrackers II", B), ("Anlagebestand", B), ("3", R),
     ("Beschreibung Nominalwert Währung Einstandskosten Marktwert % des Nettovermögens", B),
     ("Anleihen", B), ("Deutschland", B), ("Bund Example Anleihe 1.00%", R),
     ("22 - 15.08.30 1.000.000 EUR 990.000 980.000 60,00", R),
     ("Example Corp. 2.50% 24 - 01.01.29 500.000 EUR 400.000 390.000 39,00", R),
     ("1.390.000 1.370.000 99,00", B), ("Summe - Anleihen 1.390.000 1.370.000 99,00", B),
     ("SUMME ANLAGEBESTAND 1.390.000 1.370.000 99,00", B), ("Xtrackers II BETA BOND UCITS ETF", B)],
    [("4", R), ("Anhang zum Halbjahresabschluss", B)],
]

LG_REPORT = [
    [("June 2026", R), ("Legal & General", B), ("UCITS ETF Plc", B), ("Annual Report and Audited Financial Statements", R)],
    [("Contents", B), ("Notes to the financial statements 3", R), ("Schedule of Investments 3", R),
     ("Additional information 4", R)],
    [("June 2026 Legal & General UCITS ETF Plc", R), ("L&G Synthetic Leaders UCITS ETF", B),
     ("Schedule of Investments", B), ("As at 30 June 2026", B), ("Nominal Description Value % of Net Assets", B),
     ("Common Stock (30 June 2025: 99.00%)", B), ("1,000 Alpha Software Inc 1 600,000 60.00", R),
     ("2,000 Beta Systems AG 390,000 39.00", R), ("17,235 Tiny Co 1 54 –", R),
     ("Corporate Bonds (30 June 2025: 1.00%)", B), ("30,0001Example Bond Ltd 5.00% 01/01/2030 9,000 0.90", R),
     ("Total Investments", B), ("Common Stock 990,054 99.00", R), ("Other Assets and Liabilities 1,000 0 .10", R),
     ("Net Assets Attributable to Holders of Redeemable Participating Shares 1,000,000 100.00", B)],
    [("4", R), ("Additional information", B)],
]


class Resp:
    def __init__(self, status: int, body: bytes):
        self.status_code = status
        self.content = body
        self.text = body.decode("utf-8", "replace")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeWeb:
    def __init__(self, routes: dict[str, bytes | str]):
        self.routes = {k: v.encode() if isinstance(v, str) else v for k, v in routes.items()}
        self.calls: list[str] = []

    def __call__(self, url: str) -> Resp:
        self.calls.append(url)
        return Resp(200, self.routes[url]) if url in self.routes else Resp(404, b"not found")


def _master(tmp_path: Path) -> Path:
    df = pd.DataFrame([
        ("US00ALPHA011", "ALPHA SOFTWARE INC", "ALPHA SOFTWARE INC", "EC", "US", 50),
        ("DE000BETA021", "Beta Systems AG", "Beta Systems AG", "EC", "DE", 10),
        ("GB00GAMMA039", "Gamma Holdings PLC", "Gamma Holdings PLC", "EC", "GB", 30),
        ("US00ALPHA0A4", "GAMMA HOLDINGS PLC", "GAMMA HOLDINGS PLC SP ADR", "EC", "GB", 5),
        ("IE000SYNTH77", "Example Corp", "EXAMPLE CORP 2.5% 2029", "DBT", "US", 9),
    ], columns=["isin", "issuer_name", "title", "asset_cat", "country", "n_positions"])
    p = tmp_path / "security_master.parquet"
    df.to_parquet(p)
    return p


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr(avl, "_downloads", 0)
    monkeypatch.setenv("OPENBASKET_SECURITY_MASTER", str(_master(tmp_path)))
    report_route._MASTER.clear()
    return FakeWeb({
        "https://etf.dws.com/en-gb/sitemap.xml": (FIX / "dws_sitemap.xml").read_text(),
        avl.SITEMAP: (FIX / "avl_sitemap.xml").read_text(),
        f"{AVL}/sitemaps/sitemap-fds1.xml": (FIX / "avl_sitemap_fds1.xml").read_text(),
        f"{AVL}/fds/IE000SYNTH10-HALBJAHRBER-DE-20260630.pdf": make_pdf(IE_REPORT),
        f"{AVL}/fds/LU000SYNTH44-HALBJAHRBER-DE-20260630.pdf": make_pdf(LU_REPORT),
        f"{AVL}/fds/IE000LGSYN19-RECHENSCHAFT-EN-20260630.pdf": make_pdf(LG_REPORT),
        lg.SITEMAPS[0]: (FIX / "landg_sitemap.xml").read_text(),
    })


# -- robots.txt -------------------------------------------------------------------

def test_robots_wildcards_and_groups_follow_rfc9309():
    st_ = robots.parse((FIX / "dws_robots.txt").read_text(), host="etf.example.com")
    assert not st_.allows("https://etf.example.com/api/pdp/en-gb/etf/IE000SYNTH10/holdings")
    assert not st_.allows("https://etf.example.com/etfdata/export/GBR/ENG/excel/product/constituent/IE000SYNTH10/")
    assert not st_.allows("https://etf.example.com/en-gb/etf-products/?q=1")
    assert st_.allows("https://etf.example.com/en-gb/sitemap.xml")
    assert st_.allows("https://etf.example.com/en-gb/IE000SYNTH10-alpha-world-ucits-etf-1c/")
    lib = robots.parse((FIX / "avl_robots.txt").read_text(), host="dl.example.org")
    assert lib.allows("https://dl.example.org/fds/IE000SYNTH10-HALBJAHRBER-DE-20260630.pdf")
    assert lib.allows("https://dl.example.org/sitemaps/sitemap-fds1.xml")
    assert not lib.allows("https://dl.example.org/search?q=x")


def test_robots_missing_file_allows_and_server_error_disallows(monkeypatch):
    robots._CACHE.clear()
    assert robots.status("https://none.example", fetch=lambda u: Resp(404, b"")).allows("https://none.example/x")
    assert not robots.status("https://down.example", fetch=lambda u: Resp(503, b"")).allows("https://down.example/x")

    def boom(u):
        raise OSError("no route")

    assert not robots.status("https://gone.example", fetch=boom).allows("https://gone.example/x")


def test_robots_get_refuses_disallowed_paths_without_a_request(monkeypatch):
    robots._CACHE.clear()
    robots._CACHE["etf.dws.com"] = robots.parse((FIX / "dws_robots.txt").read_text(), host="etf.dws.com")
    sent = []
    monkeypatch.setattr(robots.http, "get", lambda url, **kw: sent.append(url))
    with pytest.raises(robots.RobotsDisallowed):
        robots.get("https://etf.dws.com/api/pdp/en-gb/etf/IE000SYNTH10/holdings")
    robots.get("https://etf.dws.com/en-gb/sitemap.xml")
    assert sent == ["https://etf.dws.com/en-gb/sitemap.xml"]
    robots._CACHE.clear()


# -- schedule parsing ---------------------------------------------------------------

def _lines(rows):
    return [st.Line(t, b) for t, b in rows]


def test_irish_layout_parses_positions_fx_and_residual():
    s = st.parse_schedule({0: _lines(IE_REPORT[2])}, "Xtrackers Alpha World UCITS ETF")
    h = s.holdings
    assert s.total_pct == 100.0
    assert list(h["name"][:3]) == ["Alpha Software Inc.", "Beta Systems AG", "Gamma Holdings PLC"]
    assert list(h["quantity"][:3]) == [1000.0, 2000.0, 500.0]
    assert set(h["asset_class"][:3]) == {"equity"}
    assert h.iloc[:3]["group"].tolist() == ["Software"] * 3
    fx = h[h["asset_class"] == "derivative (foreign exchange)"]
    assert fx["weight_pct"].tolist() == [-0.10]
    res = h[h["residual"].astype(bool)]
    assert res["name"].tolist() == ["Sonstiges Nettovermögen"] and res["weight_pct"].tolist() == [0.60]
    assert round(h["weight_pct"].sum(), 6) == 100.0
    assert "Übertragbare" not in " ".join(h["name"])     # nothing read after the net-assets total


def test_luxembourg_layout_joins_wrapped_names_and_skips_subtotals():
    s = st.parse_schedule({0: _lines(LU_REPORT[2])}, "Xtrackers II BETA BOND UCITS ETF")
    h = s.holdings
    assert h["name"].tolist() == ["Bund Example Anleihe 1.00% 22 - 15.08.30", "Example Corp. 2.50% 24 - 01.01.29"]
    assert h["currency"].tolist() == ["EUR", "EUR"]
    assert h["quantity"].tolist() == [1_000_000.0, 500_000.0]
    assert h["market_value"].tolist() == [980_000.0, 390_000.0]
    assert h["weight_pct"].tolist() == [60.0, 39.0]
    assert set(h["asset_class"]) == {"debt"}
    assert h["group"].tolist() == ["Deutschland", "Deutschland"]
    assert s.total_pct is None and s.investments_pct == 99.0


def test_lg_layout_footnotes_dashes_and_recap():
    s = st.parse_schedule({0: _lines(LG_REPORT[2])}, "L&G Synthetic Leaders UCITS ETF")
    h = s.holdings
    assert h["name"].tolist() == ["Alpha Software Inc", "Beta Systems AG", "Tiny Co",
                                  "Example Bond Ltd 5.00% 01/01/2030", "Other Assets and Liabilities"]
    assert h["weight_pct"].tolist() == [60.0, 39.0, 0.0, 0.90, 0.10]
    assert h.loc[3, "quantity"] == 30_000.0 and h.loc[3, "asset_class"] == "debt"
    assert "Common Stock" not in h["name"].tolist()          # the recap after "Total Investments"
    assert s.total_pct == 100.0


def test_split_line_shapes():
    assert st.split_line("1.000 Alpha Software Inc. 500.000 50,00", ",")["qty"] == 1000.0
    lu = st.split_line("Example Corp. 2.50% 24 - 01.01.29 500.000 EUR 400.000 390.000 39,00", ",")
    assert (lu["currency"], lu["qty"], lu["value"], lu["pct"]) == ("EUR", 500_000.0, 390_000.0, 39.0)
    assert st.split_line("Hologic, Inc. 34.019 USD — 3 0,00", ",")["name"] == "Hologic, Inc."
    assert st.split_line("01.07.2026 USD 1.000 EUR 900 (10) 0,00", ",")["fx"]
    assert st.split_line("Scentre Group Trust 1/Scentre Group Trust 2 1.45%", ",") is None   # coupon, not a weight
    assert st.split_line("Republic of Austria Government Bonds 3.20% 24 -", ",") is None
    assert st.number("(1.234,50)", ",") == -1234.5 and st.number("1,234.50", ".") == 1234.5


def test_contents_and_page_ranges():
    texts = ["\n".join(t for t, _ in page) for page in IE_REPORT]
    sub = st.subfund_pages(texts, "Xtrackers")
    assert sub == {"Xtrackers Alpha World UCITS ETF": [2], "Xtrackers Gamma SW AP UCITS ETF*": [3]}
    lu = st.subfund_pages(["\n".join(t for t, _ in p) for p in LU_REPORT], "Xtrackers")
    assert lu == {"Xtrackers II BETA BOND UCITS ETF": [2]}
    lg_pages = st.subfund_pages(["\n".join(t for t, _ in p) for p in LG_REPORT], "L&G")
    assert lg_pages == {"L&G Synthetic Leaders UCITS ETF": [2]}


def test_title_matching():
    titles = ["Xtrackers MSCI WORLD SW AP UCITS ETF*", "Xtrackers MSCI World Swap II UCITS ETF",
              "Xtrackers II EUR CORPORATE BOND UCITS ETF", "Xtrackers Artificial Intelligence and Big Data UCITS ETF",
              "Xtrackers MSCI EM EUROPE, MIDDLE EAST & AFRICA ESG SW AP UCITS ETF*"]
    m = lambda slug: st.match_title(xr.fund_key(slug), titles, "Xtrackers")  # noqa: E731
    assert m("msci-world-swap-ucits-etf-1c") == titles[0]
    assert m("msci-world-swap-ii-ucits-etf-1c") == titles[1]
    assert m("eur-corporate-bond-ucits-etf-1d") == titles[2]
    assert m("artificial-intelligence-big-data-ucits-etf-1c") == titles[3]
    assert m("msci-em-europe-middle-east-africa-esg-swap-ucits-etf-1c") == titles[4]
    assert m("eur-corporate-bond-short-duration-ucits-etf-1c") is None
    assert st.match_title("worldswap", titles, "Xtrackers") is None
    lg_titles = ["L&G S&P 100 UCITS ETF*", "L&G S&P 100 Equal Weight UCITS ETF*", "L&G Gerd Kommer Multifactor Equity UCITS ETF",
                 "L&G UK Quality Dividends Equal Weight UCITS ETF", "L&G Europe ex-UK Quality Dividends Equal Weight UCITS ETF"]
    assert st.match_title(lg.fund_key("ETF", "UK-Quality-Dividends"), lg_titles, "L&G", loose=True) == lg_titles[3]
    assert st.match_title(lg.fund_key("ETF", "SP-100"), lg_titles, "L&G", loose=True) == lg_titles[0]
    assert st.match_title(lg.fund_key("ETF", "Gerd-Kommer"), lg_titles, "L&G", loose=True) == lg_titles[2]
    assert st.match_title(lg.fund_key("ETF", "Gerd-Kommer"), lg_titles, "L&G") is None


# -- names to ISINs -------------------------------------------------------------------

def test_name_matching(tmp_path):
    idx = name_isin.build_index(name_isin.load_master(_master(tmp_path)))
    assert name_isin.normalise("Alphabet, Inc. A-Aktien") == "alphabet"
    assert name_isin.normalise("Marriott International Inc/MD") == "marriott international"
    assert name_isin.pick(idx["gamma holdings"], adr=False) == "GB00GAMMA039"
    assert name_isin.pick(idx["gamma holdings"], adr=True) == "US00ALPHA0A4"
    assert name_isin.pick([("US00ALPHA011", 5, ""), ("GB00GAMMA039", 5, "")], adr=False) == "GB00GAMMA039"
    assert name_isin.pick([("GB00GAMMA039", 5, ""), ("DE000BETA021", 5, "")], adr=False) is None   # no clear winner
    assert name_isin.pick([("GB00GAMMA039", 5, "X CL A"), ("DE000BETA021", 50, "X CL C")], False, "A") == "GB00GAMMA039"
    frame = pd.DataFrame({"isin": [None, None, None], "name": ["Alpha Software Inc.", "Example Corp", "Unknown AG"],
                          "asset_class": ["equity", "debt", "equity"], "weight_pct": [50.0, 30.0, 20.0]})
    out, counts = name_isin.resolve(frame, idx)
    assert out["isin"].tolist() == ["US00ALPHA011", None, None]
    assert out["isin_source"].tolist() == ["name_match", None, None]
    assert counts == {"tried": 2, "matched": 1, "matched_weight": 50.0}


# -- AVL library -------------------------------------------------------------------------

def test_avl_index_and_order(tmp_path, web):
    idx = avl.index(tmp_path, fetch=web)
    assert len(idx) == 4 and "KID" not in " ".join(idx["kind"])
    refs = avl.locate("IE000SYNTH10", idx)
    assert [(r.kind, r.period) for r in refs] == [("semi-annual report", "2026-06-30"), ("annual report", "2025-12-31")]
    assert avl.umbrella_name("June 2026\nLegal & General\nUCITS ETF Plc\nAnnual Report") == "Legal & General UCITS ETF Plc"
    assert avl.umbrella_name("Xtrackers II\nSociété") == "Xtrackers II"
    assert avl.parse_sitemap_urls("<loc>https://x.example/ETF/Health-&amp;-Care/</loc>") == ["https://x.example/ETF/Health-&-Care/"]
    assert lg.fund_key("ETF", "Healthcare-Technology-&-Innovation") == "healthcaretechnologyinnovationucitsetf"


# -- adapters ------------------------------------------------------------------------------

def test_xtrackers_products(tmp_path, web):
    prods = xr.products(tmp_path, fetch=web)
    assert validate_products(prods) == []
    assert sorted(prods["isin"]) == ["IE000SYNTH10", "IE000SYNTH28", "IE000SYNTH36", "IE000SYNTH69", "LU000SYNTH44"]
    by = prods.set_index("isin")
    assert by.loc["IE000SYNTH10", "portfolio_id"] == by.loc["IE000SYNTH28", "portfolio_id"] == "alphaworlducitsetf"
    assert by.loc["IE000SYNTH36", "replication"] == "swap"
    assert by.loc["IE000SYNTH10", "fund_name"] == "Xtrackers Alpha World UCITS ETF 1C"
    assert not any("/api/" in u or "/etfdata/" in u for u in web.calls)


def test_xtrackers_holdings_from_report(tmp_path, web, monkeypatch):
    prods = xr.products(tmp_path, fetch=web)
    ctx = FetchContext(cache_dir=str(tmp_path))
    row = prods.set_index("isin").loc["IE000SYNTH10"].to_dict() | {"isin": "IE000SYNTH10"}
    h = xr.holdings(row, ctx, fetch=web, catalogue=prods)
    assert h.as_of == "2026-06-30"
    assert h.notes[0] == "semi-annual report, positions as of 2026-06-30"
    assert "robots.txt" in h.notes[1]
    assert h.url == f"{AVL}/fds/IE000SYNTH10-HALBJAHRBER-DE-20260630.pdf"
    f = h.holdings
    assert validate_holdings(f) == []
    assert round(f["weight_pct"].sum(), 6) == 100.0
    assert f.loc[f["name"] == "Alpha Software Inc.", "isin"].item() == "US00ALPHA011"
    assert f.loc[f["name"] == "Gamma Holdings PLC", "isin"].item() == "GB00GAMMA039"
    assert set(f.loc[f["isin"].notna(), "isin_source"]) == {"name_match"}

    # The 1D share class has no report of its own: the umbrella report already read is reused.
    pdfs_before = sum(u.endswith(".pdf") for u in web.calls)
    row2 = prods.set_index("isin").loc["IE000SYNTH28"].to_dict() | {"isin": "IE000SYNTH28"}
    h2 = xr.holdings(row2, ctx, fetch=web, catalogue=prods)
    assert sum(u.endswith(".pdf") for u in web.calls) == pdfs_before
    assert h2.holdings["weight_pct"].tolist() == f["weight_pct"].tolist()
    assert any("filed under another share class" in n for n in h2.notes)

    swap = prods.set_index("isin").loc["IE000SYNTH36"].to_dict() | {"isin": "IE000SYNTH36"}
    hs = xr.holdings(swap, ctx, fetch=web, catalogue=prods)
    assert any(n.startswith("swap-based ETF") for n in hs.notes)

    lu = prods.set_index("isin").loc["LU000SYNTH44"].to_dict() | {"isin": "LU000SYNTH44"}
    hl = xr.holdings(lu, ctx, fetch=web, catalogue=prods)
    assert hl.holdings["weight_pct"].sum() == 99.0
    assert any("are unresolved" in n for n in hl.notes)


def test_xtrackers_stand_in_is_labelled(tmp_path, web, monkeypatch):
    prods = xr.products(tmp_path, fetch=web)
    ctx = FetchContext(cache_dir=str(tmp_path))
    new = prods.set_index("isin").loc["IE000SYNTH69"].to_dict() | {"isin": "IE000SYNTH69"}
    with pytest.raises(LookupError):
        xr.holdings(new, ctx, fetch=web, catalogue=prods)
    monkeypatch.setitem(xr.STAND_INS, "alphaworldiiucitsetf",
                        {"peer_key": "alphaworlducitsetf", "index": "Alpha World", "evidence": "test"})
    h = xr.holdings(new, ctx, fetch=web, catalogue=prods)
    assert h.notes[0].startswith("holdings of Xtrackers Alpha World UCITS ETF 1C (IE000SYNTH10) on the same index")
    assert "used as a stand-in" in h.notes[0] and "not the fund's own holdings" in h.notes[0]


def test_lgim_products_and_holdings(tmp_path, web, monkeypatch):
    for sm in lg.SITEMAPS[1:]:
        web.routes.pop(sm, None)
    prods = lg.products(tmp_path, fetch=web)
    assert validate_products(prods) == []
    assert sorted(prods["isin"]) == ["IE000LGSYN19", "IE000LGSYN27"]          # ETF pages only by default
    assert not any("/srp/" in u for u in web.calls)
    row = prods.set_index("isin").loc["IE000LGSYN27"].to_dict() | {"isin": "IE000LGSYN27"}
    h = lg.holdings(row, FetchContext(cache_dir=str(tmp_path)), fetch=web, catalogue=prods)
    assert h.notes[0] == "annual report, positions as of 2026-06-30"
    assert "Legal & General UCITS ETF Plc" in h.notes[2]
    f = h.holdings
    assert validate_holdings(f) == [] and round(f["weight_pct"].sum(), 6) == 100.0
    assert f.loc[f["name"] == "Beta Systems AG", "isin"].item() == "DE000BETA021"


def test_umbrella_duplicates_are_not_fetched_again(tmp_path, web):
    pdf = make_pdf(IE_REPORT)
    refs = [avl.ReportRef(i, "semi-annual report", "DE", "2026-06-30", f"{AVL}/fds/{i}-HALBJAHRBER-DE-20260630.pdf")
            for i in ("IE000SYNTH10", "IE000SYNTH28", "IE000SYNTH36")]
    for r in refs:
        web.routes[r.url] = pdf
    first = avl.fetch_report(refs[0], "Xtrackers", tmp_path, fetch=web)
    assert not avl.known_duplicate(refs[2], "Xtrackers", tmp_path)
    second = avl.fetch_report(refs[1], "Xtrackers", tmp_path, fetch=web)
    assert second.pdf == first.pdf and len(avl.cached_reports(tmp_path)) == 1
    assert avl.known_duplicate(refs[2], "Xtrackers", tmp_path)
    idx = pd.DataFrame([{"isin": "IE000SYNTH36", "kind": refs[2].kind, "lang": "DE", "period": refs[2].period,
                         "url": refs[2].url}])
    rep, title, tried, _ = report_route.find_schedule("IE000SYNTH36", "neverlaunchedfundxyzucitsetf", "Xtrackers",
                                                      idx, [], tmp_path, fetch=web)
    assert rep is None and refs[2].url not in web.calls and "duplicates" in tried[0]


def test_download_budget(tmp_path, web, monkeypatch):
    monkeypatch.setattr(avl, "MAX_DOWNLOADS", 0)
    idx = avl.index(tmp_path, fetch=web)
    with pytest.raises(avl.DownloadBudgetExceeded):
        avl.fetch_report(avl.locate("IE000SYNTH10", idx)[0], "Xtrackers", tmp_path, fetch=web)


def test_registered_and_flagged():
    reg = discover()
    assert {"xtrackers_reports", "lgim_reports"} <= set(reg)
    flags = {
        "openbasket.sources.robots": "public",
        "openbasket.sources.schedule_tables": "public",
        "openbasket.sources.name_isin": "public",
        "openbasket.sources.avl_documents": "local_only",
        "openbasket.sources.report_route": "local_only",
        "openbasket.sources.issuers.xtrackers_reports": "local_only",
        "openbasket.sources.issuers.lgim_reports": "local_only",
    }
    for mod, flag in flags.items():
        assert importlib.import_module(mod).REDISTRIBUTION == flag


@pytest.mark.network
def test_live_xtrackers_msci_world(tmp_path):
    prods = xr.products(tmp_path)
    row = prods[prods["isin"] == "IE00BJ0KDQ92"].iloc[0].to_dict()
    h = xr.holdings(row, FetchContext(cache_dir=str(tmp_path)), catalogue=prods)
    assert validate_holdings(h.holdings) == []
    assert 99.0 <= h.holdings["weight_pct"].sum() <= 101.0
    assert len(h.holdings) > 1000
