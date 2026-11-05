"""German, Austrian and Liechtenstein fund reports: the schedule parser, the Bundesanzeiger and IFM."""

from datetime import date

import pandas as pd
import pytest

from openbasket.sources import bundesanzeiger as ba
from openbasket.sources import kag_reports as kr
from openbasket.sources import redistribution
from openbasket.sources.issuers import HOLDING_COLUMNS, discover
from openbasket.sources.issuers import ifm_li

GERMAN = """Inhaltsverzeichnis
Vermögensaufstellung zum 30.06.2026 7
Halbjahresbericht für Beispielfonds
Vermögensaufstellung zum 30.06.2026
Gattungsbezeichnung ISIN Markt Stück bzw. Anteile Bestand Käufe Verkäufe Kurs Kurswert %
Aktien
Apple Inc. US0378331005 STK 1.000 0 0 USD 250,0000 213.000,00 60,00
Verzinsliche Wertpapiere
0,7500 % A.P.Moeller-Maersk A/S EO-Medium-Term Nts 2021(21/31) XS2410368042 EUR 300 0 0 % 87,2036 106.500,00 30,00
Investmentanteile
Beispiel Rentenfonds DE0008491051 ANT 100 0 0 EUR 10,0000 1.000,00 0,28
Während des Berichtszeitraumes abgeschlossene Geschäfte
Microsoft Corp. US5949181045 STK 500 500 0 0 1,00 0,50 0,25""".splitlines()


def test_german_schedule_reads_isin_weight_and_section():
    df = kr.parse_lines(GERMAN, own={"DE000A12BTT8"})
    assert df["isin"].tolist() == ["US0378331005", "XS2410368042", "DE0008491051"]  # not the transactions
    assert df["weight_pct"].tolist() == [60.0, 30.0, 0.28]
    assert df["asset_class"].tolist() == ["equity", "debt", "fund"]
    assert df.loc[0, "name"] == "Apple Inc."
    assert kr.plausible(df) and kr.total(df) == pytest.approx(90.28)


def test_lines_before_the_schedule_heading_are_not_holdings():
    lines = ["Wertpapierliste des Masterfonds", "Other Corp US0378331005 STK 1 0 0 USD 1,00 1,00 50,00"] + GERMAN[3:]
    assert kr.total(kr.parse_lines(lines)) == pytest.approx(90.28)


def test_own_isins_and_tables_of_share_classes_are_skipped():
    lines = ["Vermögensaufstellung", "2025/2026 Ausschütter AT0000724257 EUR 77,64 2,30 0,00 1,07"]
    assert kr.parse_lines(lines, own={"AT0000724257"}).empty


def test_swiss_layout_with_isin_on_its_own_line():
    lines = ["Vermögensinventar / Käufe und Verkäufe", "USD Johnson & Johnson", "US4781601046",
             "965.00 435.00 810.00 227.25 184’073 92.08%", "Vermögensinventar / Käufe und Verkäufe 15",
             "CH0012032048", "CHF Roche 10.00 0.00 10.00 250.00 2’500 5.00%"]
    df = kr.parse_lines(lines)
    assert dict(zip(df["isin"], df["weight_pct"])) == {"US4781601046": 92.08, "CH0012032048": 5.0}


def test_report_for_several_funds_is_split_by_fund_total_and_name():
    def block(isin, pct):
        return [f"Fonds {isin} ANT 1 0 0 EUR 1,00 1,00 {pct}"]
    lines = (["Halbjahresbericht"] + block("DE000A1JDWK4", "97,00") + ["III. Fondsvermögen EUR 1.000,00 100,00",
             "Anteilwert HANSAbalance A EUR 85,970"] + block("DE0008479098", "98,00")
             + ["Fondsvermögen EUR 2.000,00 100,00", "Anteilwert HANSAcentro EUR 95,232"])
    assert kr.parse_lines(lines, fund_name="HANSAcentro")["isin"].tolist() == ["DE0008479098"]
    assert kr.parse_lines(lines, fund_name="HANSAbalance Inhaber-Anteile A")["isin"].tolist() == ["DE000A1JDWK4"]


def test_html_rows_with_isin_glued_to_name():
    html = ("<p>Vermögensaufstellung zum 31.03.2026</p><table><tr><td>US0378331005Apple Inc.</td><td>STK</td>"
            "<td>3.744.928,00</td><td>USD</td><td>253,7900</td><td>824.950.331,67</td><td>95,95</td></tr></table>")
    df = kr.parse_html(html)
    assert df["isin"].tolist() == ["US0378331005"] and df["weight_pct"].tolist() == [95.95]
    out = kr.as_holdings(df)
    assert list(out.columns) == HOLDING_COLUMNS


RESULTS = """
<div class="row"><div class="col-md-5"><div class="info">
<a href="https://www.bundesanzeiger.de/pub/de/suchen2?3-1.-search~table~panel-rows-0-search~table~row~panel-publication~link">Anlage-/Vertragsbedingungen</a>
<br/>UniGlobal<br/>DE0008491051</div></div><div class="col-md-2"><div class="date">02.10.2026</div></div></div>
<div class="row back"><div class="col-md-5"><div class="info">
<a href="https://www.bundesanzeiger.de/pub/de/suchen2?3-1.-search~table~panel-rows-1-search~table~row~panel-publication~link">Halbjahresbericht01.10.2025 - 31.03.2026</a>
<br/>UniGlobal<br/>DE0008491051</div></div><div class="col-md-2"><div class="date">29.05.2026</div></div></div>
<div class="row"><div class="col-md-5"><div class="info">
<a href="https://www.bundesanzeiger.de/pub/de/suchen2?3-1.-search~table~panel-rows-2-search~table~row~panel-publication~link">Jahresbericht zum 30. September 2025</a>
<br/>UniGlobal<br/>DE0008491051</div></div><div class="col-md-2"><div class="date">30.12.2025</div></div></div>
"""


def test_bundesanzeiger_results_and_newest_report():
    rs = ba.parse_results(RESULTS)
    assert [r.published for r in rs] == ["2026-10-02", "2026-05-29", "2025-12-30"]
    assert rs[2].period_end == "2025-09-30" and rs[1].period_end == "2026-03-31"
    best = ba.newest_report(rs, "DE0008491051")
    assert best.title.startswith("Halbjahresbericht") and "rows-1" in best.link
    assert ba.newest_report(rs, "DE0000000000") is None
    assert ba.schedule_date("<p>Vermögensaufstellung zum 28.02.2026</p>") == "2026-02-28"


class FakeClient:
    def __init__(self):
        self.calls = []

    def search(self, query):
        self.calls.append(f"search {query}")
        return ba.parse_results(RESULTS)

    def page(self, link):
        self.calls.append("page")
        return ("<table><tr><td>US0378331005Apple Inc.</td><td>STK</td><td>1,00</td><td>USD</td><td>1,00</td>"
                "<td>1,00</td><td>97,50</td></tr></table>")


def test_bundesanzeiger_store_searches_once_a_month(tmp_path):
    store = ba.Store(tmp_path, client=FakeClient())
    lines, meta = store.holdings("DE0008491051", today=date(2026, 11, 5), fund_name="UniGlobal")
    assert meta["as_of"] == "2026-03-31" and meta["total_pct"] == pytest.approx(97.5)
    assert lines["isin"].tolist() == ["US0378331005"]
    store.holdings("DE0008491051", today=date(2026, 11, 20), fund_name="UniGlobal")
    assert store.client.calls == ["search DE0008491051", "page"]  # cached
    store.holdings("DE0008491051", today=date(2026, 12, 20), fund_name="UniGlobal")
    assert store.client.calls.count("search DE0008491051") == 2 and store.client.calls.count("page") == 1


def test_ifm_documents_page():
    html = ("<table><tr><td>3R Alternative Fund -CHF-</td><td>LI1332866527</td><td>133286652</td><td>CHF</td></tr>"
            "<tr><td>Other</td><td>not an isin</td></tr></table>")
    d = ifm_li.parse_docs(html)
    assert d.to_dict("records") == [{"isin": "LI1332866527", "fund_name": "3R Alternative Fund -CHF-", "currency": "CHF"}]


def test_adapters_are_local_only():
    adapters = discover()
    for name in ("bundesanzeiger_de", "avl_kag", "ifm_li"):
        assert adapters[name].redistribution == "local_only" and redistribution(name) == "local_only"
