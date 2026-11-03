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


def test_match_directory_only_full_physical_of_blocked_issuers():
    directory = pd.DataFrame(
        [
            {"isin": "LU0274221281", "fund_name": "Example SMI ETF", "issuer": "UBS Fund Management (Switzerland) AG",
             "index": "SMI", "replication": "Full replication"},
            {"isin": "IE00B3YCGJ38", "fund_name": "Example swap ETF", "issuer": "Invesco Investment Management Ltd",
             "index": "S&P 500", "replication": "Swap"},
            {"isin": "IE00B5BMR087", "fund_name": "Peer itself", "issuer": "UBS AG", "index": "S&P 500",
             "replication": "Full replication"},
            {"isin": "IE00BKM4GZ66", "fund_name": "Example EM ETF", "issuer": "UBS AG", "index": "MSCI EM IMI",
             "replication": "Full replication"},
            {"isin": "CH0017142719", "fund_name": "Already curated", "issuer": "UBS AG", "index": "SMI",
             "replication": "Full replication"},
            {"isin": "LU0446734104", "fund_name": "Example sampled ETF", "issuer": "UBS AG", "index": "MSCI EMU",
             "replication": "Optimised sampling"},
            # Not a blocked issuer: it has (or may have) a direct source, so no stand-in.
            {"isin": "IE00B3XXRP09", "fund_name": "Vanguard S&P 500 UCITS ETF", "issuer": "Vanguard Group (Ireland)",
             "index": "S&P 500", "replication": "Full replication"},
            {"isin": "IE00BFZXGZ54", "fund_name": "Example Nasdaq-100", "issuer": "Swiss Life Asset Managers",
             "index": "Nasdaq-100", "replication": "Full replication"},
            {"isin": "IE00B53SZB19", "fund_name": "Covered elsewhere", "issuer": "Pictet Asset Management",
             "index": "Nasdaq-100", "replication": "Full replication"},
        ]
    )
    out = ip.match_directory(directory, covered={"IE00B53SZB19"})
    # IE00BFZXGZ54 is a curated target, IE00B53SZB19 is covered by a direct adapter.
    assert out["isin"].tolist() == ["LU0274221281"]
    assert out["index_key"].iloc[0] == "SMI"
    assert out["replication_method"].iloc[0] == ip.FULL
    assert out["fund_issuer"].iloc[0] == "UBS Fund Management (Switzerland) AG"
    assert validate_products(out) == []


def test_blocked_issuers():
    assert ip.blocked_issuer("UBS Fund Management (Luxembourg) S.A.") == "UBS"
    assert ip.blocked_issuer("WisdomTree Management Ltd") == "WisdomTree"
    assert ip.blocked_issuer("Swiss Life Asset Management AG") == "Swiss Life"
    assert ip.blocked_issuer("Invesco Markets plc", "IE00B60SX394") == "Invesco EMEA"
    assert ip.blocked_issuer("Invesco Capital Management LLC", "US46090E1038") is None  # US: N-PORT
    assert ip.blocked_issuer("Vanguard Group (Ireland) Limited") is None
    assert ip.blocked_issuer(None) is None


def test_stand_in_is_local_only_and_listed_last():
    assert ip.ADAPTER.redistribution == "local_only"
    assert ip.ADAPTER.stand_in is True


def test_swiss_peer_pages_use_the_working_path():
    for key in ("SMI", "SLI"):
        page = ip.INDICES[key].peers[0].page
        assert "/ch/individual/en/" in page and "/ch/individuals/" not in page
    assert ip._page_url("/ch/individuals/en/products/261154/x") == "https://www.ishares.com/ch/individual/en/products/261154/x"
    assert ip._page_url("/uk/individual/en/products/253743/x") == "https://www.ishares.com/uk/individual/en/products/253743/x"


def test_every_peer_records_its_replication_evidence():
    for idx in ip.INDICES.values():
        for peer in idx.peers:
            assert peer.replication_evidence and "@@" not in peer.replication_evidence, peer.isin


def test_share_classes_of_one_fund_share_a_portfolio_id():
    df = ip.products().set_index("isin")
    assert df.loc["IE0032077012", "portfolio_id"] == df.loc["IE00BFZXGZ54", "portfolio_id"] == "NDX:invesco-eqqq"
    assert "IE0032077012" in df.loc["IE00BFZXGZ54", "replication_evidence"]


def test_peer_replication_from_key_facts():
    assert ip.peer_replication({"productStructure": "Physical", "fundMethodologyTypeCode": "Replicated"}) == ip.FULL
    assert ip.peer_replication({"productStructure": "Physical", "fundMethodologyTypeCode": "Physical Replication"}) == ip.FULL
    assert ip.peer_replication({"productStructure": "Physical", "fundMethodologyTypeCode": "Optimised"}) == "physical-sampled"
    assert ip.peer_replication({"productStructure": "Synthetic"}) == "swap"
    assert ip.peer_replication({}) is None


def test_sampled_peer_is_refused(monkeypatch, tmp_path):
    screener = json.loads((FIX / "ishares_ch_screener.json").read_text())
    payload = json.loads((FIX / "ishares_ch_holdings.json").read_text())
    payload["componentsByNameMap"]["keyFundFacts"] = {"containersByNameMap": {"keyFundFacts": {"dataPointsByNameMap": {
        "productStructure": {"value": "Physical"}, "fundMethodologyTypeCode": {"value": "Optimised"}}}}}
    monkeypatch.setattr(ip.http, "get", lambda url, params=None, **kw: FakeResponse(
        screener if "product-screener" in url else payload))
    with pytest.raises(LookupError, match="not a full physical peer"):
        ip.holdings(smi_product(), FetchContext(cache_dir=str(tmp_path)))


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
    assert note.startswith(
        "stand-in: holdings of iShares SMI ETF (CH) (CH0008899764), a physically replicating ETF on the "
        "Swiss Market Index (SMI), "
    )
    assert "positions as of 2026-10-30" in note
    assert "used for UBS ETF (CH) - SMI (CHF) A-dis (CH0017142719)" in note
    assert "not the fund's own holdings" in note
    assert any(n.startswith("peer replication: ") and "iShares" in n for n in got.notes)
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


def test_normalise_spdr_currency_and_futures_lines():
    raw = pd.DataFrame(
        [
            {"isin": None, "cusip": None, "sedol": "Unassigned", "name": "U.S. Dollar", "ticker": None,
             "weight_pct": None, "currency": "USD", "is_debt": False},
            {"isin": None, "cusip": None, "sedol": None, "name": "British Pounds", "ticker": None,
             "weight_pct": None, "currency": "GBP", "is_debt": False},
            {"isin": None, "cusip": None, "sedol": None, "name": "EMINI S&P DEC26", "ticker": None,
             "weight_pct": None, "currency": "USD", "is_debt": False},
            {"isin": "US0378331005", "cusip": None, "sedol": None, "name": "Apple Inc.", "ticker": "AAPL",
             "weight_pct": 7.6, "currency": "USD", "is_debt": False},
        ]
    )
    assert ip.normalise_spdr(raw)["asset_class"].tolist() == ["cash", "cash", "derivative (futures)", "equity"]


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
    assert "unknown date" in note and idx.name in note and t.isin in note and idx.peers[0].isin in note


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
    # The source link shown for the rows is a working page.
    from openbasket import http

    page = smi_product()["product_page_url"]
    assert http.get(page, timeout=60).status_code == 200
