import pytest

from openbasket.lookthrough import FundRef, Holdings, Line, choose, look_through


class FakeProviders:
    """Funds keyed by series id; lines whose ``ticker`` names a series are funds."""

    def __init__(self, funds, isin_to_fund, securities=None):
        self.funds = funds
        self.isin_to_fund = isin_to_fund
        self.securities = securities or {}

    def resolve(self, isin):
        sid = self.isin_to_fund.get(isin)
        return FundRef(sid, None, sid, "test") if sid else None

    def resolve_line(self, line):
        return FundRef(line.ticker, None, line.ticker, "test") if line.ticker in self.funds else None

    def holdings(self, ref):
        return self.funds.get(ref.series_id, [])

    def security(self, isin):
        return self.securities.get(isin)


def H(source, lines, as_of="2026-07-31"):
    return Holdings(source, as_of, lines)


def test_direct_holdings_are_not_rescaled():
    p = FakeProviders(
        {"F": [H("nport", [Line(60, "US0000000001"), Line(30, "US0000000002"), Line(5, None, name="cash")])]},
        {"US1111111111": "F"},
    )
    r = look_through("US1111111111", p)
    assert r.summary["resolved_pct"] == pytest.approx(90)
    assert r.summary["unresolved_pct"] == pytest.approx(10)
    assert r.summary["unidentified_lines_pct"] == pytest.approx(5)
    assert [x["weight_pct"] for x in r.leaves] == [60, 30]


def test_fund_of_funds_multiplies_through():
    p = FakeProviders(
        {
            "TOP": [H("nport", [Line(50, "US0000000010", ticker="SUB"), Line(50, "US0000000001")])],
            "SUB": [H("nport", [Line(40, "US0000000001"), Line(60, "US0000000002")])],
        },
        {"US1111111111": "TOP"},
    )
    r = look_through("US1111111111", p)
    by_isin = {}
    for x in r.leaves:
        by_isin[x["constituent_isin"]] = by_isin.get(x["constituent_isin"], 0) + x["weight_pct"]
    assert by_isin == {"US0000000001": pytest.approx(70), "US0000000002": pytest.approx(30)}
    assert {x["depth"] for x in r.leaves} == {0, 1}


def test_cycle_is_not_expanded():
    p = FakeProviders(
        {
            "A": [H("nport", [Line(100, "US0000000020", ticker="B")])],
            "B": [H("nport", [Line(100, "US0000000030", ticker="A")])],
        },
        {"US1111111111": "A"},
    )
    r = look_through("US1111111111", p)
    assert len(r.leaves) == 1
    assert r.leaves[0]["note"] == "fund cycle, not expanded"
    assert r.leaves[0]["asset_class"] == "fund"


def test_over_100_reported_gives_negative_unresolved():
    p = FakeProviders({"F": [H("nport", [Line(70, "US0000000001"), Line(32, "US0000000002")])]}, {"X": "F"})
    r = look_through("X", p)
    assert r.summary["unresolved_pct"] == pytest.approx(-2)


def test_unmatched_isin_is_fully_unresolved():
    r = look_through("US9999999999", FakeProviders({}, {}))
    assert r.summary["matched"] is False
    assert r.summary["unresolved_pct"] == 100
    assert r.leaves == []


def test_direct_security_is_its_own_constituent():
    sec = H("security_master", [Line(100, "US0378331005", name="Apple Inc", asset_class="equity")], as_of=None)
    r = look_through("US0378331005", FakeProviders({}, {}, {"US0378331005": sec}))
    assert r.summary["resolved_pct"] == 100
    assert r.leaves[0]["constituent_isin"] == "US0378331005"


def test_choose_prefers_order_then_coverage():
    a = H("tickerinside", [Line(95, "US0000000001"), Line(5, None)])
    b = H("nport", [Line(100, "US0000000001")])
    assert choose([a, b], 90).source == "tickerinside"
    assert choose([a, b], 99).source == "nport"
