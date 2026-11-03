"""Stand-in holdings for Swiss-bank physical index ETFs, and the swiss pipeline's publishing guards."""

from pathlib import Path

import pandas as pd
import pytest

from openbasket.sources import swiss_registry
from openbasket.sources.issuers import (
    HOLDING_COLUMNS,
    PRODUCT_COLUMNS,
    FetchContext,
    IssuerHoldings,
    discover,
    validate_holdings,
)
from openbasket.sources.issuers import swiss_standin as st

FIX = Path(__file__).parent / "fixtures" / "swiss"


@pytest.mark.parametrize(
    "underlying, key",
    [
        ("SMI Total Return", "smi"),
        ("SMI (Total Return) ", "smi"),
        ("SMI Swiss Market Index", "smi swiss market"),
        ("S&P 500 Index", "s&p 500"),
        ("MSCI Switzerland 100% hedged to EUR Index (Net Return)", "msci switzerland"),
        ("EURO STOXX 50 NR", "euro stoxx 50"),
        ("FTSE 100", "ftse 100"),
    ],
)
def test_canon_index(underlying, key):
    assert st.canon_index(underlying) == key


def test_match_index_needs_the_index_in_the_fund_name():
    assert st.match_index("SMI Total Return", "UBS SMI ETF CHF dis").key == "smi"
    assert st.match_index("S&P 500 Index", "UBS Core S&P 500 UCITS ETF USD dis").key == "sp500"
    assert st.match_index("MSCI World Index", "Pictet AI Enhanced World Equity UCITS ETF USD Acc") is None
    assert st.match_index("MSCI Emerging Markets Index", "UBS MSCI Emerging Markets Socially Responsible UCITS ETF") is None
    assert st.match_index("SMI Equal Weighted TR", "UBS SMI Equal Weight ETF CHF acc") is None


def test_select_targets_skips_swaps_peers_and_non_etfs():
    six, _ = swiss_registry.parse_six((FIX / "six_funds.json").read_bytes())
    t = st.select_targets(six)
    assert list(t["isin"]) == ["CH0017142719", "IE00B7K93397"]
    assert list(t.columns) == PRODUCT_COLUMNS + st.EXTRA_COLUMNS
    assert (t["bank"] == "ubs").all() and (t["redistribution"] == "local_only").all()


def test_holdings_are_the_peer_file_labelled(monkeypatch):
    peer = pd.DataFrame(
        {
            "isin": ["CH0012005267", "-", "CH0038863350"],
            "issueName": ["NOVARTIS AG", "CHF CASH", "NESTLE SA"],
            "holdingPercent": [16.17161, 0.3, 14.03692],
            "assetClass": ["Equity", "Cash", "Equity"],
            "location": ["Switzerland", "Switzerland", "Switzerland"],
            "ticker": ["NOVN", "CHF", "NESN"],
            "cusip": ["-", "-", "-"],
        }
    )
    calls = []

    def fake(pid, site, cache_dir=None):
        calls.append((pid, site))
        return IssuerHoldings("2026-11-02", peer, "https://www.blackrock.com/varnish-api/x")

    monkeypatch.setattr(st.ishares, "holdings", fake)
    product = {"isin": "CH0017142719", "fund_name": "UBS SMI ETF CHF dis", "index_key": "smi", "peer_isin": "CH0008899764",
               "peer_name": "iShares SMI ETF (CH)", "peer_site": "ch", "peer_portfolio_id": "261154",
               "peer_page_url": "https://www.ishares.com/ch/x", "target_replication": "physical (SIX; full or sampled not stated)"}
    got = st.holdings(product, FetchContext(cache_dir=None))
    assert calls == [("261154", "ch")]
    assert got.as_of == "2026-11-02" and not validate_holdings(got.holdings)
    assert list(got.holdings.columns) == HOLDING_COLUMNS
    assert got.holdings["weight_pct"].tolist() == [16.17161, 0.3, 14.03692]  # never rescaled
    assert pd.isna(got.holdings["isin"].tolist()[1])
    assert got.notes[0].startswith("stand-in:") and "not the fund's own holdings" in got.notes[0]


def test_adapters_registered_with_flags():
    reg = discover()
    for name in ("swiss_standin", "swiss_vontobel", "swiss_migros"):
        assert name in reg
    assert st.ENABLED_BY_DEFAULT is True and st.REDISTRIBUTION == "local_only"


# -- pipeline guards ----------------------------------------------------------


def test_ci_reads_only_public_sources_and_no_holdings(monkeypatch):
    from openbasket.pipelines.swiss import nodes

    seen = {}
    monkeypatch.setattr(nodes.swiss_registry, "build_registry", lambda cache, sources: seen.setdefault("sources", list(sources)))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    nodes.build_registry({"cache_dir": None, "registry_sources": ["gleif", "firds", "six", "finma"]})
    assert seen["sources"] == ["gleif", "firds"]
    holdings, summary = nodes.sample_holdings({"sample": ["CH0017142719"]})
    assert holdings.empty and summary.empty


def test_public_registry_drops_local_only_rows():
    from openbasket.pipelines.swiss import nodes

    df = pd.DataFrame({"isin": ["A", "B"], "redistribution": ["public", "local_only"]})
    assert nodes.public_registry(df)["isin"].tolist() == ["A"]


# -- network ------------------------------------------------------------------


@pytest.mark.network
def test_network_smi_standin(tmp_path):
    products = st.products(tmp_path)
    row = products[products["isin"] == "CH0017142719"].iloc[0].to_dict()
    got = st.holdings(row, FetchContext(cache_dir=str(tmp_path / "ishares")))
    df = got.holdings
    assert not validate_holdings(df)
    assert 99 < df["weight_pct"].sum() < 101 and df["isin"].notna().sum() >= 18
