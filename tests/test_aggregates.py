import json

import pandas as pd
import pytest

from openbasket import aggregates
from openbasket.datasets import connection
from openbasket.pipelines.publish.nodes import _aggregate_breakdowns, load_aggregates


def _rows(*rows):
    df = pd.DataFrame(rows, columns=["fund_id", "weight_pct", "sector", "country", "asset_class"])
    df.insert(1, "constituent_isin", [f"XS{i:09d}0" for i in range(len(df))])
    return df


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


def test_build_fills_missing_sectors_from_the_sector_map():
    funds = pd.DataFrame([{"id": "IE00B5BMR087", "isins": ["IE00B5BMR087"], "source": "ishares", "as_of": "2026-11-02",
                           "unresolved_pct": 0.0, "n_constituents": 2}])
    rel = connection().from_df(pd.DataFrame([
        ("IE00B5BMR087", "US0378331005", 60.0, None, "US", "equity"),
        ("IE00B5BMR087", "US5949181045", 40.0, "Health Care", "US", "equity"),  # the issuer's own label wins
    ], columns=["fund_id", "constituent_isin", "weight_pct", "sector", "country", "asset_class"]))
    sectors = pd.DataFrame({"isin": ["US0378331005", "US5949181045"], "sector": ["Information Technology", "Information Technology"]})
    doc = aggregates.build(funds, rel, {"computed_at": "2026-11-04T00:00:00Z"}, sectors)
    assert doc["funds"]["IE00B5BMR087"]["sector"][:2] == [["Information Technology", 60], ["Health Care", 40]]


def test_splits_are_dated_by_their_last_commit(tmp_path):
    import subprocess

    from openbasket.pipelines.publish.nodes import committed_at

    doc = {"version": 1, "computed_at": "2026-11-01T00:00:00Z", "method": "m", "n_funds": 1, "funds": {
        "IE00B5BMR087": {"isins": ["IE00B5BMR087"], "as_of": "2026-11-02", "holdings_source": "ishares",
                         "sector": [["Unresolved", 0]]}}}
    path = tmp_path / "aggregates.json"
    path.write_text(json.dumps(doc))
    assert committed_at(path) is None or load_aggregates(str(path))  # outside a repository: the computed date
    assert load_aggregates(str(path))["IE00B5BMR087"]["updated_at"] in ("2026-11-01T00:00:00Z", committed_at(path))
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.org", "GIT_COMMITTER_DATE": "2026-11-05T10:00:00+01:00", "PATH": "/usr/bin:/bin"}
    for cmd in (["git", "init", "-q"], ["git", "add", "aggregates.json"], ["git", "commit", "-q", "-m", "splits"]):
        subprocess.run(cmd, cwd=tmp_path, env=env, check=True)
    assert committed_at(path) == "2026-11-05T09:00:00Z"
    assert load_aggregates(str(path))["IE00B5BMR087"]["updated_at"] == "2026-11-05T09:00:00Z"


def _entry(isin, computed_at=None, it=50):
    e = {"isins": [isin], "as_of": "2026-10-01", "holdings_source": "ishares",
         "sector": [["Information Technology", it], ["Unresolved", 0]]}
    if computed_at:
        e["computed_at"] = computed_at
    return e


def test_merge_keeps_funds_a_run_missed_until_they_are_too_old():
    new = {"IE00B5BMR087": _entry("IE00B5BMR087", "2026-11-04T00:00:00Z", it=60)}
    previous = {"computed_at": "2026-10-01T00:00:00Z", "funds": {
        "IE00B5BMR087": _entry("IE00B5BMR087", it=40),                         # recomputed: the new one wins
        "IE00B4L5Y983": _entry("IE00B4L5Y983"),                                # its scraper failed: kept
        "IE00B6YX5C33": _entry("IE00B6YX5C33", "2026-01-01T00:00:00Z"),        # older than keep_days: dropped
    }}
    out, kept = aggregates.merge(new, previous, "2026-11-04T00:00:00Z", 180)
    assert kept == 1 and sorted(out) == ["IE00B4L5Y983", "IE00B5BMR087"]
    assert out["IE00B5BMR087"]["sector"][0] == ["Information Technology", 60]
    assert out["IE00B4L5Y983"]["computed_at"] == "2026-10-01T00:00:00Z"  # the file's date, for an entry without one


def test_build_keeps_earlier_splits_and_the_site_shows_their_own_date(tmp_path):
    prev = tmp_path / "aggregates.json"
    prev.write_text(json.dumps({"version": 1, "computed_at": "2026-10-01T00:00:00Z", "method": "m", "n_funds": 1,
                                "funds": {"IE00B4L5Y983": _entry("IE00B4L5Y983")}}))
    funds = pd.DataFrame([{"id": "IE00B5BMR087", "isins": ["IE00B5BMR087"], "source": "ishares", "as_of": "2026-11-02",
                           "unresolved_pct": 0.0, "n_constituents": 1}])
    rel = connection().from_df(_rows(("IE00B5BMR087", 100.0, "Information Technology", "US", "equity")))
    doc = aggregates.build(funds, rel, {"computed_at": "2026-11-04T00:00:00Z"}, None, {"aggregates_path": str(prev)})
    assert doc["n_kept"] == 1 and sorted(doc["funds"]) == ["IE00B4L5Y983", "IE00B5BMR087"]
    prev.write_text(json.dumps(doc))
    index = load_aggregates(str(prev))
    assert index["IE00B5BMR087"]["updated_at"] in ("2026-11-04T00:00:00Z",)  # not in git here: the computed date
    assert index["IE00B4L5Y983"]["updated_at"] == "2026-10-01T00:00:00Z"


def _git(cwd, *args):
    import subprocess

    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.org", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
           "HOME": str(cwd)}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def test_share_commits_only_the_splits_file(tmp_path, monkeypatch):
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    _git(tmp_path, "clone", "-q", str(remote), str(work))
    (work / "README.md").write_text("hi\n")
    (work / "public_aggregates").mkdir()
    _git(work, "add", "README.md")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "push", "-q", "origin", "HEAD:main")
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@example.org", "HOME": str(tmp_path)}.items():
        monkeypatch.setenv(k, v)
    # Something else is changed and staged in the working tree: it must not be committed.
    (work / "README.md").write_text("local edit\n")
    _git(work, "add", "README.md")
    doc = {"version": 1, "computed_at": "2026-11-04T00:00:00Z", "method": "m", "n_funds": 1,
           "funds": {"IE00B5BMR087": _entry("IE00B5BMR087", "2026-11-04T00:00:00Z")}}
    path = work / "public_aggregates" / "aggregates.json"
    path.write_text(json.dumps(doc))
    cfg = {"share": {"enabled": True, "push": True}}
    got = aggregates.share(doc, cfg, str(path))
    assert got["shared"] and got["path"] == "public_aggregates/aggregates.json"
    assert _git(remote, "show", "--name-only", "--format=", "main") == "public_aggregates/aggregates.json"
    assert _git(remote, "show", "main:README.md") == "hi"
    assert _git(work, "diff", "--cached", "--name-only") == "README.md"  # the user's staged edit is untouched
    assert aggregates.share(doc, cfg, str(path))["reason"].endswith("is unchanged on origin/main")
    assert aggregates.share(doc, {"share": {"enabled": False}}, str(path)) == {
        "shared": False, "reason": "aggregates.share.enabled is false"}
    with pytest.raises(aggregates.AggregatesError):
        aggregates.share({**doc, "funds": {"IE00B5BMR087": {"rows": [["US0378331005", 7]]}}}, cfg, str(path))


def test_build_counts_the_unresolved_row_once():
    funds = pd.DataFrame([{"id": "AU0000005886", "isins": ["AU0000005886"], "source": "vanguard_au",
                           "as_of": "2026-09-30", "unresolved_pct": 60.0, "n_constituents": 1}])
    rel = connection().from_df(pd.DataFrame([
        ("AU0000005886", "US0378331005", 40.0, "Information Technology", "US", "equity"),
        ("AU0000005886", None, 60.0, None, None, "unresolved"),  # the fund's own unresolved-weight row
    ], columns=["fund_id", "constituent_isin", "weight_pct", "sector", "country", "asset_class"]))
    doc = aggregates.build(funds, rel, {"computed_at": "2026-11-04T00:00:00Z", "keep_previous": False})
    f = doc["funds"]["AU0000005886"]
    for d in aggregates.DIMENSIONS:
        assert sum(v for _, v in f[d]) == 100, (d, f[d])
    assert f["asset_class"] == [["Equity", 40], ["Unresolved", 60]]
