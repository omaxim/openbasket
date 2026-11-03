"""The published API and release files against docs/source/data-api.md, from a tiny universe run."""

import json

import pandas as pd
import pytest

from openbasket.datasets import DuckDBParquetDataset
from openbasket.pipelines.publish.nodes import FUND_FILE_KEYS, publish, resolution_bucket

from .test_universe import SOURCES, _run, build_universe, FUND_MASTER, PRODUCTS, security_master

META_KEYS = {"api_version", "contract", "built_at", "release_tag", "release_url", "full_data_url", "n_funds", "n_isins", "sources"}
SOURCE_KEYS = {"id", "name", "url", "licence", "credit", "n_funds", "as_of_min", "as_of_max"}
FUND_KEYS = {
    "id", "isin", "isins", "name", "ticker", "issuer", "source", "source_url", "credit", "as_of", "matched_via", "note",
    "status", "reported_total_pct", "resolved_pct", "unexpanded_fund_pct", "unresolved_pct", "unidentified_lines_pct",
    "n_constituents", "n_rows", "truncated", "listed_pct", "breakdowns", "fields", "constituents", "unresolved",
    "full_data_url",
}
FIELDS_V11 = ["isin", "name", "weight_pct", "asset_class", "source", "as_of", "depth", "via", "country", "sector"]


@pytest.fixture
def built(monkeypatch, tmp_path):
    universe = build_universe(PRODUCTS, FUND_MASTER, security_master(), {"issuers": {"ishares": "iShares"}})
    funds, constituents, run = _run(universe, monkeypatch, tmp_path, fail="US4642872265")
    # Round-trip through the catalog's formats, as Kedro hands them to the publish node.
    funds.to_parquet(tmp_path / "funds.parquet", index=False)
    ds = DuckDBParquetDataset(str(tmp_path / "constituents.parquet"), order_by="fund_id")
    ds.save(constituents)

    def go(**params):
        p = {"release_tag": "2026-11", "repo": "omaxim/openbasket", "site_dir": str(tmp_path / "site"),
             "release_dir": str(tmp_path / "release"), "max_constituents": 1000, "site_budget_mb": 800,
             "sources": {**SOURCES, "spdr": {"name": "SPDR", "url": "https://www.ssga.com/", "licence": "x", "credit": "SPDR"}},
             **params}
        report = publish(universe, pd.read_parquet(tmp_path / "funds.parquet"), ds.load(), run, p)
        api = tmp_path / "site" / "api" / "v1"
        return report, api, tmp_path / "release"

    return go


def _json(path):
    return json.loads(path.read_text())


def test_meta_matches_contract(built):
    _, api, _ = built()
    meta = _json(api / "meta.json")
    assert set(meta) == META_KEYS
    assert meta["release_url"] == "https://github.com/omaxim/openbasket/releases/tag/2026-11"
    assert meta["full_data_url"] == "https://github.com/omaxim/openbasket/releases/download/2026-11/openbasket-lookthrough.parquet"
    assert meta["built_at"].endswith("Z")
    by_id = {s["id"]: s for s in meta["sources"]}
    assert all(set(s) == SOURCE_KEYS for s in meta["sources"])
    # Every source a row came from is listed with its credit.
    assert {"ishares", "nport"} <= set(by_id)
    assert by_id["ishares"]["credit"] == "Holdings data from iShares by BlackRock"
    assert by_id["ishares"]["as_of_min"] == by_id["ishares"]["as_of_max"] == "2026-11-02"


def test_index_and_aliases(built):
    _, api, _ = built()
    meta, index = _json(api / "meta.json"), _json(api / "index.json")
    assert index["fields"] == ["id", "isin", "name", "ticker", "issuer", "kind", "source", "as_of", "resolved_pct", "n_constituents"]
    ids = [r[0] for r in index["rows"]]
    assert "US4642872265" not in ids  # its run failed
    assert "S000000003" in ids and "IE00B5BMR087" in ids
    assert {r[5] for r in index["rows"]} == {"fund"}
    assert index["aliases"]["IE00BYX5MX67"] == "IE00B5BMR087"
    assert index["aliases"]["IE00B5BMR087"] == "IE00B5BMR087"
    assert _json(api / "funds" / "IE00BYX5MX67.json") == {"alias_of": "IE00B5BMR087"}
    assert meta["n_funds"] == len(ids) and meta["n_isins"] == len(index["aliases"])


def test_fund_file_matches_contract(built):
    _, api, _ = built()
    doc = _json(api / "funds" / "US0000000AA2.json")
    assert list(doc) == FUND_FILE_KEYS and set(doc) == FUND_KEYS
    assert doc["fields"] == FIELDS_V11
    assert doc["source"] == "nport" and doc["source_url"] and doc["credit"] and doc["as_of"] == "2026-07-31"
    weights = [abs(r[2]) for r in doc["constituents"]]
    assert weights == sorted(weights, reverse=True)
    assert all(r[4] and r[5] for r in doc["constituents"])  # every row has its own source and as-of
    assert {r[4] for r in doc["constituents"]} == {"nport", "ishares"}
    un = doc["unresolved"]
    assert list(un) == ["weight_pct", "note", "source", "source_url", "as_of"]
    assert un["weight_pct"] == doc["unresolved_pct"]
    assert (un["source"], un["source_url"], un["as_of"]) == (doc["source"], doc["source_url"], doc["as_of"])
    assert doc["truncated"] is False and doc["isins"] == ["US0000000AA2", "US0000000AB1"]
    feeder = _json(api / "funds" / "S000000003.json")
    assert list(feeder) == FUND_FILE_KEYS
    assert feeder["isin"] is None and feeder["constituents"] == [] and feeder["source"] == "nport"
    assert feeder["unresolved_pct"] == 100
    # No holdings: the unresolved entry says why and where the fund was listed.
    fun = feeder["unresolved"]
    assert fun["note"] == feeder["note"] and fun["note"]
    # (This fund master has no report dates, so the as-of date is unknown here.)
    assert fun["source"] == "nport" and fun["source_url"] and fun["as_of"] == feeder["as_of"]


def test_cap_and_budget_are_reported(built):
    report, api, _ = built(max_constituents=1)
    doc = _json(api / "funds" / "US0000000AA2.json")
    assert doc["truncated"] is True and len(doc["constituents"]) == 1
    assert report["api"]["n_funds_truncated"] >= 1 and report["api"]["rows_only_in_release"] >= 1

    report, api, _ = built(site_budget_mb=0.000001, min_constituents=1, max_constituents=3)
    assert report["api"]["budget_cuts"] and report["api"]["max_constituents_used"] == 1
    assert report["api"]["over_budget"] is True


def test_release_files_and_quality_report(built):
    report, _, release = built()
    full = pd.read_parquet(release / "openbasket-lookthrough.parquet")
    assert {"fund_id", "constituent_isin", "source", "source_ref", "as_of", "name_source"} <= set(full.columns)
    assert full.groupby("fund_id")["constituent_isin"].apply(lambda s: s.isna().sum()).eq(1).all()
    funds = pd.read_parquet(release / "openbasket-funds.parquet")
    assert funds["id"].is_unique and funds["error"].notna().sum() == 1
    q = _json(release / "quality-report.json")
    assert q["counts"]["by_universe_source"]["ishares"]["failed"] == 1
    assert q["top_errors"][0]["examples"] == ["US4642872265"]
    assert q["release_tag"] == "2026-11" and "seconds_per_fund" in q and "run" in q
    assert sum(q["resolution"]["resolved_pct_buckets"].values()) == q["resolution"]["n_funds"]


@pytest.mark.parametrize("pct, band", [(-2, "below 0"), (0, "0 to 50"), (92.2, "90 to 99"), (100.0, "99 to 101"), (102.97, "above 101")])
def test_resolution_bucket(pct, band):
    assert resolution_bucket(pct) == band


def test_contract_1_1_counts_and_breakdowns(built):
    _, api, _ = built()
    meta = _json(api / "meta.json")
    assert meta["api_version"] == 1 and meta["contract"] == "1.1"
    doc = _json(api / "funds" / "US0000000AA2.json")
    # Apple is one constituent ISIN but two rows (N-PORT direct, iShares through IVV).
    assert doc["n_constituents"] == 1 and doc["n_rows"] == 2 == len(doc["constituents"])
    assert doc["listed_pct"] == pytest.approx(sum(r[2] for r in doc["constituents"]))
    assert doc["status"] in ("ok", "partial", "over_100", "leveraged")
    bd = doc["breakdowns"]
    assert set(bd) == {"asset_class", "country", "sector", "source", "top10_pct"}
    for dim in ("asset_class", "country", "sector", "source"):
        assert bd[dim][-1] == ["Unresolved", doc["unresolved_pct"]]
        assert sum(v for _, v in bd[dim]) == pytest.approx(100, abs=1e-3)
        values = [v for _, v in bd[dim][:-1]]
        assert values == sorted(values, reverse=True)
    assert dict(bd["source"][:-1]) == {"nport": pytest.approx(60), "ishares": pytest.approx(29.7)}
    assert bd["country"][0][0] == "US"  # N-PORT's country code; the iShares line has none here
    assert bd["top10_pct"] == pytest.approx(89.7)


def test_breakdowns_use_the_full_list_when_truncated(built):
    _, api, _ = built(max_constituents=1)
    doc = _json(api / "funds" / "US0000000AA2.json")
    assert doc["truncated"] and doc["n_rows"] == 2 and len(doc["constituents"]) == 1
    assert doc["listed_pct"] == pytest.approx(60)
    assert dict(doc["breakdowns"]["source"])["ishares"] == pytest.approx(29.7)


def test_no_holdings_fund_file_and_sources(built):
    _, api, _ = built()
    feeder = _json(api / "funds" / "S000000003.json")
    assert feeder["status"] == "no_holdings" and feeder["n_rows"] == 0 and feeder["listed_pct"] == 0
    assert feeder["breakdowns"]["source"] == [["Unresolved", 100.0]]
    assert feeder["note"] and feeder["source_url"]
    meta = _json(api / "meta.json")
    ids = {s["id"] for s in meta["sources"]}
    funds = [_json(p) for p in (api / "funds").glob("*.json")]
    row_sources = {r[4] for f in funds if "constituents" in f for r in f["constituents"]}
    fund_sources = {f["source"] for f in funds if "source" in f}
    assert row_sources | fund_sources <= ids
    assert all(s["name"] and s["credit"] for s in meta["sources"])


def test_quality_report_flags_shared_isins_and_statuses(built):
    report, _, _ = built()
    assert "shared_constituent_isins" in report and report["shared_constituent_isins"]["n_isins"] == 0
    assert sum(report["counts"]["status"].values()) == report["run"]["n_attempted"]
    assert "unexpanded_funds" in report
