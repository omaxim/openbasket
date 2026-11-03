import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from openbasket.lookthrough import STATUSES
from openbasket.pipelines.publish.nodes import FUND_FILE_KEYS

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
FIXTURES = SITE / "fixtures" / "api" / "v1"
CORE_JS = SITE / "assets" / "core.js"

_spec = importlib.util.spec_from_file_location("assemble_site", ROOT / "scripts" / "assemble_site.py")
assemble_site = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(assemble_site)


@pytest.fixture
def inputs(tmp_path):
    docs = tmp_path / "html"
    (docs / "_static").mkdir(parents=True)
    (docs / "index.html").write_text("<html>docs</html>")
    (docs / "_static" / "openbasket.css").write_text("body{}")
    (docs / ".buildinfo").write_text("config")
    api = tmp_path / "api"
    (api / "v1" / "funds").mkdir(parents=True)
    (api / "v1" / "meta.json").write_text('{"api_version": 1}')
    (api / "v1" / "funds" / "IE00B5BMR087.json").write_text("{}")
    return docs, api, tmp_path / "pages"


def test_assemble_lays_out_pages_directory(inputs):
    docs, api, out = inputs
    assemble_site.assemble(docs, api, out)
    assert (out / "index.html").is_file()
    assert (out / "assets" / "app.js").is_file()
    assert (out / "assets" / "core.js").is_file()
    assert (out / "assets" / "app.css").is_file()
    assert (out / "docs" / "index.html").read_text() == "<html>docs</html>"
    assert (out / "docs" / "_static" / "openbasket.css").is_file()
    assert (out / "api" / "v1" / "meta.json").is_file()
    assert (out / "api" / "v1" / "funds" / "IE00B5BMR087.json").is_file()
    assert (out / "CNAME").read_text() == "openbasket.leithresearch.org\n"
    assert (out / ".nojekyll").exists()
    assert not (out / "pipeline").exists()


def test_assemble_leaves_out_fixtures_and_build_files(inputs):
    docs, api, out = inputs
    assemble_site.assemble(docs, api, out)
    assert not (out / "fixtures").exists()
    assert not (out / "docs" / ".buildinfo").exists()


def test_assemble_replaces_its_own_previous_output(inputs):
    docs, api, out = inputs
    assemble_site.assemble(docs, api, out)
    (out / "stale").mkdir()
    (out / "stale" / "old.json").write_text("{}")
    assemble_site.assemble(docs, api, out)
    assert not (out / "stale").exists()
    assert (out / "index.html").is_file()


def test_assemble_refuses_a_folder_it_did_not_write(inputs):
    docs, api, out = inputs
    (out / "notes").mkdir(parents=True)
    (out / "notes" / "keep.txt").write_text("mine")
    with pytest.raises(ValueError, match="not written by this script"):
        assemble_site.assemble(docs, api, out)
    assert (out / "notes" / "keep.txt").read_text() == "mine"


def test_assemble_writes_into_an_empty_folder(inputs):
    docs, api, out = inputs
    out.mkdir()
    assemble_site.assemble(docs, api, out)
    assert (out / "CNAME").is_file()


@pytest.mark.parametrize("rel", ["src", "conf", "docs/" + "source", "tests", "build", ".", "site/assets"])
def test_out_inside_the_repository_must_be_under_build(inputs, rel):
    docs, api, _ = inputs
    with pytest.raises(ValueError, match="overwrite"):
        assemble_site._check_out(ROOT / rel, [SITE, docs, api])


def test_out_under_build_is_allowed(inputs):
    docs, api, _ = inputs
    assemble_site._check_out(ROOT / "build" / "pages-test-does-not-exist", [SITE, docs, api])


@pytest.mark.parametrize("where", ["home", "root"])
def test_out_cannot_be_home_or_the_filesystem_root(inputs, where):
    docs, api, _ = inputs
    target = Path.home() if where == "home" else Path("/")
    with pytest.raises(ValueError, match="overwrite"):
        assemble_site._check_out(target, [docs, api])


def test_assemble_wants_the_folder_that_holds_v1(inputs):
    docs, api, out = inputs
    with pytest.raises(FileNotFoundError, match="v1"):
        assemble_site.assemble(docs, api / "v1", out)


def test_assemble_wants_built_docs(inputs, tmp_path):
    _, api, out = inputs
    with pytest.raises(FileNotFoundError, match="sphinx-build"):
        assemble_site.assemble(tmp_path / "missing", api, out)


@pytest.mark.parametrize("target", ["docs", "api", "parent", "inside_docs"])
def test_assemble_refuses_to_overwrite_its_inputs(inputs, target):
    docs, api, _ = inputs
    out = {"docs": docs, "api": api, "parent": docs.parent, "inside_docs": docs / "pages"}[target]
    with pytest.raises(ValueError, match="overwrite"):
        assemble_site.assemble(docs, api, out)
    assert (docs / "index.html").exists()


def test_assemble_copies_the_pipeline_view(inputs, tmp_path):
    docs, api, out = inputs
    viz = tmp_path / "viz"
    (viz / "api").mkdir(parents=True)
    (viz / "index.html").write_text("<html>kedro-viz</html>")
    (viz / "api" / "main").write_text("{}")
    assemble_site.assemble(docs, api, out, pipeline=viz)
    assert (out / "pipeline" / "index.html").read_text() == "<html>kedro-viz</html>"
    assert (out / "pipeline" / "api" / "main").is_file()


def test_assemble_wants_a_built_pipeline_view(inputs, tmp_path):
    docs, api, out = inputs
    with pytest.raises(FileNotFoundError, match="Kedro-Viz"):
        assemble_site.assemble(docs, api, out, pipeline=tmp_path / "nope")


def test_cli(inputs, capsys):
    docs, api, out = inputs
    assert assemble_site.main(["--docs", str(docs), "--api", str(api), "--out", str(out), "--domain", "example.test"]) == 0
    assert (out / "CNAME").read_text() == "example.test\n"
    assert "Assembled" in capsys.readouterr().out
    assert assemble_site.main(["--docs", str(docs / "nope"), "--api", str(api), "--out", str(out)]) == 1
    assert assemble_site.main(["--docs", str(docs), "--api", str(api), "--out", str(ROOT / "src")]) == 1


def test_assemble_with_fixture_api(tmp_path, inputs):
    docs, _, out = inputs
    assemble_site.assemble(docs, FIXTURES.parent, out)
    assert (out / "api" / "v1" / "index.json").is_file()


def test_landing_page_references_existing_assets():
    html = (SITE / "index.html").read_text()
    assert 'data-api="api/v1"' in html
    for ref in re.findall(r'(?:href|src)="(assets/[^"?#]+)', html):
        assert (SITE / ref).is_file(), ref
    assert html.index("assets/core.js") < html.index("assets/app.js")
    assert "not investment advice" in html
    assert "not affiliated" in html
    assert "https://github.com/omaxim/openbasket" in html


def test_landing_page_links_docs_and_pipeline_in_nav_and_footer():
    html = (SITE / "index.html").read_text()
    nav = html[html.index('<nav class="nav">'):html.index("</nav>")]
    footer = html[html.index("<footer>"):html.index("</footer>")]
    for part in (nav, footer):
        assert 'href="docs/"' in part
        assert 'href="pipeline/"' in part
    assert 'id="coverage"' in html


def test_fund_view_is_not_a_live_region_and_examples_survive_status():
    html = (SITE / "index.html").read_text()
    assert re.search(r'<div id="fund-view"\s*>', html)
    assert re.search(r'id="fund-status"[^>]*aria-live="polite"', html)
    status = re.search(r'<p[^>]*id="search-status"[^>]*>(.*?)</p>', html, re.S)
    assert status and status.group(1).strip() == ""
    examples = re.search(r'<p[^>]*id="search-examples"[^>]*>(.*?)</p>', html, re.S)
    assert examples and examples.group(1).count("data-isin=") == 3


def test_page_links_to_moved_sources_docs():
    js = (SITE / "assets" / "app.js").read_text()
    assert "sources.html" not in js
    assert "${DOCS}sources/" in js


def test_docs_templates_find_the_landing_page_from_any_depth():
    for name in ("page.html", "sidebar/brand.html"):
        text = (ROOT / "docs" / ("source") / "_templates" / name).read_text()
        assert "pathto('_static/', 1)" in text
        assert "pathto(master_doc)|replace" not in text
        assert "pipeline/" in text


# The fixtures stand in for the real build, so they must keep to data-api.md v1.1.

INDEX_FIELDS = ["id", "isin", "name", "ticker", "issuer", "kind", "source", "as_of", "resolved_pct", "n_constituents"]
FUND_FIELDS = ["isin", "name", "weight_pct", "asset_class", "source", "as_of", "depth", "via", "country", "sector"]
# The same keys, in the same order, as the publish pipeline writes them.
FUND_KEYS = FUND_FILE_KEYS
BREAKDOWNS = ["asset_class", "country", "sector", "source"]
MATCHED_VIA = {"ishares", "spdr", "nport_lei", "openfigi_ticker", "sec_ticker", "series_id", "issuer_id", "proxy",
               "security_master", "directory"}


def _reject(constant):
    raise ValueError(f"{constant} is not valid JSON for a browser")


def _load(name):
    return json.loads((FIXTURES / name).read_text(), parse_constant=_reject)


def _funds():
    return [_load(f"funds/{r[0]}.json") for r in _load("index.json")["rows"]]


def test_fixture_meta_and_index_follow_contract():
    meta, index = _load("meta.json"), _load("index.json")
    assert meta["api_version"] == 1
    for s in meta["sources"]:
        assert s["name"] and s["url"].startswith("https://") and s["licence"] and s["credit"], s["id"]
        assert set(s) == {"id", "name", "url", "licence", "credit", "n_funds", "as_of_min", "as_of_max"}
    assert "openfigi" in {s["id"] for s in meta["sources"]}
    assert index["fields"] == INDEX_FIELDS
    assert meta["n_funds"] == len(index["rows"])
    ids = {r[0] for r in index["rows"]}
    assert set(index["aliases"].values()) <= ids
    assert all(r[5] == "fund" for r in index["rows"])


def test_fixture_fund_files_follow_contract():
    listed = {s["id"]: s for s in _load("meta.json")["sources"]}
    for fund in _funds():
        fid = fund["id"]
        assert list(fund) == FUND_KEYS, fid
        assert fund["status"] in STATUSES and fund["unexpanded_fund_pct"] == 0.0, fid
        assert list(fund["unresolved"]) == ["weight_pct", "note", "source", "source_url", "as_of"], fid
        assert fund["fields"] == FUND_FIELDS
        assert fund["source"] in listed and fund["source_url"].startswith("https://") and fund["credit"]
        assert fund["matched_via"] in MATCHED_VIA | set(listed), fid
        rows = fund["constituents"]
        weights = [abs(c[2]) for c in rows]
        assert weights == sorted(weights, reverse=True)
        assert {c[4] for c in rows} <= set(listed)
        assert all(c[8] is None or isinstance(c[8], str) for c in rows)
        assert fund["unresolved"]["weight_pct"] == pytest.approx(100 - fund["resolved_pct"], abs=1e-4)
        assert fund["unresolved"].get("source", fund["source"]) == fund["source"]
        assert fund["unresolved"].get("source_url", fund["source_url"]) == fund["source_url"]
        assert fund["truncated"] == (fund["n_rows"] > len(rows))
        assert fund["n_constituents"] <= fund["n_rows"]
        assert fund["listed_pct"] == pytest.approx(sum(c[2] for c in rows), abs=1e-3)
        if not fund["truncated"]:
            assert fund["n_constituents"] == len({c[0] for c in rows})
            assert fund["listed_pct"] == pytest.approx(fund["resolved_pct"], abs=1e-3)


def test_fixture_rows_are_unique_per_isin_source_as_of_and_via():
    for fund in _funds():
        keys = [(c[0], c[4], c[5], c[7]) for c in fund["constituents"]]
        assert len(keys) == len(set(keys)), fund["id"]


def test_fixture_breakdowns_cover_the_full_holdings():
    for fund in _funds():
        b = fund["breakdowns"]
        assert list(b) == [*BREAKDOWNS, "top10_pct"], fund["id"]
        for key in BREAKDOWNS:
            entries = b[key]
            assert entries[-1] == ["Unresolved", pytest.approx(fund["unresolved_pct"], abs=1e-4)], (fund["id"], key)
            assert all(e[0] != "Unresolved" for e in entries[:-1])
            # Rows without a value are labelled "Unknown", never null.
            assert all(isinstance(e[0], str) and e[0] for e in entries), (fund["id"], key)
            values = [e[1] for e in entries[:-1]]
            assert values == sorted(values, reverse=True), (fund["id"], key)
            # Over every row of the fund, so resolved + unresolved, never rescaled.
            assert sum(e[1] for e in entries) == pytest.approx(100, abs=1e-2), (fund["id"], key)
        assert b["top10_pct"] <= fund["resolved_pct"] + 1e-6 or fund["resolved_pct"] < 0


def test_fixture_covers_the_cases_the_page_handles():
    index = _load("index.json")
    aliases = {k: v for k, v in index["aliases"].items() if k != v}
    assert aliases
    for isin, target in aliases.items():
        assert _load(f"funds/{isin}.json") == {"alias_of": target}
    funds = _funds()
    assert any(f["truncated"] for f in funds)
    assert any(f["isin"] is None and re.fullmatch(r"S\d{9}", f["id"]) for f in funds)
    assert any(c[6] > 0 for f in funds for c in f["constituents"])  # a fund of funds
    assert any(f["n_rows"] > f["n_constituents"] for f in funds)  # an ISIN reached by two routes
    assert any(f["note"] and "swap" in f["note"] for f in funds)
    assert any(f["unresolved_pct"] < 0 for f in funds)
    assert any(any(c[8] for c in f["constituents"]) and any(c[9] for c in f["constituents"]) for f in funds)


def test_fixture_no_holdings_fund():
    empty = [f for f in _funds() if f["n_rows"] == 0]
    assert empty
    for f in empty:
        assert f["constituents"] == [] and f["resolved_pct"] == 0 and f["unresolved_pct"] == 100
        assert f["as_of"] and f["note"] and "listed" in f["credit"].lower() and "no holdings" in f["credit"].lower()
        assert f["unresolved"] == {"weight_pct": 100, "note": f["note"], "source": f["source"], "source_url": f["source_url"],
                                   "as_of": f["as_of"]}
        assert f["status"] == "no_holdings"
        assert all(f["breakdowns"][k] == [["Unresolved", 100]] for k in BREAKDOWNS)


def test_fixture_nport_funds_name_their_registrant_and_tickerinside_says_what_changed():
    funds = _funds()
    assert all(f["issuer"] for f in funds if f["source"] == "nport")
    sources = {s["id"]: s for s in _load("meta.json")["sources"]}
    ti = sources["tickerinside"]["credit"]
    assert "creativecommons.org/licenses/by/4.0" in ti and "Changed" in ti
    for f in funds:
        if f["source"] == "tickerinside":
            assert "creativecommons.org/licenses/by/4.0" in f["credit"] and "Changed" in f["credit"]


# The page logic in site/assets/core.js, run with node.

NODE = shutil.which("node")


def _core(expr: str):
    """Evaluate ``expr`` with ``core`` bound to site/assets/core.js and return its JSON value."""
    script = f"const core = require({json.dumps(str(CORE_JS))}); process.stdout.write(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(out.stdout)


needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_core_only_routes_on_isin_or_series_hashes():
    assert _core('["#api", "#top", "#release", "", "#search"].map((h) => core.hashRoute(h, null))') == [None] * 5
    assert _core('core.hashRoute("#IE00B5BMR087", null)') == "IE00B5BMR087"
    assert _core('core.hashRoute("#ie00b5bmr087", null)') == "IE00B5BMR087"
    assert _core('core.hashRoute("#S000004406", "US4642898674")') == "S000004406"
    # The fund already shown is not reloaded.
    assert _core('core.hashRoute("#IE00B5BMR087", "IE00B5BMR087")') is None
    assert _core('core.hashRoute("#%E0%A4%A", null)') is None


@needs_node
def test_core_query_wins_over_hash():
    assert _core('core.idFromUrl("?isin=US4642898674", "#api")') == "US4642898674"
    assert _core('core.idFromUrl("", "#US4642898674")') == "US4642898674"
    assert _core('core.idFromUrl("", "#api")') is None


@needs_node
def test_core_moves_a_hash_fund_to_the_query_so_in_page_links_keep_it():
    assert _core('core.fundFromHash("", "#IE00B5BMR087")') is True
    assert _core('core.fundFromHash("?isin=US4642898674", "#IE00B5BMR087")') is False
    assert _core('core.fundFromHash("", "#api")') is False
    # Fresh load of /#CSPX: the address becomes ?isin=CSPX; a click on #api then keeps CSPX.
    steps = _core(
        """(() => {
            const shown = core.idFromUrl("", "#IE00B5BMR087");
            const href = new URL(core.fundHref("https://openbasket.example/#IE00B5BMR087", shown));
            href.hash = "api";
            return [shown, href.href, core.idFromUrl(href.search, href.hash)];
        })()"""
    )
    assert steps == ["IE00B5BMR087", "https://openbasket.example/?isin=IE00B5BMR087#api", "IE00B5BMR087"]
    # ?isin=AOR with #CSPX typed after it: CSPX replaces AOR in the query, so #api keeps CSPX.
    assert _core(
        'core.fundHref("https://openbasket.example/docs/?isin=US4642898674#IE00B5BMR087", '
        'core.hashRoute("#IE00B5BMR087", "US4642898674"))'
    ) == "https://openbasket.example/docs/?isin=IE00B5BMR087"


@needs_node
def test_core_totals_for_a_truncated_fund_account_for_the_cut_rows():
    fund = _load("funds/US4642898674.json")
    t = _core(f"(() => {{ const f = {json.dumps(fund)}; return core.totals(f, core.rowsOf(f)); }})()")
    assert t["truncated"] is True and t["nRows"] == fund["n_rows"]
    assert t["listed"] == pytest.approx(fund["listed_pct"])
    # listed + not listed + unresolved = 100: nothing is lost or rescaled.
    assert t["listed"] + t["notListed"] + t["unresolved"] == pytest.approx(100, abs=1e-3)
    rows = _core(f"(() => {{ const f = {json.dumps(fund)}; const r = core.rowsOf(f); return core.closingRows(f, r, core.totals(f, r)); }})()")
    assert [r["name"].split(":")[0] for r in rows] == ["Not listed in this file", "Unresolved weight"]
    assert rows[0]["weight"] == pytest.approx(fund["resolved_pct"] - fund["listed_pct"], abs=1e-6)


@needs_node
def test_core_totals_count_unexpanded_funds_as_listed_rows():
    fund = {"resolved_pct": 60.0, "unexpanded_fund_pct": 30.0, "unresolved_pct": 10.0, "n_rows": 3, "listed_pct": 80.0,
            "truncated": True, "fields": ["isin", "weight_pct"], "constituents": [["US0378331005", 50.0], ["US78462F1030", 30.0]],
            "unresolved": {"weight_pct": 10.0}}
    t = _core(f"(() => {{ const f = {json.dumps(fund)}; return core.totals(f, core.rowsOf(f)); }})()")
    assert t["unexpanded"] == 30 and t["inRows"] == 90 and t["notListed"] == pytest.approx(10)
    # A 1.0 file without the key: nothing unexpanded.
    del fund["unexpanded_fund_pct"]
    assert _core(f"(() => {{ const f = {json.dumps(fund)}; return core.totals(f, core.rowsOf(f)).unexpanded; }})()") == 0


@needs_node
def test_core_totals_for_a_complete_and_an_empty_fund():
    full = _load("funds/US78467X1090.json")
    t = _core(f"(() => {{ const f = {json.dumps(full)}; return core.totals(f, core.rowsOf(f)); }})()")
    assert t["truncated"] is False and t["notListed"] == 0 and t["noHoldings"] is False
    rows = _core(f"(() => {{ const f = {json.dumps(full)}; const r = core.rowsOf(f); return core.closingRows(f, r, core.totals(f, r)); }})()")
    assert [r["name"] for r in rows] == ["Unresolved weight"]
    empty = _load("funds/IE000TOBHQ26.json")
    t = _core(f"(() => {{ const f = {json.dumps(empty)}; return core.totals(f, core.rowsOf(f)); }})()")
    assert t["noHoldings"] is True and t["unresolved"] == 100


@needs_node
def test_core_rows_are_read_by_field_name():
    fund = {"source": "ishares", "as_of": "2026-11-02", "fields": ["name", "isin", "weight_pct", "country"],
            "constituents": [["APPLE", "US0378331005", 7.5, "US"]]}
    r = _core(f"core.rowsOf({json.dumps(fund)})[0]")
    assert (r["isin"], r["name"], r["weight"], r["country"], r["source"], r["as_of"], r["sector"]) == (
        "US0378331005", "APPLE", 7.5, "US", "ishares", "2026-11-02", "")


@needs_node
def test_core_chart_entries_keep_unresolved_last_and_group_the_tail():
    pairs = [["Unresolved", 2.0]] + [[f"L{i}", float(i)] for i in range(1, 13)]
    out = _core(f'core.chartEntries({json.dumps(pairs)}, 99, null, 8)')
    shown, everything = out["shown"], out["all"]
    assert [e["label"] for e in shown[:8]] == [f"L{i}" for i in range(12, 4, -1)]
    assert shown[8]["other"] and shown[8]["label"] == "4 others" and shown[8]["value"] == pytest.approx(10)
    assert shown[-1] == {"key": "Unresolved", "label": "Unresolved", "value": 2.0, "unresolved": True}
    assert len(everything) == 13 and everything[-1]["unresolved"]
    assert sum(e["value"] for e in shown) == pytest.approx(sum(p[1] for p in pairs))
    # Unresolved is never dropped: without an entry it comes from the fund's figure, negative included.
    out = _core('core.chartEntries([["US", 101.3]], -1.3, null, 8)')
    assert out["shown"][-1]["value"] == -1.3 and out["shown"][-1]["unresolved"]


@needs_node
def test_core_local_breakdowns_label_missing_values_unknown():
    rows = [{"isin": "A", "name": "a", "weight": 60.0, "asset": "equity", "country": "US", "sector": "", "source": "x"},
            {"isin": "B", "name": "b", "weight": 39.0, "asset": "equity", "country": "", "sector": "", "source": "x"}]
    out = _core(f"core.localBreakdowns({json.dumps(rows)}, 1.0)")
    assert out["country"] == [["US", 60.0], ["Unknown", 39.0], ["Unresolved", 1.0]]
    assert out["sector"] == [["Unknown", 99.0], ["Unresolved", 1.0]]
    assert out["top10_pct"] == 99.0


@needs_node
def test_core_top_holdings_add_up_an_isin_across_routes():
    rows = [{"isin": "A", "name": "a", "weight": 1.0}, {"isin": "B", "name": "b", "weight": 1.5},
            {"isin": "A", "name": "a", "weight": 1.0}, {"isin": None, "name": "cash", "weight": 9.0}]
    out = _core(f"core.topHoldings({json.dumps(rows)}, 10)")
    assert [(x["isin"], x["value"]) for x in out] == [("A", 2.0), ("B", 1.5)]


@needs_node
def test_core_top_ten_matches_the_fixture_breakdown():
    for fid in ("US4642898674", "IE00B4L5Y983", "US78462F1030"):
        fund = _load(f"funds/{fid}.json")
        top = _core(f"(() => {{ const f = {json.dumps(fund)}; return core.topHoldings(core.rowsOf(f), 10); }})()")
        assert sum(x["value"] for x in top) == pytest.approx(fund["breakdowns"]["top10_pct"], abs=1e-3), fid


@needs_node
def test_core_matched_via_covers_the_contract_enum():
    texts = _core(f'{json.dumps(sorted(MATCHED_VIA - {"ishares", "spdr"}))}.map((v) => core.matchedVia(v))')
    assert all(t and "fund list" not in t for t in texts)
    assert _core('core.matchedVia("xtrackers", (v) => "Xtrackers")') == "the fund's ISIN in the Xtrackers fund list"
    assert _core('core.matchedVia(null)') == "–"


@needs_node
def test_core_csv_cells_are_safe():
    out = _core('["=1+1", "-2", "a,b", "say \\"hi\\"", 1.23456789, NaN, null, true].map(core.csvCell)')
    assert out == ["'=1+1", "'-2", '"a,b"', '"say ""hi"""', "1.234568", "", "", "true"]


@needs_node
def test_core_link_parts_leave_trailing_punctuation_out():
    parts = _core('core.linkParts("CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Changed.")')
    assert parts == [{"text": "CC BY 4.0 ("}, {"url": "https://creativecommons.org/licenses/by/4.0/"}, {"text": "). Changed."}]
    assert _core('core.linkParts("javascript:alert(1)")') == [{"text": "javascript:alert(1)"}]


@needs_node
def test_page_scripts_parse():
    for name in ("core.js", "app.js"):
        subprocess.run([NODE, "--check", str(SITE / "assets" / name)], check=True, timeout=30)
