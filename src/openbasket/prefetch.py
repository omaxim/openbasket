"""Read every selected fund's issuer file before the universe run, one worker per host.

The universe pipeline expands funds one after another, so a full local run
waits for every issuer site in turn: its time is the sum over the sites.
Prefetching does the network part first, with one worker thread per host, so
the sites are read side by side and the time is about that of the slowest
site. The universe run that follows finds every file in the per-day cache
(``lookthrough.issuer_cache_dir``) and makes almost no issuer requests.

Politeness does not change:

- every request still waits for its host's slot in the process-wide
  :data:`openbasket.throttle.THROTTLE`, through :mod:`openbasket.http` or the
  iShares and SPDR clients, so a host never sees more than one request per
  configured interval, however many workers run;
- a host's jobs run one after another on its own worker, so a host is never
  asked two things at once;
- a ``Crawl-delay`` that an adapter declares (``CRAWL_DELAY`` in its module)
  or that robots.txt states only ever lengthens a host's interval, and a
  job whose URL robots.txt disallows is skipped;
- a ``local_only`` source (issuer sites, iShares and SPDR) is never fetched
  in public mode.

A job is one fund portfolio: share classes of one portfolio are one job (the
same grouping the universe uses, so the share class fetched is the one the
universe asks for), and portfolios whose holdings sit in one shared document
(an umbrella fund's report) are one job whose portfolios are read in turn, so
the document is downloaded once when the adapter caches it.

Each job calls the same ``holdings`` function the pipeline calls, with the
same ``cache_dir``, so a file is cached exactly as the universe run would
cache it: only once it parses.

What the worker threads touch
-----------------------------

The workers only call issuer ``holdings`` functions (iShares, SPDR and the
registered adapters), which use :mod:`openbasket.http` or
:mod:`openbasket.throttle` (both guard their per-host state with a lock) and
write their own files in the cache directory. Everything else is done on the
calling thread before the workers start or after they stop: reading the
product table, grouping, the robots and redistribution gates, the report.
The workers never use the shared DuckDB connection
(:func:`openbasket.datasets.connection`), the OpenFIGI client or the
providers' ``lru_cache`` methods. Adapters that read a local store instead of
the network (``cnmv_spain``, ``cvm_brazil``, or any adapter whose module sets
``PREFETCH = False``) are not scheduled at all: there is nothing to fetch
and their stores are not meant to be shared between threads.

An adapter can tell the prefetch where its requests go, in its module:
``prefetch_url(product) -> str`` (the URL ``holdings`` will request first),
``HOLDINGS_HOST`` (one host for every product), ``document_key(product)``
(products with the same key share one document) and ``CRAWL_DELAY``
(seconds). Without them the host is taken from the product's
``holdings_url``, the adapter's ``SITES[site]["host"]``, its module ``HOST``
or its homepage, in that order. A wrong guess costs speed, never
politeness: the throttle keys on the real host of every request.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import urllib.robotparser
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import pandas as pd
import requests

from . import http, throttle
from .sources import LOCAL_ONLY, PUBLIC, ishares, redistribution, spdr
from .sources.issuers import FetchContext, IssuerAdapter

logger = logging.getLogger(__name__)

#: Issuers read by code in :mod:`openbasket.sources` rather than by an adapter.
LEGACY = ("ishares", "spdr")

#: Adapters whose ``holdings`` read a store built in bulk by their own
#: pipeline, so a fund needs no request of its own.
LOCAL_STORE_ADAPTERS = frozenset({"cnmv_spain", "cvm_brazil"})

#: A ``holdings_url`` with one of these endings is a document; portfolios
#: that share one are one job. Any other shared URL (an API endpoint such as
#: a GraphQL URL) is not a document and does not merge jobs.
DOCUMENT_SUFFIXES = (".pdf", ".xlsx", ".xls", ".csv", ".zip", ".xml", ".txt", ".json")

#: robots.txt product token, as in :mod:`openbasket.sources.robots`.
AGENT = "openbasket"

#: Universe selection keys the prefetch follows, so it reads what the next universe run reads.
SELECTION_KEYS = ("sources", "ids", "sample", "seed", "limit")


# -- planning ---------------------------------------------------------------


@dataclass
class Job:
    """One download: one portfolio, or several portfolios that share one document."""

    source: str                # "ishares", "spdr" or an adapter name
    key: str                   # what makes two portfolios one job
    host: str                  # netloc the job's requests go to
    url: str | None            # the URL requested first, when it is known
    members: list[dict] = field(default_factory=list)  # product rows, one per portfolio


def publish_mode(params: dict | None, publish_params: dict | None = None) -> str:
    """``"public"`` or ``"local"``.

    ``publish.mode`` decides when it is set; otherwise ``prefetch.mode``
    (``conf/public`` sets it to ``public``); otherwise ``$KEDRO_ENV ==
    "public"``. Anything that is not exactly ``local`` counts as public, so a
    typo never lets a public build read a ``local_only`` source.
    """
    mode = (publish_params or {}).get("mode") or (params or {}).get("mode")
    if mode is None:
        mode = "public" if os.environ.get("KEDRO_ENV", "").strip().lower() == "public" else "local"
    return "local" if str(mode).strip().lower() == "local" else "public"


def _product(row: dict) -> dict:
    """The product dict ``holdings`` gets, as the providers build it: NaN dropped, ``None`` kept."""
    return {k: v for k, v in row.items() if not (isinstance(v, float) and pd.isna(v))}


def products_by_issuer_id(issuer_products: pd.DataFrame) -> dict[str, dict]:
    """One product row per issuer fund ID, chosen as :class:`openbasket.providers.DataProviders` chooses it.

    That is the first share class listed, with a product page borrowed from
    another class when it has none, so the job calls ``holdings`` with the
    row the universe run will call it with.
    """
    from .providers import issuer_id

    ip = issuer_products.copy()
    if "issuer" not in ip:
        ip["issuer"] = "ishares"
    ip["issuer"] = ip["issuer"].fillna("ishares").astype(str).str.lower()
    ip = ip.astype(object).where(ip.notna(), None)
    out: dict[str, dict] = {}
    for r in ip.to_dict("records"):
        first = out.setdefault(issuer_id(r["issuer"], r["site"], r["portfolio_id"]), r)
        if not first.get("product_page_url") and r.get("product_page_url"):
            first["product_page_url"] = r["product_page_url"]
    return out


def select_funds(issuer_products: pd.DataFrame, universe_params: dict | None) -> pd.DataFrame:
    """The issuer funds the universe run with the same ``universe`` parameters will expand.

    Share classes are grouped exactly as the universe groups them, and
    ``sources``, ``ids``, ``sample``, ``seed`` and ``limit`` select from them
    the same way. N-PORT series are not issuer funds and are left out; an
    ``ids`` entry that is a series ID therefore selects nothing here.
    """
    from .pipelines.universe.nodes import UNIVERSE_COLUMNS, _issuer_funds, select_universe

    funds = _issuer_funds(issuer_products, {})
    frame = pd.DataFrame(funds, columns=UNIVERSE_COLUMNS) if funds else pd.DataFrame(columns=UNIVERSE_COLUMNS)
    return select_universe(frame, {k: (universe_params or {}).get(k) for k in SELECTION_KEYS})


def _module(adapter: IssuerAdapter):
    return sys.modules.get(getattr(adapter.holdings, "__module__", "") or "")


def _netloc(url: object) -> str | None:
    if not isinstance(url, str) or "://" not in url:
        return None
    return urlparse(url).netloc.lower() or None


def _is_document(url: object) -> bool:
    return isinstance(url, str) and urlparse(url).path.lower().endswith(DOCUMENT_SUFFIXES)


def _ishares_target(product: dict) -> tuple[str, str | None, str]:
    site = str(product.get("site") or "uk").lower()
    cfg = getattr(ishares, "SITES", {}).get(site) or {}
    tmpl = getattr(ishares, "PRODUCT_DATA_URL", None)
    url = tmpl.format(data_site=cfg["data_site"]) if tmpl and cfg.get("data_site") else None
    host = _netloc(url) or _netloc(getattr(ishares, "HOST", None)) or "www.blackrock.com"
    return host, url, f"ishares:{site}:{product.get('portfolio_id')}"


def _adapter_target(adapter: IssuerAdapter, product: dict, iid: str) -> tuple[str, str | None, str]:
    mod = _module(adapter)
    url = None
    if mod is not None and callable(getattr(mod, "prefetch_url", None)):
        url = mod.prefetch_url(product)
    url = url or (product.get("holdings_url") if _netloc(product.get("holdings_url")) else None)
    host = _netloc(url)
    if host is None and mod is not None:
        host = (str(getattr(mod, "HOLDINGS_HOST", "") or "").lower() or None)
        site_cfg = (getattr(mod, "SITES", None) or {}).get(product.get("site")) if host is None else None
        if host is None and isinstance(site_cfg, dict):
            host = _netloc(site_cfg.get("host"))
        host = host or _netloc(getattr(mod, "HOST", None))
    host = host or _netloc(adapter.homepage) or adapter.name
    key = None
    if mod is not None and callable(getattr(mod, "document_key", None)):
        key = mod.document_key(product)
    if key is None and _is_document(url):
        key = url
    return host, url, f"{adapter.name}:{key}" if key else iid


def skip_reason(source: str, adapters: dict[str, IssuerAdapter], mode: str) -> str | None:
    """Why a source's funds are not prefetched, or ``None`` when they are."""
    if source in LEGACY:
        cls = redistribution(source)
    elif source in adapters:
        adapter = adapters[source]
        cls = adapter.redistribution
        mod = _module(adapter)
        if source in LOCAL_STORE_ADAPTERS or (mod is not None and getattr(mod, "PREFETCH", True) is False):
            return "reads a local store; nothing to fetch per fund"
    else:
        return "adapter not enabled (issuers.enabled / issuers.disabled)"
    if mode != "local" and cls != PUBLIC:
        return f"{cls or LOCAL_ONLY} source; public mode never fetches it"
    return None


def plan(
    issuer_products: pd.DataFrame,
    adapters: dict[str, IssuerAdapter],
    *,
    mode: str = "local",
    universe_params: dict | None = None,
) -> tuple[list[Job], dict]:
    """The jobs to run, and what was left out and why.

    Returns ``(jobs, info)``; ``info`` has ``n_funds`` (selected issuer
    funds), ``n_share_classes``, ``skipped`` (source -> reason, with a fund
    count) and ``merged`` (portfolios that joined another's job because they
    share its document).
    """
    funds = select_funds(issuer_products, universe_params)
    by_id = products_by_issuer_id(issuer_products)
    jobs: dict[tuple[str, str], Job] = {}
    skipped: dict[str, dict] = {}
    n_classes = merged = 0
    for f in funds.to_dict("records"):
        source, iid = f["universe_source"], f["issuer_id"]
        n_classes += len(f.get("isins") or [])
        why = skip_reason(source, adapters, mode)
        if why is None and source == "spdr" and not _netloc((by_id.get(iid) or {}).get("holdings_url")):
            why = "no holdings file listed"
        if why is not None or iid not in by_id:
            entry = skipped.setdefault(source, {"reason": why or "not in issuer_products", "funds": 0})
            entry["funds"] += 1
            continue
        product = _product(by_id[iid])
        if source == "ishares":
            host, url, key = _ishares_target(product)
        elif source == "spdr":
            url = product["holdings_url"]
            host, key = _netloc(url), f"spdr:{url}"
        else:
            host, url, key = _adapter_target(adapters[source], product, iid)
        job = jobs.get((source, key))
        if job is None:
            jobs[(source, key)] = Job(source=source, key=key, host=host, url=url, members=[product])
        else:
            job.members.append(product)
            merged += 1
    info = {"n_funds": len(funds), "n_share_classes": n_classes, "skipped": skipped, "merged": merged}
    return list(jobs.values()), info


# -- fetching ---------------------------------------------------------------


def fetcher(source: str, adapters: dict[str, IssuerAdapter], cache_dir: str | None) -> Callable[[dict], object]:
    """The call the universe makes for one product of ``source``, with the same cache."""
    if source == "ishares":
        return lambda p: ishares.holdings(str(p["portfolio_id"]), str(p["site"]).lower(), cache_dir=cache_dir)
    if source == "spdr":
        return lambda p: spdr.holdings(p["holdings_url"], cache_dir=cache_dir)
    adapter = adapters[source]
    return lambda p: adapter.holdings(p, FetchContext(cache_dir=cache_dir))


class _Meter:
    """Counts requests and response bytes per worker thread while it is installed.

    It wraps :meth:`requests.Session.send`, which every request made with
    ``requests`` passes through (``openbasket.http`` and the iShares and
    SPDR clients alike), and counts on the thread that sent the request.
    """

    def __init__(self):
        self.local = threading.local()
        self._orig = None

    def start(self) -> None:
        self.local.counts = None
        self._orig = requests.Session.send
        orig, local = self._orig, self.local

        def send(session, request, **kw):
            r = orig(session, request, **kw)
            counts = getattr(local, "counts", None)
            if counts is not None:
                counts[0] += 1
                if kw.get("stream"):
                    counts[1] += int(r.headers.get("Content-Length") or 0)
                else:
                    counts[1] += len(r.content or b"")
            return r

        requests.Session.send = send

    def stop(self) -> None:
        if self._orig is not None:
            requests.Session.send = self._orig
            self._orig = None

    def track(self) -> list[int]:
        """Start counting on this thread; returns ``[requests, bytes]``, updated in place."""
        self.local.counts = [0, 0]
        return self.local.counts


@dataclass
class _Robots:
    allows: Callable[[str], bool]
    crawl_delay: float | None
    summary: str


def robots_for(host: str) -> _Robots:
    """robots.txt rules for ``host``: from :mod:`openbasket.sources.robots` when it is installed, else read here.

    Read here, the file is fetched once through :mod:`openbasket.http` and
    parsed with :mod:`urllib.robotparser`; as RFC 9309 says, a missing file
    (4xx) allows everything and an unreachable one (5xx or a network error)
    allows nothing.
    """
    try:
        from .sources import robots  # optional: added with the robots-alternatives sources
    except ImportError:
        robots = None
    if robots is not None:
        st = robots.status(host)
        delay = None
        if st.parser is not None:
            try:
                delay = st.parser.crawl_delay(robots.AGENT)
            except Exception:  # an odd Crawl-delay value is no delay
                delay = None
        return _Robots(st.allows, float(delay) if delay else None, st.summary())
    url = f"https://{host}/robots.txt"
    try:
        r = http.get(url, timeout=30, retries=2, allow_redirects=True)
    except Exception as exc:  # unreachable: assume complete disallow
        return _Robots(_allow_none, None, f"{host}: robots.txt unreachable ({type(exc).__name__}); nothing fetched")
    if r.status_code >= 500:
        return _Robots(_allow_none, None, f"{host}: robots.txt HTTP {r.status_code}; nothing fetched")
    if r.status_code >= 400:
        return _Robots(_allow_all, None, f"{host}: no robots.txt (HTTP {r.status_code})")
    rp = urllib.robotparser.RobotFileParser(url)
    rp.parse(r.text.splitlines())
    delay = rp.crawl_delay(AGENT)
    return _Robots(lambda u: rp.can_fetch(AGENT, u), float(delay) if delay else None,
                   f"{host}: robots.txt HTTP {r.status_code}")


def _allow_all(url: str) -> bool:
    return True


def _allow_none(url: str) -> bool:
    return False


def lengthen_interval(host: str, seconds: float) -> float:
    """Make the minimum interval for ``host`` at least ``seconds`` everywhere; never shortens it.

    Returns the host's interval now in force.
    """
    t = throttle.THROTTLE
    current = max(t.interval(host), http._intervals.get(host, http.DEFAULT_MIN_INTERVAL))
    if seconds > current:
        t.per_host_s[host] = float(seconds)
        http.set_min_interval(host, float(seconds))
        return float(seconds)
    return current


def _crawl_delay(jobs: list[Job], adapters: dict[str, IssuerAdapter]) -> float | None:
    """The longest ``CRAWL_DELAY`` declared by an adapter module with jobs on this host."""
    delays = []
    for source in {j.source for j in jobs}:
        mod = _module(adapters[source]) if source in adapters else sys.modules.get(f"{__package__}.sources.{source}")
        d = getattr(mod, "CRAWL_DELAY", None) if mod is not None else None
        if isinstance(d, (int, float)) and d > 0:
            delays.append(float(d))
    return max(delays) if delays else None


def _run_host(
    host: str,
    jobs: list[Job],
    adapters: dict[str, IssuerAdapter],
    cache_dir: str | None,
    meter: _Meter,
    *,
    check_robots: bool,
    log_every: int,
    log_every_s: float,
    errors_kept: int,
) -> dict:
    """Run one host's jobs in turn on the calling thread; never raises."""
    t0 = time.perf_counter()
    counts = meter.track()
    out = {"sources": sorted({j.source for j in jobs}), "jobs": len(jobs), "calls": 0, "ok": 0, "failed": 0,
           "skipped": 0, "top10_only": 0, "seconds": 0.0, "requests": 0, "bytes": 0, "req_per_s": 0.0,
           "interval_s": None, "crawl_delay_s": None, "robots": None, "errors": []}
    allows: Callable[[str], bool] = _allow_all
    try:
        delay = _crawl_delay(jobs, adapters)
        if check_robots:
            rb = robots_for(host)
            out["robots"] = rb.summary
            allows = rb.allows
            if rb.crawl_delay:
                delay = max(delay or 0.0, rb.crawl_delay)
        out["crawl_delay_s"] = delay
        out["interval_s"] = lengthen_interval(host, delay) if delay else max(
            throttle.THROTTLE.interval(host), http._intervals.get(host, http.DEFAULT_MIN_INTERVAL))
    except Exception as exc:  # robots trouble skips this host's jobs, never the run
        out["robots"] = f"{host}: robots.txt check failed ({type(exc).__name__}: {exc}); nothing fetched"
        allows = _allow_none
    fetch = {s: fetcher(s, adapters, cache_dir) for s in out["sources"]}
    last_log = time.perf_counter()
    for i, job in enumerate(jobs, 1):
        if job.url and not allows(job.url):
            out["skipped"] += 1
            if len(out["errors"]) < errors_kept:
                out["errors"].append(f"{job.key}: robots.txt disallows {job.url}")
        else:
            failed = False
            for product in job.members:
                out["calls"] += 1
                try:
                    fetch[job.source](product)
                except ishares.NoFullHoldings:
                    out["top10_only"] += 1  # a real answer, cached by the client
                except Exception as exc:  # one broken file must not stop the host's other jobs
                    failed = True
                    if len(out["errors"]) < errors_kept:
                        out["errors"].append(f"{job.key}: {type(exc).__name__}: {exc}"[:300])
                    logger.debug("prefetch %s %s failed", host, job.key, exc_info=True)
            out["failed" if failed else "ok"] += 1
        now = time.perf_counter()
        if i == len(jobs) or (log_every and i % log_every == 0) or now - last_log >= log_every_s:
            last_log = now
            elapsed = now - t0
            rate = counts[0] / elapsed if elapsed else 0.0
            left = elapsed / i * (len(jobs) - i)
            logger.info("prefetch %s: %d/%d jobs, %d failed, %d skipped, %d requests (%.2f req/s), about %.0f min to go",
                        host, i, len(jobs), out["failed"], out["skipped"], counts[0], rate, left / 60)
    out["seconds"] = round(time.perf_counter() - t0, 2)
    out["requests"], out["bytes"] = counts[0], counts[1]
    out["req_per_s"] = round(counts[0] / out["seconds"], 3) if out["seconds"] else 0.0
    return out


def _host_seconds_guess(host: str) -> float:
    return max(throttle.THROTTLE.interval(host), http._intervals.get(host, http.DEFAULT_MIN_INTERVAL))


def run(
    jobs: list[Job],
    adapters: dict[str, IssuerAdapter],
    cache_dir: str | None,
    *,
    max_hosts: int = 16,
    check_robots: bool = True,
    log_every: int = 50,
    log_every_s: float = 60.0,
    errors_kept: int = 5,
) -> dict:
    """Run ``jobs`` with one worker per host, at most ``max_hosts`` at once.

    Hosts with the most work start first, so the slowest host sets the wall
    time rather than the order. Returns ``{"hosts": {host: stats}, ...}``.
    """
    by_host: dict[str, list[Job]] = {}
    for j in jobs:
        by_host.setdefault(j.host, []).append(j)
    order = sorted(by_host, key=lambda h: (-len(by_host[h]) * _host_seconds_guess(h), h))
    workers = max(1, min(int(max_hosts or 1), len(order) or 1))
    t0 = time.perf_counter()
    hosts: dict[str, dict] = {}
    meter = _Meter()
    meter.start()
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="prefetch") as pool:
            futures = {
                pool.submit(_run_host, h, by_host[h], adapters, cache_dir, meter, check_robots=check_robots,
                            log_every=log_every, log_every_s=log_every_s, errors_kept=errors_kept): h
                for h in order
            }
            for fut in as_completed(futures):
                h = futures[fut]
                try:
                    hosts[h] = fut.result()
                except Exception as exc:  # _run_host never raises; this is a last guard
                    hosts[h] = {"jobs": len(by_host[h]), "ok": 0, "failed": len(by_host[h]), "errors": [repr(exc)]}
    finally:
        meter.stop()
    wall = time.perf_counter() - t0
    host_sum = sum(v.get("seconds", 0.0) for v in hosts.values())
    return {
        "workers": workers,
        "wall_seconds": round(wall, 2),
        "sum_host_seconds": round(host_sum, 2),
        "speedup": round(host_sum / wall, 2) if wall else None,
        "hosts": {h: hosts[h] for h in sorted(hosts)},
    }


def estimate(full_jobs: list[Job], hosts: dict[str, dict], max_hosts: int) -> dict:
    """Wall time of a prefetch over ``full_jobs``, from the rates this run measured.

    A host measured here (with at least one request per two jobs, so the
    files were not already cached) costs its measured seconds per job;
    other hosts cost their interval per job. The wall time schedules the
    hosts, longest first, onto ``max_hosts`` workers.
    """
    per_host: dict[str, int] = {}
    for j in full_jobs:
        per_host[j.host] = per_host.get(j.host, 0) + 1
    est = {}
    for h, n in per_host.items():
        m = hosts.get(h) or {}
        measured = m.get("jobs") and m.get("requests", 0) >= 0.5 * m["jobs"] and m.get("seconds")
        spj = m["seconds"] / m["jobs"] if measured else _host_seconds_guess(h)
        est[h] = {"jobs": n, "seconds_per_job": round(spj, 3), "minutes": round(n * spj / 60, 1),
                  "measured": bool(measured)}
    loads = [0.0] * max(1, int(max_hosts or 1))
    for minutes in sorted((v["minutes"] for v in est.values()), reverse=True):
        i = loads.index(min(loads))
        loads[i] += minutes
    return {"hosts": dict(sorted(est.items())), "wall_minutes": round(max(loads), 1),
            "sequential_minutes": round(sum(v["minutes"] for v in est.values()), 1)}


def prefetch(
    issuer_products: pd.DataFrame,
    adapters: dict[str, IssuerAdapter],
    *,
    cache_dir: str | None,
    params: dict | None = None,
    universe_params: dict | None = None,
    publish_params: dict | None = None,
) -> dict:
    """Plan and run the prefetch; returns the report."""
    p = params or {}
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    mode = publish_mode(p, publish_params)
    up = dict(universe_params or {})
    if p.get("sources"):
        up["sources"] = p["sources"]
    jobs, info = plan(issuer_products, adapters, mode=mode, universe_params=up)
    for source, entry in sorted(info["skipped"].items()):
        logger.info("prefetch: %s skipped (%d funds): %s", source, entry["funds"], entry["reason"])
    max_hosts = int(p.get("max_hosts", 16) or 16)
    n_hosts = len({j.host for j in jobs})
    logger.info("prefetch (%s mode): %d jobs for %d funds on %d hosts, %d workers", mode, len(jobs), info["n_funds"],
                n_hosts, min(max_hosts, n_hosts))
    result = run(jobs, adapters, cache_dir, max_hosts=max_hosts, check_robots=bool(p.get("check_robots", True)),
                 log_every=int(p.get("log_every", 50) or 0), log_every_s=float(p.get("log_every_s", 60)),
                 errors_kept=int(p.get("errors_kept", 5)))
    full_jobs, _ = plan(issuer_products, adapters, mode=mode,
                        universe_params={k: v for k, v in up.items() if k == "sources"})
    totals = {k: sum(int(h.get(k) or 0) for h in result["hosts"].values())
              for k in ("jobs", "calls", "ok", "failed", "skipped", "top10_only", "requests", "bytes")}
    report = {
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "cache_dir": cache_dir,
        "max_hosts": max_hosts,
        "selection": {k: up.get(k) for k in SELECTION_KEYS},
        "n_funds": info["n_funds"],
        "n_share_classes": info["n_share_classes"],
        "n_jobs": len(jobs),
        "merged_shared_documents": info["merged"],
        "skipped_sources": info["skipped"],
        **totals,
        **result,
        "full_run_estimate": estimate(full_jobs, result["hosts"], max_hosts),
    }
    logger.info("prefetch: %d/%d jobs ok, %d failed, %d skipped in %.0f s (%.0f s of host time, %.1fx)",
                totals["ok"], len(jobs), totals["failed"], totals["skipped"], result["wall_seconds"],
                result["sum_host_seconds"], result["speedup"] or 0)
    return report
