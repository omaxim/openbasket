import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_pipeline_viz", ROOT / "scripts" / "build_pipeline_viz.py")
viz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(viz)


def _fake_build(path: Path, head: str = "") -> Path:
    (path / "api").mkdir(parents=True)
    (path / ".vite").mkdir()
    (path / ".vite" / "manifest.json").write_text("{}")
    (path / "index.html").write_text(f"<html><head>{head}</head></html>")
    (path / "telemetry.html").write_text("<script>heap.load(1)</script>")
    (path / "autoreload.html").write_text("<html></html>")
    (path / "api" / "main").write_text("{}")
    return path


def test_clean_build_drops_dev_files(tmp_path):
    build = _fake_build(tmp_path / "build")
    viz.clean_build(build)
    assert sorted(p.name for p in build.iterdir()) == ["api", "index.html"]


def test_clean_build_refuses_tracking(tmp_path):
    build = _fake_build(tmp_path / "build", head='<script src="https://cdn.heapanalytics.com/js/heap-1.js"></script>')
    with pytest.raises(RuntimeError, match="tracking"):
        viz.clean_build(build)


def test_clean_build_needs_index(tmp_path):
    (tmp_path / "build").mkdir()
    with pytest.raises(FileNotFoundError):
        viz.clean_build(tmp_path / "build")


def test_stage_project_copies_base_conf_only(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    viz.stage_project(ROOT, work)
    assert (work / "pyproject.toml").is_file()
    assert (work / "src" / "openbasket" / "settings.py").is_file()
    assert (work / "conf" / "base" / "catalog.yml").is_file()
    assert (work / "conf" / "local").is_dir() and not any((work / "conf" / "local").iterdir())


def test_build_viz_writes_out_and_keeps_project_build_untouched(tmp_path, monkeypatch):
    """``kedro viz build`` writes ./build in its cwd: that must be the temp copy, not the project."""
    calls = []

    def fake_run(cmd, cwd, env, check):
        calls.append((cmd, Path(cwd), env))
        _fake_build(Path(cwd) / "build")

    monkeypatch.setattr(viz.subprocess, "run", fake_run)
    out = viz.build_viz(tmp_path / "pages" / "pipeline", kedro="kedro")
    (cmd, cwd, env), = calls
    assert cmd == ["kedro", "viz", "build"]
    assert cwd != ROOT and not cwd.exists()
    assert env["KEDRO_DISABLE_TELEMETRY"] == "1" and env["DO_NOT_TRACK"] == "1"
    assert (out / "index.html").is_file() and not (out / "telemetry.html").exists()


def test_clean_build_strips_the_staging_folder_from_dataset_paths(tmp_path):
    work = tmp_path / "openbasket-viz-abc"
    build = _fake_build(work / "build")
    (build / "api" / "nodes").mkdir()
    (build / "api" / "nodes" / "universe").write_text(
        f'{{"filepath": "{work}/data/07_model_output/universe", "other": "{work.resolve()}/data/01_raw/x"}}'
    )
    (build / "api" / "main").write_text(f'{{"filepath": "{work}/data/03_primary/universe.parquet"}}')
    (build / "api" / "nodes" / "node1").write_text(f'{{"filepath": "{work.name}/src/openbasket/pipelines/x/nodes.py"}}')
    viz.clean_build(build, work)
    assert (build / "api" / "nodes" / "node1").read_text() == '{"filepath": "src/openbasket/pipelines/x/nodes.py"}'
    assert '"filepath": "data/07_model_output/universe"' in (build / "api" / "nodes" / "universe").read_text()
    assert (build / "api" / "main").read_text() == '{"filepath": "data/03_primary/universe.parquet"}'
    leftover = [f for f in build.rglob("*") if f.is_file() and work.name in f.read_text()]
    assert leftover == []


def test_build_viz_output_never_names_the_staging_folder(tmp_path, monkeypatch):
    def fake_run(cmd, cwd, env, check):
        b = _fake_build(Path(cwd) / "build")
        (b / "api" / "main").write_text(f'{{"filepath": "{cwd}/data/01_raw/issuers/issuer_products.parquet"}}')

    monkeypatch.setattr(viz.subprocess, "run", fake_run)
    out = viz.build_viz(tmp_path / "pipeline", kedro="kedro")
    text = (out / "api" / "main").read_text()
    assert text == '{"filepath": "data/01_raw/issuers/issuer_products.parquet"}'
    assert "openbasket-viz-" not in text
