"""N-PORT over several quarters: quarter selection, remote tables, coverage, latest filing per series."""

import io
import os
import zipfile
from pathlib import Path

import pandas as pd
import pytest
import requests

from openbasket.datasets import DuckDBParquetDataset, connection
from openbasket.pipelines.consolidate.nodes import latest_filings, nport_holdings
from openbasket.pipelines.ingest import nodes as ingest_nodes
from openbasket.pipelines.nport_history.nodes import nport_quarter_coverage
from openbasket.pipelines.security_master.nodes import cusip_isin_map, nport_positions
from openbasket.sources import nport, sec_nport_history as h

FIX = Path(__file__).parent / "fixtures" / "sec-nport-history"
ROOT = Path(__file__).resolve().parents[1]


def _zip_bytes(quarter: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted((FIX / quarter).glob("*.tsv")):
            zf.writestr(p.name, p.read_bytes())
    return buf.getvalue()


@pytest.fixture(scope="module")
def manifest(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("nport_history")
    rows = []
    for q in ("2026q2", "2026q3"):
        zpath = tmp / f"{q}_nport.zip"
        zpath.write_bytes(_zip_bytes(q))
        rows += nport.zip_to_parquet(connection(), zpath, q, tmp / "pq", tables=nport.TABLES)
    return pd.DataFrame(rows)


TICKERS = pd.DataFrame(
    {"cik": ["0000000001", "0000000003"], "series_id": ["S000000001", "S000000003"],
     "class_id": ["C1", "C3"], "ticker": ["EXEQX", "EXBDX"]}
)


# -- quarters -------------------------------------------------------------------


def test_previous_quarters_cross_year():
    assert h.previous_quarters("2026q2", 3) == ["2026q1", "2025q4", "2025q3"]
    assert h.previous_quarters("2026q3", 0) == []


def test_resolve_quarters_adds_lookback_and_named_quarters():
    assert h.resolve_quarters(["2026q3"], {"lookback": 2, "quarters": []}) == ["2026q1", "2026q2", "2026q3"]
    assert h.resolve_quarters(["2026Q3"], {"lookback": 1, "quarters": ["2025q1", "2026q2"]}) == [
        "2025q1", "2026q2", "2026q3"
    ]
    assert h.resolve_quarters(["2026q3"], None) == ["2026q3"]
    assert h.resolve_quarters(["2026q3"], {"lookback": 0}) == ["2026q3"]
    with pytest.raises(ValueError):
        h.resolve_quarters(["2026-09"], {})
    with pytest.raises(ValueError):
        h.resolve_quarters([], {"lookback": 1})


def test_available_quarters_from_index():
    assert h.available_quarters((FIX / "index.html").read_text()) == ["2025q4", "2026q1", "2026q2", "2026q3"]


def test_configured_quarters_reads_both_parameter_files():
    qs = h.configured_quarters(ROOT / "conf" / "base")
    assert qs[-1] == h.sort_quarters(qs)[-1]
    assert len(qs) >= 2


def test_parameter_files_merge_without_collision():
    from kedro.config import OmegaConfigLoader

    params = OmegaConfigLoader(str(ROOT / "conf"), base_env="base", default_run_env="base")["parameters"]
    assert "quarters" in params["nport"]
    assert {"lookback", "quarters"} <= set(params["nport_history"])


def test_download_nport_loads_history_quarters(monkeypatch, tmp_path):
    got = []

    def fake_download(url, dest, overwrite=False):
        got.append(url)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"zip")
        return Path(dest)

    monkeypatch.setattr(ingest_nodes.sec, "download", fake_download)
    out = ingest_nodes.download_nport(["2026q3"], str(tmp_path), history={"lookback": 1, "quarters": []})
    assert out["quarter"].tolist() == ["2026q2", "2026q3"]
    assert got == [h.NPORT_URL.format(quarter="2026q2"), h.NPORT_URL.format(quarter="2026q3")]


def test_ingest_pipeline_passes_history_params():
    from openbasket.pipelines.ingest import create_pipeline

    node = next(n for n in create_pipeline().nodes if n.name == "download_nport")
    assert "params:nport_history" in node.inputs


# -- remote tables --------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status, content=b"", headers=None):
        self.status_code, self.content, self.headers = status, content, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def test_remote_tables_read_only_the_members_asked_for(monkeypatch, tmp_path):
    blob = _zip_bytes("2026q2")
    ranges = []

    def fake_request(method, url, headers=None, **kw):
        if method == "HEAD":
            return _FakeResponse(200, headers={"Accept-Ranges": "bytes", "Content-Length": str(len(blob))})
        a, b = headers["Range"].removeprefix("bytes=").split("-")
        ranges.append((int(a), int(b)))
        return _FakeResponse(206, blob[int(a): int(b) + 1])

    monkeypatch.setattr(h.http, "request", fake_request)
    rows = h.remote_tables_to_parquet("2026q2", tmp_path, headers={"User-Agent": "test"})
    assert [r["table"] for r in rows] == ["SUBMISSION", "FUND_REPORTED_INFO"]
    assert [r["rows"] for r in rows] == [4, 4]
    sub = connection().read_parquet(rows[0]["path"]).df()
    assert set(sub["QUARTER"]) == {"2026q2"}
    assert not list(tmp_path.glob("**/*.tsv"))  # extracted text is removed
    assert all(0 <= a <= b < len(blob) for a, b in ranges)


def test_range_file_refuses_servers_without_ranges(monkeypatch):
    monkeypatch.setattr(h.http, "request", lambda *a, **k: _FakeResponse(200, headers={"Content-Length": "10"}))
    with pytest.raises(OSError):
        h.RangeFile("https://example.org/x.zip")


# -- latest filing across quarters ------------------------------------------------


def test_latest_filing_per_series_across_quarters(manifest):
    lf = latest_filings(manifest).set_index("series_id")
    assert set(lf.index) == {"S000000001", "S000000002", "S000000003", "S000000004", "S000000005"}
    assert lf.at["S000000001", "accession_number"] == "C-S1-Q3"  # newer report in the newer quarter
    assert lf.at["S000000003", "quarter"] == "2026q2"  # only filed in the older quarter
    assert lf.at["S000000004", "accession_number"] == "C-S4-Q2A"  # amendment in a later quarter wins
    assert h.duplicate_accessions(manifest) == 0
    assert h.duplicate_holding_ids(manifest) == 0


def test_repeated_holding_ids_are_detected(tmp_path):
    rows = []
    for q in ("2026q2", "2026q3"):
        pq = tmp_path / f"{q}.parquet"
        connection().execute(f"copy (select '1' as HOLDING_ID, '{q}' as QUARTER) to '{pq}' (format parquet)")
        rows.append({"quarter": q, "table": "FUND_REPORTED_HOLDING", "path": str(pq), "rows": 1})
    assert h.duplicate_holding_ids(pd.DataFrame(rows)) == 1


def test_quarter_coverage_counts_added_series(manifest):
    cov = nport_quarter_coverage(manifest, TICKERS).set_index("quarter")
    assert cov.index.tolist() == ["2026q3", "2026q2"]
    assert cov.at["2026q3", "series_total"] == 3  # the closed-end filing without a series is not counted
    assert cov.at["2026q2", "series_total"] == 5
    assert cov.at["2026q2", "series_added"] == 2
    assert cov.at["2026q2", "added_with_ticker"] == 1
    assert cov.at["2026q2", "added_final_filing"] == 1
    assert cov.at["2026q2", "added_between_filings"] == 1  # S3: May report, next one not due yet
    assert cov.at["2026q2", "added_net_assets_usd"] == 500
    assert str(cov.at["2026q2", "added_report_date_max"])[:10] == "2026-05-31"


def test_holdings_of_a_series_come_from_its_own_quarter(manifest, tmp_path):
    pos = nport_positions(manifest)
    lf = latest_filings(manifest)
    ds = DuckDBParquetDataset(str(tmp_path / "h.parquet"), order_by="series_id")
    ds.save(nport_holdings(pos, lf, cusip_isin_map(pos)))
    hold = ds.load().df()
    s3 = hold[hold["series_id"] == "S000000003"]
    assert sorted(s3["holding_id"]) == ["104", "105"]
    assert s3["weight_pct"].sum() == pytest.approx(99.8)  # as reported, never rescaled
    assert str(s3["report_date"].iloc[0])[:10] == "2026-05-31"
    s4 = hold[hold["series_id"] == "S000000004"]
    assert s4["holding_id"].tolist() == ["203"]  # the original filing's lines are not mixed in
    s1 = hold[hold["series_id"] == "S000000001"]
    assert sorted(s1["holding_id"]) == ["201", "202"]
    assert set(s1["isin"]) == {"US0378331005", "US5949181045"}


# -- network --------------------------------------------------------------------


@pytest.mark.network
def test_sec_lists_quarters_and_serves_one_table_by_range(tmp_path):
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    if not os.environ.get("SEC_USER_AGENT"):
        pytest.skip("SEC_USER_AGENT is not set")
    quarters = h.available_quarters()
    assert "2026q1" in quarters and len(quarters) >= 20
    older = quarters[-2]
    rows = h.remote_tables_to_parquet(older, tmp_path, tables=("SUBMISSION",))
    assert rows[0]["rows"] > 5000
    sub = connection().read_parquet(rows[0]["path"]).df()
    assert sub["SUB_TYPE"].str.startswith("NPORT-P").all()
