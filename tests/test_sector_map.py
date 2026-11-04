import pandas as pd

from openbasket.datasets import connection
from openbasket.pipelines.publish.nodes import _finish_breakdowns, _source_entries, _stream_constituents, fill_sectors
from openbasket.sources import financedatabase as findb, redistribution


def test_sector_map_keeps_one_sector_per_company_isin():
    equities = pd.DataFrame([
        {"isin": "US0378331005", "sector": "Information Technology"},  # Apple, two listings
        {"isin": "us0378331005", "sector": "Information Technology"},
        {"isin": "US5949181045", "sector": "Information Technology"},  # Microsoft, listed under two sectors
        {"isin": "US5949181045", "sector": "Communication Services"},
        {"isin": "US0378331006", "sector": "Financials"},              # bad check digit
        {"isin": "US02079K3059", "sector": "Search Engines"},          # not a GICS sector
        {"isin": None, "sector": "Energy"},
    ])
    out, stats = findb.sector_map(equities)
    assert out.to_dict("records") == [{"isin": "US0378331005", "sector": "Information Technology"}]
    assert stats == {"listings": 7, "isins": 1, "conflicting_isins": 1}


def _rel():
    return connection().from_df(pd.DataFrame([
        # fund_id, constituent_isin, name, weight_pct, asset_class, source, as_of, depth, via_fund, country, sector
        ("S1", "US0378331005", "Apple", 60.0, "equity", "nport", "2026-06-30", 0, None, "US", None),
        ("S1", "US5949181045", "Microsoft", 30.0, "equity", "nport", "2026-06-30", 0, None, "US", "Software"),
        ("S1", "US912810TM08", "Treasury", 9.0, "debt", "nport", "2026-06-30", 0, None, "US", None),
    ], columns=["fund_id", "constituent_isin", "name", "weight_pct", "asset_class", "source", "as_of", "depth",
                "via_fund", "country", "sector"]).assign(name_source=None))


def test_fill_sectors_only_fills_rows_without_one_and_says_so():
    sectors = pd.DataFrame({"isin": ["US0378331005", "US5949181045"],
                            "sector": ["Information Technology", "Information Technology"]})
    rows = fill_sectors(_rel(), sectors).order("weight_pct desc").fetchall()
    cols = fill_sectors(_rel(), sectors).columns
    got = [(r[cols.index("name")], r[cols.index("sector")], r[cols.index("sector_source")]) for r in rows]
    assert got == [("Apple", "Information Technology", "financedatabase"),
                   ("Microsoft", "Software", None),  # the holdings source's own sector wins
                   ("Treasury", None, None)]


def test_filled_weight_is_in_the_breakdowns_and_the_source_is_credited():
    sectors = pd.DataFrame({"isin": ["US0378331005"], "sector": ["Information Technology"]})
    rel = fill_sectors(_rel(), sectors)
    (fid, n, rows, sums), = list(_stream_constituents(rel, 10, {"S1"}))
    bd = _finish_breakdowns(sums, 1.0)
    assert bd["sector"][0] == ["Information Technology", 60.0]
    assert bd["sector_from"] == [["financedatabase", 60.0]]
    entries = _source_entries({"nport": {"name": "N-PORT"}, "financedatabase": {"name": "FinanceDatabase"}}, rel,
                              pd.DataFrame({"id": ["S1"], "source": ["nport"], "matched_via": [None]}))
    assert {e["id"] for e in entries} == {"nport", "financedatabase"}
    assert redistribution("financedatabase") == "public"


def test_no_sector_map_leaves_rows_as_they_are():
    rel = fill_sectors(_rel(), None)
    (_, _, _, sums), = list(_stream_constituents(rel, 10, {"S1"}))
    assert "sector_from" not in _finish_breakdowns(sums, 1.0)
