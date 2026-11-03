"""iShares regional sites and the registered ``ishares`` adapter.

Fixtures in ``tests/fixtures/ishares-regions`` are tiny and synthetic: the
field names and shapes of the real responses, with invented funds and values.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from openbasket.identifiers import cusip_to_isin
from openbasket.sources import ishares
from openbasket.sources.issuers import (
    HOLDING_COLUMNS,
    PRODUCT_COLUMNS,
    FetchContext,
    discover,
    validate_holdings,
    validate_products,
)
from openbasket.sources.issuers import ishares as adapter

FIX = Path(__file__).parent / "fixtures" / "ishares-regions"
CH_ETF, CH_IDX, IE_SWAP, IE_ETF = "CH00000000A8", "CH00000000B6", "IE00000000C8", "IE00000000D6"


class Resp:
    def __init__(self, name: str):
        self.content = (FIX / name).read_bytes()
        self.text = self.content.decode("utf-8-sig")

    def json(self):
        # Like requests, a byte-order mark makes this fail.
        return json.loads(self.content.decode("utf-8"))


def route(calls: list | None = None):
    """A stand-in for ``ishares._get`` that answers from the fixtures."""

    def get(url, params):
        if calls is not None:
            calls.append((url, dict(params or {})))
        params = params or {}
        if "product-screener-server" in url:
            return Resp({"ishares-ch": "screener_ch.json", "de-ishares-v2": "screener_de.json"}[params["siteName"]])
        if url.endswith("product-sitemap.xml"):
            return Resp("sitemap_au.xml")
        if "/jp/" in url and url.endswith(".jsn"):
            return Resp("screener_jp.json")
        if "/ca/" in url and url.endswith(".jsn"):
            return Resp("screener_ca.json")
        if url.endswith("1524727817804.ajax"):
            return Resp("screener_fr.json")
        if "get-product-data" in url:
            if params["component"] == "keyFundFacts":
                return Resp({"blk-au": "keyfacts_au.json", "ca-one": "keyfacts_ca.json"}[params["targetSite"]])
            return Resp({"900003": "holdings_swap_de.json", "900001": "holdings_bond_ch.json",
                         "900002": "holdings_top10.json"}[params["portfolioId"]])
        raise AssertionError(f"unexpected request {url} {params}")

    return get


@pytest.fixture
def fake(monkeypatch):
    calls: list = []
    monkeypatch.setattr(ishares, "_get", route(calls))
    return calls


def test_every_site_has_product_data_parameters():
    for site, cfg in ishares.SITES.items():
        assert cfg["data_site"] in (ishares.EMEA_DATA, ishares.ONE_DATA), site
        assert cfg["targetSite"] and cfg["locale"] and cfg["appSubType"] in ("ISHARES", "ONE"), site
        assert cfg.get("screener", "varnish") in ("varnish", "v3", "legacy", "sitemap"), site


def test_products_switzerland(fake):
    df = ishares.products("ch")
    assert df["isin"].tolist() == [CH_ETF, CH_IDX, IE_SWAP]  # the row without an ISIN is dropped
    etf = df.iloc[0]
    assert etf["domicile"] == "CH" and etf["currency"] == "CHF" and etf["fund_id"] == "IS-EXCH"
    assert etf["holdings_scope"] == "full" and etf["asset_class"] == "Equity"
    # The screener says /individuals/, which answers 404; the page is under /individual/.
    assert etf["product_page_url"] == "https://www.ishares.com/ch/individual/en/products/900001/example-fund"
    assert "targetSite=ishares-ch" in etf["holdings_url"] and "locale=en_CH" in etf["holdings_url"]
    assert etf["total_net_assets"] == pytest.approx(1.5e9)
    assert df.set_index("isin").loc[CH_IDX, "holdings_scope"] == "top10"
    assert df.set_index("isin").loc[IE_SWAP, "replication"] == "swap"
    url, params = fake[0]
    assert url.startswith("https://www.ishares.com/varnish-api/") and params["siteName"] == "ishares-ch"


def test_products_local_language_site_gets_english_labels_and_iso_domicile(fake):
    df = ishares.products("de").set_index("isin")
    assert df.loc[IE_ETF, "domicile"] == "IE"
    assert df.loc[IE_ETF, "asset_class"] == "Fixed Income"
    assert df.loc[IE_SWAP, "asset_class"] == "Equity"


def test_products_v3_japan(fake):
    df = ishares.products("jp")
    assert df[["isin", "domicile", "ticker", "holdings_scope"]].values.tolist() == [["JP3000000004", "JP", "9999", "full"]]
    assert df["product_page_url"].iloc[0].startswith("https://www.blackrock.com/jp/")


def test_products_legacy_france_with_byte_order_mark(fake):
    df = ishares.products("fr").set_index("isin")
    assert df.loc["IE00000000E4", "replication"] == "swap"
    assert df.loc["IE00000000E4", "domicile"] == "IE"
    assert (df.drop(index="IE00000000E4")["holdings_scope"] == "top10").all()


def test_products_australia_from_sitemap_and_key_facts(fake):
    df = ishares.products("au")
    assert df[["portfolio_id", "isin", "ticker", "currency", "domicile", "fund_id"]].values.tolist() == [
        ["900030", "AU000000XYZ2", "XYZ", "AUD", "AU", "XYZ"]]
    # The sitemap lists the fund twice; it is looked up once.
    assert sum(1 for _, p in fake if p.get("component") == "keyFundFacts") == 1


def test_products_canada_isin_from_key_facts_cusip(fake):
    df = ishares.products("ca")
    assert df["isin"].tolist() == [cusip_to_isin("464000009", "CA")]
    assert df["isin"].iloc[0].startswith("CA") and df["currency"].iloc[0] == "CAD"


def test_key_facts_lookups_can_be_capped(fake):
    assert ishares.products("ca", max_lookups=0).empty


def _site_frames(fake):
    return pd.concat([ishares.products("ch"), ishares.products("de")], ignore_index=True)


def test_merge_sites_keeps_one_row_per_isin(fake):
    merged = adapter.merge_sites(_site_frames(fake), ["ch", "de"])
    assert validate_products(merged) == []
    assert list(merged.columns[: len(PRODUCT_COLUMNS)]) == PRODUCT_COLUMNS
    m = merged.set_index("isin")
    assert m.loc[IE_SWAP, "site"] == "ch" and m.loc[IE_SWAP, "sites"] == "ch,de"
    assert m.loc[IE_SWAP, "portfolio_id"] == "IS-EXSW" and m.loc[IE_SWAP, "share_class_id"] == "900003"
    # A DE-only share class with no fund ticker keeps its own portfolio ID.
    assert m.loc[IE_ETF, "site"] == "de" and m.loc[IE_ETF, "portfolio_id"] == "900005"
    assert (merged["issuer"] == "ishares").all()


def test_merge_sites_prefers_site_order_then_full_holdings(fake):
    merged = adapter.merge_sites(_site_frames(fake), ["de", "ch"]).set_index("isin")
    assert merged.loc[IE_SWAP, "site"] == "de"
    # The fund ticker comes from the CH row of the same share class.
    assert merged.loc[IE_SWAP, "portfolio_id"] == "IS-EXSW"
    a = pd.DataFrame([{"site": "uk", "portfolio_id": "1", "fund_id": None, "isin": CH_IDX, "holdings_scope": "top10"},
                      {"site": "ch", "portfolio_id": "1", "fund_id": None, "isin": CH_IDX, "holdings_scope": "full"}])
    assert adapter.merge_sites(a, ["uk", "ch"])["site"].tolist() == ["ch"]


def test_adapter_products_only_full_holdings(fake, monkeypatch):
    monkeypatch.setattr(adapter, "SITES", ("ch", "de"))
    df = adapter.products()
    assert validate_products(df) == []
    assert CH_IDX not in set(df["isin"]) and {CH_ETF, IE_SWAP, IE_ETF} <= set(df["isin"])
    assert set(adapter.all_products()["holdings_scope"]) == {"full", "top10"}


def test_adapter_skips_a_failing_site(fake, monkeypatch):
    def boom(site, max_lookups=None):
        raise RuntimeError("down")

    monkeypatch.setattr(ishares, "products", boom)
    assert adapter.products(["ch"]).empty


def test_holdings_swap_fund(fake, tmp_path):
    product = {"site": "de", "portfolio_id": "IS-EXSW", "share_class_id": "900003", "isin": IE_SWAP, "replication": "swap"}
    got = adapter.holdings(product, FetchContext(cache_dir=str(tmp_path)))
    h = got.holdings
    assert validate_holdings(h) == [] and list(h.columns) == HOLDING_COLUMNS + ["sector"]
    assert got.as_of == "2026-11-02"
    assert "portfolioId=900003" in got.url and "component=holdings%2CkeyFundFacts" in got.url
    # Weights exactly as reported, never rescaled.
    assert h["weight_pct"].tolist() == [100.01, 60.5, -60.5, -0.02, 0.01]
    assert h["isin"].isna().tolist() == [True, False, False, False, True]
    # The basket long and short cancel out: the exposure is the index line.
    assert h.loc[h["isin"].notna(), "weight_pct"].sum() == pytest.approx(-0.02)
    assert h["asset_class"].tolist() == ["equity"] * 4 + ["cash"]
    assert any("Swap-based" in n for n in got.notes)
    cached = list(tmp_path.glob("de_900003_*.json"))
    assert len(cached) == 1 and "content" not in json.loads(cached[0].read_text())


def test_holdings_bond_identifiers(fake, tmp_path):
    got = adapter.holdings({"site": "ch", "portfolio_id": "IS-EXCH", "share_class_id": "900001"},
                           FetchContext(cache_dir=str(tmp_path)))
    h = got.holdings
    assert validate_holdings(h) == []
    assert h["isin"].tolist() == ["XS10000000C6", None, None]  # bad check digit dropped
    assert h["cusip"].tolist()[2] is not None and h["sedol"].tolist() == ["B000001", None, None]
    assert h["weight_pct"].sum() == pytest.approx(100.0)
    assert any("sampled" in n for n in got.notes)


def test_adapter_top10_fund_is_a_typed_answer_not_ten_lines(fake, tmp_path):
    with pytest.raises(ishares.NoFullHoldings) as e:
        adapter.holdings({"site": "ch", "portfolio_id": "IS-EXIX", "share_class_id": "900002"},
                         FetchContext(cache_dir=str(tmp_path)))
    assert "ten largest" in e.value.plain
    # The answer is kept, so a rerun the same day does not ask again.
    assert len(list(tmp_path.glob("ch_900002_*.json"))) == 1


def test_pseudo_cusip_built_from_the_sedol_is_dropped():
    df = pd.DataFrame({"isin": ["JP3854600008", None], "cusip": ["S64351455", "91282CMM0"],
                       "sedol": ["6435145", "B000001"], "issueName": ["A", "B"], "holdingPercent": [60.0, 40.0]})
    out = adapter.to_holding_columns(df)
    assert out["cusip"].tolist() == [None, "91282CMM0"] and out["sedol"].tolist() == ["6435145", "B000001"]


def test_products_australia_link_from_the_sitemap(fake):
    assert ishares.products("au")["product_page_url"].tolist() == [
        "https://www.blackrock.com/au/products/900030/example-australia-etf"]


def test_merge_sites_takes_a_link_that_opens():
    rows = pd.DataFrame([
        {"site": "latam", "portfolio_id": "1", "fund_id": "F", "isin": IE_ETF, "holdings_scope": "full",
         "product_page_url": "https://www.blackrock.com/latam/x"},
        {"site": "uk", "portfolio_id": "1", "fund_id": "F", "isin": IE_ETF, "holdings_scope": "top10",
         "product_page_url": "https://www.ishares.com/uk/x"},
        {"site": "latam", "portfolio_id": "2", "fund_id": None, "isin": IE_SWAP, "holdings_scope": "full",
         "product_page_url": "https://www.blackrock.com/latam/y"},
    ])
    m = adapter.merge_sites(rows, ["latam", "uk"]).set_index("isin")
    assert m.loc[IE_ETF, "site"] == "latam" and m.loc[IE_ETF, "product_page_url"] == "https://www.ishares.com/uk/x"
    # No other site lists it: no link rather than one that answers 403.
    assert m.loc[IE_SWAP, "product_page_url"] is None


def test_holdings_top10_from_module(fake):
    got = ishares.holdings("900002", "ch", top10=True)
    assert got.scope == "top10" and got.as_of == "2026-09-30"
    assert got.holdings["issueName"].tolist()[0].startswith("SWITZERLAND")
    assert got.holdings["holdingPercent"].sum() == pytest.approx(2.2221)
    assert got.holdings["isin"].isna().all()
    assert validate_holdings(adapter.to_holding_columns(got.holdings)) == []


def test_holdings_not_cached_when_parsing_fails(monkeypatch, tmp_path):
    class Bad:
        content = b"{}"

        def json(self):
            return {"componentsByNameMap": {"holdings": {"containersByNameMap": {"documents": {}}}}}

    monkeypatch.setattr(ishares, "_get", lambda url, params: Bad())
    with pytest.raises(KeyError):
        ishares.holdings("1", "ch", cache_dir=tmp_path)
    assert not list(tmp_path.iterdir())


def test_replication_from_key_facts():
    f = ishares.replication_from_key_facts
    assert f({"productStructure": "Synthetic"}) == "swap"
    assert f({"productStructure": "Physical", "fundMethodologyTypeCode": "Optimised"}) == "sampled"
    assert f({"productStructure": "Physical", "fundMethodologyTypeCode": "Replicated"}) == "physical"
    assert f({}) is None


def test_adapter_is_registered():
    a = discover()["ishares"]
    assert a.frequency == "daily" and a.credit and a.licence and "Switzerland" in a.regions


@pytest.mark.network
def test_live_ishares_switzerland_smi():
    p = adapter.products(["ch"])
    assert validate_products(p) == []
    row = p.set_index("isin").loc["CH0008899764"].to_dict() | {"isin": "CH0008899764"}
    assert row["domicile"] == "CH" and row["site"] == "ch"
    got = adapter.holdings(row, FetchContext())
    assert validate_holdings(got.holdings) == []
    assert got.holdings["weight_pct"].sum() == pytest.approx(100, abs=1)
    assert got.holdings.loc[got.holdings["isin"].notna(), "weight_pct"].sum() > 95
