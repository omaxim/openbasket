"""Fund and bond directory: parsers, merge and pipeline wiring on tiny fixtures."""

from __future__ import annotations

import gzip
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from openbasket.identifiers import is_valid_isin
from openbasket.sources import directory as d

FIX = Path(__file__).parent / "fixtures" / "directory"


def _zip(name: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, content)
    return buf.getvalue()


@pytest.fixture(scope="module")
def listings() -> dict[str, pd.DataFrame]:
    ecb = gzip.compress((FIX / "ecb_eligible.tsv").read_text().encode("utf-16"))
    return {
        "six_funds": d.parse_six_funds((FIX / "six_funds.json").read_bytes()),
        "six_bonds": d.parse_six_bonds((FIX / "six_bonds.json").read_bytes()),
        "xetra": d.parse_xetra((FIX / "xetra.csv").read_bytes(), "https://example.org/xetra.csv"),
        "euronext_etfs": d.parse_euronext_etfs((FIX / "euronext_etfs.csv").read_bytes()),
        "euronext_bonds": d.parse_euronext_bonds((FIX / "euronext_bonds.csv").read_bytes()),
        "ecb_eligible": d.parse_ecb_eligible(ecb, "https://www.ecb.europa.eu/paym/coll/assets/html/dla/ea_MID/ea_csv_261102.csv.gz"),
        "esma_firds_funds": d.parse_firds_funds(
            _zip("FULINS_C_20261031_01of01.xml", (FIX / "firds_c.xml").read_bytes()),
            "esma_firds_funds", "https://firds.esma.europa.eu/firds/FULINS_C_20261031_01of01.zip", "2026-10-31",
        ),
        "esma_firds_debt": d.parse_firds_debt(
            _zip("FULINS_D_20261031_01of01.xml", (FIX / "firds_d.xml").read_bytes()),
            "esma_firds_debt", "https://firds.esma.europa.eu/firds/FULINS_D_20261031_01of01.zip", "2026-10-31",
        ),
    }


@pytest.fixture(scope="module")
def gleif(listings) -> dict[str, pd.DataFrame]:
    isins = set().union(*(set(l["isin"]) for l in listings.values()))
    return {
        "isin_lei": d.parse_gleif_isin_lei(_zip("lei-isin.csv", (FIX / "gleif_isin_lei.csv").read_bytes()), isins),
        "relationships": d.parse_gleif_relationships(_zip("rr.csv", (FIX / "gleif_rr.csv").read_bytes())),
        "entities": pd.DataFrame(d.parse_gleif_records(json.loads((FIX / "gleif_records.json").read_text()))),
    }


FUND_SOURCES = ["six_funds", "xetra", "euronext_etfs", "esma_firds_funds"]
BOND_SOURCES = ["six_bonds", "euronext_bonds", "ecb_eligible", "esma_firds_debt"]


def test_listings_meet_their_contract(listings):
    for key in FUND_SOURCES:
        df = listings[key]
        assert list(df.columns) == d.FUND_LISTING_COLUMNS, key
        assert df["isin"].map(is_valid_isin).all() and not df["isin"].duplicated().any(), key
        assert df["as_of"].notna().all() and df["source_url"].notna().all() and (df["source"] == key).all(), key
    for key in BOND_SOURCES:
        df = listings[key]
        assert list(df.columns) == d.BOND_LISTING_COLUMNS, key
        assert df["isin"].map(is_valid_isin).all() and not df["isin"].duplicated().any(), key
        assert df["as_of"].notna().all(), key


def test_six_funds(listings):
    six = listings["six_funds"].set_index("isin")
    assert len(six) == 4  # the fund traded in two currencies is kept once
    assert six.loc["CH0000000015", "replication"] == "physical"
    assert six.loc["IE0000000020", "replication"] == "physical"  # lower-case "physical" in the feed
    assert six.loc["LU0000000033", "replication"] == "swap"  # "Synthetic"
    assert six.loc["CH0000000072", "replication"] is None  # "Unknown"
    assert six.loc["CH0000000072", "instrument_type"] == "fund"
    assert six.loc["CH0000000072", "asset_class"] == "real_estate"
    assert six.loc["IE0000000020", "asset_class"] == "fixed_income"
    assert six.loc["CH0000000015", "domicile"] == "CH"
    assert six.loc["CH0000000015", "as_of"] == "2026-11-03"


def test_six_bonds(listings):
    b = listings["six_bonds"].set_index("isin")
    assert b.loc["CH0000000122", "maturity"] == "2030-06-08"
    assert b.loc["CH0000000122", "coupon_pct"] == 1.5
    assert b.loc["AT0000000179", "currency"] == "EUR"


def test_xetra_keeps_exchange_traded_products_only(listings):
    x = listings["xetra"].set_index("isin")
    assert set(x.index) == {"IE0000000020", "DE0000000058"}  # the share (CS) is dropped
    assert x.loc["IE0000000020", "asset_class"] == "fixed_income"  # RENTEN
    assert x.loc["DE0000000058", "instrument_type"] == "ETC"
    assert x.loc["IE0000000020", "as_of"] == "2026-11-03"


def test_euronext(listings):
    e = listings["euronext_etfs"].set_index("isin")
    assert e.loc["FR0000000085", "venues"] == "ETFP;XAMS;XPAR"
    assert e.loc["IE0000000020", "venues"] == "XPAR"
    assert e.loc["XS0000000066", "instrument_type"] == "ETN"
    assert e.loc["FR0000000085", "as_of"] == "2026-11-03"
    b = listings["euronext_bonds"].set_index("isin")
    assert b.loc["FR0000000150", "venues"] == "ETLX;XPAR"
    assert b.loc["NO0000000161", "maturity"] == "2028-05-22"


def test_ecb(listings):
    e = listings["ecb_eligible"].set_index("isin")
    assert e.loc["DE0000000140", "issuer_country"] == "DE"
    assert e.loc["DE0000000140", "coupon_pct"] == 0.5
    assert e.loc["FR0000000150", "maturity"] == "2031-06-15"
    assert (e["as_of"] == "2026-11-02").all()  # from the file name


def test_firds(listings):
    f = listings["esma_firds_funds"].set_index("isin")
    assert f.loc["IE0000000020", "venues"] == "FRAB;XETR"
    assert f.loc["IE0000000020", "instrument_type"] == "ETF"
    assert f.loc["US0000000101", "instrument_type"] == "REIT"
    assert f.loc["IE0000000046", "fund_lei"] == "5493000000000000BB02"
    debt = listings["esma_firds_debt"].set_index("isin")
    assert set(debt.index) == {"XS0000000132", "FR0000000150"}  # structured note (DE...) dropped
    assert debt.loc["XS0000000132", "coupon_pct"] == 4.2
    assert debt.loc["XS0000000132", "maturity"] == "2035-02-13"


def test_gleif_parsers(gleif):
    assert set(gleif["isin_lei"]["isin"]) == {"CH0000000015", "FR0000000150"}
    rel = gleif["relationships"]
    assert set(rel["relationship"]) == {"IS_FUND-MANAGED_BY", "IS_SUBFUND_OF"}
    assert "5493000000000000CC03" not in set(rel["lei"])  # inactive link dropped


@pytest.mark.parametrize(
    "texts, key",
    [
        (("iShares J.P. Morgan USD EM Bond UCITS ETF",), "ishares"),
        (("SSGA SPDR ETFs Europe I plc",), "spdr"),
        (("Multi Units Luxembourg (SICAV)",), "amundi"),
        (("XT MSCI WORLD 1C",), "xtrackers"),
        (("DWS Investment SA, Luxembourg",), "xtrackers"),
        (("Swisscanto Fondsleitung AG, Zürich",), "swisscanto"),
        (("JPMorgan ETFs Irland ICAV",), "jpmorgan"),
        (("Example Fund", "BLACKROCK ASSET MANAGEMENT IRELAND LIMITED"), "ishares"),
        (("Example Fund", None), None),
        (("Global Exposure Fund",), None),  # "Global" alone is not Global X
    ],
)
def test_issuer_key(texts, key):
    assert d.issuer_key(*texts) == key


def test_fund_directory(listings, gleif):
    fd = d.build_fund_directory(
        [listings[k] for k in FUND_SOURCES], gleif["isin_lei"], gleif["relationships"], gleif["entities"], adapters={"ubs", "ishares"}
    )
    assert list(fd.columns) == d.FUND_DIRECTORY_COLUMNS
    assert fd["isin"].is_unique and fd["isin"].map(is_valid_isin).all()
    assert fd[["source", "source_url", "credit", "as_of"]].notna().all().all()
    r = fd.set_index("isin")

    ubs = r.loc["IE0000000020"]
    assert ubs["issuer"] == "ubs" and ubs["adapter"] == "ubs" and ubs["holdings_route"] == "ubs"
    assert ubs["name"] == "UBS Core Example UCITS ETF USD acc" and ubs["name_source"] == "six_funds"
    assert ubs["venues"] == "FRAB;XETR;XPAR;XSWX"
    assert ubs["listed_in"] == "esma_firds_funds;euronext_etfs;six_funds;xetra"
    assert ubs["currency"] == "USD" and ubs["currency_source"] == "six_funds"
    assert ubs["manager_lei"] == "5493000000000000MG12" and ubs["manager_name"] == "UBS Example Management S.A."
    assert ubs["as_of"] == "2026-11-03"
    assert "SIX" in ubs["credit"] and "ESMA" in ubs["credit"] and "Euronext" in ubs["credit"]

    swap = r.loc["LU0000000033"]
    assert swap["issuer"] == "xtrackers" and swap["replication"] == "swap" and swap["replication_source"] == "six_funds"
    assert swap["adapter"] is None and swap["holdings_route"] == "no_holdings_source"

    # Unbranded sub-fund: issuer comes from its umbrella's manager in GLEIF.
    sub = r.loc["IE0000000046"]
    assert sub["umbrella_lei"] == "5493000000000000UM10" and sub["umbrella_name"] == "EXAMPLE UMBRELLA PLC"
    assert sub["manager_lei"] == "5493000000000000MG11" and sub["manager_source"] == "gleif_umbrella"
    assert sub["issuer"] == "hsbc" and sub["issuer_source"] == "gleif"
    assert sub["issuer_name"] == "HSBC Example Asset Management (Europe) Limited"
    assert sub["domicile"] == "IE" and sub["domicile_source"] == "gleif"

    # Fund LEI from the GLEIF ISIN map when FIRDS does not list the fund.
    assert r.loc["CH0000000015", "fund_lei"] == "5493000000000000GG07"
    assert r.loc["CH0000000015", "fund_lei_source"] == "gleif"

    etn = r.loc["XS0000000066"]
    assert etn["instrument_type"] == "ETN" and etn["domicile"] is None and etn["issuer"] == "leverage_shares"
    assert r.loc["FR0000000085", "domicile"] == "FR" and r.loc["FR0000000085", "domicile_source"] == "isin_prefix"


def test_bond_directory(listings, gleif):
    bd = d.build_bond_directory([listings[k] for k in BOND_SOURCES], gleif["isin_lei"])
    assert list(bd.columns) == d.BOND_DIRECTORY_COLUMNS
    assert bd["isin"].is_unique
    assert bd[["source", "credit", "as_of"]].notna().all().all()
    r = bd.set_index("isin")
    fr = r.loc["FR0000000150"]
    assert fr["issuer_name"] == "Example Paris SA" and fr["issuer_name_source"] == "ecb_eligible"
    assert fr["issuer_lei"] == "5493000000000000EE05" and fr["issuer_lei_source"] == "esma_firds_debt"
    assert fr["coupon_pct"] == 3 and fr["maturity"] == "2031-06-15" and fr["name_source"] == "euronext_bonds"
    assert fr["listed_in"] == "ecb_eligible;esma_firds_debt;euronext_bonds"
    assert r.loc["CH0000000122", "name_source"] == "six_bonds"
    assert r.loc["XS0000000132", "currency"] == "USD"


def test_coverage_and_route(listings, gleif):
    fd = d.build_fund_directory([listings[k] for k in FUND_SOURCES], adapters={"ubs"})
    cov = d.coverage(fd)
    assert cov["isins"].sum() == len(fd)
    assert int(cov.loc[cov["issuer"] == "ubs", "with_adapter"].sum()) == 1
    assert d.route("IE0000000020", fd)["adapter"] == "ubs"
    assert d.route("US0000000000", fd) is None


def test_cache_written_only_after_parse(tmp_path):
    day = date(2026, 11, 3)

    def bad(_: bytes) -> pd.DataFrame:
        raise ValueError("unparseable")

    with pytest.raises(ValueError):
        d.cached(tmp_path, "six_funds", ".json", lambda: b"{}", bad, day=day)
    assert not (tmp_path / "six_funds" / "2026-11-03.json").exists()
    content = (FIX / "six_funds.json").read_bytes()
    out = d.cached(tmp_path, "six_funds", ".json", lambda: content, d.parse_six_funds, day=day)
    assert (tmp_path / "six_funds" / "2026-11-03.json").exists() and len(out) == 4
    again = d.cached(tmp_path, "six_funds", ".json", lambda: pytest.fail("should read the cache"), d.parse_six_funds, day=day)
    assert again.equals(out)


def test_link_discovery():
    assert d.xetra_csv_url('<a href="/resource/blob/1/abc/data/t7-xetr-allTradableInstruments.csv">') == (
        "https://www.cashmarket.deutsche-boerse.com/resource/blob/1/abc/data/t7-xetr-allTradableInstruments.csv"
    )
    page = '{"jsongateway_download":"\\/product_directory\\/data\\/etf-all-markets\\/download?mics=XPAR%2CXAMS"}'
    assert d.euronext_download_url(page) == "https://live.euronext.com/en/product_directory/data/etf-all-markets/download?mics=XPAR%2CXAMS"
    ecb = '<a href="/paym/coll/assets/html/dla/ea_MID/ea_csv_261101.csv.gz"></a><a href="/paym/coll/assets/html/dla/ea_MID/ea_csv_261102.csv.gz">'
    assert d.ecb_csv_url(ecb).endswith("ea_csv_261102.csv.gz")


def test_ecb_cache_hit_keeps_file_date_and_url(tmp_path, monkeypatch):
    """A second run on the same day reads the cache and still knows which file it was."""
    content = gzip.compress((FIX / "ecb_eligible.tsv").read_text().encode("utf-16"))
    page = '<a href="/paym/coll/assets/html/dla/ea_MID/ea_csv_261102.csv.gz">'

    class R:
        def __init__(self, text="", content=b""):
            self.text, self.content = text, content

        def raise_for_status(self):
            pass

    monkeypatch.setattr(d.http, "get", lambda url, **kw: R(text=page) if url == d.ECB_PAGE else R(content=content))
    first = d.ecb_eligible(tmp_path)
    monkeypatch.setattr(d.http, "get", lambda url, **kw: pytest.fail("should read the cache"))
    again = d.ecb_eligible(tmp_path)
    assert (again["as_of"] == "2026-11-02").all()
    assert again["source_url"].str.endswith("ea_csv_261102.csv.gz").all()
    assert again.equals(first)


@pytest.mark.parametrize(
    "texts, key",
    [
        (("Raiffeisen-Global-Dividende-ESG-Aktien",), "raiffeisen"),
        (("Vontobel Fund - mtx Sustainable Emerging Markets Leaders",), "vontobel"),
    ],
)
def test_issuer_key_raiffeisen(texts, key):
    assert d.issuer_key(*texts) == key


def _fund(isin: str, name: str, **kw) -> dict:
    return {c: None for c in d.FUND_LISTING_COLUMNS} | {
        "isin": isin, "name": name, "as_of": "2026-11-03", "source": "esma_firds_funds", "source_url": "u"} | kw


def test_directory_raiffeisen_and_ambiguous_brands():
    listing = pd.DataFrame([
        _fund("AT0000495288", "Raiffeisen-Gl.Div-ESG-Akt.", domicile="AT"),
        _fund("CH0000000015", "Raiffeisen Futura Swiss Stock", domicile="CH"),
        _fund("LU0000000017", "Amova ARK Disruptive Innovation", fund_lei="5493000000000000FR01"),
    ])
    rel = pd.DataFrame([{"lei": "5493000000000000FR01", "relationship": "IS_FUND-MANAGED_BY", "parent_lei": "5493000000000000FR02"}])
    ent = pd.DataFrame([{"lei": "5493000000000000FR02", "legal_name": "FundRock Management Company S.A.", "jurisdiction": "LU", "category": "GENERAL"}])
    fd = d.build_fund_directory([listing], None, rel, ent).set_index("isin")
    assert fd.loc["AT0000495288", "issuer"] == "raiffeisen"
    assert fd.loc["CH0000000015", "issuer"] == "vontobel"
    assert fd.loc["LU0000000017", "issuer"] is None  # "ARK" in the name, but GLEIF's manager is FundRock


def test_etf_only_adapters_do_not_route_mutual_funds():
    listing = pd.DataFrame([
        _fund("IE00B4L5Y983", "iShares Core MSCI World UCITS ETF", instrument_type="ETF"),
        _fund("LU0171293920", "BGF - US Basic Value Fund", instrument_type="fund"),
    ])
    fd = d.build_fund_directory([listing], adapters={"ishares"}, etf_only_adapters={"ishares"}).set_index("isin")
    assert fd.loc["IE00B4L5Y983", "holdings_route"] == "ishares"
    assert fd.loc["LU0171293920", "issuer"] == "ishares" and fd.loc["LU0171293920", "holdings_route"] == "no_holdings_source"
    listed = d.build_fund_directory([listing], adapters={"ishares"}, adapter_isins={"ishares": {"LU0171293920"}})
    assert listed.set_index("isin")["adapter"].to_dict() == {"IE00B4L5Y983": None, "LU0171293920": "ishares"}


def test_redistribution_of_directory_sources():
    from openbasket.sources import redistribution

    public = {k for k, s in d.SOURCES.items() if s.redistribution == "public"}
    assert public == {"esma_firds_funds", "esma_firds_debt", "ecb_eligible", "gleif"}
    assert redistribution("six_funds") == "local_only" and redistribution("esma_firds_funds") == "public"
    assert redistribution("esma_firds") == "public"


def test_pipeline_nodes_skip_disabled_sources():
    from openbasket.pipelines.directory import create_pipeline
    from openbasket.pipelines.directory import nodes as n

    params = {"sources": {}}
    assert n.dir_six_funds(params).empty
    assert list(n.dir_six_bonds(params).columns) == d.BOND_LISTING_COLUMNS
    outputs = set(create_pipeline().all_outputs())
    assert {"fund_directory", "bond_directory", "directory_coverage", "directory_sources"} <= outputs


def test_public_build_reads_public_sources_only(monkeypatch):
    from openbasket.pipelines.directory import nodes as n

    monkeypatch.setattr(d, "six_funds", lambda cache_dir=None: pytest.fail("SIX must not be read in the public build"))
    params = {"sources": {"six_funds": True, "esma_firds_funds": True}, "public_only": True}
    assert n.dir_six_funds(params).empty
    report = n.directory_sources(params).set_index("source")
    assert report.loc["six_funds", "status"] == "local_only" and report.loc["six_funds", "redistribution"] == "local_only"
    with pytest.raises(RuntimeError, match="esma_firds_funds"):
        n.directory_sources(params | {"required_sources": ["esma_firds_funds"]})


def test_failed_source_is_reported(monkeypatch):
    from openbasket.pipelines.directory import nodes as n

    def boom(cache_dir=None):
        raise ConnectionError("unreachable")

    monkeypatch.setattr(d, "xetra", boom)
    assert n.dir_xetra({"sources": {"xetra": True}}).empty
    row = n.directory_sources({}).set_index("source").loc["xetra"]
    assert row["status"] == "failed" and "unreachable" in row["error"]


@pytest.mark.network
def test_six_funds_live():
    df = d.six_funds()
    assert len(df) > 2000
    assert {"CH", "IE", "LU"} <= set(df["domicile"])
    assert (df["replication"] == "swap").sum() > 50
    smi = df.set_index("isin").loc["CH0008899764"]  # iShares SMI ETF (CH)
    assert d.issuer_key(smi["name"], smi["issuer_name"]) == "ishares"
