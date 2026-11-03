"""Combine the landing page, the Sphinx HTML and the JSON API into one directory.

The result is what GitHub Pages serves at https://openbasket.leithresearch.org/:

    out/            the landing page (site/, without its fixtures)
    out/docs/       the Sphinx HTML
    out/api/v1/     the static JSON API (docs/source/data-api.md)
    out/CNAME       the custom domain
    out/.nojekyll   so Pages serves folders starting with "_" (Sphinx's _static)

Usage::

    python scripts/assemble_site.py --docs docs/build/html --api build/site/api --out build/pages
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
DOMAIN = "openbasket.leithresearch.org"


def _ignore_site(root: Path):
    """Leave out development-only files; fixtures stand in for the real API locally."""

    def ignore(directory: str, names: list[str]) -> set[str]:
        skip = {n for n in names if n == ".DS_Store"}
        if Path(directory).resolve() == root.resolve():
            skip |= {n for n in names if n == "fixtures"}
        return skip

    return ignore


def _check_inputs(site: Path, docs: Path, api: Path, out: Path) -> None:
    if not (site / "index.html").is_file():
        raise FileNotFoundError(f"No landing page at {site / 'index.html'}")
    if not (docs / "index.html").is_file():
        raise FileNotFoundError(f"No Sphinx HTML at {docs} (build it with sphinx-build first)")
    if not (api / "v1" / "meta.json").is_file():
        raise FileNotFoundError(f"No API at {api}: expected {api / 'v1' / 'meta.json'} (pass the folder that holds v1/)")
    target = out.resolve()
    for src in (site, docs, api, ROOT):
        s = src.resolve()
        if target == s or target in s.parents:
            raise ValueError(f"--out {out} would overwrite {src}")


def assemble(docs: Path, api: Path, out: Path, site: Path = SITE, domain: str = DOMAIN) -> Path:
    """Write the deployable site to ``out``, replacing anything already there."""
    docs, api, out, site = Path(docs), Path(api), Path(out), Path(site)
    _check_inputs(site, docs, api, out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(site, out, ignore=_ignore_site(site))
    shutil.copytree(docs, out / "docs", ignore=shutil.ignore_patterns(".buildinfo", ".doctrees", ".DS_Store"))
    shutil.copytree(api, out / "api", ignore=shutil.ignore_patterns(".DS_Store"))
    (out / "CNAME").write_text(f"{domain}\n")
    (out / ".nojekyll").write_text("")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--docs", type=Path, required=True, help="Sphinx HTML output, e.g. docs/build/html")
    parser.add_argument("--api", type=Path, required=True, help="folder holding v1/, e.g. build/site/api")
    parser.add_argument("--out", type=Path, required=True, help="directory to write, e.g. build/pages")
    parser.add_argument("--site", type=Path, default=SITE, help="landing page source (default: site/)")
    parser.add_argument("--domain", default=DOMAIN, help=f"custom domain for CNAME (default: {DOMAIN})")
    args = parser.parse_args(argv)
    try:
        out = assemble(args.docs, args.api, args.out, site=args.site, domain=args.domain)
    except (FileNotFoundError, ValueError) as exc:
        print(f"assemble_site: {exc}", file=sys.stderr)
        return 1
    n = sum(1 for p in out.rglob("*") if p.is_file())
    print(f"Assembled {n} files in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
