"""The prefetch step: hosts in parallel, each host at its own pace, nothing local_only in public mode.

Everything here is offline: three fake hosts (a.test, b.test, c.test) are
served by a fake ``requests.Session.send``, and the throttle runs on a
virtual clock, one timeline per worker thread, so pacing is checked exactly
and no test sleeps.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from itertools import pairwise
from pathlib import Path

import pandas as pd
import pytest
import requests

from openbasket import http, prefetch, throttle
from openbasket.pipelines.prefetch.nodes import prefetch_issuer_holdings
from openbasket.sources import ishares, spdr
from openbasket.sources.issuers import HOLDING_COLUMNS, IssuerAdapter, IssuerHoldings

INTERVAL = 1.0


class VirtualTime:
    """``time.monotonic`` and ``time.sleep`` on a per-thread virtual clock: sleeping only moves the clock."""

    def __init__(self):
        self.local = threading.local()

    def monotonic(self) -> float:
        return getattr(self.local, "t", 0.0)

    def sleep(self, seconds: float) -> None:
        self.local.t = self.monotonic() + max(0.0, seconds)


class Web:
    """The fake hosts. Records every request with the sending thread's virtual time."""

    def __init__(self, clock: VirtualTime):
        self.clock = clock
        self.lock = threading.Lock()
        self.log: list[tuple[str, str, float]] = []  # (host, path, virtual time)
        self.robots = {"c.test": "User-agent: *\nCrawl-delay: 2\nDisallow: /private/\n"}
        self.bodies: dict[str, bytes] = {}

    def send(self, session, request, **kw):
        u = requests.utils.urlparse(request.url)
        with self.lock:
            self.log.append((u.netloc, u.path, self.clock.monotonic()))
        r = requests.Response()
        r.url = request.url
        r.request = request
        if u.path == "/robots.txt":
            text = self.robots.get(u.netloc)
            r.status_code, r._content = (200, text.encode()) if text else (404, b"")
        else:
            r.status_code, r._content = 200, self.bodies.get(request.url, b"holdings for " + u.path.encode())
        return r

    def times(self, host: str, robots: bool = True) -> list[float]:
        return [t for h, p, t in self.log if h == host and (robots or p != "/robots.txt")]


@pytest.fixture
def web(monkeypatch):
    clock = VirtualTime()
    t = throttle.Throttle(default_s=INTERVAL)
    for mod in (throttle, http, ishares, spdr):
        monkeypatch.setattr(mod, "THROTTLE", t)
    monkeypatch.setattr(throttle, "time", clock)
    monkeypatch.setattr(http, "_intervals", {})
    w = Web(clock)
    monkeypatch.setattr(requests.Session, "send", w.send)
    return w


def _fake_holdings(product: dict, ctx) -> IssuerHoldings:
    """An adapter's ``holdings``: per-day cache, written only after the file parses."""
    url = product["holdings_url"]
    cache = Path(ctx.cache_dir) / f"fake_{product['issuer']}_{product['portfolio_id']}_{date.today().isoformat()}.txt"
    if cache.exists():
        text = cache.read_text()
    else:
        text = http.get(url).text
        if "broken" in url:
            raise ValueError("the file does not parse")
        cache.write_text(text)
    return IssuerHoldings(as_of=None, holdings=pd.DataFrame(columns=HOLDING_COLUMNS), url=url)


def _adapter(name: str, redistribution: str = "local_only", holdings=_fake_holdings) -> IssuerAdapter:
    return IssuerAdapter(
        name=name, title=name.title(), homepage=f"https://{name}.test", credit=name, licence="test", regions=("XX",),
        products=lambda: pd.DataFrame(), holdings=holdings, redistribution=redistribution,
    )


def _row(issuer, pid, isin, url, site="xx", name=None, tna=None) -> dict:
    return {"issuer": issuer, "site": site, "portfolio_id": pid, "isin": isin, "ticker": None,
            "fund_name": name or f"{issuer} {pid}", "domicile": "IE", "currency": "USD", "asset_class": "Equity",
            "replication": "physical", "product_page_url": None, "holdings_url": url, "total_net_assets": tna}


def _three_hosts(n: int = 4) -> pd.DataFrame:
    rows = []
    for host, issuer in (("a.test", "alpha"), ("b.test", "beta"), ("c.test", "gamma")):
        rows += [_row(issuer, f"{issuer[0]}{i}", f"XS{issuer[0].upper()}{i:09d}", f"https://{host}/funds/{i}.csv")
                 for i in range(n)]
    return pd.DataFrame(rows)


def _adapters(**kw) -> dict[str, IssuerAdapter]:
    return {n: _adapter(n, **kw) for n in ("alpha", "beta", "gamma")}


def _run(products, adapters, tmp_path, **params):
    return prefetch.prefetch(products, adapters, cache_dir=str(tmp_path), params={"max_hosts": 16, **params},
                             universe_params={}, publish_params={})


def test_hosts_run_concurrently(web, tmp_path):
    """(a) Each host's first file waits until all three hosts are being read at once."""
    barrier = threading.Barrier(3, timeout=10)
    seen: set[str] = set()
    lock = threading.Lock()

    def holdings(product, ctx):
        host = requests.utils.urlparse(product["holdings_url"]).netloc
        with lock:
            first = host not in seen
            seen.add(host)
        if first:
            barrier.wait()  # BrokenBarrierError, a failed job, unless the hosts overlap
        return _fake_holdings(product, ctx)

    report = _run(_three_hosts(), {n: _adapter(n, holdings=holdings) for n in ("alpha", "beta", "gamma")}, tmp_path)
    assert report["workers"] == 3
    assert {h: (v["ok"], v["failed"]) for h, v in report["hosts"].items()} == {
        "a.test": (4, 0), "b.test": (4, 0), "c.test": (4, 0)}


def test_one_host_never_exceeds_its_rate(web, tmp_path):
    """(b) Requests to one host are at least its interval apart; robots.txt Crawl-delay lengthens it."""
    report = _run(_three_hosts(6), _adapters(), tmp_path)
    for host, gap in (("a.test", INTERVAL), ("b.test", INTERVAL), ("c.test", 2.0)):
        times = web.times(host)
        assert len(times) == 7  # robots.txt and six files
        assert all(b - a >= gap - 1e-9 for a, b in pairwise(times)), (host, times)
    assert report["hosts"]["c.test"]["crawl_delay_s"] == 2.0
    assert report["hosts"]["c.test"]["interval_s"] == 2.0
    assert report["hosts"]["a.test"]["interval_s"] == INTERVAL
    assert report["hosts"]["a.test"]["requests"] == 7 and report["hosts"]["a.test"]["bytes"] > 0


def test_a_shared_host_is_still_paced_when_two_workers_reach_it(web, tmp_path, monkeypatch):
    """The pace is the throttle's, by the real host of each request, not the host the plan guessed."""

    def misdirected(product, ctx):
        # gamma's products name c.test, so gamma gets its own worker, but its requests go to a.test.
        return _fake_holdings({**product, "holdings_url": product["holdings_url"].replace("c.test", "a.test")}, ctx)

    adapters = {**_adapters(), "gamma": _adapter("gamma", holdings=misdirected)}
    products = _three_hosts(3)
    # One clock for every thread, so the two workers' slots on a.test are comparable.
    monkeypatch.setattr(throttle.time, "monotonic", lambda: 0.0)
    granted: list[float] = []
    serial = threading.Lock()
    real_wait = throttle.Throttle.wait

    def wait(self, url, min_interval=None):
        with serial:  # sleeps are virtual, so this blocks nobody; it pairs each wait with its slot
            real_wait(self, url, min_interval)
            if "//a.test/" in url:
                granted.append(self._last["a.test"])

    monkeypatch.setattr(throttle.Throttle, "wait", wait)
    report = _run(products, adapters, tmp_path, check_robots=False)
    assert report["workers"] == 3 and report["ok"] == 9
    assert len(granted) == 6
    assert granted == sorted(granted) and all(b - a >= INTERVAL - 1e-9 for a, b in pairwise(granted)), granted


def test_one_failing_job_does_not_stop_the_others(web, tmp_path):
    """(c) A file that does not parse fails its job only; it is not cached."""
    products = _three_hosts(3)
    products.loc[products["portfolio_id"] == "b1", "holdings_url"] = "https://b.test/funds/broken.csv"
    report = _run(products, _adapters(), tmp_path)
    b = report["hosts"]["b.test"]
    assert (b["jobs"], b["ok"], b["failed"]) == (3, 2, 1)
    assert "does not parse" in b["errors"][0]
    assert all(report["hosts"][h]["failed"] == 0 for h in ("a.test", "c.test"))
    assert (report["ok"], report["failed"]) == (8, 1)
    cached = sorted(p.name.split("_")[2] for p in tmp_path.iterdir())
    assert cached == ["a0", "a1", "a2", "b0", "b2", "g0", "g1", "g2"]


def test_robots_disallowed_job_is_skipped(web, tmp_path):
    products = _three_hosts(2)
    products.loc[products["portfolio_id"] == "g1", "holdings_url"] = "https://c.test/private/1.csv"
    report = _run(products, _adapters(), tmp_path)
    c = report["hosts"]["c.test"]
    assert (c["ok"], c["skipped"]) == (1, 1)
    assert not [p for h, p, _ in web.log if p.startswith("/private/")]


def test_public_mode_never_schedules_local_only(web, tmp_path):
    """(d) In public mode only public adapters are planned: no local_only adapter, no iShares, no SPDR."""
    products = pd.concat([
        _three_hosts(2),
        pd.DataFrame([_row("ishares", "253743", "IE00B5BMR087", None, site="uk", name="iShares Core S&P 500"),
                      _row("spdr", "spy", "US78462F1030", "https://www.ssga.com/x/holdings-daily-us-en-spy.xlsx",
                           site="us")]),
    ], ignore_index=True)
    adapters = {"alpha": _adapter("alpha"), "beta": _adapter("beta", "public"), "gamma": _adapter("gamma")}
    jobs, info = prefetch.plan(products, adapters, mode="public")
    assert {j.source for j in jobs} == {"beta"}
    assert set(info["skipped"]) == {"alpha", "gamma", "ishares", "spdr"}
    assert all("public mode" in v["reason"] for v in info["skipped"].values())

    report = prefetch.prefetch(products, adapters, cache_dir=str(tmp_path), params={"mode": "public"},
                               universe_params={}, publish_params={})
    assert {h for h, _, _ in web.log} == {"b.test"}
    assert report["mode"] == "public" and set(report["hosts"]) == {"b.test"}


@pytest.mark.parametrize(
    "params, publish, env, expected",
    [
        ({}, {}, "", "local"),
        ({"mode": "public"}, {}, "", "public"),
        ({"mode": "local"}, {"mode": "public"}, "", "public"),   # publish.mode wins
        ({}, {}, "public", "public"),                             # --env public without the parameter
        ({"mode": "Pubic"}, {}, "", "public"),                    # anything but "local" is public
        ({"mode": "local"}, {}, "public", "local"),
    ],
)
def test_publish_mode(monkeypatch, params, publish, env, expected):
    monkeypatch.setenv("KEDRO_ENV", env)
    assert prefetch.publish_mode(params, publish) == expected


def test_local_store_and_disabled_adapters_are_not_scheduled():
    products = pd.DataFrame([_row("cnmv_spain", "1", "ES0000000001", None), _row("nobody", "2", "XS0000000002", None)])
    adapters = {"cnmv_spain": _adapter("cnmv_spain", "public")}
    jobs, info = prefetch.plan(products, adapters, mode="local")
    assert jobs == []
    assert "local store" in info["skipped"]["cnmv_spain"]["reason"]
    assert "not enabled" in info["skipped"]["nobody"]["reason"]


def test_dedupe_share_classes_and_shared_documents():
    """(e) Share classes of one portfolio are one job; portfolios in one umbrella document are one job."""
    umbrella = "https://c.test/reports/umbrella-annual-report.pdf"
    products = pd.DataFrame([
        # one adapter portfolio, three share classes
        _row("alpha", "p1", "XSA000000001", "https://a.test/funds/p1.csv", tna=30.0),
        _row("alpha", "p1", "XSA000000002", "https://a.test/funds/p1.csv", tna=20.0),
        _row("alpha", "p1", "XSA000000003", "https://a.test/funds/p1.csv", tna=10.0),
        # two sub-funds whose holdings are in one report
        _row("gamma", "s1", "XSC000000001", umbrella),
        _row("gamma", "s2", "XSC000000002", umbrella),
        # one API endpoint for every fund is not a shared document
        _row("beta", "q1", "XSB000000001", "https://b.test/graphql"),
        _row("beta", "q2", "XSB000000002", "https://b.test/graphql"),
        # iShares share classes: separate portfolio ids, one fund name; the largest is the one read
        _row("ishares", "100", "IE0000000011", None, site="uk", name="iShares Example UCITS ETF", tna=5.0),
        _row("ishares", "101", "IE0000000012", None, site="uk", name="iShares Example UCITS ETF", tna=50.0),
    ])
    jobs, info = prefetch.plan(products, _adapters(), mode="local")
    by_source = {}
    for j in jobs:
        by_source.setdefault(j.source, []).append(j)
    assert len(by_source["alpha"]) == 1 and len(by_source["alpha"][0].members) == 1
    assert len(by_source["gamma"]) == 1
    assert [m["portfolio_id"] for m in by_source["gamma"][0].members] == ["s1", "s2"]
    assert len(by_source["beta"]) == 2
    assert [[m["portfolio_id"] for m in j.members] for j in by_source["ishares"]] == [["101"]]
    assert by_source["ishares"][0].host == "www.blackrock.com"
    assert info["merged"] == 1 and info["n_funds"] == 6 and info["n_share_classes"] == 9


def test_umbrella_document_is_downloaded_once(web, tmp_path):
    """Both sub-funds are read, but the shared report is fetched once when the adapter caches by document."""
    umbrella = "https://c.test/reports/umbrella.pdf"

    def holdings(product, ctx):
        cache = Path(ctx.cache_dir) / f"doc_{date.today().isoformat()}.pdf"
        if not cache.exists():
            cache.write_bytes(http.get(product["holdings_url"]).content)
        return IssuerHoldings(as_of=None, holdings=pd.DataFrame(columns=HOLDING_COLUMNS), url=umbrella)

    products = pd.DataFrame([_row("gamma", "s1", "XSC000000001", umbrella), _row("gamma", "s2", "XSC000000002", umbrella)])
    report = _run(products, {"gamma": _adapter("gamma", holdings=holdings)}, tmp_path)
    c = report["hosts"]["c.test"]
    assert (c["jobs"], c["calls"], c["ok"]) == (1, 2, 1)
    assert web.times("c.test", robots=False) == [pytest.approx(2.0)]  # once, after robots.txt and its Crawl-delay


def test_selection_follows_universe_sample(web):
    """The prefetch reads exactly the issuer funds ``universe.sample`` picks."""
    from openbasket.pipelines.universe.nodes import _issuer_funds, select_universe

    products = _three_hosts(8)
    params = {"sample": 3, "seed": 7}
    jobs, _ = prefetch.plan(products, _adapters(), universe_params=params)
    universe = pd.DataFrame(_issuer_funds(products, {}))
    expected = select_universe(universe, params)["issuer_id"]
    got = {f"{m['issuer']}-{m['site']}:{m['portfolio_id']}" for j in jobs for m in j.members}
    assert got == set(expected) and len(got) == 9


def test_universe_then_reads_the_cache(web, tmp_path):
    """After the prefetch, the universe's own call to the same ``holdings`` makes no request."""
    products = _three_hosts(2)
    adapters = _adapters()
    _run(products, adapters, tmp_path, check_robots=False)
    before = len(web.log)
    from openbasket.sources.issuers import FetchContext

    for p in products.to_dict("records"):
        adapters[p["issuer"]].holdings(p, FetchContext(cache_dir=str(tmp_path)))
    assert len(web.log) == before


def test_ishares_and_spdr_jobs_use_their_clients_and_cache(web, tmp_path, monkeypatch):
    payload = {"componentsByNameMap": {"holdings": {"containersByNameMap": {"all": {"dataPointsByNameMap": {
        "asOfDate": {"value": "20261102"}, "isin": {"value": "['US0378331005']"},
        "issueName": {"value": "['APPLE']"}, "holdingPercent": {"value": "[100.0]"}}}}}}}
    monkeypatch.setattr(ishares.requests, "get", lambda url, params=None, **kw: _json_response(payload))
    monkeypatch.setattr(spdr, "_get", lambda url, params=None: _bytes_response(b"not a workbook"))
    products = pd.DataFrame([
        _row("ishares", "253743", "IE00B5BMR087", None, site="uk", name="iShares Core S&P 500"),
        _row("spdr", "spy", "US78462F1030", "https://www.ssga.com/x/holdings-daily-us-en-spy.xlsx", site="us"),
    ])
    report = _run(products, {}, tmp_path, check_robots=False)
    assert report["hosts"]["www.blackrock.com"]["ok"] == 1
    assert report["hosts"]["www.ssga.com"]["failed"] == 1  # not a workbook: failed, and not cached
    assert [p.name for p in tmp_path.iterdir()] == [f"uk_253743_{date.today().isoformat()}.json"]


def _json_response(body):
    r = requests.Response()
    r.status_code, r._content = 200, json.dumps(body).encode()
    return r


def _bytes_response(content: bytes):
    r = requests.Response()
    r.status_code, r._content = 200, content
    return r


def test_estimate_schedules_hosts_longest_first():
    jobs = [prefetch.Job("x", str(i), host, None) for host, n in (("a", 60), ("b", 30), ("c", 30)) for i in range(n)]
    hosts = {"a": {"jobs": 10, "requests": 10, "seconds": 10.0}}  # measured: 1 s per job
    est = prefetch.estimate(jobs, hosts, max_hosts=2)
    assert est["hosts"]["a"] == {"jobs": 60, "seconds_per_job": 1.0, "minutes": 1.0, "measured": True}
    assert est["sequential_minutes"] == 2.0 and est["wall_minutes"] == 1.0


def test_node_configures_the_throttle_and_writes_to_the_issuer_cache(web, tmp_path, monkeypatch):
    monkeypatch.setattr("openbasket.pipelines.prefetch.nodes.issuer_adapters", lambda p: _adapters())
    report = prefetch_issuer_holdings(
        _three_hosts(1), {"max_hosts": 2, "check_robots": False},
        {"http": {"default_interval_s": 3.0, "per_host_interval_s": {"b.test": 5.0}}, "sample": None},
        {}, {"issuer_cache_dir": str(tmp_path)}, {},
    )
    assert report["workers"] == 2 and report["ok"] == 3
    assert throttle.THROTTLE.default_s == 3.0 and throttle.THROTTLE.interval("b.test") == 5.0
    assert len(list(tmp_path.iterdir())) == 3


def test_prefetch_is_not_in_the_default_pipeline(monkeypatch):
    from openbasket import pipeline_registry
    from openbasket.pipelines import ingest
    from openbasket.pipelines import prefetch as prefetch_pipeline

    found = {"ingest": ingest.create_pipeline(), "prefetch": prefetch_pipeline.create_pipeline()}
    monkeypatch.setattr(pipeline_registry, "find_pipelines", lambda raise_errors: dict(found))
    pipelines = pipeline_registry.register_pipelines()
    default = {n.name for n in pipelines["__default__"].nodes}
    assert default and "prefetch_issuer_holdings" not in default
    assert {n.name for n in pipelines["prefetch"].nodes} == {"prefetch_issuer_holdings"}
