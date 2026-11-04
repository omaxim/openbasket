"""Fund reports from the AVL Investmentfonds document library, read by ISIN.

``https://dl.avl-investmentfonds.de`` serves funds' statutory documents as
``/fds/<ISIN>-<TYPE>-<LANG>-<YYYYMMDD>.pdf``: ``RECHENSCHAFT`` is the annual
report, ``HALBJAHRBER`` the semi-annual report, and the date is the report's
period end. ``/sitemap.xml`` lists twelve ``/sitemaps/sitemap-fds*.xml``
files with every document URL. Its robots.txt allows ``/fds/``, ``/pdf/``,
``/sitemap.xml`` and ``/sitemaps/`` for all agents (it shuts out named AI
training crawlers; openbasket identifies itself as ``openbasket``) and
disallows everything else, so only those paths are requested, each after a
robots.txt check (:mod:`openbasket.sources.robots`).

An umbrella's report is the same PDF under each share class's ISIN. A
downloaded report is kept in the cache with its contents (sub-fund titles
and page ranges), and a later share class whose sub-fund is in a cached
report of the same kind, period and domicile reuses it instead of
downloading it again.

Nothing here is published: the cache lives under ``data/`` (gitignored).
"""

from __future__ import annotations

import gzip
import hashlib
import html
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from . import robots
from . import schedule_tables as st

#: Reports are the issuers' copyright documents; AVL publishes no reuse licence.
REDISTRIBUTION = "local_only"

logger = logging.getLogger(__name__)

HOST = "https://dl.avl-investmentfonds.de"
SITEMAP = HOST + "/sitemap.xml"
KINDS = {"HALBJAHRBER": "semi-annual report", "RECHENSCHAFT": "annual report"}
LANG_ORDER = {"EN": 0, "DE": 1, "FR": 2}
DEFAULT_CACHE = Path("data/01_raw/issuers/robots_alternatives")
#: Reports downloaded per process at most (umbrella reports run to 25 MB); raise
#: it with OPENBASKET_MAX_REPORT_DOWNLOADS for a full first run.
MAX_DOWNLOADS = int(os.environ.get("OPENBASKET_MAX_REPORT_DOWNLOADS", "12"))
_downloads = 0


class DownloadBudgetExceeded(RuntimeError):
    pass
_FDS = re.compile(r"/fds/([A-Z]{2}[A-Z0-9]{9}\d)-([A-Z]+)-([A-Z]{2})-(\d{8})\.pdf")


@dataclass(frozen=True)
class ReportRef:
    isin: str          # share class the URL is filed under
    kind: str          # "annual report" | "semi-annual report"
    lang: str
    period: str        # ISO period end (the positions date)
    url: str


@dataclass
class CachedReport:
    ref: ReportRef
    pdf: Path
    umbrella: str      # first line of the cover page
    subfunds: dict[str, list[int]]


def cache_dir(path: str | Path | None) -> Path:
    p = Path(path) if path else DEFAULT_CACHE
    p.mkdir(parents=True, exist_ok=True)
    return p


# -- index ----------------------------------------------------------------------

def parse_sitemap_urls(xml: str) -> list[str]:
    return [html.unescape(u) for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)]


def parse_fds(urls: list[str]) -> pd.DataFrame:
    rows = []
    for u in urls:
        m = _FDS.search(u)
        if m and m.group(2) in KINDS:
            isin, typ, lang, d = m.groups()
            rows.append((isin, KINDS[typ], lang, f"{d[:4]}-{d[4:6]}-{d[6:]}", u))
    return pd.DataFrame(rows, columns=["isin", "kind", "lang", "period", "url"])


def index(cache: str | Path | None = None, max_age_days: float = 7, fetch=None) -> pd.DataFrame:
    """Every annual and semi-annual report AVL lists, one row per URL; cached for a week."""
    path = cache_dir(cache) / "avl_index.parquet"
    if path.exists() and datetime.now().timestamp() - path.stat().st_mtime < max_age_days * 86400:
        return pd.read_parquet(path)
    get = fetch or (lambda u: robots.get(u, timeout=120))
    top = get(SITEMAP)
    top.raise_for_status()
    urls: list[str] = []
    for sm in parse_sitemap_urls(top.text):
        if "/sitemaps/" not in sm:
            continue
        r = get(sm)
        if r.status_code == 200:
            urls.extend(parse_sitemap_urls(r.text))
    frame = parse_fds(urls)
    frame.to_parquet(path, index=False)
    return frame


def locate(isin: str, idx: pd.DataFrame) -> list[ReportRef]:
    """Reports filed under ``isin``, newest period first, English before German."""
    rows = idx[idx["isin"] == isin]
    refs = [ReportRef(r.isin, r.kind, r.lang, r.period, r.url) for r in rows.itertuples()]
    return sorted(refs, key=lambda r: (r.period, -LANG_ORDER.get(r.lang, 9), r.kind == "annual report"), reverse=True)


# -- download cache -----------------------------------------------------------------

def _key(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def _meta_path(cache: Path, url: str) -> Path:
    return cache / "reports" / f"{_key(url)}.json"


def _load(meta: Path) -> CachedReport | None:
    try:
        d = json.loads(meta.read_text())
    except (OSError, ValueError):
        return None
    if d.get("duplicate_of"):
        return None     # listed under the original's entry
    pdf = meta.with_suffix(".pdf")
    if not pdf.exists():
        return None
    return CachedReport(ReportRef(**d["ref"]), pdf, d["umbrella"], {k: v for k, v in d["subfunds"].items()})


def _load_duplicate(meta: Path, ref: ReportRef) -> CachedReport | None:
    try:
        d = json.loads(meta.read_text())
    except (OSError, ValueError):
        return None
    if not d.get("duplicate_of") or not (meta.parent / d["duplicate_of"]).exists():
        return None
    return CachedReport(ref, meta.parent / d["duplicate_of"], d["umbrella"], d["subfunds"])


def cached_reports(cache: str | Path | None = None) -> list[CachedReport]:
    folder = cache_dir(cache) / "reports"
    if not folder.exists():
        return []
    return [c for c in (_load(m) for m in sorted(folder.glob("*.json"))) if c]


def umbrella_name(cover: str) -> str:
    """The fund company's name from the cover page ("Xtrackers II", "Legal & General UCITS ETF Plc")."""
    words = re.sub(r"\s+", " ", cover[:600]).split()
    for i, w in enumerate(words):
        if w.strip(".,").lower() in ("plc", "icav", "sicav"):
            start = i
            while start > 0 and i - start < 7 and not re.search(r"[\d,]", words[start - 1]):
                start -= 1
            return " ".join(words[start:i + 1]).strip(".,")
    return next((x.strip() for x in cover.splitlines() if x.strip()), "").strip("* ")


def fetch_report(ref: ReportRef, brand: str, cache: str | Path | None = None, fetch=None) -> CachedReport:
    """Download one report (unless cached), find its sub-fund schedules and cache both."""
    folder = cache_dir(cache) / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    meta = _meta_path(folder.parent, ref.url)
    hit = _load(meta) or _load_duplicate(meta, ref)
    if hit:
        return hit
    global _downloads
    if _downloads >= MAX_DOWNLOADS:
        raise DownloadBudgetExceeded(f"report download budget ({MAX_DOWNLOADS}) used up; {ref.url} not fetched")
    _downloads += 1
    get = fetch or (lambda u: robots.get(u, timeout=300))
    r = get(ref.url)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise ValueError(f"{ref.url} did not return a PDF")
    digest = hashlib.sha1(r.content).hexdigest()
    same = next((c for c in cached_reports(folder.parent) if _digest(c) == digest), None)
    if same is not None:
        # AVL files one umbrella PDF under every share class's ISIN. Remember that this
        # edition is a duplicate, so other share classes do not fetch it again.
        _mark_duplicate(folder.parent, ref, brand)
        meta.write_text(json.dumps({"ref": asdict(ref), "umbrella": same.umbrella, "subfunds": same.subfunds,
                                    "sha1": digest, "duplicate_of": same.pdf.name}))
        return CachedReport(ref, same.pdf, same.umbrella, same.subfunds)
    pdf = meta.with_suffix(".pdf")
    pdf.write_bytes(r.content)
    texts = st.pdf_page_texts(pdf)
    with gzip.open(meta.with_suffix(".txt.gz"), "wt") as fh:
        json.dump(texts, fh)
    subfunds = st.subfund_pages(texts, brand)
    umbrella = umbrella_name(texts[0] if texts else "")
    meta.write_text(json.dumps({"ref": asdict(ref), "umbrella": umbrella, "subfunds": subfunds, "sha1": digest}))
    return CachedReport(ref, pdf, umbrella, subfunds)


def _digest(c: CachedReport) -> str:
    try:
        d = json.loads(_meta_path(c.pdf.parent.parent, c.ref.url).read_text())
    except (OSError, ValueError):
        d = {}
    return d.get("sha1") or hashlib.sha1(c.pdf.read_bytes()).hexdigest()


def _edition(ref: ReportRef, brand: str) -> str:
    return "|".join([brand, ref.isin[:2], ref.kind, ref.period, ref.lang])


def _mark_duplicate(cache: Path, ref: ReportRef, brand: str) -> None:
    path = cache / "report_duplicates.json"
    try:
        seen = json.loads(path.read_text())
    except (OSError, ValueError):
        seen = {}
    seen[_edition(ref, brand)] = seen.get(_edition(ref, brand), 0) + 1
    path.write_text(json.dumps(seen))


def known_duplicate(ref: ReportRef, brand: str, cache: str | Path | None = None) -> bool:
    """True once a download of this edition (brand, domicile, kind, period, language) proved a duplicate.

    Every umbrella of that edition filed so far is then already cached, and a
    share class whose sub-fund is in none of them (typically one launched after
    the report date) is not fetched again under its own ISIN.
    """
    try:
        seen = json.loads((cache_dir(cache) / "report_duplicates.json").read_text())
    except (OSError, ValueError):
        return False
    return seen.get(_edition(ref, brand), 0) > 0


def is_cached(ref: ReportRef, cache: str | Path | None = None) -> bool:
    meta = _meta_path(cache_dir(cache), ref.url)
    return bool(_load(meta) or _load_duplicate(meta, ref))


def reusable(fund_key: str, brand: str, kind: str, period: str, domicile: str,
             cache: str | Path | None = None, loose: bool = False) -> tuple[CachedReport, str] | None:
    """A cached report of the same kind, period and domicile that contains the sub-fund."""
    for c in cached_reports(cache):
        if c.ref.kind != kind or c.ref.period != period or c.ref.isin[:2] != domicile:
            continue
        title = st.match_title(fund_key, c.subfunds, brand, loose)
        if title:
            return c, title
    return None


def latest_cached(fund_key: str, brand: str, domicile: str, cache: str | Path | None = None,
                  max_age_days: int = 400, loose: bool = False) -> tuple[CachedReport, str] | None:
    """Newest cached report of the domicile that contains the sub-fund (for share classes AVL does not list)."""
    best = None
    cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()
    for c in cached_reports(cache):
        if c.ref.isin[:2] != domicile or c.ref.period < cutoff:
            continue
        title = st.match_title(fund_key, c.subfunds, brand, loose)
        if title and (best is None or (c.ref.period, c.ref.kind) > (best[0].ref.period, best[0].ref.kind)):
            best = (c, title)
    return best
