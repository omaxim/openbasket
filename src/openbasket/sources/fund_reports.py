"""Holdings from statutory fund reports, found by ISIN on public document hosts.

Every UCITS fund publishes an annual and a semi-annual report with the full
schedule of investments of each sub-fund; Swiss funds publish the same under
the Collective Investment Schemes Act. Several issuers block automated access
to their own sites (UBS, Invesco EMEA, Pictet, Fidelity, Swiss Life...), but
their reports are also published, by ISIN and without any gate, on:

``avl``
    AVL Investmentfonds' document library, ``https://dl.avl-investmentfonds.de``.
    Its sitemap (``/sitemap.xml``, twelve files of about 35,000 URLs each)
    lists ``/fds/<ISIN>-<TYPE>-<LANG>-<YYYYMMDD>.pdf`` for about 88,000 share
    classes, where ``TYPE`` is ``RECHENSCHAFT`` (annual report) or
    ``HALBJAHRBER`` (semi-annual report) and the date is the report's period
    end. It keeps the latest report of each type. ``robots.txt`` allows
    ``/fds/`` and ``/sitemaps/`` for all agents (it shuts out named AI
    training crawlers, which this client is not).
``wisdomtree``
    WisdomTree's own document service,
    ``https://dataspanapi.wisdomtree.com/pdr/documents/<TYPE>/UCITS/GB/EN-GB/<ISIN>/``
    with ``TYPE`` ``ANNUAL_REPORT`` or ``SEMI_ANNUAL_REPORT``; the file name
    in ``Content-Disposition`` carries the fund name.
``extraetf``
    extraETF's public document links, ``https://docs.extraetf.com/download/<ISIN>/<lang>/<AR|SAR>``.
    Used last, for funds the others do not have (Swiss ETFs).

A report is downloaded once and its page texts are cached; share classes and
sub-funds of the same umbrella then reuse them. The schedule is parsed by
:mod:`openbasket.sources.report_parser`.

Not used: fundinfo's document API (``robots.txt`` disallows ``/document/``),
swissfunddata.ch (needs an investor-type disclaimer click), and the issuers'
own sites behind Akamai, Cloudflare, AWS WAF or Fastly challenges.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha1
from pathlib import Path

import pandas as pd

from .. import http
from . import report_parser as rp
from .issuers import HOLDING_COLUMNS, IssuerHoldings

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = "data/01_raw/issuers/fund_reports"

AVL_HOST = "https://dl.avl-investmentfonds.de"
AVL_SITEMAP = AVL_HOST + "/sitemap.xml"
AVL_KINDS = {"RECHENSCHAFT": "annual report", "HALBJAHRBER": "semi-annual report"}
WISDOMTREE_DOCS = "https://dataspanapi.wisdomtree.com/pdr/documents/{type}/UCITS/GB/EN-GB/{isin}/"
WISDOMTREE_KINDS = {"SEMI_ANNUAL_REPORT": "semi-annual report", "ANNUAL_REPORT": "annual report"}
EXTRAETF_DOCS = "https://docs.extraetf.com/download/{isin}/{lang}/{type}"
EXTRAETF_KINDS = {"SAR": "semi-annual report", "AR": "annual report"}

HOSTS = {
    "avl": ("AVL Investmentfonds document library", AVL_HOST),
    "wisdomtree": ("WisdomTree document service", "https://dataspanapi.wisdomtree.com"),
    "extraetf": ("extraETF document links", "https://docs.extraetf.com"),
}

LANG_ORDER = {"EN": 0, "DE": 1, "FR": 2}
MAX_DOWNLOADS = 4          # reports downloaded per fund before giving up
_FDS_RE = re.compile(r"/fds/([A-Z]{2}[A-Z0-9]{9}\d)-([A-Z]+)-([A-Z]{2})-(\d{8})\.pdf")


@dataclass
class ReportRef:
    isin: str
    url: str
    kind: str                     # "annual report" or "semi-annual report"
    host: str                     # key of HOSTS
    lang: str | None = None
    period_end: str | None = None  # ISO date from the locator (AVL file name), if known
    published: str | None = None   # Last-Modified, if the host sends it
    filename: str | None = None


@dataclass
class ReportHoldings(IssuerHoldings):
    """:class:`IssuerHoldings` plus what was read and how well it parsed."""

    kind: str | None = None
    sub_fund: str | None = None
    stats: dict = field(default_factory=dict)
    report: ReportRef | None = None


# --------------------------------------------------------------------------
# Locators
# --------------------------------------------------------------------------


def _cache(cache_dir: str | Path | None) -> Path:
    p = Path(cache_dir or DEFAULT_CACHE_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def parse_avl_sitemap(xml: str) -> pd.DataFrame:
    """Rows ``isin, type, lang, date, url`` from one AVL ``sitemap-fds`` file."""
    rows = [(m.group(1), m.group(2), m.group(3), m.group(4), AVL_HOST + m.group(0)) for m in _FDS_RE.finditer(xml)]
    return pd.DataFrame(rows, columns=["isin", "type", "lang", "date", "url"])


def avl_index(cache_dir: str | Path | None = None, max_age_days: float = 7) -> pd.DataFrame:
    """Every AVL report file (annual and semi-annual only), cached for a week.

    Building it reads the sitemap index and its twelve ``sitemap-fds`` files
    (about 50 MB), one request per second.
    """
    path = _cache(cache_dir) / "avl_reports_index.parquet"
    if path.exists() and time.time() - path.stat().st_mtime < max_age_days * 86400:
        return pd.read_parquet(path)
    r = http.get(AVL_SITEMAP)
    r.raise_for_status()
    maps = [u for u in re.findall(r"<loc>([^<]+)</loc>", r.text) if "sitemap-fds" in u]
    frames = []
    for u in maps:
        logger.info("Reading AVL sitemap %s", u)
        rr = http.get(u, timeout=120)
        rr.raise_for_status()
        frames.append(parse_avl_sitemap(rr.text))
    df = pd.concat(frames, ignore_index=True) if frames else parse_avl_sitemap("")
    keep = df[df["type"].isin(AVL_KINDS) | df["type"].isin(["KID", "PRIIP"])].drop_duplicates("url")
    keep.reset_index(drop=True).to_parquet(path)
    return keep


def avl_locate(isin: str, index: pd.DataFrame) -> list[ReportRef]:
    rows = index[(index["isin"] == isin) & index["type"].isin(AVL_KINDS)]
    out = []
    for r in rows.itertuples(index=False):
        d = f"{r.date[:4]}-{r.date[4:6]}-{r.date[6:]}"
        out.append(ReportRef(isin, r.url, AVL_KINDS[r.type], "avl", r.lang, d, filename=r.url.rsplit("/", 1)[-1]))
    return out


def _last_modified(resp) -> str | None:
    lm = resp.headers.get("last-modified")
    if not lm:
        return None
    try:
        return parsedate_to_datetime(lm).astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return None


def _disposition_name(resp) -> str | None:
    cd = resp.headers.get("content-disposition") or ""
    m = re.search(r'filename="?([^";]+)"?', cd)
    return m.group(1) if m else None


def wisdomtree_locate(isin: str) -> list[ReportRef]:
    out = []
    for typ, kind in WISDOMTREE_KINDS.items():
        url = WISDOMTREE_DOCS.format(type=typ, isin=isin)
        r = http.request("HEAD", url, allow_redirects=True)
        if r.status_code == 200 and "pdf" in (r.headers.get("content-type") or ""):
            out.append(ReportRef(isin, url, kind, "wisdomtree", "EN", None, _last_modified(r), _disposition_name(r)))
    return out


def extraetf_locate(isin: str, langs: tuple[str, ...] = ("en", "de")) -> list[ReportRef]:
    out = []
    for lang in langs:
        for typ, kind in EXTRAETF_KINDS.items():
            url = EXTRAETF_DOCS.format(isin=isin, lang=lang, type=typ)
            r = http.request("HEAD", url, allow_redirects=True)
            if r.status_code == 200 and "pdf" in (r.headers.get("content-type") or ""):
                out.append(ReportRef(isin, url, kind, "extraetf", lang.upper(), None, _last_modified(r)))
    return out


def wisdomtree_fund_name(filename: str | None) -> str | None:
    """'SEMI_ANNUAL_REPORT-EN-GB-IE00BZ56RN96-WisdomTree Global Quality ... - USD.pdf' -> the fund name."""
    if not filename:
        return None
    m = re.match(r"^[A-Z_]+-[A-Z]{2}-[A-Z]{2}-[A-Z0-9]{12}-(.+?)(?:\.pdf)?$", filename)
    return m.group(1).strip() if m else None


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------


def _week() -> str:
    y, w, _ = date.today().isocalendar()
    return f"{y}w{w:02d}"


def report_pages(ref: ReportRef, cache_dir: str | Path | None = None) -> list[str]:
    """Page texts of a report, downloaded once and cached (gzip JSON, no PDF kept).

    AVL file names carry the period end, so their cache never goes stale;
    undated links (WisdomTree, extraETF) are re-read once a week.
    """
    path = _text_path(ref, cache_dir)
    if path.exists():
        return json.loads(gzip.decompress(path.read_bytes()))
    logger.info("Fetching %s %s", ref.kind, ref.url)
    r = http.get(ref.url, timeout=180)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise ValueError(f"{ref.url} did not return a PDF")
    pages = rp.extract_pages(r.content)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(pages).encode()))
    return pages


def _text_path(ref: ReportRef, cache_dir) -> Path:
    key = sha1(ref.url.encode()).hexdigest()[:16]
    stamp = "" if ref.host == "avl" else "_" + _week()
    return _cache(cache_dir) / "texts" / f"{ref.host}_{ref.isin}_{key}{stamp}.json.gz"


def _sidecar(ref: ReportRef, cache_dir) -> Path:
    t = _text_path(ref, cache_dir)
    return t.with_name(t.name.replace(".json.gz", ".titles.json"))


def _same_edition(ref: ReportRef) -> tuple:
    return (ref.host, ref.kind, ref.lang, ref.period_end, ref.published, ref.isin[:2])


def remember_titles(ref: ReportRef, pages: list[str], brands: list[str] | None, cache_dir) -> None:
    """Note which sub-funds a cached report holds, so other share classes can reuse it."""
    if not brands:
        return
    path = _sidecar(ref, cache_dir)
    if path.exists() or not _text_path(ref, cache_dir).exists():
        return
    titles = sorted({s.title for s in rp.segments(pages, brands)})
    meta = {"url": ref.url, "isin": ref.isin, "edition": list(_same_edition(ref)), "titles": titles}
    path.write_text(json.dumps(meta))


def shared_report(ref: ReportRef, fund_name: str, brands, cache_dir) -> tuple[ReportRef, list[str]] | None:
    """A cached copy of the same umbrella report (same host, kind, language, period or publication
    date, and domicile) that holds a sub-fund matching ``fund_name`` specifically.

    AVL and WisdomTree serve the umbrella report once per share-class ISIN; this avoids
    downloading the same document again for every share class.
    """
    folder = _cache(cache_dir) / "texts"
    if not folder.exists() or _text_path(ref, cache_dir).exists():
        return None
    edition = list(_same_edition(ref))
    for side in folder.glob(f"{ref.host}_*.titles.json"):
        try:
            meta = json.loads(side.read_text())
        except ValueError:
            continue
        if meta.get("edition") != edition:
            continue
        if any(_specific_match(t, fund_name) for t in meta.get("titles", [])):
            text = side.with_name(side.name.replace(".titles.json", ".json.gz"))
            if text.exists():
                other = ReportRef(meta["isin"], meta["url"], ref.kind, ref.host, ref.lang, ref.period_end, ref.published)
                return other, json.loads(gzip.decompress(text.read_bytes()))
    return None


def _specific_match(title: str, fund_name: str) -> bool:
    """A title that matches the fund name fully and covers most of its non-share-class part."""
    frac, chars = rp.title_score(title, fund_name)
    key = portfolio_key(fund_name)
    return rp.compact(title) == key or (frac >= 0.999 and chars >= 0.85 * len(key))


_KID_NAME = [
    re.compile(r"(?:Product name|Name of (?:the )?product|Product|Produktname|Name des Produkts|Produkt|Nom du produit|Produit)\s*:\s*(.+)", re.I),
]


def kid_fund_name(pages: list[str], isin: str) -> str | None:
    """The fund name a KID / PRIIPs KID gives for a share class."""
    text = "\n".join(pages[:1])
    for rx in _KID_NAME:
        m = rx.search(text)
        if m and len(m.group(1).strip()) > 5:
            return re.sub(r"\s*\(?ISIN.*$", "", m.group(1)).strip(" ,;")
    i = text.find(isin)
    if i < 0:
        return None
    before = text[:i].split("\n")
    chunk = " ".join(before[-3:])
    chunk = re.split(r"(?i)invest\.|decision[^.]*\.|anlageentscheidung[^.]*\.", chunk)[-1]
    chunk = re.split(r'\s*\(the "?Fund"?\)|\s*\("?der Fonds"?\)|,\s*a sub-fund|,\s*ein Teilfonds|\s*\(ISIN', chunk)[0]
    chunk = " ".join(chunk.split())
    return chunk or None


def avl_fund_name(isin: str, index: pd.DataFrame, cache_dir: str | Path | None = None) -> str | None:
    """Read the share class's KID on AVL for its fund name (used when no name is known)."""
    rows = index[(index["isin"] == isin) & index["type"].isin(["KID", "PRIIP"])].copy()
    if rows.empty:
        return None
    rows["pref"] = rows["lang"].map(LANG_ORDER).fillna(9)
    url = rows.sort_values(["pref", "date"], ascending=[True, False]).iloc[0]["url"]
    try:
        pages = report_pages(ReportRef(isin, url, "KID", "avl"), cache_dir)
    except Exception as exc:  # a missing KID only costs the name
        logger.info("No KID for %s: %s", isin, exc)
        return None
    return kid_fund_name(pages, isin)


# --------------------------------------------------------------------------
# One fund
# --------------------------------------------------------------------------


def rank(refs: list[ReportRef]) -> list[ReportRef]:
    """Newest period first (by period end, else publication date), English before German."""

    def key(r: ReportRef):
        d = r.period_end or r.published or ""
        return (d, -LANG_ORDER.get((r.lang or "").upper(), 5), r.kind == "semi-annual report")

    return sorted(refs, key=key, reverse=True)


def acceptable(stats: dict) -> bool:
    s = stats.get("weight_sum_pct") or 0
    return stats.get("lines", 0) > 0 and 50 <= s <= 150


def lookup(
    isin: str,
    fund_name: str | None = None,
    brands: list[str] | None = None,
    locators: tuple[str, ...] = ("wisdomtree", "avl", "extraetf"),
    cache_dir: str | Path | None = None,
    index: pd.DataFrame | None = None,
) -> ReportHoldings:
    """Holdings of the sub-fund a share-class ISIN belongs to, from its latest statutory report.

    Locators are tried in order. Each one's reports are tried newest first
    (at most :data:`MAX_DOWNLOADS` downloads in all) until one parses: lines
    found and weights summing to between 50% and 150%. If the report that
    parses is not that locator's newest (the newer one was unreadable or did
    not contain the sub-fund), the remaining locators are asked for a newer
    one, and the newest report that parses wins.
    """
    t0 = time.time()
    attempts: list[str] = []
    found_any = False
    best: tuple[ReportRef, rp.ScheduleResult, list[str]] | None = None
    accepted: tuple[ReportRef, rp.ScheduleResult, list[str]] | None = None
    downloads = 0
    for loc in locators:
        if accepted is not None and loc == "wisdomtree":
            continue
        if loc == "wisdomtree":
            if brands and not any(b.lower().startswith("wisdomtree") for b in brands):
                continue
            refs = wisdomtree_locate(isin)
        elif loc == "avl":
            index = index if index is not None else avl_index(cache_dir)
            refs = avl_locate(isin, index)
        elif loc == "extraetf":
            refs = extraetf_locate(isin)
        else:
            raise ValueError(f"Unknown locator {loc!r}")
        if not refs:
            continue
        found_any = True
        if not fund_name:
            fund_name = next((wisdomtree_fund_name(r.filename) for r in refs if r.host == "wisdomtree"), None)
        if not fund_name and index is not None:
            fund_name = avl_fund_name(isin, index, cache_dir)
        if not fund_name:
            raise LookupError(f"No fund name known for {isin}, so its sub-fund cannot be found in the report")
        ranked = rank(refs)
        for ref in ranked:
            if downloads >= MAX_DOWNLOADS:
                break
            try:
                shared = shared_report(ref, fund_name, brands, cache_dir)
                if shared is not None:
                    # the same umbrella report, already read for another share class: cite this
                    # share class's own link, and say where the text came from
                    attempts.append(f"{ref.url}: text reused from the identical report filed under {shared[0].isin} ({shared[0].url})")
                    pages = shared[1]
                else:
                    downloads += 1
                    pages = report_pages(ref, cache_dir)
            except Exception as exc:
                attempts.append(f"{ref.url}: {exc}")
                continue
            if rp.text_quality(pages) < 0.8:
                attempts.append(f"{ref.url}: no readable text layer")
                continue
            res = rp.subfund_schedule(pages, fund_name, brands, isin)
            remember_titles(ref, pages, brands, cache_dir)
            attempts.append(
                f"{ref.url}: {res.stats.get('lines', 0)} lines, sum {res.stats.get('weight_sum_pct')}"
                + ("" if res.title else ", sub-fund not found")
            )
            if acceptable(res.stats):
                if accepted is None or (res.as_of or "") > (accepted[1].as_of or ""):
                    accepted = (ref, res, pages)
                break
            if res.stats.get("lines", 0) and (best is None or res.stats["lines"] > best[1].stats.get("lines", 0)):
                best = (ref, res, pages)
        if accepted is not None and accepted[0].url == ranked[0].url:
            break       # this locator's newest report parsed: nothing newer to look for
    if accepted is not None:
        ref, res, pages = accepted
        return _result(isin, fund_name, ref, res, pages, attempts, time.time() - t0)
    if best is not None:
        ref, res, pages = best
        return _result(isin, fund_name, ref, res, pages, attempts, time.time() - t0)
    if not found_any:
        raise LookupError(f"No annual or semi-annual report found for {isin} on {', '.join(locators)}")
    raise LookupError(f"No schedule of investments for {fund_name!r} ({isin}) could be read: " + "; ".join(attempts))


def _result(isin, fund_name, ref: ReportRef, res: rp.ScheduleResult, pages, tried, seconds) -> ReportHoldings:
    df = res.holdings.copy()
    as_of = res.as_of or ref.period_end
    kind = ref.kind or res.kind
    st = dict(res.stats)
    st["seconds"] = round(seconds, 1)
    swiss = bool(re.search(r"inventory of fund assets|vermögensinventar", "\n".join(pages[i - 1] for i in st.get("pages", [])[:2]), re.I))
    host_name, _ = HOSTS[ref.host]
    notes = [
        f"{kind}, positions as of {as_of}",
        f"Schedule of investments of '{res.title}' (pages {_pages(st.get('pages', []))}) in the fund's {kind}"
        + (f" ({ref.lang} edition)" if ref.lang else "")
        + "; this is the fund's statutory report, not its current daily holdings.",
        f"Report found by ISIN on the {host_name}: {ref.url}"
        + ("" if ref.host == "wisdomtree" else " (a copy of the fund's own published report)."),
        f"{st.get('lines')} lines; weights as printed, not rescaled, sum to {st.get('weight_sum_pct')}%"
        f" (investments {st.get('invested_weight_pct')}%, the rest is cash and other net assets).",
    ]
    if swiss:
        notes.append("Swiss inventory: weights are percent of total fund assets, as the report prints them, not of net assets.")
    if st.get("isin_share", 0) < 0.5:
        notes.append(f"The report names holdings without ISINs ({st.get('isin_share', 0):.0%} of lines carry one); the others stay unresolved until matched by name.")
    if res.match_score < 0.999:
        notes.append(f"Sub-fund matched loosely: '{res.title}' for '{fund_name}'.")
    if df["name"].str.contains(r"\bswaps?\b", case=False, regex=True).any():
        notes.append("The schedule contains swaps: for a swap-based fund it lists the substitute basket and the swap, not the index it tracks.")
    if not st.get("found_total_line"):
        notes.append("The schedule has no net-assets total line; cash and other net assets are not listed and stay unresolved.")
    if not acceptable(st):
        notes.append("Parse is incomplete: the lines do not sum to near 100%; treat with care.")
    for a in tried:
        if a.startswith(ref.url) and "text reused" in a:
            msg = a.split(": ", 1)[1]
            notes.append(msg[:1].upper() + msg[1:] + ".")
    others = [a for a in tried if not a.startswith(ref.url)]
    if others:
        notes.append("Other reports tried: " + "; ".join(others))
    df = df[HOLDING_COLUMNS + [c for c in df.columns if c not in HOLDING_COLUMNS]]
    return ReportHoldings(as_of, df.reset_index(drop=True), ref.url, notes, kind, res.title, st, ref)


def _pages(p: list[int]) -> str:
    return f"{p[0]}-{p[-1]}" if p else "?"


# --------------------------------------------------------------------------
# Fund lists
# --------------------------------------------------------------------------

SIX_REF_CSV = (
    "https://www.six-group.com/fqs/ref.csv?select=FundLongName,ValorSymbol,ValorNumber,ISIN,IssuerNameFull,"
    "FundCurrency,AssetClassDesc,LegalStructureCountryDesc,ProductLineDesc"
    "&where=PortalSegment=FU&orderby=FundLongName&page=1&pagesize=99999"
)


def six_funds(cache_dir: str | Path | None = None, max_age_days: float = 1) -> pd.DataFrame:
    """Funds and ETFs listed on SIX Swiss Exchange, with issuer names (one CSV, cached a day)."""
    path = _cache(cache_dir) / "six_funds.csv"
    if not path.exists() or time.time() - path.stat().st_mtime > max_age_days * 86400:
        r = http.get(SIX_REF_CSV, timeout=120)
        r.raise_for_status()
        path.write_bytes(r.content)
    return pd.read_csv(path, sep=";", encoding="latin-1", dtype=str)


_CLASS_TAIL = re.compile(
    r"(?:-(?:Inc|Acc|Dist?)\b|\s+(?:(?!ETF\b|FCP\b)[A-Z]{1,4}\d?-?|h[A-Z]{3}|acc|dis|dist|inc|Acc|Dis|Dist|Inc|UKdis|"
    r"[A-Z]?[a-z]?-?(?:acc|dis|qdist|mdist)|\(?(?:Acc|Dis|Dist|Inc|ACC)\)|\(?hedged\)?|Hedged|-|–))+$"
)


def portfolio_key(name: str) -> str:
    """'UBS Core MSCI World UCITS ETF USD acc' -> 'ubscoremsciworlducitsetf' (shared by share classes)."""
    base = _CLASS_TAIL.sub("", name.strip())
    return rp.compact(base or name)


def products_from_six(six: pd.DataFrame, issuer: str, pattern: str, avl_isins: set[str] | None, extra_ok=None) -> pd.DataFrame:
    """Share classes of one issuer family that a report route covers."""
    rx = re.compile(pattern, re.I)
    sel = six[six["IssuerNameFull"].fillna("").str.contains(rx)]
    rows = []
    for r in sel.itertuples(index=False):
        isin = r.ISIN
        covered = (avl_isins is not None and isin in avl_isins) or (extra_ok is not None and extra_ok(r))
        if not covered:
            continue
        rows.append(
            {
                "issuer": issuer,
                "site": "six",
                "portfolio_id": portfolio_key(r.FundLongName),
                "isin": isin,
                "ticker": r.ValorSymbol or None,
                "fund_name": r.FundLongName,
                "domicile": isin[:2],
                "currency": r.FundCurrency,
                "asset_class": r.AssetClassDesc,
                "replication": None,
                "product_page_url": None,
                "holdings_url": None,
            }
        )
    from .issuers import PRODUCT_COLUMNS

    return pd.DataFrame(rows, columns=PRODUCT_COLUMNS).drop_duplicates("isin").reset_index(drop=True)
