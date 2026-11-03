import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from openbasket.sources.issuers import (
    HOLDING_COLUMNS,
    PRODUCT_COLUMNS,
    FetchContext,
    discover,
    validate_holdings,
    validate_products,
)
from openbasket.sources.issuers import sec_nmfp as m

FIXTURES = Path(__file__).parent / "fixtures" / "sec-nmfp"
REF = m.DatasetRef("20260909", "20261007")


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in FIXTURES.glob("NMFP_*.tsv"):
            zf.write(p, p.name)
    return buf.getvalue()


@pytest.fixture
def ds(monkeypatch):
    d = m.Dataset(_zip(), REF.url)
    monkeypatch.setattr(m, "datasets", lambda: [REF])
    monkeypatch.setattr(m, "load", lambda ref, cache_dir=None: d)
    return d


def test_registered():
    a = discover()["sec_nmfp"]
    assert a.frequency == "monthly"
    assert "Form N-MFP" in a.credit


def test_parse_index():
    refs = m.parse_index((FIXTURES / "index.html").read_text())
    assert [r.period for r in refs] == ["20260909-20261007", "20260810-20260908"]
    assert refs[0].url.endswith("/files/dera/data/form-n-mfp-data-sets/20260909-20261007_nmfp.zip")


def test_filings_prefer_amendment(ds):
    f = ds.filings().set_index("series_id")
    assert len(f) == 2
    assert f.at["S000000001", "accession_number"] == "0000000001-26-000002"
    assert f.at["S000000001", "registrant"] == "Example Trust & Co"
    assert f.at["S000000002", "feeder"]
    assert f.at["S000000001", "filing_url"] == (
        "https://www.sec.gov/Archives/edgar/data/1/000000000126000002/0000000001-26-000002-index.htm"
    )


def test_holdings(ds):
    h = m.holdings({"portfolio_id": "S000000001"}, FetchContext())
    df = h.holdings
    assert validate_holdings(df) == []
    assert set(HOLDING_COLUMNS) <= set(df.columns)
    assert h.as_of == "2026-09-30"
    assert h.url == REF.url
    assert len(df) == 6  # the superseded original filing is ignored
    # fractions of net assets become percent, never rescaled
    assert df["weight_pct"].tolist() == [40.12, 20.0, 35.0, 3.0, 0.0, 2.0]
    assert df["weight_pct"].sum() == pytest.approx(100.12)
    by = df.set_index("security_id")
    assert by.at["1", "isin"] == "US912797ZZ37"
    assert pd.isna(by.at["2", "isin"]) and by.at["2", "cusip"] == "3130AZZZ9"
    # repo trade codes are not securities
    assert pd.isna(by.at["3", "cusip"]) and by.at["3", "reported_cusip"] == "ARP1ZZZZ1"
    assert by.at["3", "asset_class"] == "repurchase agreement"
    # a reported ISIN that contradicts the CUSIP is rebuilt from the CUSIP
    assert by.at["4", "isin"] == "US99999MAA45"
    assert by.at["4", "name"] == "EXAMPLE CITY & COUNTY"
    assert pd.isna(by.at["5", "isin"])  # "0" is not an ISIN
    assert by.at["6", "asset_class"] == "cash"
    assert any("0.00%" in n for n in h.notes)
    assert any("rebuilt" in n for n in h.notes)


def test_feeder_points_to_master(ds):
    h = m.holdings({"portfolio_id": "S000000002"}, FetchContext())
    df = h.holdings
    assert df["asset_class"].tolist() == ["fund"]
    assert df["held_series_id"].tolist() == ["S000000001"]
    assert any("S000000001" in n for n in h.notes)


def test_unknown_series(ds):
    with pytest.raises(LookupError):
        m.holdings({"portfolio_id": "S000999999"}, FetchContext())


def test_products(ds):
    tickers = pd.DataFrame(
        {"series_id": ["S000000001", "S000000001"], "class_id": ["C000000011", "C000000012"], "ticker": ["EXGXX", "EXRXX"]}
    )
    series = ds.filings()
    cls = m.classes(ds, tickers)
    assert cls.set_index("class_id").at["C000000012", "ticker"] == "EXRXX"
    ev = m.isin_evidence(ds, series, gleif=False)
    assert ev.to_dict("records") == [{"isin": "US99999G1094", "series_id": "S000000001", "via": "N-MFP holding"}]
    p = m.build_products(series, ev, cls, REF.url)
    assert validate_products(p) == []
    assert set(PRODUCT_COLUMNS) <= set(p.columns)
    row = p.iloc[0]
    assert row["portfolio_id"] == "S000000001"
    assert pd.isna(row["ticker"])  # two classes: which one this ISIN is is unknown
    assert row["replication"] == "physical"
    assert row["asset_class"] == "Money Market (Government)"


def test_ticker_only_for_a_single_share_class(ds):
    series = ds.filings()
    ev = m.isin_evidence(ds, series, gleif=False)
    # One of the two classes has a ticker: the ISIN may belong to the other one.
    one_known = pd.DataFrame({"series_id": ["S000000001"], "class_id": ["C000000011"], "ticker": ["EXGXX"]})
    p = m.build_products(series, ev, m.classes(ds, one_known), REF.url)
    assert pd.isna(p.iloc[0]["ticker"])
    single = m.classes(ds, one_known)
    single = single[single["class_id"] == "C000000011"]
    assert m.build_products(series, ev, single, REF.url).iloc[0]["ticker"] == "EXGXX"


def test_products_with_gleif(ds, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(m, "gleif_isins", lambda lei: calls.append(lei) or (["US99999G2084", "XX123"] if lei.endswith("B02") else []))
    cache = tmp_path / "gleif.json"
    ev = m.isin_evidence(ds, ds.filings(), gleif=True, gleif_cache=cache).set_index("isin")
    assert ev.at["US99999G2084", "series_id"] == "S000000002"
    assert "XX123" not in ev.index
    n = len(calls)
    again = m.isin_evidence(ds, ds.filings(), gleif=True, gleif_cache=cache)
    assert len(calls) == n and len(again) == len(ev)  # answered from the cache


def test_adapter_is_public():
    from openbasket.sources import redistribution

    assert discover()["sec_nmfp"].redistribution == "public" and redistribution("sec_nmfp") == "public"


def test_repo_collateral(ds):
    c = m.repo_collateral({"portfolio_id": "S000000001"}, FetchContext())
    assert c["security_id"].tolist() == ["3", "3"]
    assert c["value_usd"].sum() == pytest.approx(357_000_000)


def test_cache_written_after_parse(tmp_path, monkeypatch):
    content = _zip()

    class R:
        def __init__(self, c):
            self.content = c

        def raise_for_status(self):
            pass

    monkeypatch.setattr(m, "_sec_get", lambda url, **kw: R(b"not a zip"))
    m._datasets.pop(REF.url, None)
    with pytest.raises(zipfile.BadZipFile):
        m.load(REF, tmp_path)
    assert not list(tmp_path.iterdir())
    monkeypatch.setattr(m, "_sec_get", lambda url, **kw: R(content))
    m.load(REF, tmp_path)
    assert (tmp_path / "sec_nmfp_20260909-20261007.zip").exists()
    m._datasets.pop(REF.url, None)


@pytest.mark.network
def test_real_fund():
    import openbasket.settings  # noqa: F401  loads SEC_USER_AGENT from .env

    h = m.holdings({"portfolio_id": "S000007051"}, FetchContext())  # Fidelity Government Money Market Fund
    df = h.holdings
    assert validate_holdings(df) == []
    assert len(df) > 100
    assert 90 < df["weight_pct"].sum() < 115
    assert h.as_of and h.as_of[:4] >= "2026"
