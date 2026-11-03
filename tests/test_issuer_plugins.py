"""Registered issuer adapters are picked up by ingest, the universe, the providers and publish."""

import json
from datetime import date

import pandas as pd
import pytest

from openbasket.datasets import DuckDBParquetDataset, connection
from openbasket.lookthrough import look_through
from openbasket.pipelines.ingest.nodes import download_issuer_products
from openbasket.pipelines.lookthrough.nodes import issuer_adapters
from openbasket.pipelines.publish.nodes import publish
from openbasket.pipelines.universe.nodes import build_universe, run_universe
from openbasket.providers import DataProviders
from openbasket.sources import issuers
from openbasket.sources.issuers import PRODUCT_COLUMNS, IssuerAdapter, IssuerHoldings

FETCHED = []


def fake_products() -> pd.DataFrame:
    rows = [
        {"issuer": "fakeco", "site": "ch", "portfolio_id": "P1", "isin": "LU0274208692", "ticker": "XMWO",
         "fund_name": "Fake MSCI World UCITS ETF", "domicile": "LU", "currency": "USD", "asset_class": "Equity",
         "replication": "physical", "product_page_url": "https://fake.example/funds/LU0274208692", "holdings_url": None,
         "total_net_assets": 9e9},
        # A second share class of the same portfolio.
        {"issuer": "fakeco", "site": "ch", "portfolio_id": "P1", "isin": "IE00BJ0KDQ92", "ticker": "XDWD",
         "fund_name": "Fake MSCI World UCITS ETF 1C", "domicile": "LU", "currency": "EUR", "asset_class": "Equity",
         "replication": "physical", "product_page_url": None, "holdings_url": None, "total_net_assets": 1e9},
    ]
    return pd.DataFrame(rows)[PRODUCT_COLUMNS + ["total_net_assets"]]


def fake_holdings(product: dict, ctx) -> IssuerHoldings:
    FETCHED.append((product["isin"], ctx.cache_dir))
    rows = [
        # Reported ISIN.
        {"isin": "US0378331005", "cusip": None, "sedol": None, "ticker": "AAPL", "name": "APPLE INC", "weight_pct": 5.0,
         "asset_class": "equity", "currency": "USD", "country": "United States", "sector": "Information Technology"},
        # CUSIP that N-PORT maps to an ISIN.
        {"isin": None, "cusip": "594918104", "sedol": None, "ticker": "MSFT", "name": "MICROSOFT", "weight_pct": 4.0,
         "asset_class": "equity", "currency": "USD", "country": "US", "sector": "Information Technology"},
        # CUSIP only derivable; OpenFIGI knows the derived ISIN.
        {"isin": None, "cusip": "023135106", "sedol": None, "ticker": "AMZN", "name": "AMAZON", "weight_pct": 3.0,
         "asset_class": "equity", "currency": "USD", "country": "US", "sector": "Consumer Discretionary"},
        # CUSIP-shaped internal code: the derived ISIN is unknown to OpenFIGI, so it is dropped.
        {"isin": None, "cusip": "38259P508", "sedol": None, "ticker": None, "name": "INTERNAL CODE", "weight_pct": 1.0,
         "asset_class": "equity", "currency": "USD", "country": None, "sector": None},
        # SEDOL that N-PORT maps.
        {"isin": None, "cusip": None, "sedol": "B03MLX2", "ticker": "SHEL", "name": "SHELL PLC", "weight_pct": 2.0,
         "asset_class": "equity", "currency": "GBP", "country": "United Kingdom", "sector": "Energy"},
        {"isin": None, "cusip": None, "sedol": None, "ticker": None, "name": "USD CASH", "weight_pct": 0.5,
         "asset_class": "cash", "currency": "USD", "country": None, "sector": None},
    ]
    return IssuerHoldings("2026-11-02", pd.DataFrame(rows), f"https://fake.example/holdings/{product['portfolio_id']}.csv")


FAKE = IssuerAdapter(
    name="fakeco", title="FakeCo ETFs", homepage="https://fake.example/", credit="Holdings data from FakeCo ETFs",
    licence="Public website data; terms not reviewed", regions=("CH",), products=fake_products, holdings=fake_holdings,
)


def broken_products():
    raise ConnectionError("site down")


BROKEN = IssuerAdapter(
    name="brokenco", title="Broken", homepage="https://broken.example/", credit="Broken", licence="x", regions=("XX",),
    products=broken_products, holdings=fake_holdings,
)


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(issuers, "_REGISTRY", {"fakeco": FAKE, "brokenco": BROKEN})
    FETCHED.clear()
    return issuers._REGISTRY


class FakeFigi:
    known = {"US0231351067"}

    def map(self, id_type, values, exch_code=None):
        return {v: ([{"name": "X", "exchCode": "US"}] if v in self.known else []) for v in values}


def test_ingest_unions_registered_adapters(registry):
    products = download_issuer_products([], [], {"enabled": None, "disabled": []})
    assert set(products["issuer"]) == {"fakeco"}  # brokenco failed and was skipped
    assert products["isin"].tolist() == ["LU0274208692", "IE00BJ0KDQ92"]
    assert set(products["listed_on"]) == {date.today().isoformat()}
    assert download_issuer_products([], [], {"disabled": ["fakeco"]}).empty
    assert set(issuer_adapters({"enabled": ["fakeco"]})) == {"fakeco"}
    assert set(issuer_adapters({"enabled": ["fakeco", "nosuch"]})) == {"fakeco"}


def _tables():
    fm = pd.DataFrame(columns=["series_id", "series_name", "series_lei", "ticker", "has_nport", "cik"])
    nh = connection().from_df(pd.DataFrame({c: pd.Series([], dtype=object) for c in
                                            ("series_id", "isin", "cusip", "issuer_lei")}))
    sm = connection().from_df(pd.DataFrame({"isin": pd.Series([], dtype=object), "issuer_lei": pd.Series([], dtype=object),
                                            "issuer_type": pd.Series([], dtype=object), "n_positions": pd.Series([], dtype=int)}))
    return fm, nh, sm


def providers(products, **kw):
    fm, nh, sm = _tables()
    return DataProviders(
        nh, sm, fm, pd.DataFrame(columns=["ticker", "isin"]), FakeFigi(), source_order=["issuer", "nport"],
        issuer_products=products, issuer_cache_dir="/tmp/cache", adapters=issuer_adapters(None),
        cusip_isin={"594918104": ("US5949181045", "reported_for_cusip")}, sedol_isin={"B03MLX2": "GB00BP6MXD84"}, **kw,
    )


def test_adapter_holdings_are_read_and_identified(registry):
    products = download_issuer_products([], [], {})
    p = providers(products)
    ref = p.resolve("IE00BJ0KDQ92")
    assert ref.issuer_id == "fakeco-ch:P1" and ref.via == "fakeco"
    [h] = p.holdings(ref)
    assert h.source == "fakeco" and h.as_of == "2026-11-02"
    assert h.source_url == "https://fake.example/funds/LU0274208692"
    assert FETCHED == [("LU0274208692", "/tmp/cache")]
    found = {l.name: (l.isin, l.isin_source) for l in h.lines}
    assert found == {
        "APPLE INC": ("US0378331005", "reported"),
        "MICROSOFT": ("US5949181045", "nport_cusip"),
        "AMAZON": ("US0231351067", "derived_cusip"),
        "INTERNAL CODE": (None, None),
        "SHELL PLC": ("GB00BP6MXD84", "nport_sedol"),
        "USD CASH": (None, None),
    }
    by_name = {l.name: l for l in h.lines}
    assert by_name["APPLE INC"].country == "US" and by_name["SHELL PLC"].country == "GB"
    assert by_name["SHELL PLC"].sector == "Energy"
    assert p.source_status(ref) == "issuer=ok"


def test_disabled_adapter_fund_explains_itself(registry):
    products = download_issuer_products([], [], {})
    fm, nh, sm = _tables()
    p = DataProviders(nh, sm, fm, pd.DataFrame(columns=["ticker", "isin"]), None, source_order=["issuer"],
                      issuer_products=products, adapters={})
    r = look_through("LU0274208692", p)
    assert r.summary["status"] == "no_holdings" and "not enabled" in r.summary["note"]


def test_universe_and_publish_with_an_adapter(registry, tmp_path):
    products = download_issuer_products([], [], {})
    fm, nh, sm = _tables()
    universe = build_universe(products, fm, sm, {})
    [fund] = universe.to_dict("records")
    assert fund["id"] == "LU0274208692" and list(fund["isins"]) == ["LU0274208692", "IE00BJ0KDQ92"]
    assert fund["issuer"] == "FakeCo ETFs" and fund["universe_source"] == "fakeco"
    lt = {"source_order": ["issuer", "nport"], "max_depth": 4, "min_resolved_pct": 90.0, "use_openfigi": False}
    up = {"work_dir": str(tmp_path / "parts"), "rows_per_part": 100, "log_every": 10}
    cmap = connection().from_df(pd.DataFrame({"cusip": ["594918104"], "isin": ["US5949181045"], "isin_source": ["reported_for_cusip"]}))
    smap = pd.DataFrame({"sedol": ["B03MLX2"], "isin": ["GB00BP6MXD84"]})
    funds, constituents, run = run_universe(universe, nh, sm, fm, pd.DataFrame(columns=["ticker", "isin"]), products, cmap,
                                            smap, lt, up, {}, {})
    f = funds.iloc[0]
    assert f["source"] == "fakeco" and f["matched_via"] == "fakeco" and f["credit"] == "Holdings data from FakeCo ETFs"
    # Without OpenFIGI, the derived Amazon ISIN cannot be checked and is left out.
    assert f["resolved_pct"] == pytest.approx(11.0) and run["issuer_adapters"] == ["brokenco", "fakeco"]
    ds = DuckDBParquetDataset(str(tmp_path / "c.parquet"), order_by="fund_id")
    ds.save(constituents)
    params = {"release_tag": "2026-11", "repo": "omaxim/openbasket", "site_dir": str(tmp_path / "site"),
              "release_dir": str(tmp_path / "release"), "sources": {}}
    publish(universe, funds, ds.load(), run, params, {})
    api = tmp_path / "site" / "api" / "v1"
    meta = json.loads((api / "meta.json").read_text())
    [entry] = meta["sources"]
    assert entry["id"] == "fakeco" and entry["credit"] == "Holdings data from FakeCo ETFs"
    assert entry["url"] == "https://fake.example/" and entry["licence"]
    doc = json.loads((api / "funds" / "LU0274208692.json").read_text())
    assert {r[4] for r in doc["constituents"]} == {"fakeco"}
    assert json.loads((api / "funds" / "IE00BJ0KDQ92.json").read_text()) == {"alias_of": "LU0274208692"}
    countries = dict(doc["breakdowns"]["country"])
    assert countries["US"] == pytest.approx(9.0) and countries["GB"] == pytest.approx(2.0)
