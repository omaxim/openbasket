import json
from pathlib import Path

import pandas as pd
import pytest

from openbasket.identifiers import is_valid_isin
from openbasket.sources.issuers import FetchContext, discover, validate_holdings, validate_products
from openbasket.sources.issuers import amundi

FIXTURES = Path(__file__).parent / "fixtures" / "amundi"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def fake_api(monkeypatch):
    """Route amundi._post to fixtures by host and path; record every call."""
    calls = []
    routes = {
        ("www.amundietf.de", amundi.PRODUCTS_PATH, "list"): _load("list_deu.json"),
        ("www.amundietf.fr", amundi.PRODUCTS_PATH, "list"): _load("list_fra.json"),
        ("www.amundietf.de", amundi.PAGE_LINKS_PATH, "links"): _load("page_links_deu.json"),
        ("www.amundietf.de", amundi.PRODUCTS_PATH, "LU1437024729"): _load("composition_physical.json"),
        ("www.amundietf.de", amundi.PRODUCTS_PATH, "LU1681044993"): _load("composition_swap.json"),
    }

    def post(url, body, timeout=120):
        host, path = url.removeprefix("https://").split("/", 1)
        path = "/" + path
        kind = "links" if "pagesIds" in body else (body["productIds"][0] if body.get("productIds") else "list")
        calls.append((host, path, kind, body))
        assert body["context"]["userProfileName"] == "RETAIL"
        key = (host, path, kind)
        if key not in routes:
            return {} if kind == "links" else {"products": []}
        return routes[key]

    monkeypatch.setattr(amundi, "_post", post)
    return calls


def test_registered():
    adapter = discover()["amundi"]
    assert adapter.frequency == "daily"
    assert "Amundi" in adapter.credit and "permission" in adapter.licence


def test_parse_products():
    df = amundi.parse_products(_load("list_deu.json"), "de", _load("page_links_deu.json"))
    assert validate_products(df) == []
    assert df["isin"].tolist() == ["IE000BI8OT95", "LU1437024729", "LU1681043599", "LU1681044993"]  # invalid ISIN dropped
    assert df["replication"].tolist() == ["physical", "sampled", "swap", "swap"]
    assert df["domicile"].tolist() == ["IE", "LU", "LU", "LU"]
    assert df.loc[2, "portfolio_id"] == df.loc[3, "portfolio_id"] == "9000003"
    assert df.loc[0, "ticker"] == "EXW1 GY"  # main listing for the site's country
    assert df.loc[1, "ticker"] == "EXAG"  # no German listing: the mnemonic
    assert df.loc[0, "product_page_url"].startswith("https://www.amundietf.de/de/privatanleger/products/")
    assert df.loc[1, "product_page_url"] == "https://www.amundietf.de/de/privatanleger"  # no link: site home
    assert (df["holdings_url"] == "https://www.amundietf.de/mapi/ProductAPI/getProductsData").all()
    assert df.loc[3, "currency"] == "CHF" and df.loc[2, "range"] == "LYXOR"


def test_products_union_keeps_first_site(fake_api):
    df = amundi.products(["de", "fr"])
    assert validate_products(df) == []
    assert len(df) == 5
    assert df.set_index("isin").loc["LU1681043599", "site"] == "de"
    fr_only = df.set_index("isin").loc["FR0011871128"]
    assert fr_only["site"] == "fr" and fr_only["domicile"] == "FR" and fr_only["replication"] == "swap"
    # page links are only asked for ISINs a site adds
    links = [c for c in fake_api if c[2] == "links"]
    assert [c[3]["pagesIds"] for c in links] == [
        ["IE000BI8OT95", "LU1437024729", "LU1681043599", "LU1681044993"],
        ["FR0011871128"],
    ]


def test_parse_holdings_physical():
    as_of, df, notes = amundi.parse_holdings(_load("composition_physical.json"), "LU1437024729")
    assert as_of == "2026-10-30"
    assert notes == []
    assert validate_holdings(df) == []
    assert df["weight_pct"].tolist() == pytest.approx([50.0, 25.0, 20.0, 4.0, 1.0])  # fractions x 100
    assert df["asset_class"].tolist() == ["equity", "debt", "debt", "derivative (futures)", "cash"]
    assert df.loc[0, "ticker"] == "AAPL UW" and pd.isna(df.loc[0, "cusip"])
    assert df.loc[1, "cusip"] == "01F0306B9" and pd.isna(df.loc[1, "ticker"])  # bbg is the CUSIP
    assert not is_valid_isin("FR0000000001") and pd.isna(df.loc[3, "isin"])  # bad check digit -> None
    assert pd.isna(df.loc[4, "isin"]) and df.loc[4, "issuer_type"] == "CASH"


def test_holdings_swap_note_and_cache(fake_api, tmp_path):
    product = amundi.parse_products(_load("list_deu.json"), "de").set_index("isin").loc["LU1681044993"].to_dict()
    product["isin"] = "LU1681044993"
    ctx = FetchContext(cache_dir=str(tmp_path))
    got = amundi.holdings(product, ctx)
    assert got.as_of == "2026-10-30"  # latest of the position dates
    assert any("substitute basket" in n for n in got.notes)
    assert any("2 dates" in n for n in got.notes)
    assert got.url == "https://www.amundietf.de/mapi/ProductAPI/getProductsData"
    assert got.holdings["weight_pct"].sum() == pytest.approx(95.9)  # never rescaled
    assert validate_holdings(got.holdings) == []
    assert len(list(tmp_path.glob("amundi_de_LU1681044993_*.json"))) == 1
    n = len(fake_api)
    again = amundi.holdings(product, ctx)
    assert len(fake_api) == n  # served from the cache
    assert again.holdings.equals(got.holdings)


def test_unparseable_response_is_not_cached(fake_api, tmp_path):
    product = {"isin": "IE000BI8OT95", "site": "de", "replication": "physical"}
    with pytest.raises(LookupError):  # the fake API has no composition for it
        amundi.holdings(product, FetchContext(cache_dir=str(tmp_path)))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.network
def test_network_one_fund(tmp_path):
    product = {"isin": "FR0007052782", "site": "de", "replication": "physical"}  # Amundi CAC 40 UCITS ETF Dist
    got = amundi.holdings(product, FetchContext(cache_dir=str(tmp_path)))
    df = got.holdings
    assert validate_holdings(df) == []
    assert len(df) >= 40
    assert got.as_of and got.as_of[:4] >= "2026"
    assert 95 < df["weight_pct"].sum() < 105
    assert df["isin"].notna().mean() > 0.9
