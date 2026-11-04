"""Stand-ins: a US fund on the same index for a fund with no public holdings."""

import pandas as pd
import pytest

from openbasket import stand_in
from openbasket.lookthrough import STATUSES, Holdings, Line
from openbasket.pipelines.universe.nodes import expand_fund


@pytest.mark.parametrize("name,key", [
    ("iShares Core S&P 500 UCITS ETF USD (Acc)", "s&p 500"),
    ("iShares Core S&P 500 ETF", "s&p 500"),
    ("Vanguard S&P500 UCITS ETF", "s&p 500"),
    ("Xtrackers MSCI World Swap UCITS ETF 1C", "msci world"),
    ("Amundi MSCI EM UCITS ETF - Acc", "msci emerging markets"),
    ("iShares MSCI Eurozone ETF", "msci emu"),
    ("Lyxor Core MSCI EMU (DR) UCITS ETF - Dist", "msci emu"),
    ("Invesco EQQQ Nasdaq-100 UCITS ETF", "eqqq nasdaq 100"),
    ("iShares ESG Aware MSCI USA ETF", "esg aware msci usa"),
    ("iShares MSCI World SRI UCITS ETF", "msci world sri"),
    ("iShares S&P 500 Equal Weight UCITS ETF", "s&p 500 equal weight"),
    ("iShares MSCI Japan GBP Hedged UCITS ETF (Acc)", "msci japan"),
])
def test_index_key(name, key):
    assert stand_in.index_key(name) == key


@pytest.mark.parametrize("name", [
    "ProShares Ultra S&P500", "Direxion Daily S&P 500 Bull 3X Shares", "Global X S&P 500 Covered Call ETF",
    "FT Vest U.S. Equity Buffer ETF - S&P 500", "Russell Investments Global Equity Fund", "iShares MSCI ETF",
    "Vanguard 500 Index Fund", "Fidelity Contrafund", None,
])
def test_no_key_for_non_trackers_and_names_without_an_index(name):
    assert stand_in.index_key(name) is None


FUND_MASTER = pd.DataFrame([
    {"series_id": "S000004310", "series_name": "iShares Core S&P 500 ETF", "net_assets": 8.8e11,
     "report_date": pd.Timestamp("2026-06-30"), "has_nport": True},
    {"series_id": "S000000002", "series_name": "Schwab S&P 500 Index Fund", "net_assets": 1.0e11,
     "report_date": pd.Timestamp("2026-06-30"), "has_nport": True},
    {"series_id": "S000000003", "series_name": "iShares MSCI World ETF", "net_assets": 0.0,
     "report_date": pd.Timestamp("2026-06-30"), "has_nport": True},
    {"series_id": "S000000004", "series_name": "ProShares Ultra S&P500", "net_assets": 9.9e12,
     "report_date": pd.Timestamp("2026-06-30"), "has_nport": True},
])

UNIVERSE = pd.DataFrame([
    {"id": "IE00B5BMR087", "name": "iShares Core S&P 500 UCITS ETF", "universe_source": "ishares", "series_id": None},
    {"id": "IE00BFMXXD54", "name": "Vanguard S&P 500 UCITS ETF", "universe_source": "fund_registry", "series_id": None},
    {"id": "US4642872000", "name": "iShares Core S&P 500 ETF", "universe_source": "ishares", "series_id": None},
    {"id": "IE00B4L5Y983", "name": "iShares Core MSCI World UCITS ETF", "universe_source": "ishares", "series_id": None},
    {"id": "S000004310", "name": "iShares Core S&P 500 ETF", "universe_source": "nport", "series_id": "S000004310"},
    {"id": "IE00BYX2JD69", "name": "iShares MSCI World SRI UCITS ETF", "universe_source": "ishares", "series_id": None},
])


def test_largest_us_fund_with_net_assets_is_the_peer():
    p = stand_in.peers(FUND_MASTER).set_index("index_key")
    assert p.loc["s&p 500", "series_id"] == "S000004310"
    assert "msci world" not in p.index  # no net assets
    assert len(p) == 1  # the leveraged fund has no key


def test_only_non_us_listed_funds_get_stand_ins():
    st = stand_in.stand_ins(UNIVERSE, FUND_MASTER)
    assert dict(zip(st["fund_id"], st["series_id"])) == {"IE00B5BMR087": "S000004310", "IE00BFMXXD54": "S000004310"}


def test_ref_names_the_us_fund_index_and_date():
    refs = stand_in.refs(UNIVERSE, FUND_MASTER)
    r = refs["IE00B5BMR087"]
    assert r.series_id == "S000004310" and r.via == "stand_in" and r.name == "iShares Core S&P 500 ETF"
    assert "S&P 500" in r.note and "2026-06-30" in r.note and "not this fund's own holdings" in r.note
    assert "stand_in" in STATUSES


class _Providers:
    """Own holdings only for ``own``; the US series has two lines."""

    def __init__(self, own: bool):
        self.own = own

    def holdings(self, ref):
        if ref.series_id == "S000004310":
            return [Holdings("nport", "2026-06-30", [Line(60.0, isin="US0378331005", name="Apple"),
                                                     Line(39.5, isin="US5949181045", name="Microsoft")])]
        if self.own:
            return [Holdings("cnmv_spain", "2026-09-30", [Line(99.0, isin="US0378331005", name="Apple")])]
        return []

    def resolve_line(self, line):
        return None

    def local_only_issuer(self, ref):
        return "iShares"


FUND = {"id": "IE00B5BMR087", "isin": "IE00B5BMR087", "isins": ["IE00B5BMR087"], "issuer_id": "ishares-uk:1",
        "universe_source": "ishares", "name": "iShares Core S&P 500 UCITS ETF", "list_source": "issuer_list"}
PARAMS = {"max_depth": 4, "min_resolved_pct": 90.0}


def test_stand_in_is_used_when_own_holdings_are_missing():
    ref = stand_in.refs(UNIVERSE, FUND_MASTER)["IE00B5BMR087"]
    rec, rows = expand_fund(FUND, _Providers(own=False), PARAMS, ref)
    assert rec["status"] == "stand_in" and rec["matched_via"] == "stand_in" and rec["source"] == "nport"
    assert rec["name"] == "iShares Core S&P 500 UCITS ETF" and rec["as_of"] == "2026-06-30"
    assert "may not republish" in rec["note"] and "S000004310" in rec["note"]
    held = rows.dropna(subset=["constituent_isin"])
    assert held["weight_pct"].sum() == pytest.approx(99.5)  # not rescaled
    assert set(held["via_fund"]) == {"iShares Core S&P 500 ETF"}
    assert rows.loc[rows["constituent_isin"].isna(), "weight_pct"].iloc[0] == pytest.approx(0.5)


def test_own_holdings_win_over_a_stand_in():
    ref = stand_in.refs(UNIVERSE, FUND_MASTER)["IE00B5BMR087"]
    rec, _ = expand_fund(FUND, _Providers(own=True), PARAMS, ref)
    assert rec["source"] == "cnmv_spain" and rec["status"] != "stand_in"
