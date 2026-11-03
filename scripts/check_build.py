"""Check a publish build before anything is released or deployed.

Run by ``.github/workflows/publish.yml`` after ``scripts/assemble_site.py``::

    python scripts/check_build.py --api build/site/api --release build/release \\
        --pages build/pages --min-funds 10000 \\
        --summary "$GITHUB_STEP_SUMMARY" --notes build/release-notes.md

It fails (exit 1) when:

- fewer funds were published than ``--min-funds``;
- a source is missing from ``meta.json`` or lacks a name, link, licence or
  credit line: every source id used by a fund or a constituent row (and by an
  ``unresolved`` entry that names one) must be listed in ``meta.sources``;
- a fund file or a constituent row has no source or no as-of date;
- TickerInside is credited without a link to the CC BY 4.0 licence;
- the assembled site is over GitHub Pages' 1 GB limit;
- a page carries a tracking script (Kedro-Viz can inject Heap analytics).

It warns when the site is near the limit, when the newest N-PORT holdings are
old, or when ``nport.quarters`` lacks the latest quarter SEC should have out.
Errors and warnings are printed as GitHub annotations and listed in the job
summary. The release notes are written from ``meta.json`` either way.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

#: GitHub Pages refuses a published site larger than this.
PAGES_LIMIT_BYTES = 1_000_000_000
#: Warn from here, so a growing universe is noticed before deploys fail.
PAGES_WARN_BYTES = 900_000_000
#: N-PORT holdings are reported monthly and released quarterly, with a lag of
#: two months or so; older than this means a quarter was probably not added.
NPORT_STALE_DAYS = 150
#: SEC posts a quarter's N-PORT data sets some weeks after the quarter ends.
NPORT_PUBLISH_LAG_DAYS = 60
#: Sources that supply names or identifiers rather than dated holdings.
UNDATED_SOURCES = {"openfigi"}
CC_BY_URL = "creativecommons.org/licenses/by/4.0"
TRACKING_MARKERS = ("heapanalytics", "heap.load(", "googletagmanager", "google-analytics.com")
REQUIRED_SOURCE_FIELDS = ("name", "url", "licence", "credit")


class Findings:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def _blank(v: object) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


# --- sources -----------------------------------------------------------------


def check_meta_sources(meta: dict, found: Findings) -> dict[str, dict]:
    """Every ``meta.sources`` entry must name the source, link to it, and give its licence and credit."""
    sources = meta.get("sources")
    if not isinstance(sources, list) or not sources:
        found.error("meta.json lists no sources; every published row must name where it came from.")
        return {}
    by_id: dict[str, dict] = {}
    for i, s in enumerate(sources):
        sid = s.get("id") if isinstance(s, dict) else None
        if _blank(sid):
            found.error(f"meta.sources[{i}] has no id.")
            continue
        if sid in by_id:
            found.error(f"meta.sources lists {sid!r} twice.")
        by_id[sid] = s
        missing = [k for k in REQUIRED_SOURCE_FIELDS if _blank(s.get(k))]
        if missing:
            found.error(f"Source {sid!r} in meta.json has no {', '.join(missing)}.")
        url = s.get("url")
        if isinstance(url, str) and url and not url.startswith(("https://", "http://")):
            found.error(f"Source {sid!r} has a url that is not a link: {url!r}.")
        if sid not in UNDATED_SOURCES and (_blank(s.get("as_of_min")) or _blank(s.get("as_of_max"))):
            found.warn(f"Source {sid!r} in meta.json has no as-of range.")
    ti = by_id.get("tickerinside")
    if ti is not None and CC_BY_URL not in str(ti.get("credit") or ""):
        found.error(
            "The TickerInside credit line does not link the CC BY 4.0 licence "
            f"(https://{CC_BY_URL}/), which its licence requires."
        )
    return by_id


def _col(fields: list, name: str) -> int | None:
    return fields.index(name) if isinstance(fields, list) and name in fields else None


def scan_api(api_v1: Path, known: set[str], found: Findings, max_examples: int = 5) -> dict:
    """Check the source and as-of date of every fund and every row in ``index.json`` and ``funds/*.json``."""
    stats = {"fund_files": 0, "alias_files": 0, "rows": 0}
    unknown: dict[str, list[str]] = {}
    no_source: list[str] = []
    no_as_of: list[str] = []

    def note_unknown(sid: str, where: str) -> None:
        unknown.setdefault(sid, [])
        if len(unknown[sid]) < max_examples:
            unknown[sid].append(where)

    index_path = api_v1 / "index.json"
    index_ids: list[str] = []
    if index_path.is_file():
        index = json.loads(index_path.read_text())
        fields = index.get("fields") or []
        i_id, i_src = _col(fields, "id"), _col(fields, "source")
        for row in index.get("rows") or []:
            fid = row[i_id] if i_id is not None else "?"
            index_ids.append(fid)
            if i_src is not None and not _blank(row[i_src]) and row[i_src] not in known:
                note_unknown(row[i_src], f"index.json {fid}")
    else:
        found.error(f"No {index_path}.")

    funds_dir = api_v1 / "funds"
    present = set()
    for path in sorted(funds_dir.glob("*.json")) if funds_dir.is_dir() else []:
        doc = json.loads(path.read_text())
        if "alias_of" in doc:
            stats["alias_files"] += 1
            continue
        stats["fund_files"] += 1
        fid = doc.get("id", path.stem)
        present.add(fid)
        src = doc.get("source")
        if _blank(src):
            no_source.append(fid)
        elif src not in known:
            note_unknown(src, f"funds/{path.name}")
        if _blank(doc.get("as_of")):
            no_as_of.append(fid)
        unresolved = doc.get("unresolved") or {}
        if isinstance(unresolved, dict) and not _blank(unresolved.get("source")) and unresolved["source"] not in known:
            note_unknown(unresolved["source"], f"funds/{path.name} unresolved")
        fields = doc.get("fields") or []
        c_src, c_asof = _col(fields, "source"), _col(fields, "as_of")
        rows = doc.get("constituents") or []
        stats["rows"] += len(rows)
        if rows and (c_src is None or c_asof is None):
            found.error(f"funds/{path.name}: constituent fields have no 'source' or 'as_of' column.")
            continue
        for row in rows:
            rs, ra = row[c_src], row[c_asof]
            if _blank(rs):
                no_source.append(f"{fid} row {row[0]}")
            elif rs not in known:
                note_unknown(rs, f"funds/{path.name}")
            if _blank(ra):
                no_as_of.append(f"{fid} row {row[0]}")

    for sid, where in sorted(unknown.items()):
        found.error(f"Source {sid!r} is used but not listed in meta.sources (e.g. {', '.join(where)}).")
    if no_source:
        found.error(f"{len(no_source)} funds or rows have no source (e.g. {', '.join(no_source[:max_examples])}).")
    if no_as_of:
        found.error(f"{len(no_as_of)} funds or rows have no as-of date (e.g. {', '.join(no_as_of[:max_examples])}).")
    missing_files = [f for f in index_ids if f not in present]
    if missing_files:
        found.error(f"{len(missing_files)} funds in index.json have no fund file (e.g. {', '.join(missing_files[:max_examples])}).")
    return stats


# --- size, tracking ----------------------------------------------------------


def dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def check_pages_size(pages: Path, found: Findings) -> int:
    size = dir_bytes(pages)
    if size > PAGES_LIMIT_BYTES:
        found.error(f"The site is {size / 1e6:.0f} MB, over GitHub Pages' 1 GB limit; lower publish.site_budget_mb.")
    elif size > PAGES_WARN_BYTES:
        found.warn(f"The site is {size / 1e6:.0f} MB, close to GitHub Pages' 1 GB limit.")
    return size


def check_no_tracking(pages: Path, found: Findings) -> None:
    for path in sorted(pages.rglob("*.html")):
        text = path.read_text(errors="replace").lower()
        hit = next((m for m in TRACKING_MARKERS if m in text), None)
        if hit:
            found.error(f"{path.relative_to(pages)} contains a tracking script ({hit!r}).")


# --- N-PORT freshness --------------------------------------------------------


def _parse_quarter(q: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d{4})q([1-4])", str(q).strip().lower())
    return (int(m[1]), int(m[2])) if m else None


def expected_nport_quarter(today: date, lag_days: int = NPORT_PUBLISH_LAG_DAYS) -> tuple[int, int]:
    """The newest quarter that ended at least ``lag_days`` before ``today``."""
    year, q = today.year, (today.month - 1) // 3 + 1
    while True:
        q -= 1
        if q == 0:
            year, q = year - 1, 4
        end = date(year, 3 * q, 1) + timedelta(days=31)
        end = end.replace(day=1) - timedelta(days=1)
        if (today - end).days >= lag_days:
            return year, q


def check_nport_freshness(meta: dict, quarters: list | None, today: date, found: Findings) -> None:
    nport = next((s for s in meta.get("sources") or [] if isinstance(s, dict) and s.get("id") == "nport"), None)
    newest = nport.get("as_of_max") if nport else None
    if newest:
        try:
            age = (today - date.fromisoformat(str(newest)[:10])).days
        except ValueError:
            found.warn(f"N-PORT as_of_max {newest!r} is not a date.")
        else:
            if age > NPORT_STALE_DAYS:
                found.warn(
                    f"The newest N-PORT holdings are from {newest}, {age} days ago. "
                    "Add the latest SEC quarter to nport.quarters (docs: Deployment > Quarterly maintenance)."
                )
    if quarters:
        parsed = [p for p in map(_parse_quarter, quarters) if p]
        if parsed:
            have, want = max(parsed), expected_nport_quarter(today)
            if have < want:
                found.warn(
                    f"nport.quarters ends at {have[0]}q{have[1]}; SEC should have published {want[0]}q{want[1]} by now. "
                    "Add it to conf/base/parameters.yml once it is on the N-PORT data sets page."
                )


# --- reports -----------------------------------------------------------------


def _scalars(obj: dict, prefix: str = ""):
    for key, value in obj.items():
        if isinstance(value, dict):
            yield from _scalars(value, f"{prefix}{key}.")
        elif not isinstance(value, list):
            yield f"{prefix}{key}", value


def _cell(v: object) -> str:
    return "" if v is None else str(v).replace("|", "\\|").replace("\n", " ")


def _source_link(s: dict) -> str:
    return f"[{_cell(s.get('name'))}]({s.get('url')})" if s.get("url") else _cell(s.get("name") or s.get("id"))


def _as_of(s: dict) -> str:
    lo, hi = s.get("as_of_min"), s.get("as_of_max")
    if not lo and not hi:
        return "not dated"
    return _cell(lo) if lo == hi else f"{_cell(lo)} to {_cell(hi)}"


def summary_markdown(meta: dict, report: dict, found: Findings, *, n_funds, min_funds: int, sample: str,
                     pages_bytes: int | None, scan: dict, live: bool | None) -> str:
    tag = meta.get("release_tag") or report.get("release_tag") or ""
    res = report.get("resolution") or {}
    lines = [f"## openbasket {tag}", ""]
    lines.append(
        f"Funds published: **{n_funds}** (minimum {min_funds})"
        + (f", sample of {sample} per universe source" if sample else "")
        + f". Funds with holdings: {res.get('n_funds', '?')}. Fund ISINs (with share classes): {meta.get('n_isins', '?')}."
    )
    if pages_bytes is not None:
        lines.append(f"Site size: {pages_bytes / 1e6:.1f} MB of GitHub Pages' 1,000 MB.")
    if scan:
        lines.append(f"Checked {scan['fund_files']} fund files, {scan['alias_files']} alias files, {scan['rows']} rows.")
    if live is not None:
        lines.append("This run releases and deploys." if live else
                     "**Sample run: not released or deployed.** Its files are attached to the run as artifacts.")
    lines.append("")
    if found.errors:
        lines += ["### Errors", "", *(f"- {e}" for e in found.errors), ""]
    if found.warnings:
        lines += ["### Warnings", "", *(f"- {w}" for w in found.warnings), ""]
    if meta.get("sources"):
        lines += [
            "### Sources", "",
            "| Source | Funds | As of | Licence | Credit |",
            "|---|---|---|---|---|",
            *(f"| {_source_link(s)} | {_cell(s.get('n_funds'))} | {_as_of(s)} | {_cell(s.get('licence'))} "
              f"| {_cell(s.get('credit'))} |" for s in meta["sources"]),
            "",
        ]
    lines += ["### Quality report", "", "| Key | Value |", "|---|---|",
              *(f"| `{k}` | {_cell(v)} |" for k, v in _scalars(report))]
    return "\n".join(lines) + "\n"


def release_notes(meta: dict, site_url: str, sha: str) -> str:
    lines = [
        f"Monthly build of [openbasket]({site_url}/), an aggregator that looks through "
        "funds to the securities they hold, by ISIN.",
        "",
        f"Built {meta.get('built_at', '')} from {sha[:7]}. "
        f"{meta.get('n_funds', '')} funds, {meta.get('n_isins', '')} fund ISINs.",
        "",
        "## Files",
        "",
        "- `openbasket-lookthrough.parquet`: every constituent row of every fund, never truncated.",
        "- `openbasket-funds.parquet`: one row per fund, with its source, source link and as-of date.",
        "- `quality-report.json`: coverage figures for this build.",
        "",
        f"Columns and the static JSON API are described at {site_url}/docs/data-api.html. "
        f"The pipeline that builds these files is drawn at {site_url}/pipeline/.",
        "",
        "## Sources",
        "",
        "openbasket publishes no data of its own. Every row names its source and as-of "
        "date; each fund gives a link to the page or filing its holdings came from. "
        "This build used:",
        "",
        "| Source | Funds | As of | Licence | Credit |",
        "|---|---|---|---|---|",
    ]
    for s in meta.get("sources") or []:
        lines.append(
            f"| {_source_link(s)} | {_cell(s.get('n_funds'))} | {_as_of(s)} "
            f"| {_cell(s.get('licence'))} | {_cell(s.get('credit'))} |"
        )
    lines += [
        "",
        "Keep each source's credit line, and each row's `source` and `source_ref`, in "
        "anything you publish from these files.",
    ]
    if any(isinstance(s, dict) and s.get("id") == "tickerinside" for s in meta.get("sources") or []):
        lines.append(
            f"TickerInside data is used under [CC BY 4.0](https://{CC_BY_URL}/), which requires "
            "that credit. openbasket changed it: tickers were mapped to ISINs and names "
            "filled in from SEC Form N-PORT."
        )
    lines += [
        "",
        "Not investment advice. openbasket is not affiliated with, or endorsed by, any "
        "fund issuer or data provider named here. Check figures against the source "
        "before relying on them.",
    ]
    return "\n".join(lines) + "\n"


def annotate(found: Findings) -> None:
    for w in found.warnings:
        print(f"::warning::{w}")
    for e in found.errors:
        print(f"::error::{e}")


# --- main --------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    found = Findings()
    api_v1 = Path(args.api) / "v1"
    meta_path = api_v1 / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    if not meta:
        found.error(f"No {meta_path}.")
    report_path = Path(args.release) / "quality-report.json"
    report = json.loads(report_path.read_text()) if report_path.is_file() else {}
    if not report:
        found.error(f"No {report_path}.")
    for name in ("openbasket-lookthrough.parquet", "openbasket-funds.parquet"):
        if not (Path(args.release) / name).is_file():
            found.error(f"No {Path(args.release) / name}.")

    n_funds = meta.get("n_funds")
    if not isinstance(n_funds, int) or n_funds < args.min_funds:
        found.error(f"{n_funds} funds published, fewer than the minimum of {args.min_funds}.")

    known = set(check_meta_sources(meta, found)) if meta else set()
    scan = scan_api(api_v1, known, found) if meta else {}

    pages_bytes = None
    if args.pages:
        pages = Path(args.pages)
        if not (pages / "index.html").is_file():
            found.error(f"No site at {pages}.")
        else:
            pages_bytes = check_pages_size(pages, found)
            check_no_tracking(pages, found)
            if args.require_pipeline and not (pages / "pipeline" / "index.html").is_file():
                found.error(f"No pipeline diagram at {pages / 'pipeline' / 'index.html'}.")

    today = date.fromisoformat(args.today) if args.today else datetime.now().date()
    quarters = None
    if args.params and Path(args.params).is_file():
        import yaml  # a Kedro dependency

        quarters = ((yaml.safe_load(Path(args.params).read_text()) or {}).get("nport") or {}).get("quarters")
    check_nport_freshness(meta, quarters, today, found)

    live = None if args.live is None else args.live == "true"
    if args.summary:
        with open(args.summary, "a") as fh:
            fh.write(summary_markdown(meta, report, found, n_funds=n_funds, min_funds=args.min_funds,
                                      sample=args.sample or "", pages_bytes=pages_bytes, scan=scan, live=live))
    if args.notes and meta:
        Path(args.notes).write_text(release_notes(meta, args.site_url, args.sha))
    annotate(found)
    if found.errors:
        print(f"{len(found.errors)} problems found; nothing is released or deployed.", file=sys.stderr)
        return 1
    print(f"Build checked: {n_funds} funds, {len(found.warnings)} warnings.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--api", default="build/site/api", help="folder holding v1/")
    p.add_argument("--release", default="build/release", help="folder holding the release files")
    p.add_argument("--pages", help="assembled site, e.g. build/pages")
    p.add_argument("--require-pipeline", action="store_true", help="fail if pages/pipeline/index.html is missing")
    p.add_argument("--min-funds", type=int, default=1)
    p.add_argument("--sample", default=os.environ.get("SAMPLE", ""))
    p.add_argument("--live", choices=("true", "false"), help="whether this run releases and deploys")
    p.add_argument("--params", default="conf/base/parameters.yml", help="for nport.quarters")
    p.add_argument("--today", help="YYYY-MM-DD (default: today)")
    p.add_argument("--summary", help="append the job summary (markdown) here")
    p.add_argument("--notes", help="write the release notes here")
    p.add_argument("--site-url", default=os.environ.get("SITE_URL", "https://openbasket.leithresearch.org"))
    p.add_argument("--sha", default=os.environ.get("GITHUB_SHA", "local"))
    return run(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
