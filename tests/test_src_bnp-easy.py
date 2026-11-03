"""BNP Paribas Easy adapter: share search, fundsheet and inventory workbook.

Fixtures are synthetic: the real field names and layout, invented rows.
"""

import io
import json
from datetime import datetime
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from openbasket.sources.issuers import (
    HOLDING_COLUMNS,
    FetchContext,
    discover,
    validate_holdings,
    validate_products,
)
from openbasket.sources.issuers import bnp_easy

FIXTURES = Path(__file__).parent / "fixtures" / "bnp-easy"


def _inventory(replication="Full", rows=None, french=False, total=1):
    """An inventory workbook laid out like BNP Paribas AM's (columns B to M)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "IE00B4L5Y983"
    rows = rows if rows is not None else [
        ("Equity", "EXAMPLE CORP A", "US0378331005", 0.5012),
        ("Equity", "EXAMPLE CORP B", "NL0010273215", 0.3),
        ("Equity", "EXAMPLE CORP C", "XX0000000000", 0.1987),
        ("Equity", "EXAMPLE TINY", "CH0012005267", 0),
    ]
    labels = (
        ("Date des données", "Nom du fonds", "Code ISIN", "Réplication", "Classe d'actifs", "Nom de l'entité", "Poids")
        if french
        else ("Data Date", "Fund Name", "ISIN", "Replication", "Asset Class", "Name of entity", "Weight")
    )

    def block(start):
        ws.cell(start + 1, 11, labels[0])
        ws.cell(start + 1, 12, datetime(2026, 10, 27))
        ws.cell(start + 4, 2, "Inventory for BNP PARIBAS EASY Example World UCITS ETF - IE00B4L5Y983")
        for j, v in enumerate([labels[1], None, labels[2], "Share Category", "Share Class", labels[3], "Share Reference Currency"]):
            ws.cell(start + 10, 2 + j, v)
        for j, v in enumerate(["BNP PARIBAS EASY Example World", None, "IE00B4L5Y983", "UCITS ETF EUR", "Capitalisation", replication, "EUR"]):
            ws.cell(start + 11, 2 + j, v)
        ws.cell(start + 14, 2, "Euronext Paris Ticker")
        ws.cell(start + 14, 3, "XWLD FP")
        for j, v in enumerate(labels[4:5] + labels[5:6] + (labels[2],) + labels[6:7]):
            ws.cell(start + 19, 2 + j, v)
        r = start + 20
        for row in rows:
            for j, v in enumerate(row):
                ws.cell(r, 2 + j, v)
            r += 1
        r += 3
        ws.cell(r, 4, "TOTAL")
        ws.cell(r, 5, total)
        ws.cell(r + 2, 1, "Disclaimer")
        ws.cell(r + 3, 1, "Information published in this file represent the securities holdings in the balance sheet.")
        return r + 5

    end = block(0)
    if french:
        block(end)  # the French files repeat the table below the first one
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_inventory_english():
    as_of, info, df = bnp_easy.parse_inventory(_inventory())
    assert as_of == "2026-10-27"
    assert info["replication_label"] == "Full" and info["replication"] == "physical"
    assert info["share_isin"] == "IE00B4L5Y983" and info["total_reported"] == 1
    assert list(df.columns) == HOLDING_COLUMNS
    assert validate_holdings(df) == []
    assert len(df) == 4
    assert df["weight_pct"].tolist() == pytest.approx([50.12, 30.0, 19.87, 0.0])  # fractions times 100
    assert df["isin"].tolist()[:2] == ["US0378331005", "NL0010273215"]
    assert pd.isna(df.loc[2, "isin"])  # fails its check digit
    assert set(df["asset_class"]) == {"equity"}


def test_parse_inventory_french_stops_at_first_total():
    as_of, info, df = bnp_easy.parse_inventory(_inventory(french=True))
    assert as_of == "2026-10-27"
    assert len(df) == 4  # the repeated table is not read twice
    assert df["weight_pct"].sum() == pytest.approx(99.99)


def test_parse_inventory_synthetic_and_bonds():
    rows = [("Bond", "EXAMPLE GOVT 4.125 07/09/2035", "US0378331005", 0.011), ("Bond", "EXAMPLE CORP 5.1", "NL0010273215", 0.0081)]
    _, info, df = bnp_easy.parse_inventory(_inventory(replication="Synthetic", rows=rows))
    assert info["replication"] == "swap"
    assert set(df["asset_class"]) == {"debt"}
    assert df["weight_pct"].tolist() == pytest.approx([1.1, 0.81])


def test_parse_inventory_needs_header():
    wb = openpyxl.Workbook()
    wb.active.append(["Something else"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError):
        bnp_easy.parse_inventory(buf.getvalue())


def test_parse_share_search():
    payload = json.loads((FIXTURES / "sharesearch.json").read_text())
    df = bnp_easy.parse_share_search(payload, "en-ch")
    assert validate_products(df[bnp_easy.PRODUCT_COLUMNS]) == []
    assert df["isin"].tolist() == ["IE00B4L5Y983", "IE00B5BMR087", "FR0011550185", "LU1377381717", "LU3307218399"]
    first = df.iloc[0]
    assert first["portfolio_id"] == "90001" and df.iloc[1]["portfolio_id"] == "90001"
    assert first["domicile"] == "IE" and df.iloc[1]["currency"] == "CHF"
    assert first["product_page_url"] == (
        "https://www.bnpparibas-am.com/en-ch/fundsheet/equity/"
        "bnp-paribas-easy-example-world-ucits-etf-ucits-etf-eur-c-ie00b4l5y983"
    )
    assert first["holdings_url"] == "https://api.bnpparibas-am.com/push/fundsheet/IP_CH-FSE/ENG/CHE/IE00B4L5Y983"
    by = dict(zip(df["isin"], df["replication"]))
    assert by["FR0011550185"] == "swap"  # PEA fund on a US index
    assert by["LU3307218399"] == "swap"  # asset class Other
    assert by["IE00B4L5Y983"] is None
    assert dict(zip(df["isin"], df["_check"]))["LU1377381717"]  # PEA on a pan-European index


class FakeResponse:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code, self._payload, self.content = status, payload, content

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


@pytest.fixture
def fake_http(monkeypatch):
    calls = []
    fundsheet = json.loads((FIXTURES / "fundsheet.json").read_text())
    no_docs = dict(fundsheet, publications={})
    search = json.loads((FIXTURES / "sharesearch.json").read_text())
    synthetic = _inventory(replication="Synthetic")

    def get(url, **kw):
        calls.append(url)
        if "/sharesearchv2/" in url:
            return FakeResponse(payload=search)
        if url.endswith("/IE00B4L5Y983"):
            return FakeResponse(payload=fundsheet)
        if url.endswith("/LU1377381717"):
            doc = dict(fundsheet["publications"]["ENG"]["documents"][1], url="https://docfinder.example.invalid/api/files/synthetic/1")
            return FakeResponse(payload=dict(fundsheet, publications={"ENG": {"documents": [doc]}}))
        if url.endswith("/IE00B5BMR087"):  # a share class with no inventory of its own
            return FakeResponse(payload=dict(no_docs, fundshare_selection={"shares": [
                {"isin_code": "IE00B5BMR087", "share_type": "Track Classic, Capitalisation"},
                {"isin_code": "IE00B4L5Y983", "share_type": "UCITS ETF EUR, Capitalisation"},
            ]}))
        if url.endswith("/LU3307218399"):
            return FakeResponse(payload=dict(no_docs, fundshare_selection={"shares": [{"isin_code": "LU3307218399"}]}))
        if "inventory-eng" in url:
            return FakeResponse(content=_inventory())
        if "synthetic" in url:
            return FakeResponse(content=synthetic)
        return FakeResponse(status=404, payload={"key": "ENTITY_NOT_FOUND"})

    monkeypatch.setattr(bnp_easy.http, "get", get)
    return calls


def test_products_merges_sites_and_checks_pea_funds(fake_http):
    df = bnp_easy.products(sites=("en-gb", "en-ch"))
    assert validate_products(df) == []
    assert list(df.columns) == bnp_easy.PRODUCT_COLUMNS
    assert len(df) == 5 and set(df["site"]) == {"en-gb"}  # first site wins
    by = dict(zip(df["isin"], df["replication"]))
    assert by["LU1377381717"] == "swap"  # read from its inventory
    assert by["IE00B4L5Y983"] is None
    assert sum("/sharesearchv2/" in c for c in fake_http) == 2


def test_holdings_reads_english_inventory_and_caches(fake_http, tmp_path):
    product = {"isin": "IE00B4L5Y983", "site": "en-gb"}
    got = bnp_easy.holdings(product, FetchContext(cache_dir=str(tmp_path)))
    assert got.as_of == "2026-10-27"
    assert got.url == "https://docfinder.example.invalid/api/files/inventory-eng/1024"
    assert validate_holdings(got.holdings) == []
    assert got.holdings["weight_pct"].sum() == pytest.approx(99.99)
    assert any("Full -> physical" in n for n in got.notes)
    assert len(list(tmp_path.iterdir())) == 2  # workbook + url/notes
    n = len(fake_http)
    again = bnp_easy.holdings(product, FetchContext(cache_dir=str(tmp_path)))
    assert len(fake_http) == n and again.url == got.url and len(again.holdings) == 4


def test_holdings_falls_back_to_sibling_share_class(fake_http):
    got = bnp_easy.holdings({"isin": "IE00B5BMR087", "site": "en-gb"}, FetchContext())
    assert len(got.holdings) == 4
    assert "share class IE00B4L5Y983" in got.notes[0]


def test_holdings_without_inventory_is_empty_and_says_so(fake_http, tmp_path):
    got = bnp_easy.holdings({"isin": "LU3307218399", "site": "en-gb"}, FetchContext(cache_dir=str(tmp_path)))
    assert got.as_of is None and got.holdings.empty and list(got.holdings.columns) == HOLDING_COLUMNS
    assert "no holdings inventory" in got.notes[0]
    assert not any(tmp_path.iterdir())  # nothing cached that did not parse


def test_swap_holdings_are_flagged(fake_http):
    got = bnp_easy.holdings({"isin": "LU1377381717", "site": "en-gb"}, None)
    assert any("substitute basket" in n for n in got.notes)


def test_registered():
    ad = discover()["bnp-easy"]
    assert ad.frequency == "weekly" and "BNP Paribas Asset Management" in ad.credit
    assert "written authorisation" in ad.licence


@pytest.mark.network
def test_live_bnp_easy_msci_world():
    product = {"isin": "IE000A0GH076", "site": "en-gb"}
    got = bnp_easy.holdings(product, FetchContext())
    assert got.as_of is not None
    assert validate_holdings(got.holdings) == []
    assert len(got.holdings) > 1000
    assert got.holdings["weight_pct"].sum() == pytest.approx(100, abs=2)
    assert got.holdings["isin"].notna().mean() > 0.99
