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
    SUBSTITUTE_BASKET,
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
    assert first["holdings_url"] == "https://api.bnpparibas-am.com/push/fundsheet/IP_CH-IND/ENG/CHE/IE00B4L5Y983"
    by = dict(zip(df["isin"], df["replication"]))
    assert by["FR0011550185"] == "swap"  # PEA fund on a US index
    assert by["LU3307218399"] == "swap"  # asset class Other
    assert by["IE00B4L5Y983"] is None
    assert dict(zip(df["isin"], df["_check"]))["LU1377381717"]  # PEA on a pan-European index
    assert dict(zip(df["isin"], df["_check"]))["IE00B4L5Y983"]  # unknown replication is checked too


def test_only_private_investor_profiles_are_read():
    for site, (profile, _, _) in bnp_easy.SITES.items():
        assert not bnp_easy.PROFESSIONAL_PROFILES.search(profile), site
    assert bnp_easy.SITES["en-gb"][0] == "IP_UK-IND"
    assert bnp_easy.PROFESSIONAL_PROFILES.search("IP_UK-FSE")


def test_professional_profile_is_refused(monkeypatch):
    monkeypatch.setitem(bnp_easy.SITES, "en-gb", ("IP_UK-FSE", "ENG", "GBR"))
    with pytest.raises(ValueError, match="professional"):
        bnp_easy.fundsheet_url("en-gb", "IE00B4L5Y983")


def test_replication_from_policy():
    assert bnp_easy.replication_from_policy(
        "The Product seeks to replicate the MSCI World Index by investing in a substitute basket, composed of "
        "shares of companies (synthetic replication). When employing synthetic replication, the sub-fund will "
        "achieve exposure to the Index by entering into total return swaps (TRS). When investing in a sample "
        "of shares, the tracking error may be higher."
    ) == "swap"
    assert bnp_easy.replication_from_policy("The sub-fund uses a full replication of the index.") == "physical"
    assert bnp_easy.replication_from_policy(None) is None


def _etf_inventory(nav_date="2026-11-02"):
    """An Easy II ETF inventory laid out like BNP Paribas AM's (header in row 1, weights in percent)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["ISIN", "Security Name", "Asset Type", "Currency", "Weight in %", "Nav Date"])
    ws.append([None, "DEFERRED INT REC - FIXED INCOME", "ASSET", "EUR", -0.01, nav_date])
    ws.append([None, "US DOLLAR", "CASH", "USD", 0.6, nav_date])
    ws.append(["US0378331005", "EXAMPLE TREASURY 0.25% 2025", "FIXED INCOME", "USD", "60.41", nav_date])
    ws.append(["XS0000000000", "EXAMPLE CORP 5% 2030", "FIXED INCOME", "EUR", 39.0, nav_date])
    ws.append([None, "SOLD USD/BOUGHT EUR", "FOREIGN EXCHANGE", "USD", "0.05", nav_date])
    ws.append([None, "TOTAL EXPENSE RATIO", "LIABILITIES", "USD", -0.05, nav_date])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_etf_inventory():
    as_of, info, df = bnp_easy.parse_etf_inventory(_etf_inventory())
    assert as_of == "2026-11-02" and info["layout"] == "etf"
    assert list(df.columns) == HOLDING_COLUMNS
    assert validate_holdings(df) == []
    assert df["weight_pct"].sum() == pytest.approx(100.0)  # percent, kept as is
    assert df["isin"].tolist()[2] == "US0378331005" and pd.isna(df["isin"].iloc[3])  # bad check digit
    assert df["asset_class"].tolist() == ["other", "cash", "debt", "debt", "derivative (foreign exchange)", "other"]
    assert df["currency"].tolist()[1] == "USD"


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
    assert by["IE00B4L5Y983"] == "physical"  # "Full" in its inventory
    assert sum("/sharesearchv2/" in c for c in fake_http) == 2
    assert all("/IP_UK-IND/" in c or "/IP_CH-IND/" in c for c in fake_http if "bnpparibas-am.com/push" in c)


def test_non_pea_synthetic_fund_is_swap(monkeypatch):
    """A fund with no PEA flag whose inventory or fund page says synthetic ends up as ``swap``."""
    search = {"shares": [
        {"compartment_id": 1, "compart_name": "BNP PARIBAS EASY EXAMPLE WORLD II", "eng_asset_class": "Equity",
         "region": "Global", "flags": ["etf_flag"], "isin_code": "IE00B4L5Y983", "val_currency": "EUR",
         "legal_name": "BNP PARIBAS EASY EXAMPLE WORLD II [UCITS ETF EUR, C]"},
        {"compartment_id": 2, "compart_name": "BNP PARIBAS EASY EXAMPLE ACWI II", "eng_asset_class": "Equity",
         "region": "Global", "flags": ["etf_flag"], "isin_code": "IE00B5BMR087", "val_currency": "EUR",
         "legal_name": "BNP PARIBAS EASY EXAMPLE ACWI II [UCITS ETF EUR, C]"},
    ]}
    inv = {"category": "DOC_ACCOUNTING_INVENTORY", "format": "xlsx", "language": "ENG",
           "url": "https://docfinder.example.invalid/api/files/synthetic/1"}
    policy = {"overview": {"disclaimers": {"investment_policy": "... a substitute basket (synthetic replication) ..."}}}

    def get(url, **kw):
        if "/sharesearchv2/" in url:
            return FakeResponse(payload=search)
        if url.endswith("/IE00B4L5Y983"):
            return FakeResponse(payload={"publications": {"ENG": {"documents": [inv]}}})
        if url.endswith("/IE00B5BMR087"):
            return FakeResponse(payload=dict(policy, publications={}))
        if "synthetic" in url:
            return FakeResponse(content=_inventory(replication="Synthetic"))
        raise AssertionError(url)

    monkeypatch.setattr(bnp_easy.http, "get", get)
    df = bnp_easy.products(sites=("en-gb",))
    assert dict(zip(df["isin"], df["replication"])) == {"IE00B4L5Y983": "swap", "IE00B5BMR087": "swap"}


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
    assert set(got.holdings["asset_class"]) == {SUBSTITUTE_BASKET}


def test_holdings_reads_etf_inventory_and_flags_old_files(monkeypatch, tmp_path):
    doc = {"category": "DOC_FUND_INVENTORY_ETF", "format": "xlsx", "language": "ENG", "reference_date": "2026-05-18",
           "url": "https://docfinder.example.invalid/api/files/etf/1024"}
    fundsheet = {"publications": {"ENG": {"documents": [doc]}}}

    def get(url, **kw):
        if "/fundsheet/" in url:
            return FakeResponse(payload=fundsheet)
        return FakeResponse(content=_etf_inventory(nav_date="2026-05-14"))

    monkeypatch.setattr(bnp_easy.http, "get", get)
    ctx = FetchContext(cache_dir=str(tmp_path))
    got = bnp_easy.holdings({"isin": "IE000EOB5120", "site": "en-ch"}, ctx)
    assert got.as_of == "2026-05-14" and got.url == doc["url"]
    assert got.holdings["weight_pct"].sum() == pytest.approx(100.0)
    assert any("including cash" in n for n in got.notes)
    assert any("days old" in n for n in got.notes)
    again = bnp_easy.holdings({"isin": "IE000EOB5120", "site": "en-ch"}, ctx)
    assert again.as_of == got.as_of and len(again.holdings) == len(got.holdings)


def test_registered():
    ad = discover()["bnp_easy"]
    assert ad.frequency == "irregular" and "BNP Paribas Asset Management" in ad.credit
    assert "written authorisation" in ad.licence
    assert ad.redistribution == "local_only"


@pytest.mark.network
def test_live_bnp_easy_msci_world():
    product = {"isin": "IE000A0GH076", "site": "en-gb"}
    got = bnp_easy.holdings(product, FetchContext())
    assert got.as_of is not None
    assert validate_holdings(got.holdings) == []
    assert len(got.holdings) > 1000
    assert got.holdings["weight_pct"].sum() == pytest.approx(100, abs=2)
    assert got.holdings["isin"].notna().mean() > 0.99
