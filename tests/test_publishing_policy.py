"""The publishing policy: the public build never reads or writes a local_only source's holdings.

A fake ``local_only`` adapter (FakeCo, an issuer site) and a fake ``public``
one (OpenCo, a regulator's open data) run through ingest, the universe, the
providers and publish in both modes. In the full mode (a run on your own
machine) FakeCo's rows appear; in the public mode FakeCo is never asked for
anything, its funds are listed by name and ISIN with status ``local_only``
and no rows, and the build passes ``scripts/check_build.py``.
"""

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from openbasket import sources
from openbasket.datasets import DuckDBParquetDataset, connection
from openbasket.lookthrough import look_through
from openbasket.pipelines.ingest import nodes as ingest
from openbasket.pipelines.lookthrough.nodes import issuer_adapters
from openbasket.pipelines.publish.nodes import LocalOnlyInPublicBuild, publish
from openbasket.pipelines.universe.nodes import build_universe, run_universe
from openbasket.providers import DataProviders
from openbasket.sources import fund_identity, issuers
from openbasket.sources.issuers import PRODUCT_COLUMNS, IssuerAdapter, IssuerHoldings

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_build", ROOT / "scripts" / "check_build.py")
check_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_build)

CALLS: list[tuple[str, str]] = []
RUN_LOCALLY = "https://openbasket.leithresearch.org/docs/running-locally.html"


def _product(issuer, pid, isin, name, page):
    return {"issuer": issuer, "site": "eu", "portfolio_id": pid, "isin": isin, "ticker": None, "fund_name": name,
            "domicile": "LU", "currency": "EUR", "asset_class": "Equity", "replication": "physical",
            "product_page_url": page, "holdings_url": f"{page}/holdings.csv"}


def fake_products():
    CALLS.append(("products", "fakeco"))
    return pd.DataFrame([
        _product("fakeco", "P1", "LU0274208692", "FakeCo World UCITS ETF", "https://fake.example/LU0274208692"),
        _product("fakeco", "P2", "IE00BJ0KDQ92", "FakeCo Europe UCITS ETF", "https://fake.example/IE00BJ0KDQ92"),
    ])[PRODUCT_COLUMNS]


def open_products():
    CALLS.append(("products", "openco"))
    return pd.DataFrame([
        _product("openco", "C1", "ES0105336038", "OpenCo Fondo Global", "https://open.example/ES0105336038"),
    ])[PRODUCT_COLUMNS]


def _holdings(name):
    def holdings(product, ctx):
        CALLS.append(("holdings", name))
        rows = [
            {"isin": "US0378331005", "cusip": None, "sedol": None, "ticker": "AAPL", "name": "APPLE INC",
             "weight_pct": 60.0, "asset_class": "equity", "currency": "USD", "country": "US"},
            # A FakeCo fund inside the fund: in the public build it cannot be looked through.
            {"isin": "IE00BJ0KDQ92", "cusip": None, "sedol": None, "ticker": None, "name": "FAKECO EUROPE",
             "weight_pct": 40.0, "asset_class": "fund", "currency": "EUR", "country": "IE"},
        ] if name == "openco" else [
            {"isin": "US5949181045", "cusip": None, "sedol": None, "ticker": "MSFT", "name": "MICROSOFT",
             "weight_pct": 99.0, "asset_class": "equity", "currency": "USD", "country": "US"},
        ]
        return IssuerHoldings("2026-10-31", pd.DataFrame(rows), f"https://{name}.example/{product['portfolio_id']}.csv")
    return holdings


FAKE = IssuerAdapter(
    name="fakeco", title="FakeCo ETFs", homepage="https://fake.example/", credit="Holdings data from FakeCo ETFs",
    licence="Website terms restrict republication", regions=("LU",), products=fake_products,
    holdings=_holdings("fakeco"),
)
OPEN = IssuerAdapter(
    name="openco", title="OpenCo regulator filings", homepage="https://open.example/", credit="Data from OpenCo",
    licence="Open data", regions=("ES",), products=open_products, holdings=_holdings("openco"),
    redistribution="public",
)


@pytest.fixture
def registry(monkeypatch):
    issuers.safe_discover()  # real adapters register in the real registry first
    monkeypatch.setattr(issuers, "_REGISTRY", {"fakeco": FAKE, "openco": OPEN})
    CALLS.clear()

    def no_builtin(*a, **kw):
        raise AssertionError("the public build must not read the iShares or SPDR lists")

    return no_builtin


def _tables():
    fm = pd.DataFrame(columns=["series_id", "series_name", "series_lei", "ticker", "has_nport", "cik"])
    nh = connection().from_df(pd.DataFrame({c: pd.Series([], dtype=object) for c in
                                            ("series_id", "isin", "cusip", "issuer_lei")}))
    sm = connection().from_df(pd.DataFrame({"isin": pd.Series([], dtype=object), "issuer_lei": pd.Series([], dtype=object),
                                            "issuer_type": pd.Series([], dtype=object), "n_positions": pd.Series([], dtype=int)}))
    return fm, nh, sm


PUBLISH_SOURCES = {
    "issuer_list": {"name": "Issuers' public fund lists", "url": RUN_LOCALLY, "licence": "Facts only",
                    "credit": "Fund name and ISIN as listed by the fund's issuer", "listed_by": "the issuer's public fund list"},
    "fund_directory": {"name": "Fund directory", "url": "https://dir.example/", "licence": "Facts only",
                       "credit": "Fund names from public listings", "listed_by": "the fund directory"},
}


def _publish_params(tmp_path, mode, **identity):
    return {
        "mode": mode, "run_locally_url": RUN_LOCALLY, "release_tag": "2026-11", "repo": "omaxim/openbasket",
        "site_dir": str(tmp_path / mode / "site"), "release_dir": str(tmp_path / mode / "release"),
        "sources": PUBLISH_SOURCES, "identity": {"registries": {}, "issuer_lists": [], **identity},
    }


def _run(products, tmp_path, pp):
    fm, nh, sm = _tables()
    universe = build_universe(products, fm, sm, {}, pp)
    lt = {"source_order": ["issuer", "nport"], "max_depth": 4, "min_resolved_pct": 90.0, "use_openfigi": False}
    up = {"work_dir": str(tmp_path / pp["mode"] / "parts"), "rows_per_part": 100, "log_every": 10}
    cmap = connection().from_df(pd.DataFrame({"cusip": pd.Series([], dtype=object), "isin": pd.Series([], dtype=object),
                                              "isin_source": pd.Series([], dtype=object)}))
    smap = pd.DataFrame({"sedol": pd.Series([], dtype=object), "isin": pd.Series([], dtype=object)})
    funds, constituents, run = run_universe(universe, nh, sm, fm, pd.DataFrame(columns=["ticker", "isin"]), products, cmap,
                                            smap, lt, up, PUBLISH_SOURCES, {}, pp)
    ds = DuckDBParquetDataset(str(tmp_path / pp["mode"] / "c.parquet"), order_by="fund_id")
    ds.save(constituents)
    return universe, funds, ds.load(), run


def _api(pp) -> Path:
    return Path(pp["site_dir"]) / "api" / "v1"


def _doc(pp, fid):
    return json.loads((_api(pp) / "funds" / f"{fid}.json").read_text())


def test_publish_mode_is_validated():
    assert sources.publish_mode(None) == "full"
    assert sources.publish_mode({"mode": "public"}) == "public"
    assert sources.publish_mode({}) == "full"
    with pytest.raises(ValueError, match="publish.mode"):
        sources.publish_mode({"mode": "pubic"})


def test_full_mode_reads_every_source_and_publishes_its_rows(registry, tmp_path, monkeypatch):
    monkeypatch.setattr(ingest.spdr, "products", lambda region: pd.DataFrame(columns=PRODUCT_COLUMNS))
    pp = _publish_params(tmp_path, "full")
    products = ingest.download_issuer_products(["uk"], [], {}, pp)
    assert set(products["issuer"]) == {"fakeco", "openco"}
    universe, funds, rel, run = _run(products, tmp_path, pp)
    assert ("holdings", "fakeco") in CALLS
    by_id = funds.set_index("id")
    assert by_id.at["LU0274208692", "status"] == "ok" and by_id.at["LU0274208692", "source"] == "fakeco"
    # OpenCo's fund holds a FakeCo fund, which is looked through.
    assert by_id.at["ES0105336038", "unexpanded_fund_pct"] == 0
    publish(universe, funds, rel, run, pp, {})
    doc = _doc(pp, "LU0274208692")
    assert doc["status"] == "ok" and {r[4] for r in doc["constituents"]} == {"fakeco"}
    meta = json.loads((_api(pp) / "meta.json").read_text())
    assert meta["mode"] == "full"
    assert {s["id"]: s["redistribution"] for s in meta["sources"]} == {"fakeco": "local_only", "openco": "public"}
    report = json.loads((Path(pp["release_dir"]) / "quality-report.json").read_text())
    assert report["publishing"]["mode"] == "full" and report["publishing"]["withheld"] == []
    assert report["counts"]["published_by_status"] == {"ok": 3}
    # A full build is for the person who ran it: the public gate refuses it.
    assert check_build.main(["--api", str(Path(pp["site_dir"]) / "api"), "--release", pp["release_dir"],
                             "--params", "none.yml"]) == 1


def _cached_issuer_list(tmp_path) -> str:
    """What a full run leaves in data/01_raw/issuers/issuer_products.parquet, with every column."""
    full = pd.concat([fake_products(), open_products()], ignore_index=True).assign(listed_on="2026-11-02")
    path = tmp_path / "issuer_products.parquet"
    full.to_parquet(path, index=False)
    CALLS.clear()
    return str(path)


def test_public_mode_never_asks_a_local_only_source_and_lists_its_funds(registry, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ingest.spdr, "products", registry)
    pp = _publish_params(tmp_path, "public", issuer_lists=[_cached_issuer_list(tmp_path)])
    products = ingest.download_issuer_products(["uk", "us"], ["us", "emea"], {}, pp)
    assert CALLS == [("products", "openco")]
    fake = products[products["issuer"] == "fakeco"]
    assert sorted(fake["isin"]) == ["IE00BJ0KDQ92", "LU0274208692"]
    # Only identity facts: no product page, holdings link, ticker or domicile from the issuer's list.
    assert fake["product_page_url"].isna().all() and fake["holdings_url"].isna().all() and fake["domicile"].isna().all()
    assert set(fake["identity_source"]) == {"issuer_list"} and set(fake["identity_url"]) == {"https://fake.example/"}

    universe, funds, rel, run = _run(products, tmp_path, pp)
    assert ("holdings", "fakeco") not in CALLS and ("holdings", "openco") in CALLS
    by_id = funds.set_index("id")
    lo = by_id.loc["LU0274208692"]
    assert lo["status"] == "local_only" and lo["source"] == "issuer_list" and lo["matched_via"] == "issuer_list"
    assert lo["note"].startswith("Holdings for this fund are available from FakeCo ETFs when you run openbasket yourself")
    assert RUN_LOCALLY in lo["note"] and lo["sources_status"] == "issuer=local_only"
    assert lo["credit"].endswith("its holdings are available from FakeCo ETFs when you run openbasket yourself")
    assert run["n_local_only"] == 2 and run["mode"] == "public"
    # The public fund holding a FakeCo fund keeps it as an unexpanded row that says why.
    op = by_id.loc["ES0105336038"]
    assert op["status"] != "local_only" and op["unexpanded_fund_pct"] == pytest.approx(40.0)
    sub = rel.filter("fund_id = 'ES0105336038' and constituent_isin = 'IE00BJ0KDQ92'").df().iloc[0]
    assert sub["source"] == "openco" and sub["asset_class"] == "fund"
    assert sub["note"].startswith("Holdings for this fund are available from FakeCo ETFs")
    assert set(rel.df()["source"]) == {"openco", "issuer_list"}

    publish(universe, funds, rel, run, pp, {})
    doc = _doc(pp, "LU0274208692")
    assert doc["status"] == "local_only" and doc["constituents"] == [] and doc["n_rows"] == 0
    assert doc["source"] == "issuer_list" and doc["source_url"] == "https://fake.example/"
    assert doc["unresolved"]["weight_pct"] == 100.0 and doc["unresolved"]["source"] == "issuer_list"
    assert doc["unresolved"]["note"].startswith("Holdings for this fund are available from FakeCo ETFs")
    meta = json.loads((_api(pp) / "meta.json").read_text())
    assert meta["mode"] == "public"
    assert [s["id"] for s in meta["sources"]] == ["issuer_list", "openco"]
    assert all(s["redistribution"] == "public" for s in meta["sources"])
    index = json.loads((_api(pp) / "index.json").read_text())
    assert {r[0] for r in index["rows"]} == {"LU0274208692", "IE00BJ0KDQ92", "ES0105336038"}
    report = json.loads((Path(pp["release_dir"]) / "quality-report.json").read_text())
    assert report["publishing"]["withheld"] == ["fakeco"]
    assert report["publishing"]["local_only_by_issuer"] == {"FakeCo ETFs": 2}
    assert report["counts"]["published_by_status"]["local_only"] == 2
    # Nothing from FakeCo in any file the public build writes.
    for f in Path(pp["site_dir"]).rglob("*.json"):
        assert "fakeco" not in f.read_text().replace("FakeCo", "")
    lt = pd.read_parquet(Path(pp["release_dir"]) / "openbasket-lookthrough.parquet")
    assert set(lt["source"]) == {"openco", "issuer_list"}
    capsys.readouterr()
    code = check_build.main(["--api", str(Path(pp["site_dir"]) / "api"), "--release", pp["release_dir"],
                             "--params", "none.yml"])
    assert code == 0, capsys.readouterr().out


def test_public_publish_refuses_a_full_run(registry, tmp_path, monkeypatch):
    monkeypatch.setattr(ingest.spdr, "products", lambda region: pd.DataFrame(columns=PRODUCT_COLUMNS))
    full = _publish_params(tmp_path, "full")
    universe, funds, rel, run = _run(ingest.download_issuer_products([], [], {}, full), tmp_path, full)
    public = _publish_params(tmp_path, "public")
    with pytest.raises(LocalOnlyInPublicBuild, match="fakeco"):
        publish(universe, funds, rel, run, public, {})
    assert not Path(public["release_dir"]).exists() and not Path(public["site_dir"]).exists()


def test_public_mode_without_any_identity_source_leaves_local_only_funds_out(registry, tmp_path):
    pp = _publish_params(tmp_path, "public")
    products = ingest.download_issuer_products([], [], {}, pp)
    assert set(products["issuer"]) == {"openco"} and CALLS == [("products", "openco")]


def test_registry_names_win_over_the_issuer_list(tmp_path):
    reg = tmp_path / "fund_directory.parquet"
    pd.DataFrame({
        "isin": ["LU0274208692", "LU1681043599", "XX0000000000"],
        "name": ["FakeCo MSCI World (directory name)", "FakeCo Japan", "bad isin"],
        "adapter": ["fakeco", "fakeco", "fakeco"],
        "source_url": ["https://dir.example/LU0274208692", None, None],
        "as_of": ["2026-10-30", "2026-10-30", None],
        "holdings_route": ["issuer", "issuer", None],
    }).to_parquet(reg, index=False)
    lst = tmp_path / "issuer_products.parquet"
    pd.DataFrame({"isin": ["LU0274208692", "IE00BJ0KDQ92", "US4642872000"], "fund_name": ["List name", "Europe", "IVV"],
                  "issuer": ["fakeco", "fakeco", "ishares"], "product_page_url": ["https://p/1", "https://p/2", "https://p/3"],
                  "holdings_url": ["h1", "h2", "h3"], "listed_on": ["2026-11-02"] * 3}).to_parquet(lst, index=False)
    rows = fund_identity.identity_rows({"fakeco": {"title": "FakeCo ETFs", "homepage": "https://fake.example/"}},
                                       {"fund_directory": str(reg), "firds": str(tmp_path / "missing.parquet")},
                                       [str(lst)], today="2026-11-03")
    by = rows.set_index("isin")
    assert sorted(by.index) == ["IE00BJ0KDQ92", "LU0274208692", "LU1681043599"]  # no iShares: not asked for
    assert by.at["LU0274208692", "fund_name"] == "FakeCo MSCI World (directory name)"
    assert by.at["LU0274208692", "identity_source"] == "fund_directory"
    assert by.at["LU0274208692", "identity_url"] == "https://dir.example/LU0274208692"
    assert by.at["IE00BJ0KDQ92", "identity_source"] == "issuer_list"
    assert by.at["IE00BJ0KDQ92", "identity_url"] == "https://fake.example/"
    assert by.at["IE00BJ0KDQ92", "listed_on"] == "2026-11-02"
    assert list(rows.columns) == fund_identity.IDENTITY_COLUMNS


def test_local_only_fund_that_files_nport_is_published_as_its_series(registry, tmp_path):
    """A US issuer ETF the public build cannot read from its issuer is still public through N-PORT."""
    lst = tmp_path / "list.parquet"
    pd.DataFrame({"isin": ["US4642872000", "IE00B5BMR087"], "fund_name": ["iShares Core S&P 500 ETF (list)", "iShares Core S&P 500 UCITS ETF"],
                  "issuer": ["ishares", "ishares"], "listed_on": ["2026-11-02"] * 2}).to_parquet(lst, index=False)
    products = fund_identity.identity_rows({"ishares": {"title": "iShares", "homepage": "https://www.ishares.com/"}},
                                           {}, [str(lst)], today="2026-11-03")
    fm = pd.DataFrame([{"series_id": "S000004310", "series_name": "iShares Core S&P 500 ETF", "series_lei": "LEI-IVV",
                        "ticker": "IVV", "has_nport": True, "cik": "1100663", "registrant_name": "iShares Trust",
                        "report_date": "2026-07-31"}])
    sm = connection().from_df(pd.DataFrame([{"isin": "US4642872000", "issuer_lei": "LEI-IVV", "issuer_type": "RF",
                                             "n_positions": 40}]))
    universe = build_universe(products, fm, sm, {}, {"mode": "public"})
    by = universe.set_index("id")
    ivv = by.loc["US4642872000"]
    assert ivv["universe_source"] == "nport" and ivv["series_id"] == "S000004310" and pd.isna(ivv["issuer_id"])
    assert ivv["name"] == "iShares Core S&P 500 ETF" and ivv["issuer"] == "iShares Trust" and ivv["list_source"] == "nport"
    cspx = by.loc["IE00B5BMR087"]
    assert cspx["universe_source"] == "ishares" and cspx["list_source"] == "issuer_list" and pd.isna(cspx["ticker"])
    # The same universe in a full run keeps the issuer's fund (with N-PORT as its fallback).
    full = build_universe(products, fm, sm, {}, {"mode": "full"}).set_index("id")
    assert full.at["US4642872000", "universe_source"] == "ishares" and full.at["US4642872000", "series_id"] == "S000004310"


def test_providers_in_public_mode_never_fetch_a_local_only_issuer(registry):
    products = pd.concat([fake_products(), open_products()], ignore_index=True)
    CALLS.clear()
    fm, nh, sm = _tables()

    def providers(mode):
        return DataProviders(nh, sm, fm, pd.DataFrame(columns=["ticker", "isin"]), None, source_order=["issuer", "nport"],
                             issuer_products=products, adapters=issuer_adapters(None), mode=mode)

    r = look_through("LU0274208692", providers("public"))
    assert CALLS == [] and r.summary["status"] == "local_only" and r.summary["local_only"] == "FakeCo ETFs"
    assert r.summary["sources_status"] == "issuer=local_only" and r.leaves == []
    r = look_through("LU0274208692", providers("full"))
    assert CALLS == [("holdings", "fakeco")] and r.summary["status"] == "ok"
    # Even with "issuer" left out of source_order, the status says why there is nothing.
    p = DataProviders(nh, sm, fm, pd.DataFrame(columns=["ticker", "isin"]), None, source_order=["nport"],
                      issuer_products=products, adapters=issuer_adapters(None), mode="public")
    assert look_through("LU0274208692", p).summary["status"] == "local_only"


def test_redistribution_of_identity_sources():
    for sid in ("issuer_list", "fund_directory", "fund_registry", "firds", "gleif"):
        assert sources.redistribution(sid) == "public"


class _Figi:
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def map(self, id_type, values):
        self.asked += list(values)
        return {v: self.answers.get(v, []) for v in values}


def test_us_identity_funds_meet_their_series_by_an_openfigi_ticker(tmp_path):
    lst = tmp_path / "list.parquet"
    pd.DataFrame({"isin": ["US4642882652", "IE00B5BMR087"], "fund_name": ["iShares MSCI Kokusai ETF", "CSPX"],
                  "issuer": ["ishares", "ishares"], "ticker": ["TOK", "CSPX"], "listed_on": ["2026-11-02"] * 2}
                 ).to_parquet(lst, index=False)
    rows = fund_identity.identity_rows({"ishares": {"title": "iShares", "homepage": "https://www.ishares.com/"}},
                                       {}, [str(lst)], today="2026-11-03")
    assert rows["ticker"].isna().all()  # the issuer's ticker is not read
    figi = _Figi({"US4642882652": [{"ticker": "TOK LN", "exchCode": "LN"}, {"ticker": "TOK", "exchCode": "US"}]})
    rows = fund_identity.add_us_tickers(rows, figi)
    assert figi.asked == ["US4642882652"]
    by = rows.set_index("isin")
    assert by.at["US4642882652", "ticker"] == "TOK" and by.at["US4642882652", "ticker_source"] == "openfigi"
    assert pd.isna(by.at["IE00B5BMR087", "ticker"])
    fm = pd.DataFrame([{"series_id": "S000004385", "series_name": "iShares MSCI Kokusai ETF", "series_lei": "LEI-TOK",
                        "ticker": "TOK", "has_nport": True, "cik": "1100663", "registrant_name": "iShares Trust",
                        "report_date": "2026-07-31"}])
    _, _, sm = _tables()
    u = build_universe(rows, fm, sm, {}, {"mode": "public"}).set_index("id")
    tok = u.loc["US4642882652"]
    assert tok["series_match"] == "ticker" and tok["universe_source"] == "nport" and tok["issuer"] == "iShares Trust"
    assert len(u) == 2  # the series is not listed a second time


def test_a_failing_openfigi_leaves_tickers_empty():
    class Down:
        def map(self, *a):
            raise ConnectionError("down")

    rows = pd.DataFrame([{**{c: None for c in fund_identity.IDENTITY_COLUMNS}, "isin": "US4642882652"}])
    assert fund_identity.add_us_tickers(rows, Down())["ticker"].isna().all()
