import json

import pandas as pd
import pytest

from openbasket import aggregates
from openbasket.datasets import connection
from openbasket.pipelines.publish.nodes import _aggregate_breakdowns, load_aggregates


def _rows(*rows):
    return pd.DataFrame(rows, columns=["fund_id", "weight_pct", "sector", "country", "asset_class"])


@pytest.mark.parametrize("label, asset_class, sector", [
    ("Information Technology", "equity", "Information Technology"),
    ("IT", "equity", "Information Technology"),
    ("Semiconductors", "equity", "Information Technology"),
    ("Banks", "equity", "Financials"),
    ("Treasury", "debt", "Government Bonds"),
    (None, "debt", "Unknown"),
    ("Collectibles", "equity", "Other"),
    (None, "equity", "Unknown"),
])
def test_sector_of(label, asset_class, sector):
    assert aggregates.sector_of(label, asset_class) == sector


def test_fund_aggregates_round_fold_and_keep_unresolved():
    rows = _rows(
        ("F", 60.4, "Information Technology", "US", "equity"),
        ("F", 30.2, "Banks", "GB", "equity"),
        ("F", 0.3, "Utilities", "JP", "equity"),
        ("F", 0.3, "Energy", "BR", "equity"),
    )
    agg = aggregates.fund_aggregates(rows, unresolved_pct=8.8)
    assert agg["sector"] == [["Information Technology", 60], ["Financials", 30], ["Other", 1], ["Unresolved", 9]]
    assert agg["region"][:2] == [["North America", 60], ["Europe", 30]]
    assert agg["country"][-1] == ["Unresolved", 9]
    assert all(isinstance(p, int) for d in aggregates.DIMENSIONS for _, p in agg[d])


@pytest.mark.parametrize("bad", [
    {"sector": [["Apple Inc", 7]]},                 # a company name
    {"country": [["US0378331005", 7]]},             # an ISIN
    {"sector": [["Information Technology", 7.59]]},  # not whole percent
    {"rows": [["US0378331005", 7]]},                # an unexpected key
])
def test_validate_refuses_anything_but_coarse_splits(bad):
    with pytest.raises(aggregates.AggregatesError):
        aggregates.validate({"version": 1, "funds": {"IE00B5BMR087": bad}})


def test_build_covers_local_only_funds_only():
    funds = pd.DataFrame([
        {"id": "IE00B5BMR087", "isins": ["IE00B5BMR087"], "source": "ishares", "as_of": "2026-11-02",
         "unresolved_pct": 0.1, "n_constituents": 2},
        {"id": "US4642872000", "isins": ["US4642872000"], "source": "nport", "as_of": "2026-06-30",
         "unresolved_pct": 0.2, "n_constituents": 1},
    ])
    rel = connection().from_df(_rows(
        ("IE00B5BMR087", 70.0, "Information Technology", "US", "equity"),
        ("IE00B5BMR087", 29.9, "Health Care", "US", "equity"),
        ("US4642872000", 99.8, "Information Technology", "US", "equity"),
    ))
    doc = aggregates.build(funds, rel, {"computed_at": "2026-11-04T00:00:00Z"})
    assert list(doc["funds"]) == ["IE00B5BMR087"]  # the N-PORT fund is public: it has its real holdings
    assert doc["funds"]["IE00B5BMR087"]["sector"][0] == ["Information Technology", 70]
    assert "US0378331005" not in json.dumps(doc)


def test_public_fund_file_gets_splits_but_no_holdings(tmp_path):
    doc = {"version": 1, "computed_at": "2026-11-04T00:00:00Z", "method": "m", "n_funds": 1, "funds": {
        "IE00B5BMR087": {"isins": ["IE00B5BMR087", "IE00BD8KRH84"], "as_of": "2026-11-02", "holdings_source": "ishares",
                         "sector": [["Information Technology", 34], ["Unresolved", 0]],
                         "country": [["US", 99], ["Unresolved", 0]],
                         "region": [["North America", 99], ["Unresolved", 0]],
                         "asset_class": [["Equity", 100], ["Unresolved", 0]]}}}
    path = tmp_path / "aggregates.json"
    path.write_text(json.dumps(doc))
    index = load_aggregates(str(path))
    local = {"id": "IE00BD8KRH84", "isins": ["IE00BD8KRH84"], "status": "local_only", "issuer": "iShares"}
    bd = _aggregate_breakdowns(local, index)  # found through the share-class ISIN
    assert bd["basis"] == "openbasket_aggregate" and bd["sector"][0] == ["Information Technology", 34]
    assert "iShares" in bd["note"] and "holdings not shown" in bd["note"]
    assert _aggregate_breakdowns({**local, "status": "ok"}, index) is None  # a fund with real holdings keeps its own
    assert load_aggregates(str(tmp_path / "missing.json")) is None
