import importlib.util
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("serve", ROOT / "scripts" / "serve.py")
serve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve)


@pytest.fixture
def inputs(tmp_path):
    docs = tmp_path / "html"
    docs.mkdir()
    (docs / "index.html").write_text("<html>docs</html>")
    api = tmp_path / "api"
    (api / "v1" / "funds").mkdir(parents=True)
    (api / "v1" / "meta.json").write_text('{"api_version": 1}')
    (api / "v1" / "funds" / "IE00B5BMR087.json").write_text('{"isin": "IE00B5BMR087"}')
    return docs, api, tmp_path / "pages-local"


@pytest.fixture
def server(inputs):
    docs, api, out = inputs
    serve._load_assemble_site().assemble(docs, api, out, domain="localhost")
    srv = serve.make_server(out, "127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_serves_landing_page_docs_and_api(server):
    status, headers, body = _get(f"{server}/")
    assert status == 200 and headers["Content-Type"].startswith("text/html") and b"openbasket" in body
    status, _, body = _get(f"{server}/docs/")
    assert status == 200 and body == b"<html>docs</html>"
    status, headers, body = _get(f"{server}/api/v1/meta.json")
    assert status == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert json.loads(body) == {"api_version": 1}
    status, headers, body = _get(f"{server}/api/v1/funds/IE00B5BMR087.json")
    assert status == 200 and headers["Content-Type"].startswith("application/json")
    assert json.loads(body)["isin"] == "IE00B5BMR087"
    status, headers, _ = _get(f"{server}/assets/app.js")
    assert status == 200 and headers["Content-Type"].startswith("text/javascript")


def test_missing_file_gets_site_404_page_and_folders_are_not_listed(server):
    status, headers, body = _get(f"{server}/api/v1/funds/XX0000000000.json")
    assert status == 404 and b"Nothing in this" in body and headers["Cache-Control"] == "no-store"
    status, _, body = _get(f"{server}/api/v1/funds/")
    assert status == 404 and b"IE00B5BMR087" not in body


def test_refuses_to_start_without_an_api(tmp_path, capsys):
    assert serve.main(["--api", str(tmp_path / "none"), "--out", str(tmp_path / "out")]) == 1
    assert "kedro run --pipeline=full" in capsys.readouterr().err
    assert serve.main(["--out", str(tmp_path / "out"), "--no-assemble"]) == 1


def test_full_pipeline_runs_everything_but_the_single_isin_lookthrough():
    from kedro.framework.project import configure_project, pipelines

    configure_project("openbasket")
    full = pipelines["full"]
    names = {n.name for n in full.nodes}
    for node in ("download_nport", "nport_to_parquet", "download_issuer_products", "security_master",
                 "fund_master", "nport_holdings", "build_universe", "run_universe", "publish"):
        assert node in names
    assert "run_lookthrough" not in names and "parse_isins" not in names
    order = [n.name for n in full.nodes]  # topologically sorted
    assert order.index("download_nport") < order.index("security_master") < order.index("run_universe")
    assert order.index("fund_master") < order.index("build_universe") < order.index("publish")
    default = {n.name for n in pipelines["__default__"].nodes}
    assert "run_universe" not in default and "publish" not in default


def test_list_sources_shows_redistribution_class_and_use():
    spec = importlib.util.spec_from_file_location("list_sources", ROOT / "scripts" / "list_sources.py")
    list_sources = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(list_sources)
    rows = {r[0]: r for r in list_sources.rows("public")}
    assert rows["ishares"][1] == "local_only" and rows["spdr"][1] == "local_only"
    assert rows["cnmv_spain"][1:3] == ("public", "on")
    assert rows["cvm_brazil"][1] == "public"
