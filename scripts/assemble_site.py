"""Combine the landing page, the Sphinx HTML and the JSON API into one directory.

The result is what GitHub Pages serves at https://openbasket.leithresearch.org/:

    out/            the landing page (site/, without its fixtures)
    out/docs/       the Sphinx HTML
    out/api/v1/     the static JSON API (docs/source/data-api.md)
    out/pipeline/   the Kedro-Viz build of the pipelines, when --pipeline is given
    out/CNAME       the custom domain
    out/.nojekyll   so Pages serves folders starting with "_" (Sphinx's _static)

Usage::

    python scripts/assemble_site.py --docs docs/build/html --api build/site/api --out build/pages \\
        [--pipeline build/pipeline]

``out`` is deleted and rewritten, so it is checked first: it must not be or
hold any input, inside the repository it must be under ``build/``, and an
existing non-empty folder is replaced only if an earlier run of this script
wrote it (it holds the marker file ``.nojekyll`` and a ``CNAME``).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
BUILD = ROOT / "build"
DOMAIN = "openbasket.leithresearch.org"
#: Files every assembled site has; their presence marks a folder this script may replace.
MARKERS = ("CNAME", ".nojekyll")


def _ignore_site(root: Path):
    """Leave out development-only files; fixtures stand in for the real API locally."""

    def ignore(directory: str, names: list[str]) -> set[str]:
        skip = {n for n in names if n == ".DS_Store"}
        if Path(directory).resolve() == root.resolve():
            skip |= {n for n in names if n == "fixtures"}
        return skip

    return ignore


def _within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _check_out(out: Path, inputs: list[Path]) -> None:
    """Refuse an ``out`` whose deletion could lose anything but an earlier assembled site."""
    target = out.resolve()
    if target == Path(target.anchor) or target == Path.home().resolve():
        raise ValueError(f"--out {out} would overwrite {target}")
    for src in inputs:
        s = src.resolve()
        # out may neither hold an input (deleting it would lose the input) nor sit inside one.
        if _within(s, target) or _within(target, s):
            raise ValueError(f"--out {out} would overwrite {src}")
    root, build = ROOT.resolve(), BUILD.resolve()
    if _within(root, target):
        raise ValueError(f"--out {out} would overwrite {ROOT}")
    if _within(target, root) and (target == build or not _within(target, build)):
        raise ValueError(f"--out {out} would overwrite part of the repository; use a folder under {BUILD}")
    if target.exists():
        if not target.is_dir():
            raise ValueError(f"--out {out} is a file, not a folder")
        if any(target.iterdir()) and not all((target / m).is_file() for m in MARKERS):
            raise ValueError(
                f"--out {out} is not empty and was not written by this script (no {' or '.join(MARKERS)}); "
                "remove it yourself or choose another folder"
            )


def _check_inputs(site: Path, docs: Path, api: Path, out: Path, pipeline: Path | None = None) -> None:
    if not (site / "index.html").is_file():
        raise FileNotFoundError(f"No landing page at {site / 'index.html'}")
    if not (docs / "index.html").is_file():
        raise FileNotFoundError(f"No Sphinx HTML at {docs} (build it with sphinx-build first)")
    if not (api / "v1" / "meta.json").is_file():
        raise FileNotFoundError(f"No API at {api}: expected {api / 'v1' / 'meta.json'} (pass the folder that holds v1/)")
    if pipeline is not None and not (pipeline / "index.html").is_file():
        raise FileNotFoundError(f"No Kedro-Viz build at {pipeline}: expected {pipeline / 'index.html'} (kedro viz build)")
    _check_out(out, [p for p in (site, docs, api, pipeline) if p is not None])


def assemble(docs: Path, api: Path, out: Path, site: Path = SITE, domain: str = DOMAIN,
             pipeline: Path | None = None) -> Path:
    """Write the deployable site to ``out``, replacing an earlier assembled site there."""
    docs, api, out, site = Path(docs), Path(api), Path(out), Path(site)
    pipeline = Path(pipeline) if pipeline is not None else None
    _check_inputs(site, docs, api, out, pipeline)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(site, out, ignore=_ignore_site(site))
    shutil.copytree(docs, out / "docs", ignore=shutil.ignore_patterns(".buildinfo", ".doctrees", ".DS_Store"))
    shutil.copytree(api, out / "api", ignore=shutil.ignore_patterns(".DS_Store"))
    if pipeline is not None:
        shutil.copytree(pipeline, out / "pipeline", ignore=shutil.ignore_patterns(".DS_Store"))
    (out / "CNAME").write_text(f"{domain}\n")
    (out / ".nojekyll").write_text("")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--docs", type=Path, required=True, help="Sphinx HTML output, e.g. docs/build/html")
    parser.add_argument("--api", type=Path, required=True, help="folder holding v1/, e.g. build/site/api")
    parser.add_argument("--out", type=Path, required=True, help="directory to write, e.g. build/pages")
    parser.add_argument("--pipeline", type=Path, default=None, help="Kedro-Viz build to serve at /pipeline/ (optional)")
    parser.add_argument("--site", type=Path, default=SITE, help="landing page source (default: site/)")
    parser.add_argument("--domain", default=DOMAIN, help=f"custom domain for CNAME (default: {DOMAIN})")
    args = parser.parse_args(argv)
    try:
        out = assemble(args.docs, args.api, args.out, site=args.site, domain=args.domain, pipeline=args.pipeline)
    except (FileNotFoundError, ValueError) as exc:
        print(f"assemble_site: {exc}", file=sys.stderr)
        return 1
    n = sum(1 for p in out.rglob("*") if p.is_file())
    print(f"Assembled {n} files in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
