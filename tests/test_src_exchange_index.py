"""Index composition proxies (sources/issuers/index_proxy.py)."""

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from openbasket.identifiers import is_valid_isin
from openbasket.sources.issuers import (
    HOLDING_COLUMNS,
    FetchContext,
    discover,
    validate_holdings,
    validate_products,
)
from openbasket.sources.issuers import index_proxy as ip

FIX = Path(__file__).parent / "fixtures" / "exchange-index"


class FakeResponse:
    def __init__(self, payload=None, content=b""):
        self._payload = payload
        self.content = content
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


@pytest.fixture
def fake_ishares(monkeypatch):
    """Serve the iShares screener and holdings fixtures; record requested URLs."""
    screener = json.loads((FIX / "ishares_ch_screener.json").read_text())
    holdings = json.loads((FIX / "ishares_ch_holdings.json").read_text())
    calls = []

    def get(url, params=None, **kw):
        calls.append((url, params))
        if "product-screener" in url:
            return FakeResponse(screener)
        if "get-product-data" in url:
            assert params["targetSite"] == "ishares-ch"
            assert params["portfolioId"] == "261154"
            return FakeResponse(holdings)
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(ip.http, "get", get)
    return calls


def product(isin="CH0017142719"):
    df = ip.products()
    return df[df["isin"] == isin].iloc[0].to_dict()


def smi_product():
    return product("CH0017142719")


# -- registry and products ---------------------------------------------------

def test_registered():
    assert discover()["index_proxy"] is ip.ADAPTER


def test_products_meet_contract():
    df = ip.products()
    assert validate_products(df) == []
    assert len(df) == len(ip.TARGETS)
    assert set(df["replication_method"]) == {ip.FULL}
    assert (df["kind"] == ip.KIND).all()


def test_mapping_is_consistent():
    for t in ip.TARGETS:
        assert is_valid_isin(t.isin)
        assert t.index in ip.INDICES
        assert t.replication == ip.FULL
        assert all(p.isin != t.isin for p in ip.INDICES[t.index].peers)
    for spec in ip.INDICES.values():
        assert spec.peers, spec.key
        assert all(is_valid_isin(p.isin) for p in spec.peers)


def test_products_leave_out_swap_and_sampled_funds():
    swap = ip.Target("IE00B3YCGJ38", "Example synthetic S&P 500 ETF", "Invesco", "SP500", "swap")
    sampled = ip.Target("CH0131872431", "Example sampled SPI ETF", "UBS", "SMI", "physical-sampled")
    df = ip.products([swap, sampled, ip.TARGETS[0]])
    assert df["isin"].tolist() == [ip.TARGETS[0].isin]


# -- replication and directory matching --------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Physical (Full replicated)", ip.FULL),
        ("Full replication", ip.FULL),
        ("Physisch vollständig", ip.FULL),
        ("Physical - Optimised", "physical-sampled"),
        ("Stratified sampling", "physical-sampled"),
        ("Synthetic (swap)", "swap"),
        ("Unfunded swap", "swap"),
        ("Physical", None),
        ("", None),
        (None, None),
    ],
)
def test_normalise_replication(text, expected):
    assert ip.normalise_replication(text) == expected


def test_index_for_is_exact():
    assert ip.index_for("SMI®").key == "SMI"
    assert ip.index_for("  Swiss  Market Index ").key == "SMI"
    assert ip.index_for("MSCI EMU").key == "MSCI_EMU"
    assert ip.index_for("MSCI EMU Small Cap") is None
    assert ip.index_for("S&P 500 Equal Weight") is None
    assert ip.index_for(None) is None


def test_match_directory_only_full_physical():
    directory = pd.DataFrame(
        [
            {"isin": "LU0274221281", "fund_name": "Example SMI ETF", "index": "SMI", "replication": "Full replication"},
            {"isin": "IE00B3YCGJ38", "fund_name": "Example swap ETF", "index": "S&P 500", "replication": "Swap"},
            {"isin": "IE00B5BMR087", "fund_name": "Peer itself", "index": "S&P 500", "replication": "Full replication"},
            {"isin": "IE00BKM4GZ66", "fund_name": "Example EM ETF", "index": "MSCI EM IMI", "replication": "Full replication"},
            {"isin": "CH0017142719", "fund_name": "Already curated", "index": "SMI", "replication": "Full replication"},
            {"isin": "LU0446734104", "fund_name": "Example sampled ETF", "index": "MSCI EMU", "replication": "Optimised sampling"},
        ]
    )
    out = ip.match_directory(directory)
    assert out["isin"].tolist() == ["LU0274221281"]
    assert out["index_key"].iloc[0] == "SMI"
    assert out["replication_method"].iloc[0] == ip.FULL
    assert validate_products(out) == []


# -- holdings ----------------------------------------------------------------

def test_holdings_from_peer(fake_ishares, tmp_path):
    got = ip.holdings(smi_product(), FetchContext(cache_dir=str(tmp_path)))
    df = got.holdings
    assert list(df.columns) == HOLDING_COLUMNS
    assert validate_holdings(df) == []
    assert got.as_of == "2026-10-30"
    # Weights are the peer's, never rescaled.
    assert df["weight_pct"].tolist() == pytest.approx([55.12345, 44.5, 0.37655, 0.0])
    assert df["isin"].tolist()[:2] == ["CH0012005267", "CH0038863350"]
    assert df["isin"].isna().tolist()[2:] == [True, True]
    assert df["asset_class"].tolist() == ["equity", "equity", "cash", "derivative (futures)"]
    assert df["currency"].iloc[0] == "CHF"
    assert "targetSite=ishares-ch" in got.url and "portfolioId=261154" in got.url
    note = got.notes[0]
    assert note.startswith("index composition proxy: ")
    assert "iShares SMI ETF (CH) (CH0008899764)" in note
    assert "positions as of 2026-10-30" in note
    assert "proxy for UBS ETF (CH) - SMI (CHF) A-dis (CH0017142719)" in note
    assert "not the fund's own holdings" in note
    assert any(n.startswith("credit: BlackRock (iShares)") for n in got.notes)
    assert any("not rescaled" in n for n in got.notes)


def test_holdings_are_cached_per_day(fake_ishares, tmp_path):
    ctx = FetchContext(cache_dir=str(tmp_path))
    ip.holdings(smi_product(), ctx)
    n = len(fake_ishares)
    ip.holdings(smi_product(), ctx)
    assert len(fake_ishares) == n == 2
    # The screener cache keeps only the fields used, not the issuer's list.
    cached = json.loads(next(tmp_path.glob("index_proxy_ishares_ch_screener_*.json")).read_text())
    assert cached == {"CH0008899764": {"portfolio_id": "261154", "fund_name": "iShares SMI ETF (CH)",
                                       "product_page_url": "https://www.ishares.com/ch/individual/en/products/261154/example"}}


def test_holdings_refuse_non_full_replication():
    swap = smi_product() | {"isin": "IE00B3YCGJ38", "replication_method": "swap"}
    with pytest.raises(ValueError, match="only used for physical-full"):
        ip.holdings(swap)


def test_holdings_fall_back_to_next_peer(monkeypatch):
    first, second = ip.INDICES["SP500"].peers
    frame = pd.DataFrame([{c: None for c in HOLDING_COLUMNS} | {"isin": "US0378331005", "weight_pct": 7.0}])

    def peer_holdings(peer, ctx=None):
        if peer is first:
            raise LookupError("not on site")
        return "2026-10-30", frame, "https://example.org/file.xlsx", None

    monkeypatch.setattr(ip, "peer_holdings", peer_holdings)
    got = ip.holdings(product("IE00B7K93397"))
    assert second.name in got.notes[0]
    assert any(n.startswith("credit: State Street") for n in got.notes)


def test_holdings_raise_when_no_peer_answers(monkeypatch):
    monkeypatch.setattr(ip, "peer_holdings", lambda peer, ctx=None: (_ for _ in ()).throw(LookupError("down")))
    with pytest.raises(LookupError, match="no peer answered"):
        ip.holdings(smi_product())


def test_normalise_spdr():
    raw = pd.DataFrame(
        [
            {"isin": "US0378331005", "cusip": None, "sedol": "2046251", "identifier": None, "name": "Example Inc",
             "ticker": "EXA", "weight_pct": 7.1, "currency": "USD", "is_debt": False},
            {"isin": None, "cusip": None, "sedol": None, "identifier": None, "name": "US DOLLAR CASH",
             "ticker": None, "weight_pct": 0.05, "currency": "USD", "is_debt": False},
        ]
    )
    df = ip.normalise_spdr(raw)
    assert list(df.columns) == HOLDING_COLUMNS
    assert df["asset_class"].tolist() == ["equity", "cash"]
    assert df["weight_pct"].tolist() == pytest.approx([7.1, 0.05])
    assert validate_holdings(df) == []


def test_invalid_identifiers_are_dropped():
    raw = pd.DataFrame({"isin": ["CH0012005268", "CH0012005267"], "issueName": ["BAD", "GOOD"],
                        "holdingPercent": [1.0, 2.0], "assetClass": ["Equity", "Equity"]})
    df = ip.normalise_ishares(raw)
    assert df["isin"].tolist() == [None, "CH0012005267"]


def test_compare():
    a = pd.DataFrame({"isin": ["CH0012005267", "CH0038863350", None], "weight_pct": [60.0, 39.5, 0.5]})
    b = pd.DataFrame({"isin": ["CH0012005267", "CH0038863350", "CH0244767585"], "weight_pct": [59.0, 40.0, 1.0]})
    c = ip.compare(a, b)
    assert c["overlap_pct"] == pytest.approx(59.0 + 39.5)
    assert c["max_abs_diff_pp"] == pytest.approx(1.0)
    assert (c["common_isins"], c["only_a"], c["only_b"]) == (2, 0, 1)
    assert c["isin_weight_a"] == pytest.approx(99.5)


def test_proxy_note_names_everything():
    t = ip.TARGETS[0]
    idx = ip.INDICES[t.index]
    note = ip.proxy_note(t, idx, idx.peers[0], None)
    assert "unknown date" in note and "SIX" in note and t.isin in note


# -- network -----------------------------------------------------------------

@pytest.mark.network
def test_network_ubs_smi_via_ishares_peer(tmp_path):
    got = ip.holdings(smi_product(), FetchContext(cache_dir=str(tmp_path)))
    df = got.holdings
    assert validate_holdings(df) == []
    assert got.as_of and got.as_of >= "2026-01-01"
    equity = df[df["asset_class"] == "equity"]
    assert 18 <= len(equity) <= 22  # the SMI has 20 constituents
    assert 99.0 <= df["weight_pct"].sum() <= 101.0
    assert equity["isin"].notna().all()
    assert "not the fund's own holdings" in got.notes[0]
