import importlib.util
import json
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_build", ROOT / "scripts" / "check_build.py")
check_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_build)

FIELDS = ["isin", "name", "weight_pct", "asset_class", "source", "as_of", "depth", "via", "country", "sector"]
CC = "https://creativecommons.org/licenses/by/4.0/"


def _source(sid, **kw):
    base = {
        "id": sid, "name": f"{sid} name", "url": f"https://{sid}.example/", "licence": "Public data",
        "credit": f"Data from {sid}", "n_funds": 1, "as_of_min": "2026-10-31", "as_of_max": "2026-10-31",
    }
    return {**base, **kw}


def _fund(fid, source="cnmv_spain", rows=None, **kw):
    rows = rows if rows is not None else [
        ["US0378331005", "APPLE", 60.0, "equity", source, "2026-10-31", 0, "Fund", "US", "Tech"],
        ["US5949181045", "MICROSOFT", 40.0, "equity", "openfigi", "2026-10-31", 0, "Fund", "US", "Tech"],
    ]
    doc = {
        "id": fid, "isin": fid, "name": "Fund", "source": source, "source_url": "https://x.example/f",
        "credit": "c", "as_of": "2026-10-31", "fields": FIELDS, "constituents": rows,
        "unresolved": {"weight_pct": 0.0, "note": None},
    }
    doc.update(kw)
    return doc


def _release_files(release: Path, sources: list) -> None:
    pq.write_table(pa.table({"fund_id": ["F"] * len(sources), "source": pa.array(sources, pa.string())}),
                   release / "openbasket-lookthrough.parquet")
    pq.write_table(pa.table({"id": ["F"], "source": [sources[0]]}), release / "openbasket-funds.parquet")


@pytest.fixture
def build(tmp_path):
    """A small, valid build: api, release files and an assembled site."""
    api = tmp_path / "site" / "api"
    v1 = api / "v1"
    (v1 / "funds").mkdir(parents=True)
    meta = {
        "api_version": 1, "built_at": "2026-11-03T08:00:00Z", "release_tag": "2026-11",
        "n_funds": 2, "n_isins": 3,
        "sources": [_source("cnmv_spain"), _source("nport", as_of_max="2026-08-31"),
                    _source("openfigi", as_of_min=None, as_of_max=None)],
    }
    (v1 / "meta.json").write_text(json.dumps(meta))
    funds = [_fund("IE00B5BMR087"), _fund("S000012345", source="nport", rows=[
        ["US0378331005", "APPLE", 100.0, "equity", "nport", "2026-08-31", 0, "Fund", "US", None]])]
    for f in funds:
        (v1 / "funds" / f"{f['id']}.json").write_text(json.dumps(f))
    (v1 / "funds" / "IE00B5BMR088.json").write_text(json.dumps({"alias_of": "IE00B5BMR087"}))
    index = {
        "fields": ["id", "isin", "name", "ticker", "issuer", "kind", "source", "as_of", "resolved_pct", "n_constituents"],
        "rows": [[f["id"], f["id"], "Fund", None, None, "fund", f["source"], f["as_of"], 100.0, 2] for f in funds],
        "aliases": {},
    }
    (v1 / "index.json").write_text(json.dumps(index))
    release = tmp_path / "release"
    release.mkdir()
    (release / "quality-report.json").write_text(json.dumps({"resolution": {"n_funds": 2}, "api": {"mb": 0.1}}))
    _release_files(release, ["cnmv_spain", "nport", None])
    pages = tmp_path / "pages"
    (pages / "pipeline").mkdir(parents=True)
    (pages / "index.html").write_text("<html></html>")
    (pages / "pipeline" / "index.html").write_text("<html><head></head></html>")
    params = tmp_path / "parameters.yml"
    params.write_text("nport:\n  quarters: [2026q2, 2026q3]\n")
    return {"tmp": tmp_path, "api": api, "v1": v1, "release": release, "pages": pages, "params": params, "meta": meta}


def _run(b, *extra, min_funds=1, today="2026-11-03"):
    summary = b["tmp"] / "summary.md"
    notes = b["tmp"] / "notes.md"
    code = check_build.main([
        "--api", str(b["api"]), "--release", str(b["release"]), "--pages", str(b["pages"]),
        "--require-pipeline", "--min-funds", str(min_funds), "--params", str(b["params"]),
        "--today", today, "--summary", str(summary), "--notes", str(notes), "--sha", "abcdef1234", *extra,
    ])
    return code, summary.read_text(), notes.read_text() if notes.exists() else ""


def _edit_meta(b, fn):
    meta = json.loads((b["v1"] / "meta.json").read_text())
    fn(meta)
    (b["v1"] / "meta.json").write_text(json.dumps(meta))


def test_valid_build_passes_and_writes_summary_and_notes(build, capsys):
    code, summary, notes = _run(build)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "::error::" not in out
    assert "Funds published: **2**" in summary
    assert "[cnmv_spain name](https://cnmv_spain.example/)" in summary and "Data from nport" in summary
    assert "Checked 2 fund files, 1 alias files, 3 rows." in summary
    assert "2026-08-31" in notes and "Not investment advice" in notes
    assert "abcdef1" in notes
    # openfigi has no dates and says so instead of "None to None"
    assert "None" not in notes and "not dated" in notes


def test_too_few_funds_fails(build, capsys):
    code, summary, _ = _run(build, min_funds=3)
    assert code == 1
    assert "fewer than the minimum of 3" in capsys.readouterr().out
    assert "### Errors" in summary


def test_empty_sources_fail(build, capsys):
    _edit_meta(build, lambda m: m.update(sources=[]))
    code, _, _ = _run(build)
    out = capsys.readouterr().out
    assert code == 1
    assert "lists no sources" in out


@pytest.mark.parametrize("field", ["name", "url", "licence", "credit"])
def test_source_missing_a_field_fails(build, capsys, field):
    _edit_meta(build, lambda m: m["sources"][0].update({field: None}))
    code, _, _ = _run(build)
    assert code == 1
    assert f"Source 'cnmv_spain' in meta.json has no {field}" in capsys.readouterr().out


def test_source_used_by_row_but_not_listed_fails(build, capsys):
    _edit_meta(build, lambda m: m.update(sources=[s for s in m["sources"] if s["id"] != "openfigi"]))
    code, _, _ = _run(build)
    out = capsys.readouterr().out
    assert code == 1
    assert "Source 'openfigi' is used but not listed in meta.sources" in out


def test_adapter_source_listed_is_accepted_in_a_full_build(build):
    """Any issuer adapter id is fine in a local (full) build as long as meta.sources lists it."""
    _edit_meta(build, lambda m: m["sources"].append(_source("xtrackers")))
    f = _fund("LU0274208692", source="xtrackers")
    f["constituents"][0][4] = "xtrackers"
    (build["v1"] / "funds" / "LU0274208692.json").write_text(json.dumps(f))
    code, _, _ = _run(build, "--mode", "full")
    assert code == 0


def test_unresolved_source_must_be_listed(build, capsys):
    f = _fund("IE00BD8QG133", source="cnmv_spain", rows=[], unresolved={"weight_pct": 100.0, "note": "n", "source": "amundi"})
    (build["v1"] / "funds" / "IE00BD8QG133.json").write_text(json.dumps(f))
    code, _, _ = _run(build)
    assert code == 1
    assert "Source 'amundi' is used" in capsys.readouterr().out


def test_row_without_as_of_or_source_fails(build, capsys):
    rows = [["US0378331005", "APPLE", 50.0, "equity", "cnmv_spain", None, 0, "F", None, None],
            ["US5949181045", "MSFT", 50.0, "equity", None, "2026-10-31", 0, "F", None, None]]
    (build["v1"] / "funds" / "IE00B5BMR087.json").write_text(json.dumps(_fund("IE00B5BMR087", rows=rows)))
    code, _, _ = _run(build)
    out = capsys.readouterr().out
    assert code == 1
    assert "have no as-of date" in out and "have no source" in out


def test_fund_without_as_of_fails(build, capsys):
    (build["v1"] / "funds" / "IE00B5BMR087.json").write_text(json.dumps(_fund("IE00B5BMR087", rows=[], as_of=None)))
    code, _, _ = _run(build)
    assert code == 1
    assert "IE00B5BMR087" in capsys.readouterr().out


def test_tickerinside_credit_must_link_cc_by(build, capsys):
    _edit_meta(build, lambda m: m["sources"].append(_source("tickerinside", credit="TickerInside, CC BY 4.0")))
    code, _, _ = _run(build)
    assert code == 1
    assert "CC BY 4.0 licence" in capsys.readouterr().out
    _edit_meta(build, lambda m: m["sources"][-1].update(
        credit=f"TickerInside (https://tickerinside.com/), {CC}; changed: tickers mapped to ISINs"))
    code, _, notes = _run(build)
    assert code == 0
    assert "openbasket changed it" in notes


def test_pages_over_limit_fails_and_near_limit_warns(build, capsys, monkeypatch):
    monkeypatch.setattr(check_build, "PAGES_LIMIT_BYTES", 100)
    monkeypatch.setattr(check_build, "PAGES_WARN_BYTES", 10)
    (build["pages"] / "big.bin").write_bytes(b"x" * 200)
    code, summary, _ = _run(build)
    assert code == 1
    assert "over GitHub Pages' 1 GB limit" in capsys.readouterr().out
    (build["pages"] / "big.bin").write_bytes(b"x" * 20)
    code, _, _ = _run(build)
    assert code == 0
    assert "::warning::The site is" in capsys.readouterr().out


def test_tracking_script_fails(build, capsys):
    (build["pages"] / "pipeline" / "index.html").write_text(
        '<script src="https://cdn.heapanalytics.com/js/heap-1.js"></script>')
    code, _, _ = _run(build)
    assert code == 1
    assert "tracking script" in capsys.readouterr().out


def test_missing_pipeline_diagram_fails(build, capsys):
    (build["pages"] / "pipeline" / "index.html").unlink()
    code, _, _ = _run(build)
    assert code == 1
    assert "No pipeline diagram" in capsys.readouterr().out


def test_index_fund_without_file_fails(build, capsys):
    (build["v1"] / "funds" / "S000012345.json").unlink()
    code, _, _ = _run(build)
    assert code == 1
    assert "have no fund file" in capsys.readouterr().out


@pytest.mark.parametrize(
    "today, expected",
    [
        (date(2026, 11, 3), (2026, 2)),   # Q3 ended 2026-09-30, 34 days earlier: not out yet
        (date(2026, 12, 3), (2026, 3)),   # 64 days after: due
        (date(2027, 1, 3), (2026, 3)),    # Q4 ended 3 days earlier
        (date(2027, 3, 3), (2026, 4)),
        (date(2026, 2, 1), (2025, 3)),
    ],
)
def test_expected_nport_quarter(today, expected):
    assert check_build.expected_nport_quarter(today) == expected


def test_stale_nport_warns_but_passes(build, capsys):
    code, summary, _ = _run(build, today="2027-03-03")
    out = capsys.readouterr().out
    assert code == 0
    assert "nport.quarters ends at 2026q3; SEC should have published 2026q4" in out
    assert "The newest N-PORT holdings are from 2026-08-31" in out
    assert "### Warnings" in summary


def test_fresh_nport_gives_no_warning(build, capsys):
    code, _, _ = _run(build, today="2026-11-03")
    assert code == 0
    assert "::warning::" not in capsys.readouterr().out


def test_sample_run_summary_says_not_deployed(build):
    code, summary, _ = _run(build, "--sample", "5", "--live", "false")
    assert code == 0
    assert "sample of 5 per universe source" in summary
    assert "not released or deployed" in summary


@pytest.mark.parametrize("today", ["2027-01-03", "2027-02-03"])
def test_old_nport_holdings_alone_do_not_warn_while_quarters_are_current(build, capsys, today):
    """N-PORT holdings are ~95 days old on release; with 2026q3 configured nothing newer is due yet."""
    _edit_meta(build, lambda m: [s.update(as_of_max="2026-07-31") for s in m["sources"] if s["id"] == "nport"])
    code, _, _ = _run(build, today=today)
    assert code == 0
    assert "::warning::" not in capsys.readouterr().out


def test_nport_age_warns_without_quarters(build, capsys):
    build["params"].write_text("other: 1\n")
    code, _, _ = _run(build, today="2027-04-01")
    out = capsys.readouterr().out
    assert code == 0
    assert "Check whether SEC has published a newer quarter" in out


# --- publishing policy: nothing local_only in a public build -----------------


def test_local_only_source_in_meta_fails(build, capsys):
    _edit_meta(build, lambda m: m["sources"].append(_source("ishares")))
    code, _, _ = _run(build)
    out = capsys.readouterr().out
    assert code == 1
    assert "Source 'ishares' in meta.json is local_only" in out


def test_meta_declaring_a_source_local_only_fails(build, capsys):
    _edit_meta(build, lambda m: m["sources"][1].update(redistribution="local_only"))
    code, _, _ = _run(build)
    assert code == 1
    assert "Source 'nport' in meta.json is local_only" in capsys.readouterr().out


def test_local_only_row_fails_even_when_listed(build, capsys):
    _edit_meta(build, lambda m: m["sources"].append(_source("spdr")))
    f = _fund("IE00B6YX5C33", source="nport")
    f["constituents"][0][4] = "spdr"
    (build["v1"] / "funds" / "IE00B6YX5C33.json").write_text(json.dumps(f))
    code, _, _ = _run(build)
    out = capsys.readouterr().out
    assert code == 1
    assert "Source 'spdr' is local_only but is published (e.g. funds/IE00B6YX5C33.json)" in out


def test_unknown_adapter_is_local_only_in_a_public_build(build, capsys):
    _edit_meta(build, lambda m: m["sources"].append(_source("xtrackers")))
    f = _fund("LU0274208692", source="xtrackers")
    f["constituents"][0][4] = "xtrackers"
    (build["v1"] / "funds" / "LU0274208692.json").write_text(json.dumps(f))
    code, _, _ = _run(build)
    out = capsys.readouterr().out
    assert code == 1
    assert "'xtrackers' in meta.json is local_only" in out and "'xtrackers' is local_only but is published" in out


def test_full_build_cannot_pass_the_public_gate(build, capsys):
    _edit_meta(build, lambda m: m.update(mode="full"))
    code, _, _ = _run(build)
    assert code == 1
    assert "only a public build (publish.mode: public) may be deployed" in capsys.readouterr().out


def test_release_file_with_local_only_rows_fails(build, capsys):
    _release_files(build["release"], ["nport", "ishares", "ishares"])
    code, _, _ = _run(build)
    out = capsys.readouterr().out
    assert code == 1
    assert "openbasket-lookthrough.parquet has rows from local_only sources: ishares (2 rows)" in out


def test_local_only_fund_listed_by_identity_passes(build):
    """A fund only a local_only issuer covers is listed by name and ISIN, from a public identity source."""
    _edit_meta(build, lambda m: m["sources"].append(_source("issuer_list")))
    note = "Holdings for this fund are available from iShares when you run openbasket yourself"
    f = _fund("IE00B4L5Y983", source="issuer_list", rows=[], status="local_only", note=note,
              unresolved={"weight_pct": 100.0, "note": note, "source": "issuer_list",
                          "source_url": "https://www.ishares.com/", "as_of": "2026-11-03"})
    (build["v1"] / "funds" / "IE00B4L5Y983.json").write_text(json.dumps(f))
    code, summary, _ = _run(build)
    assert code == 0
    assert "local_only 1" in summary


def test_fund_without_source_url_fails(build, capsys):
    f = _fund("IE00B5BMR087", source_url="")
    (build["v1"] / "funds" / "IE00B5BMR087.json").write_text(json.dumps(f))
    code, _, _ = _run(build)
    assert code == 1
    assert "1 fund files have no source_url (e.g. IE00B5BMR087)" in capsys.readouterr().out


def test_no_holdings_unresolved_without_source_url_fails(build, capsys):
    f = _fund("IE00BF2MW684", source="nport", rows=[],
              unresolved={"weight_pct": 100.0, "note": "n", "source": "nport", "source_url": None, "as_of": "2026-08-31"})
    (build["v1"] / "funds" / "IE00BF2MW684.json").write_text(json.dumps(f))
    code, _, _ = _run(build)
    assert code == 1
    assert "funds without rows have an unresolved entry with no source_url (e.g. IE00BF2MW684)" in capsys.readouterr().out


def test_source_class_needs_every_answer_to_be_public():
    assert check_build.source_class("nport") == "public"
    assert check_build.source_class("ishares") == "local_only"
    assert check_build.source_class("nport", "local_only") == "local_only"
    assert check_build.source_class("anything", "public", classify=lambda s: "public") == "public"
    assert check_build.source_class("anything", None, classify=lambda s: "local_only") == "local_only"
