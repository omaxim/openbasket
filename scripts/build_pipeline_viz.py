"""Build the static Kedro-Viz pipeline diagram into its own folder.

``kedro viz build`` always writes to ``./build`` in the project, which would
mix its files with ``build/site``, ``build/release`` and ``build/pages``. This
script runs it in a throwaway copy of the project (``pyproject.toml``, ``src``
and ``conf/base`` only, so no local config or credentials are read), drops the
development-only files, rewrites the copy's absolute paths (Kedro-Viz
resolves every dataset's ``filepath`` inside the throwaway folder) back to
project-relative ones such as ``data/01_raw/...``, refuses output that
carries a tracking script, and copies the result to ``--out``::

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


def strip_staging_paths(build: Path, work: Path) -> int:
    """Rewrite ``<work>/data/...`` to ``data/...`` in every text file; returns how many files changed.

    Both the path as given and its resolved form are replaced, since macOS
    reports ``/var/...`` temp folders as ``/private/var/...``, and so is the
    folder's bare name, which Kedro-Viz puts before node source files
    (``openbasket-viz-x/src/...``).
    """
    work = Path(work)
    prefixes = sorted({f"{p}{os.sep}" for p in (str(work), str(work.resolve()), work.name)}, key=len, reverse=True)
    changed = 0
    for f in build.rglob("*"):
        if not f.is_file():
            continue
        try:
            text = f.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        new = text
        for prefix in prefixes:
            new = new.replace(prefix, "").replace(prefix.replace("/", "\\/"), "")
        if new != text:
            f.write_text(new)
            changed += 1
    return changed


def clean_build(build: Path, work: Path | None = None) -> None:
    """Remove development-only files, strip the staging folder from paths and fail on a tracking script."""
    if not (build / "index.html").is_file():
        raise FileNotFoundError(f"kedro viz build wrote no index.html in {build}")
    for name in DEV_ONLY:
        p = build / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    if work is not None:
        strip_staging_paths(build, work)
        left = [f for f in build.rglob("*") if f.is_file() and _mentions(f, work)]
        if left:
            raise RuntimeError(f"{left[0].relative_to(build)} still names the staging folder {work}")
    for page in build.rglob("*.html"):
        text = page.read_text(errors="replace").lower()
        if any(m in text for m in TRACKING_MARKERS):
            raise RuntimeError(f"{page.relative_to(build)} carries a tracking script; is telemetry turned off?")


def _mentions(f: Path, work: Path) -> bool:
    try:
        text = f.read_text()
    except (UnicodeDecodeError, OSError):
        return False
    return Path(work).name in text


def build_viz(out: Path, project: Path = ROOT, kedro: str = "kedro") -> Path:
    out = Path(out).resolve()
    env = {**os.environ, "KEDRO_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"}
    with tempfile.TemporaryDirectory(prefix="openbasket-viz-") as tmp:
        work = stage_project(Path(project), Path(tmp))
        subprocess.run([kedro, "viz", "build"], cwd=work, env=env, check=True)
        build = work / "build"
        clean_build(build, work)
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
