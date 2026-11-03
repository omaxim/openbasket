"""Failures that must not stop a run: OpenFIGI outages, broken issuer files, a bad input ISIN."""

import json

import pandas as pd
import pytest
import requests

from openbasket import throttle
from openbasket.countries import country_code, country_label
from openbasket.datasets import connection
from openbasket.identifiers import is_placeholder_cusip
from openbasket.pipelines.lookthrough import nodes as lt_nodes
from openbasket.pipelines.security_master.nodes import cusip_isin_map, security_master
from openbasket.sources import ishares, openfigi, spdr
from openbasket.sources.openfigi import OpenFIGI


@pytest.fixture(autouse=True)
def fast_throttle(monkeypatch):
    monkeypatch.setattr(throttle, "THROTTLE", throttle.Throttle(default_s=0))
    monkeypatch.setattr(openfigi, "THROTTLE", throttle.THROTTLE)


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def _figi(tmp_path, **kw):
    f = OpenFIGI(tmp_path / "cache.parquet", api_key="k", backoff_s=0, **kw)
    f._sleep = lambda s: None
    return f


def test_openfigi_retries_5xx_and_timeouts_then_succeeds(tmp_path, monkeypatch):
    answers = iter([Resp(502), requests.Timeout("slow"), requests.ConnectionError("reset"),
                    Resp(200, [{"data": [{"name": "APPLE INC", "exchCode": "US"}]}])])

    def post(*a, **kw):
        x = next(answers)
        if isinstance(x, Exception):
            raise x
        return x

    monkeypatch.setattr(openfigi.requests, "post", post)
    f = _figi(tmp_path)
    assert f.map("ID_ISIN", ["US0378331005"])["US0378331005"][0]["name"] == "APPLE INC"
    assert f.requests == 4 and f.failures == 0


def test_openfigi_outage_never_raises_and_is_not_cached(tmp_path, monkeypatch):
    calls = []

    def post(*a, **kw):
        calls.append(1)
        return Resp(503)

    monkeypatch.setattr(openfigi.requests, "post", post)
    f = _figi(tmp_path, retries=1, max_failures=2)
    f.batch = 1
    out = f.map("ID_ISIN", ["US0378331005", "US5949181045", "US0231351067"])
    assert out == {"US0378331005": [], "US5949181045": [], "US0231351067": []}
    # Two failed batches of two attempts each, then it gives up for the run.
    assert len(calls) == 4 and f.down and f.stats()["failures"] == 2 and f.stats()["gave_up"]
    assert ("ID_ISIN", "US0378331005") not in f._cache
    assert f.map("ID_ISIN", ["US0378331005"]) == {"US0378331005": []} and len(calls) == 4


def test_openfigi_endless_429_is_a_failure_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(openfigi.requests, "post", lambda *a, **kw: Resp(429, headers={"ratelimit-reset": "0"}))
    f = _figi(tmp_path, retries=2)
    assert f.map("ID_ISIN", ["US0378331005"]) == {"US0378331005": []}
    assert f.failures == 1


def test_openfigi_checkpoints_long_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(openfigi.requests, "post", lambda url, headers, json, timeout: Resp(200, [{"data": []} for _ in json]))
    f = _figi(tmp_path, checkpoint_every=2)
    f.autosave = False
    f.batch = 1
    f.map("ID_ISIN", ["A", "B", "C"])
    assert (tmp_path / "cache.parquet").exists()


def _payload(full: bool) -> dict:
    containers = {"all": {"dataPointsByNameMap": {"isin": {"value": "['US0378331005']"},
                                                  "holdingPercent": {"value": "[7.5]"},
                                                  "asOfDate": {"value": "20261102"}}}} if full else {"ten-largest": {}}
    return {"componentsByNameMap": {"holdings": {"containersByNameMap": containers}}}


def test_issuer_response_is_cached_only_after_it_parses(tmp_path, monkeypatch):
    bodies = iter([{"unexpected": True}, _payload(True)])
    monkeypatch.setattr(ishares, "_get", lambda url, params: Resp(200, next(bodies)))
    with pytest.raises(KeyError):
        ishares.holdings("1", "uk", cache_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []
    got = ishares.holdings("1", "uk", cache_dir=tmp_path)
    assert got.as_of == "2026-11-02" and len(list(tmp_path.iterdir())) == 1


def test_ten_largest_only_is_a_typed_answer_and_is_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(ishares, "_get", lambda url, params: Resp(200, _payload(False)))
    with pytest.raises(ishares.NoFullHoldings) as e:
        ishares.holdings("2", "uk", cache_dir=tmp_path)
    assert "ten largest" in e.value.plain
    assert len(list(tmp_path.iterdir())) == 1


def test_spdr_broken_download_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(spdr, "_get", lambda url: type("R", (), {"content": b"<html>blocked</html>"})())
    with pytest.raises(Exception):
        spdr.holdings("https://www.ssga.com/x/holdings-daily-us-en-spy.xlsx", cache_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_run_lookthrough_records_a_failing_isin_and_carries_on(monkeypatch):
    real = lt_nodes.look_through

    def flaky(isin, *a, **kw):
        if isin == "US5949181045":
            raise RuntimeError("source exploded")
        return real(isin, *a, **kw)

    monkeypatch.setattr(lt_nodes, "look_through", flaky)
    empty_rel = connection().from_df(pd.DataFrame({"series_id": pd.Series([], dtype=object), "isin": pd.Series([], dtype=object),
                                                   "cusip": pd.Series([], dtype=object), "issuer_lei": pd.Series([], dtype=object)}))
    sm = connection().from_df(pd.DataFrame([{"isin": "US0378331005", "issuer_name": "Apple Inc", "title": "Apple Inc",
                                             "issuer_lei": "L", "cusip": "037833100", "ticker": "AAPL", "asset_cat": "EC",
                                             "isin_source": "reported", "n_positions": 1, "issuer_type": "CORP", "country": "US"}]))
    cmap = connection().sql("select null::varchar cusip, null::varchar isin, null::varchar isin_source where false")
    fm = pd.DataFrame(columns=["series_id", "series_name", "series_lei", "ticker", "cik"])
    params = {"source_order": ["nport"], "max_depth": 4, "min_resolved_pct": 90.0, "use_openfigi": False}
    leaves, unidentified, summary = lt_nodes.run_lookthrough(
        ["US0378331005", "US5949181045", "XX123"], empty_rel, sm, fm, pd.DataFrame(columns=["ticker", "isin"]),
        pd.DataFrame(columns=["isin", "site", "portfolio_id"]), cmap, pd.DataFrame(columns=["sedol", "isin"]), params,
    )
    s = summary.set_index("input_isin")
    assert list(s.index) == ["US0378331005", "US5949181045", "XX123"]
    assert s.at["US5949181045", "status"] == "error" and "source exploded" in s.at["US5949181045", "error"]
    assert s.at["US0378331005", "status"] == "ok" and s.at["XX123", "status"] == "no_match"
    out = lt_nodes.build_output(leaves, summary, params)
    assert "US5949181045" not in set(out["input_isin"])
    assert set(out["input_isin"]) == {"US0378331005", "XX123"}


@pytest.mark.parametrize("cusip, placeholder", [("000000000", True), ("999999999", True), ("N/A", True),
                                                ("000000AB1", True), ("037833100", False), (None, False)])
def test_placeholder_cusips(cusip, placeholder):
    assert is_placeholder_cusip(cusip) is placeholder


def test_security_master_ignores_placeholder_cusips():
    positions = connection().from_df(pd.DataFrame(
        [
            {"cusip": "000000000", "reported_isin": "IE000S9YS762", "country": "US", "issuer_name": "Fund A",
             "issuer_title": "x", "issuer_lei": "A", "reported_ticker": None, "asset_cat": "STIV", "issuer_type": "RF"},
            {"cusip": "000000000", "reported_isin": None, "country": "US", "issuer_name": "Cash sweep B",
             "issuer_title": "x", "issuer_lei": "B", "reported_ticker": None, "asset_cat": "STIV", "issuer_type": "RF"},
            {"cusip": "037833100", "reported_isin": "US0378331005", "country": "US", "issuer_name": "Apple Inc",
             "issuer_title": "x", "issuer_lei": "L", "reported_ticker": "AAPL", "asset_cat": "EC", "issuer_type": "CORP"},
            {"cusip": "037833100", "reported_isin": None, "country": "US", "issuer_name": "Apple Inc",
             "issuer_title": "x", "issuer_lei": "L", "reported_ticker": "AAPL", "asset_cat": "EC", "issuer_type": "CORP"},
        ]
    ))
    cmap = cusip_isin_map(positions)
    assert cmap.df()["cusip"].tolist() == ["037833100"]
    sm = security_master(positions, cmap).df().set_index("isin")
    # The sweep with no ISIN of its own gets none; the one that reported it keeps it, without the fake CUSIP.
    assert sm.at["US0378331005", "n_positions"] == 2
    assert sm.at["IE000S9YS762", "n_positions"] == 1 and pd.isna(sm.at["IE000S9YS762", "cusip"])


@pytest.mark.parametrize(
    "label, code",
    [("United States", "US"), ("US", "US"), ("Korea (South)", "KR"), ("Taiwan, Province of China", "TW"),
     ("Hong Kong", "HK"), ("Côte d'Ivoire", "CI"), ("Bosnia and Herzegovina", "BA"), ("Russian Federation", "RU"),
     ("Supranational", None), ("-", None)],
)
def test_country_codes(label, code):
    assert country_code(label) == code


def test_country_label_keeps_unknown_labels():
    assert country_label("Supranational") == "Supranational"
    assert country_label("United Kingdom") == "GB"
    assert country_label("-") is None and country_label(None) is None


def test_json_payload_fixture_is_valid():
    assert json.loads(json.dumps(_payload(True)))
