"""Build the static Kedro-Viz pipeline diagram into its own folder.

``kedro viz build`` always writes to ``./build`` in the project, which would
mix its files with ``build/site``, ``build/release`` and ``build/pages``. This
script runs it in a throwaway copy of the project (``pyproject.toml``, ``src``
and ``conf/base`` only, so no local config or credentials are read), drops the
development-only files, refuses output that carries a tracking script, and
copies the result to ``--out``::

    python scripts/build_pipeline_viz.py --out build/pages/pipeline

Needs the ``viz`` extra: ``pip install -e '.[viz]'``. Telemetry is turned off
for the run (``KEDRO_DISABLE_TELEMETRY`` and ``DO_NOT_TRACK``); with it on,
Kedro-Viz injects Heap analytics into the page it writes.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: Written by ``kedro viz build`` but not needed to serve the diagram.
DEV_ONLY = (".vite", "autoreload.html", "telemetry.html")
TRACKING_MARKERS = ("heapanalytics", "heap.load(")


def stage_project(project: Path, work: Path) -> Path:
    """Copy what Kedro needs to load the project's pipelines into ``work``."""
    shutil.copy2(project / "pyproject.toml", work / "pyproject.toml")
    shutil.copytree(project / "src", work / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    shutil.copytree(project / "conf" / "base", work / "conf" / "base")
    (work / "conf" / "local").mkdir(parents=True, exist_ok=True)
    return work


def clean_build(build: Path) -> None:
    """Remove development-only files and fail on a tracking script."""
    if not (build / "index.html").is_file():
        raise FileNotFoundError(f"kedro viz build wrote no index.html in {build}")
    for name in DEV_ONLY:
        p = build / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    for page in build.rglob("*.html"):
        text = page.read_text(errors="replace").lower()
        if any(m in text for m in TRACKING_MARKERS):
            raise RuntimeError(f"{page.relative_to(build)} carries a tracking script; is telemetry turned off?")


def build_viz(out: Path, project: Path = ROOT, kedro: str = "kedro") -> Path:
    out = Path(out).resolve()
    env = {**os.environ, "KEDRO_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"}
    with tempfile.TemporaryDirectory(prefix="openbasket-viz-") as tmp:
        work = stage_project(Path(project), Path(tmp))
        subprocess.run([kedro, "viz", "build"], cwd=work, env=env, check=True)
        build = work / "build"
        clean_build(build)
        if out.exists():
            shutil.rmtree(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(build, out)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", type=Path, required=True, help="folder to write, e.g. build/pages/pipeline")
    beside = Path(sys.executable).parent / "kedro"
    default = str(beside) if beside.is_file() else (shutil.which("kedro") or "kedro")
    p.add_argument("--kedro", default=default, help="kedro executable (default: the one beside this Python)")
    args = p.parse_args(argv)
    try:
        out = build_viz(args.out, kedro=args.kedro)
    except (subprocess.CalledProcessError, FileNotFoundError, RuntimeError) as exc:
        print(f"build_pipeline_viz: {exc}", file=sys.stderr)
        return 1
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    n = sum(1 for f in out.rglob("*") if f.is_file())
    print(f"Pipeline diagram: {n} files, {size / 1e6:.1f} MB in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
