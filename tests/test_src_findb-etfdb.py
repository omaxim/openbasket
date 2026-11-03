"""FinanceDatabase (ETF names, families and listings by ISIN) and albertored/etfdb (fund facts, top holdings)."""

import json
import shutil
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import yaml

from openbasket import http
from openbasket.lookthrough import look_through
from openbasket.pipelines.etfdb import create_pipeline as etfdb_pipeline
from openbasket.pipelines.etfdb.nodes import build_etfdb_meta, build_stand_ins, report_etfdb
from openbasket.pipelines.financedatabase import create_pipeline as findb_pipeline
from openbasket.pipelines.financedatabase.nodes import build_findb_funds, compare_findb
from openbasket.pipelines.ingest.nodes import download_issuer_products
from openbasket.pipelines.lookthrough.nodes import issuer_adapters
from openbasket.sources import financedatabase as findb
from openbasket.sources import issuers, redistribution
from openbasket.sources.issuers import HOLDING_COLUMNS, PRODUCT_COLUMNS, validate_holdings, validate_products
from openbasket.sources.issuers import etfdb

FIX = Path(__file__).parent / "fixtures" / "findb-etfdb"
CONF = Path(__file__).resolve().parents[1] / "conf" / "base"


def _frames():
    return {
        "etf": findb.read(FIX / "findb_etfs"),
        "fund": findb.read(FIX / "findb_funds.csv"),
        "moneymarket": findb.read(FIX / "findb_moneymarkets.csv"),
    }


class _Resp:
    def __init__(self, content: bytes = b"", payload=None, status: int = 200):
        self.content, self._payload, self.status_code = content, payload, status
        self.ok = status < 400

    def json(self):
        return self._payload if self._payload is not None else json.loads(self.content)

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


# -- FinanceDatabase -----------------------------------------------------------


def test_findb_listings_drop_invalid_isins_and_never_keep_the_summary():
    listing, stats = findb.listings(_frames())
    assert stats["etf"] == {"rows": 7, "with_isin": 6, "invalid_isin": 1, "isin_from_text": 0, "delisted": 2}
    assert stats["fund"]["with_isin"] == 0 and stats["moneymarket"]["rows"] == 1
    assert "summary" not in listing.columns
    assert set(listing["isin"]) == {"IE00TSTETF18", "LU00TSTETF21", "US00TSTETF32"}
    assert set(listing["kind"]) == {"etf"}


def test_findb_table_has_one_row_per_isin_with_every_listing():
    listing, stats = findb.listings(_frames())
    t = findb.build_table(listing, {"etf": "2026-11-01"}).set_index("isin")
    assert list(t.reset_index().columns) == findb.TABLE_COLUMNS
    a = t.loc["IE00TSTETF18"]
    assert a["name"] == "Test World Equity UCITS ETF"  # the delisted line's name is not used
    assert a["symbols"] == "TSTA.DE; TSTA.L; TSTA2.DE" and a["exchanges"] == "GER; LSE"
    assert a["mics"] == "XETR; XLON" and a["currencies"] == "EUR; USD"
    assert (a["n_listings"], a["n_active_listings"], a["delisted"]) == (3, 2, False)
    assert a["family"] == "Test Asset Management" and a["category"] == "Global Large-Cap Blend Equity"
    assert a["kind"] == "etf" and a["isin_from"] == "isin" and a["as_of"] == "2026-11-01"
    assert a["credit"] == "FinanceDatabase by Jeroen Bouma (MIT)" and a["redistribution"] == "public"
    assert t.loc["LU00TSTETF21", "delisted"] and t.loc["LU00TSTETF21", "n_active_listings"] == 0
    assert pd.isna(t.loc["US00TSTETF32", "family"])
    text = " ".join(str(v) for v in t.to_numpy().ravel())
    assert "Invented summary" not in text and "summary text" not in text

    s = findb.summarise(t.reset_index(), stats)
    assert s["distinct_isins"] == 3 and s["invalid_isin_rows_dropped"] == 1
    assert s["isins_by_kind"] == {"etf": 3} and s["isins_all_listings_delisted"] == 1
    assert s["isins_by_country_prefix"] == {"IE": 1, "LU": 1, "US": 1}
    assert s["top_families"] == {"Test Asset Management": 1, "Other Test Funds": 1}


def test_findb_isin_from_fund_text_only_when_exactly_one_is_named():
    listing, stats = findb.listings(_frames(), isin_from_text=True)
    assert stats["fund"]["isin_from_text"] == 1
    t = findb.build_table(listing).set_index("isin")
    assert t.loc["DE00TSTFND51", "kind"] == "fund" and t.loc["DE00TSTFND51", "isin_from"] == "summary_text"
    assert t.loc["IE00TSTETF18", "kinds"] == "etf"  # the two-ISIN fund text named it, but was not used
    assert findb.isin_in_text("ISIN IE00TSTETF18 and IE00TSTETF18 again") == "IE00TSTETF18"
    assert findb.isin_in_text("IE00TSTETF43 has a bad check digit") is None


def test_findb_fetch_saves_the_package_file_and_dates_it(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if "/commits" in url:
            return _Resp(payload=[{"commit": {"committer": {"date": "2026-11-01T12:06:46Z"}}}])
        return _Resp(content=b"PAR1-not-really")

    monkeypatch.setattr(http, "get", fake_get)
    row = findb.fetch("etf", tmp_path)
    assert row["url"] == "https://raw.githubusercontent.com/JerBouma/FinanceDatabase/main/compression/etfs.parquet"
    assert row["as_of"] == "2026-11-01" and Path(row["path"]).read_bytes() == b"PAR1-not-really"
    findb.fetch("etf", tmp_path)  # fetched today: reused
    assert sum("compression/etfs.parquet" in c for c in calls) == 1
    with pytest.raises(ValueError):
        findb.fetch("bonds", tmp_path)


def test_findb_nodes_build_and_compare(tmp_path):
    shutil.copytree(FIX / "findb_etfs", tmp_path / "etfs")
    manifest = pd.DataFrame([
        {"kind": "etf", "path": str(tmp_path / "etfs"), "url": "u", "as_of": "2026-11-01", "bytes": 1},
        {"kind": "fund", "path": str(FIX / "findb_funds.csv"), "url": "u", "as_of": "2026-10-01", "bytes": 1},
    ])
    table, summary = build_findb_funds(manifest, {"isin_from_text": False})
    assert summary["distinct_isins"] == 3 and len(table) == 3
    pd.DataFrame({"isin": ["IE00TSTETF18", "XS0000000009"]}).to_parquet(tmp_path / "ip.parquet")
    pd.DataFrame({"isin": ["US00TSTETF32", "IE00TSTETF18"], "open_holdings_source": ["sec_nport", None]}).to_parquet(
        tmp_path / "reg.parquet")
    compare = {"issuer_products": {"path": str(tmp_path / "ip.parquet"), "holdings": True},
               "fund_registry": {"path": str(tmp_path / "reg.parquet"), "holdings": "open_holdings_source"},
               "missing": {"path": str(tmp_path / "nope.parquet")}}
    out = compare_findb(table, {"compare": compare}).set_index("table")
    assert list(out.index) == ["issuer_products", "fund_registry", "any"]
    assert out.loc["issuer_products", "known"] == 1 and out.loc["issuer_products", "with_holdings"] == 1
    assert out.loc["fund_registry", "known"] == 2 and out.loc["fund_registry", "with_holdings"] == 1
    assert out.loc["any", "known"] == 2 and out.loc["any", "with_holdings"] == 2


def test_findb_is_public_and_etfdb_is_local_only():
    assert findb.REDISTRIBUTION == "public" and redistribution("financedatabase") == "public"
    assert redistribution("etfdb_top") == "local_only" and etfdb.REDISTRIBUTION == "local_only"


# -- albertored/etfdb ----------------------------------------------------------


def _recs():
    return etfdb.index(json.loads((FIX / "etfdb_etfs.json").read_text()))


def test_etfdb_meta_flags_swaps_leverage_and_names_the_index():
    recs = _recs()
    assert list(recs) == ["IE00TSTPHY65", "IE00TSTSWP72", "IE00TSTLEV81"]  # the invalid ISIN is skipped
    m = etfdb.meta(recs).set_index("isin")
    assert list(m.reset_index().columns) == etfdb.META_COLUMNS
    assert m.loc["IE00TSTPHY65", "replication_class"] == "physical" and not m.loc["IE00TSTPHY65", "uses_swaps"]
    assert m.loc["IE00TSTSWP72", "replication_class"] == "swap" and m.loc["IE00TSTSWP72", "uses_swaps"]
    assert m.loc["IE00TSTLEV81", "is_leveraged"] and m.loc["IE00TSTLEV81", "strategies"] == "Short & Leveraged"
    assert m.loc["IE00TSTPHY65", "index"] == "MSCI World" and m.loc["IE00TSTPHY65", "domicile_code"] == "IE"
    assert m.loc["IE00TSTPHY65", "n_top_holdings"] == 4 and m.loc["IE00TSTPHY65", "top_holdings_pct"] == pytest.approx(11.8)
    assert m.loc["IE00TSTPHY65", "holdings_update_date"] == "2026-08-27"
    assert set(m["redistribution"]) == {"local_only"}
    assert m["hedged"].dtype == "boolean" and m.loc["IE00TSTPHY65", "securities_lending"]


def test_etfdb_stand_ins_pair_a_swap_fund_with_a_physical_one_on_its_index():
    s = etfdb.stand_in_candidates(etfdb.meta(_recs()))
    assert s[["isin", "candidate_isin"]].values.tolist() == [["IE00TSTSWP72", "IE00TSTPHY65"]]
    assert s.iloc[0]["candidate_replication"] == "Full replication"


def test_etfdb_products_list_only_funds_with_top_holdings():
    p = etfdb.product_rows(_recs())
    assert validate_products(p) == []
    assert p["isin"].tolist() == ["IE00TSTPHY65", "IE00TSTSWP72"]
    assert set(PRODUCT_COLUMNS) <= set(p.columns) and set(p["issuer"]) == {"etfdb_top"}
    assert p.set_index("isin").loc["IE00TSTSWP72", "replication"] == "swap"
    assert set(p["holdings_url"]) == {etfdb.JSON_URL} and set(p["domicile"]) == {"IE"}


def test_etfdb_top_holdings_are_labelled_and_never_rescaled():
    recs = _recs()
    h = etfdb.holdings_of("IE00TSTPHY65", recs["IE00TSTPHY65"])
    assert validate_holdings(h.holdings) == [] and list(h.holdings.columns) == HOLDING_COLUMNS
    assert h.as_of == "2026-08-27" and h.url == etfdb.JSON_URL
    assert h.notes == ["top holdings only, from albertored/etfdb (2026-08-27); the rest of the fund is unresolved"]
    assert h.holdings["weight_pct"].tolist() == [5.1, 4.2, 1.5, 1.0]
    assert h.holdings["isin"].tolist()[:2] == ["US00TSTSTK13", "US00TSTSTK21"]
    assert h.holdings["isin"].isna().tolist()[2:] == [True, True]  # bad check digit, none given
    assert set(h.holdings["asset_class"]) == {"equity"}
    swap = etfdb.holdings_of("IE00TSTSWP72", recs["IE00TSTSWP72"])
    assert any("Swap based Unfunded" in n for n in swap.notes)


def test_etfdb_adapter_is_registered_local_only_and_on_by_default():
    a = issuers.safe_discover()["etfdb_top"]
    assert a.redistribution == "local_only" and a.enabled_by_default
    assert "top 10 only" in a.title and "justETF" in a.licence
    assert "etfdb_top" in issuers.select(issuers.safe_discover())


@pytest.fixture
def etfdb_dir(tmp_path, monkeypatch):
    """The fixture saved as today's etfs.json, so nothing is downloaded."""
    folder = tmp_path / "etfdb"
    folder.mkdir()
    shutil.copy(FIX / "etfdb_etfs.json", folder / f"etfs-{date.today().isoformat()}.json")
    (folder / "etfs-2020-01-01.json").write_text("[]")
    monkeypatch.setenv("OPENBASKET_ETFDB_DIR", str(folder))
    monkeypatch.setattr(http, "get", lambda *a, **k: pytest.fail("no download expected"))
    etfdb._cache.clear()
    yield folder
    etfdb._cache.clear()


def test_etfdb_fetch_downloads_once_a_day_and_prunes(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(http, "get", lambda url, **k: calls.append(url) or _Resp((FIX / "etfdb_etfs.json").read_bytes()))
    (tmp_path / "etfs-2020-01-01.json").write_text("[]")
    p = etfdb.fetch(tmp_path)
    assert calls == [etfdb.JSON_URL] and p.name == f"etfs-{date.today().isoformat()}.json"
    assert [f.name for f in tmp_path.glob("etfs-*.json")] == [p.name]
    etfdb.fetch(tmp_path)
    assert len(calls) == 1
    monkeypatch.setattr(http, "get", lambda url, **k: _Resp(payload={"not": "a list"}, content=b"{}"))
    with pytest.raises(ValueError):
        etfdb.fetch(tmp_path / "other")


def test_etfdb_pipeline_nodes(etfdb_dir, tmp_path):
    meta = build_etfdb_meta({"root": str(etfdb_dir)})
    assert len(meta) == 3 and not (etfdb_dir / "etfs-2020-01-01.json").exists()
    assert len(build_stand_ins(meta, {})) == 1
    meta[["isin"]].iloc[:1].to_parquet(tmp_path / "ip.parquet")
    summary, overlap = report_etfdb(meta, {"compare": {"issuer_products": {"path": str(tmp_path / "ip.parquet"), "holdings": True}}})
    assert summary["etfs"] == 3 and summary["with_top_holdings"] == 2 and summary["leveraged_or_short"] == 1
    assert summary["uses_swaps"] == 2 and summary["redistribution"] == "local_only"
    o = overlap.set_index(["subset", "table"])
    assert o.loc[("all", "issuer_products"), "known"] == 1 and o.loc[("with_top_holdings", "any"), "isins"] == 2


def test_etfdb_top_through_the_look_through(etfdb_dir, monkeypatch):
    """A fund only etfdb covers: its top lines as given, the rest unresolved."""
    issuers.safe_discover()
    monkeypatch.setattr(issuers, "_REGISTRY", {"etfdb_top": etfdb.ADAPTER})
    products = download_issuer_products([], [], {})
    assert set(products["issuer"]) == {"etfdb_top"}
    from openbasket.datasets import connection
    from openbasket.providers import DataProviders

    fm = pd.DataFrame(columns=["series_id", "series_name", "series_lei", "ticker", "has_nport", "cik"])
    nh = connection().from_df(pd.DataFrame({c: pd.Series([], dtype=object) for c in ("series_id", "isin", "cusip", "issuer_lei")}))
    sm = connection().from_df(pd.DataFrame({"isin": pd.Series([], dtype=object), "issuer_lei": pd.Series([], dtype=object),
                                            "issuer_type": pd.Series([], dtype=object), "n_positions": pd.Series([], dtype=int)}))
    p = DataProviders(nh, sm, fm, pd.DataFrame(columns=["ticker", "isin"]), None, source_order=["issuer", "nport"],
                      issuer_products=products, adapters=issuer_adapters(None))
    r = look_through("IE00TSTPHY65", p)
    assert r.summary["source"] == "etfdb_top" and r.summary["as_of"] == "2026-08-27"
    assert r.summary["resolved_pct"] == pytest.approx(9.3)
    assert r.summary["unresolved_pct"] == pytest.approx(90.7)
    swap = look_through("IE00TSTSWP72", p)
    assert "swap-based" in (swap.summary["note"] or "")


def test_new_pipelines_have_their_datasets_in_the_catalog():
    catalog = {}
    for name in ("catalog_financedatabase.yml", "catalog_etfdb.yml"):
        catalog.update(yaml.safe_load((CONF / name).read_text()))
    params = {}
    for name in ("parameters_financedatabase.yml", "parameters_etfdb.yml"):
        params.update(yaml.safe_load((CONF / name).read_text()))
    for pipe in (findb_pipeline(), etfdb_pipeline()):
        for ds in pipe.datasets():
            if ds.startswith("params:"):
                assert ds.split(":", 1)[1].split(".")[0] in params, ds
            else:
                assert ds in catalog, ds
    assert all(v["filepath"].startswith("data/") for v in catalog.values())


# -- network --------------------------------------------------------------------


@pytest.mark.network
def test_network_financedatabase(tmp_path):
    """The real ETF file: tens of thousands of listings, thousands of distinct ISINs."""
    row = findb.fetch("etf", tmp_path)
    listing, stats = findb.listings({"etf": findb.read(row["path"])})
    t = findb.build_table(listing, {"etf": row["as_of"]})
    assert stats["etf"]["rows"] > 20000 and len(t) > 10000
    assert "summary" not in t.columns
    assert t.set_index("isin").loc["IE00B5BMR087", "family"]  # iShares Core S&P 500 UCITS ETF


@pytest.mark.network
def test_network_etfdb(tmp_path):
    """One real ETF from albertored/etfdb: facts and top holdings."""
    recs = etfdb.index(json.loads(etfdb.fetch(tmp_path).read_text("utf-8")))
    assert len(recs) > 3000
    m = etfdb.meta(recs)
    assert m["uses_swaps"].sum() > 100 and m["is_leveraged"].sum() > 10
    p = etfdb.product_rows(recs)
    assert validate_products(p) == []
    isin = "IE00B4L5Y983" if "IE00B4L5Y983" in set(p["isin"]) else p["isin"].iloc[0]
    h = etfdb.holdings_of(isin, recs[isin])
    assert validate_holdings(h.holdings) == [] and 0 < len(h.holdings) <= 10
    assert 0 < h.holdings["weight_pct"].sum() <= 100.5
