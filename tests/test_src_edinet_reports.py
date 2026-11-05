"""EDINET: listed Japanese funds' holdings from their annual securities reports."""

import io
import zipfile
from datetime import date

import pandas as pd
import pytest

from openbasket.sources import edinet_reports as er
from openbasket.sources import redistribution


def _table(rows):
    return "<table>" + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</table>"


#: A stock ETF: balance sheet, then a schedule whose last column (shares lent) is a number too.
STOCK_ETF = (
    "<p>貸借対照表</p>" + _table([["", "前期", "当期"], ["純資産合計", "900,000", "1,000,000"]])
    + "<p>附属明細表 第1 有価証券明細表 (1)株式</p>"
    + _table([["銘柄", "株式数", "評価額（円）", "備考 貸付株式"], ["単価", "金額"],
              ["トヨタ自動車", "100", "3,000.00", "300,000", "50"],
              ["ＩＮＰＥＸ", "50", "2,000.00", "100,000", ""],
              ["小計", "銘柄数：2", "400,000", ""]])
)

#: A foreign fund: one US ETF line in dollars, converted with the yen subtotal under it.
FOREIGN_ETF = (
    _table([["", "当期"], ["純資産合計", "1,000,000"]])
    + _table([["種類", "通貨", "銘柄", "券面総額", "評価額", "備考"],
              ["投資信託受益証券", "アメリカドル", "iShares Core S&P 500 ETF", "10.000", "6,000.000", ""],
              ["", "アメリカドル 小計", "10.000", "6,000.000", ""],
              ["", "", "", "(990,000)", ""],
              ["合計", "", "990,000", ""]])
)

#: A feeder: the fund holds a mother fund, whose own balance sheet and schedule follow.
FEEDER = (
    _table([["", "前期", "当期"], ["純資産合計", "450", "500"]])
    + _table([["種類", "通貨", "銘柄", "券面総額", "評価額", "備考"],
              ["親投資信託受益証券", "日本円", "バリュー マザーファンド", "1,000", "495", ""],
              ["小計", "銘柄数：1", "1,000", "495", ""], ["", "組入時価比率：99.0%", "", "100.0%", ""]])
    + "<p>（参考） バリュー マザーファンド 貸借対照表</p>" + _table([["純資産合計", "10,000"]])
    + _table([["種類", "通貨", "銘柄", "株式数", "評価額", "備考"], ["単価", "金額"],
              ["株式", "日本円", "トヨタ自動車", "2", "3,000.00", "6,000", ""],
              ["", "ソニーグループ", "1", "4,000.00", "4,000", ""]])
)


def test_stock_schedule_value_is_shares_times_price_not_the_lending_column():
    got = er.parse_report([STOCK_ETF])
    assert got["nav"] == 1_000_000  # the current period, not the prior one
    w = {l["name"]: l["weight_pct"] for l in got["lines"]}
    assert w == {"トヨタ自動車": pytest.approx(30.0), "INPEX": pytest.approx(10.0)}
    assert {l["kind"] for l in got["lines"]} == {"株式"} and er.asset_class("株式") == "equity"


def test_foreign_lines_use_the_yen_subtotal():
    got = er.parse_report([FOREIGN_ETF])
    (line,) = got["lines"]
    assert line["currency"] == "アメリカドル" and line["weight_pct"] == pytest.approx(99.0)
    assert er.asset_class(line["kind"]) == "fund"


def test_mother_fund_units_are_replaced_by_the_mother_funds_lines():
    got = er.parse_report([FEEDER])
    w = {l["name"]: l["weight_pct"] for l in got["lines"]}
    # 99% of the fund is in the mother fund; Toyota is 60% of the mother fund.
    assert w == {"トヨタ自動車": pytest.approx(59.4), "ソニーグループ": pytest.approx(39.6)}
    assert {l["via"] for l in got["lines"]} == {"バリュー マザーファンド"}
    assert any("mother fund" in n for n in got["notes"])


def test_report_without_balance_sheet_has_no_lines():
    got = er.parse_report(["<p>nothing</p>"])
    assert got["nav"] is None and got["lines"] == []


def test_japanese_names_resolve_through_the_company_list_only_when_unambiguous():
    comp = pd.DataFrame({"name": ["トヨタ自動車株式会社", "株式会社ＩＮＰＥＸ", "同名株式会社", "同名"],
                         "code": ["7203", "1605", "1111", "2222"]})
    codes = {"7203": "JP3633400001", "1605": "JP3294460005", "1111": "JP3000000001", "2222": "JP3000000002"}
    idx = er.name_index(comp, codes)
    assert idx == {"トヨタ自動車": "JP3633400001", "INPEX": "JP3294460005"}
    lines = pd.DataFrame({"name": ["トヨタ自動車", "INPEX", "同名", "国債"], "asset_class": ["equity", "equity", "equity", "debt"],
                          "weight_pct": [1.0, 1.0, 1.0, 1.0], "isin": None, "isin_source": None})
    r = er.resolve(lines, idx)
    assert r["isin"].tolist()[:3] == ["JP3633400001", "JP3294460005", None] and r["isin"].iloc[3] is None
    assert r["isin_source"].iloc[0] == "name_match"


def test_code_isins_from_findb_and_openfigi(tmp_path):
    pd.DataFrame({"symbol": ["7203.T", "AAPL"], "isin": ["JP3633400001", "US0378331005"]}).to_parquet(tmp_path / "eq.parquet")
    pd.DataFrame({"id_type": ["ID_ISIN"], "id_value": ["JP3294460005"],
                  "result": ['[{"ticker": "1605", "exchCode": "JT", "securityType": "Common Stock"}]']}
                 ).to_parquet(tmp_path / "figi.parquet")
    assert er.code_isins(tmp_path / "eq.parquet", tmp_path / "figi.parquet") == {"7203": "JP3633400001", "1605": "JP3294460005"}


def _zip(parts: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, html in parts.items():
            z.writestr(f"XBRL/PublicDoc/{name}", html)
    return buf.getvalue()


class FakeClient:
    LIST_COLUMNS = er.api.LIST_COLUMNS

    def __init__(self):
        self.calls = []

    def fund_documents(self, day):
        self.calls.append(f"list {day}")
        if str(day) == "2026-11-03":
            return [{"docID": "S100AAAA", "fundCode": "G00001", "docTypeCode": "120", "periodEnd": "2026-08-09",
                     "submitDateTime": "2026-11-03 09:00"},
                    {"docID": "S100BBBB", "fundCode": "G00002", "docTypeCode": "160", "periodEnd": "2026-08-09",
                     "submitDateTime": "2026-11-03 09:00"}]
        return []

    def download_document(self, doc_id, kind=1):
        self.calls.append(f"doc {doc_id}")
        return _zip({"0103010_honbun_x_ixbrl.htm": STOCK_ETF, "0000000_header_x_ixbrl.htm": "<table></table>"})


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = er.EdinetStore(tmp_path / "jp", client=FakeClient())
    funds = pd.DataFrame({"fund_code": ["G00001", "G00002"], "code": ["1475", None], "fund_name": ["ETF", "投信"],
                          "fund_type": ["内国投資信託受益証券"] * 2, "issuer": ["X", "X"]})
    monkeypatch.setattr(s, "fund_codes", lambda refresh_after_days=7: funds)
    monkeypatch.setattr(s, "company_codes", lambda refresh_after_days=7: pd.DataFrame({"name": ["トヨタ自動車株式会社"], "code": ["7203"]}))
    s.refresh(days=3, today=date(2026, 11, 5))
    return s


def test_refresh_reads_each_day_once_and_parses_the_latest_annual_report(store):
    calls = store.client.calls
    assert calls.count("doc S100AAAA") == 1 and not any("S100BBBB" in c for c in calls)
    store.refresh(days=3, today=date(2026, 11, 5))
    assert store.client.calls.count("list 2026-11-03") == 1 and store.client.calls.count("doc S100AAAA") == 1
    reps = store.reports()
    assert reps[["code", "docID", "periodEnd"]].values.tolist() == [["1475", "S100AAAA", "2026-08-09"]]


def test_holdings_by_tokyo_code(store):
    got = store.holdings("1475", {"トヨタ自動車": "JP3633400001"})
    assert got["as_of"] == "2026-08-09" and got["doc_id"] == "S100AAAA" and "S100AAAA" in got["url"]
    lines = got["lines"].set_index("name")
    assert lines.loc["トヨタ自動車", "isin"] == "JP3633400001"
    assert lines["weight_pct"].sum() == pytest.approx(40.0)
    assert store.holdings("9999") is None


def test_edinet_is_public():
    assert redistribution("edinet_japan") == "public"


def test_public_build_reads_edinet_for_a_local_only_issuers_japanese_etf(store, monkeypatch):
    from openbasket.lookthrough import FundRef
    from openbasket.providers import DataProviders
    from openbasket.sources.issuers import cmvm_portugal
    from tests.test_universe import FUND_MASTER, nport_holdings, security_master

    monkeypatch.setattr(cmvm_portugal, "name_indexes", lambda: (None, None))
    products = pd.DataFrame([{"issuer": "ishares", "site": "jp", "portfolio_id": "1", "isin": "JP3048120004",
                              "ticker": "1475", "fund_name": "iShares Core TOPIX ETF"}])
    p = DataProviders(nport_holdings(), security_master(), FUND_MASTER, pd.DataFrame(columns=["ticker", "isin"]), None,
                      source_order=["issuer", "nport", "edinet"], issuer_products=products, mode="public",
                      edinet={"root": str(store.root)})
    p._edinet_index = {"トヨタ自動車": "JP3633400001"}
    p._edinet_store = store
    ref = FundRef(None, "1475", "iShares Core TOPIX ETF", "ishares", issuer_id="ishares-jp:1")
    (h,) = p.holdings(ref)
    assert h.source == "edinet_japan" and h.as_of == "2026-08-09" and "S100AAAA" in h.source_url
    assert {l.isin for l in h.lines} == {"JP3633400001", None}
    assert p.source_status(ref).startswith("issuer=local_only")


#: MUFG-style: spaced headers and totals, each stock's Tokyo code before its name, and the
#: top-30 table (評価金額, 投資比率) that every report also has, which must not be read.
CODED = (
    _table([["", "前期", "当期"], ["", "純資産合計", "", "900", "1,000"]])
    + _table([["国/地域", "種類", "銘柄名", "数量", "評価金額(円)", "投資比率(%)"], ["日本", "株式", "ニッスイ", "1", "500", "50.0"]])
    + _table([["", "銘 柄", "株式数", "評価額", "備考"], ["単価", "金額"],
              ["1332", "ニッスイ", "2", "250.00", "500", ""], ["1605", "INPEX", "1", "300.00", "300", "貸付有価証券200株"],
              ["", "合 計", "3", "", "800", ""]])
)


def test_spaced_headers_codes_and_totals():
    got = er.parse_report([CODED])
    assert got["nav"] == 1000  # the label in the second cell
    lines = {l["name"]: l for l in got["lines"]}
    assert set(lines) == {"ニッスイ", "INPEX"}  # neither the top-30 table nor the total
    assert lines["ニッスイ"]["code"] == "1332" and lines["ニッスイ"]["weight_pct"] == pytest.approx(50.0)
    df = pd.DataFrame(got["lines"]).assign(isin=None, isin_source=None)
    r = er.resolve(df, {}, codes={"1332": "JP3718800000"})
    assert r.set_index("name").loc["ニッスイ", ["isin", "isin_source"]].tolist() == ["JP3718800000", "ticker_match"]
