import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
FIXTURES = SITE / "fixtures" / "api" / "v1"

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
    assert (out / "assets" / "app.css").is_file()
    assert (out / "docs" / "index.html").read_text() == "<html>docs</html>"
    assert (out / "docs" / "_static" / "openbasket.css").is_file()
    assert (out / "api" / "v1" / "meta.json").is_file()
    assert (out / "api" / "v1" / "funds" / "IE00B5BMR087.json").is_file()
    assert (out / "CNAME").read_text() == "openbasket.leithresearch.org\n"
    assert (out / ".nojekyll").exists()


def test_assemble_leaves_out_fixtures_and_build_files(inputs):
    docs, api, out = inputs
    assemble_site.assemble(docs, api, out)
    assert not (out / "fixtures").exists()
    assert not (out / "docs" / ".buildinfo").exists()


def test_assemble_replaces_previous_output(inputs):
    docs, api, out = inputs
    (out / "stale").mkdir(parents=True)
    (out / "stale" / "old.json").write_text("{}")
    assemble_site.assemble(docs, api, out)
    assert not (out / "stale").exists()


def test_assemble_wants_the_folder_that_holds_v1(inputs):
    docs, api, out = inputs
    with pytest.raises(FileNotFoundError, match="v1"):
        assemble_site.assemble(docs, api / "v1", out)


def test_assemble_wants_built_docs(inputs, tmp_path):
    _, api, out = inputs
    with pytest.raises(FileNotFoundError, match="sphinx-build"):
        assemble_site.assemble(tmp_path / "missing", api, out)


@pytest.mark.parametrize("target", ["docs", "api", "parent"])
def test_assemble_refuses_to_overwrite_its_inputs(inputs, target):
    docs, api, _ = inputs
    out = {"docs": docs, "api": api, "parent": docs.parent}[target]
    with pytest.raises(ValueError, match="overwrite"):
        assemble_site.assemble(docs, api, out)
    assert (docs / "index.html").exists()


def test_cli(inputs, capsys):
    docs, api, out = inputs
    assert assemble_site.main(["--docs", str(docs), "--api", str(api), "--out", str(out), "--domain", "example.test"]) == 0
    assert (out / "CNAME").read_text() == "example.test\n"
    assert "Assembled" in capsys.readouterr().out
    assert assemble_site.main(["--docs", str(docs / "nope"), "--api", str(api), "--out", str(out)]) == 1


def test_assemble_with_fixture_api(tmp_path, inputs):
    docs, _, out = inputs
    assemble_site.assemble(docs, FIXTURES.parent, out)
    assert (out / "api" / "v1" / "index.json").is_file()


def test_landing_page_references_existing_assets():
    html = (SITE / "index.html").read_text()
    assert 'data-api="api/v1"' in html
    for ref in re.findall(r'(?:href|src)="(assets/[^"?#]+)', html):
        assert (SITE / ref).is_file(), ref
    assert "not investment advice" in html
    assert "not affiliated" in html
    assert "https://github.com/omaxim/openbasket" in html


# The fixtures stand in for the real build, so they must keep to data-api.md.

INDEX_FIELDS = ["id", "isin", "name", "ticker", "issuer", "kind", "source", "as_of", "resolved_pct", "n_constituents"]
FUND_FIELDS = ["isin", "name", "weight_pct", "asset_class", "source", "as_of", "depth", "via"]
FUND_KEYS = {
    "id", "isin", "isins", "name", "ticker", "issuer", "source", "source_url", "credit", "as_of", "matched_via",
    "note", "reported_total_pct", "resolved_pct", "unresolved_pct", "unidentified_lines_pct", "n_constituents",
    "truncated", "fields", "constituents", "unresolved", "full_data_url",
}
SOURCES = {"ishares", "spdr", "tickerinside", "nport", "security_master"}


def _reject(constant):
    raise ValueError(f"{constant} is not valid JSON for a browser")


def _load(name):
    return json.loads((FIXTURES / name).read_text(), parse_constant=_reject)


def test_fixture_meta_and_index_follow_contract():
    meta, index = _load("meta.json"), _load("index.json")
    assert meta["api_version"] == 1
    assert {s["id"] for s in meta["sources"]} <= SOURCES
    for s in meta["sources"]:
        assert s["name"] and s["url"].startswith("https://") and s["licence"] and s["credit"]
    assert index["fields"] == INDEX_FIELDS
    assert meta["n_funds"] == len(index["rows"])
    ids = {r[0] for r in index["rows"]}
    assert set(index["aliases"].values()) <= ids
    assert all(r[5] == "fund" for r in index["rows"])


def test_fixture_fund_files_follow_contract():
    meta, index = _load("meta.json"), _load("index.json")
    listed = {s["id"] for s in meta["sources"]}
    for row in index["rows"]:
        fund = _load(f"funds/{row[0]}.json")
        assert set(fund) == FUND_KEYS, row[0]
        assert fund["fields"] == FUND_FIELDS
        assert fund["source"] in listed and fund["source_url"].startswith("https://") and fund["credit"]
        weights = [abs(c[2]) for c in fund["constituents"]]
        assert weights == sorted(weights, reverse=True)
        assert {c[4] for c in fund["constituents"]} <= listed
        assert fund["unresolved"]["weight_pct"] == pytest.approx(100 - fund["resolved_pct"], abs=1e-4)
        assert fund["truncated"] == (fund["n_constituents"] > len(fund["constituents"]))


def test_fixture_covers_alias_truncated_and_series_cases():
    index = _load("index.json")
    aliases = {k: v for k, v in index["aliases"].items() if k != v}
    assert aliases
    for isin, target in aliases.items():
        assert _load(f"funds/{isin}.json") == {"alias_of": target}
    funds = [_load(f"funds/{r[0]}.json") for r in index["rows"]]
    assert any(f["truncated"] for f in funds)
    assert any(f["isin"] is None and re.fullmatch(r"S\d{9}", f["id"]) for f in funds)
    assert any(c[6] > 0 for f in funds for c in f["constituents"])
