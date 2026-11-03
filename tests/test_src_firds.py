import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from openbasket.datasets import DuckDBParquetDataset
from openbasket.pipelines.reference import nodes
from openbasket.sources import firds

FIXTURES = Path(__file__).parent / "fixtures" / "firds"
ROOT = Path(__file__).resolve().parents[1]
FULINS = FIXTURES / "FULINS_C_20260103_01of01.xml"
DLTINS = FIXTURES / "DLTINS_20260105_01of01.xml"


class FakeResponse:
    def __init__(self, payload=None, content=b""):
        self._payload = payload
        self._content = content
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        yield self._content

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _zip(xml: Path, dest_dir: Path) -> Path:
    dest = dest_dir / (xml.stem + ".zip")
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(xml, xml.name)
    return dest


# CFI -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "cfi, expected",
    [
        ("CEOGES", "fund"),
        ("CIOILU", "fund"),
        ("CFOIMS", "fund"),
        ("CBCIXS", "equity"),
        ("DBFNGB", "debt"),
        ("DTVUFB", "debt"),
        ("ESVUFR", "equity"),
        ("EDSXFR", "equity"),
        ("RWSNCA", "other"),
        ("FFICSX", "other"),
        ("", "unknown"),
        (None, "unknown"),
        ("CE", "unknown"),
    ],
)
def test_classify_cfi(cfi, expected):
    assert firds.classify_cfi(cfi) == expected


def test_classify_sql_matches_python():
    from openbasket.datasets import connection

    cfis = ["CEOGES", "CBCIXS", "DBFNGB", "ESVUFR", "RWSNCA", "XX", None]
    df = pd.DataFrame({"cfi": cfis})  # noqa: F841 - read by DuckDB below
    got = connection().sql(f"select {firds.classify_sql('cfi')} as c from df").df()["c"].tolist()
    assert got == [firds.classify_cfi(c) for c in cfis]


def test_classify_from_table_and_mapping():
    table = pd.DataFrame({"isin": ["CH0000000015", "CH0000000023"], "cfi": ["CEOIEU", "DBFNGB"]})
    assert firds.classify("ch0000000015", table) == "fund"
    assert firds.classify("CH0000000023", table) == "debt"
    assert firds.classify("US0000000010", table) == "unknown"
    assert firds.classify("US0000000010", {"US0000000010": "ESVUFR"}) == "equity"
    assert firds.cfi_kind("CEOIEU") == "exchange-traded fund"


# Solr lookup -----------------------------------------------------------------


def test_instruments_from_solr_dedupes_per_isin():
    docs = json.loads((FIXTURES / "solr_select.json").read_text())["response"]["docs"]
    df = firds.instruments_from_solr(docs)
    assert list(df.columns) == firds.INSTRUMENT_COLUMNS
    assert df["isin"].tolist() == ["CH0000000015", "XS0000000017"]
    etf = df.iloc[0]
    assert etf["asset_class"] == "fund"
    assert etf["venue"] == "XETA"  # the relevant venue's record
    assert etf["full_name"] == "Example SMI ETF (CH) Inhaber-Anteile"  # name most venues use
    assert (etf["n_venues"], etf["n_active_venues"]) == (3, 2)
    assert pd.isna(etf["termination_date"])
    assert etf["as_of"] == "2026-01-04"  # newest publication date, not today
    assert etf["issuer_lei"] == "529900EXAMPLE0000001"
    assert etf["source"] == "esma_firds" and "ESMA" in etf["credit"]
    assert etf["url"].startswith(firds.SOLR_URL) and "CH0000000015" in etf["url"]
    frn = df.iloc[1]
    assert frn["asset_class"] == "debt"
    assert frn["interest_type"] == "floating"
    assert pd.isna(frn["coupon_pct"])  # Solr's fixed rate "0" is not the coupon of an FRN
    assert frn["floating_ref"] == "EU0000000016" and frn["floating_spread_bps"] == 25
    assert frn["maturity_date"] == "2028-06-30"
    assert frn["nominal_total"] == 500_000_000


def test_instruments_from_solr_empty():
    df = firds.instruments_from_solr([])
    assert df.empty and list(df.columns) == firds.INSTRUMENT_COLUMNS


def test_lookup_many_batches_and_caches(monkeypatch, tmp_path):
    payload = json.loads((FIXTURES / "solr_select.json").read_text())
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(params)
        return FakeResponse(payload)

    monkeypatch.setattr(firds.http, "get", fake_get)
    isins = ["CH0000000015", "XS0000000017", "IE0000000012", "not-an-isin"]
    df = firds.lookup_many(isins, cache_dir=tmp_path, today="2026-01-05")
    assert len(calls) == 1
    assert "isin:(CH0000000015 OR XS0000000017 OR IE0000000012)" in calls[0]["q"]
    assert "latest_received_flag:1" in calls[0]["q"]
    assert df["isin"].tolist() == ["CH0000000015", "XS0000000017"]
    day = tmp_path / "firds" / "2026-01-05"
    assert json.loads((day / "IE0000000012.json").read_text()) == []  # not in FIRDS, cached for the day
    assert len(json.loads((day / "CH0000000015.json").read_text())) == 3

    again = firds.lookup_many(isins[:3], cache_dir=tmp_path, today="2026-01-05")
    assert len(calls) == 1  # served from the cache
    pd.testing.assert_frame_equal(again, df)

    rec = firds.lookup("CH0000000015", cache_dir=tmp_path, today="2026-01-05")
    assert rec["cfi"] == "CEOIEU" and rec["maturity_date"] is None
    assert firds.classify("CH0000000015", cache_dir=tmp_path, today="2026-01-05") == "fund"
    assert firds.lookup("IE0000000012", cache_dir=tmp_path, today="2026-01-05") is None


def test_lookup_does_not_cache_a_bad_response(monkeypatch, tmp_path):
    def fake_get(url, params=None, **kw):
        return FakeResponse({"response": {"numFound": 1, "docs": [{"isin": "CH0000000015", "publication_date": 5}]}})

    monkeypatch.setattr(firds.http, "get", fake_get)
    monkeypatch.setattr(firds, "instruments_from_solr", lambda docs: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(ValueError):
        firds.lookup_many(["CH0000000015"], cache_dir=tmp_path, today="2026-01-05")
    assert not (tmp_path / "firds" / "2026-01-05" / "CH0000000015.json").exists()


# Bulk files ------------------------------------------------------------------


def test_iter_records_fulins():
    recs = list(firds.iter_records(FULINS))
    assert len(recs) == 5
    r = recs[0]
    assert r["record_type"] == "RefData"
    assert (r["isin"], r["cfi"], r["issuer_lei"], r["currency"]) == ("CH0000000015", "CEOIEU", "529900EXAMPLE0000001", "CHF")
    assert (r["mic"], r["rca_mic"], r["as_of"], r["file_name"]) == ("XETA", "XETA", "2026-01-03", "FULINS_C_20260103_01of01")
    assert r["first_trade_date"] == "2010-01-04" and r["termination_date"] is None
    assert recs[3]["termination_date"] == "2025-12-31"
    assert [x["seq"] for x in recs] == [1, 2, 3, 4, 5]


def test_iter_records_dltins_from_zip_filters_cfi(tmp_path):
    recs = list(firds.iter_records(_zip(DLTINS, tmp_path)))
    assert [r["isin"] for r in recs] == ["CH0000000023", "XS0000000017", "XS0000000025", "US0000000010", "IE0000000012", "CH0000000015"]
    assert "DE0000000017" not in {r["isin"] for r in recs}  # R (warrant) dropped
    assert len(list(firds.iter_records(DLTINS, letters=None))) == 7
    assert [r["record_type"] for r in recs] == ["NewRcrd", "ModfdRcrd", "TermntdRcrd", "NewRcrd", "ModfdRcrd", "CancRcrd"]
    bond, frn, zero = recs[:3]
    assert (bond["coupon_pct"], bond["interest_type"], bond["maturity_date"]) == (1.25, "fixed", "2033-03-15")
    assert (bond["nominal_total"], bond["nominal_currency"], bond["seniority"]) == (250_000_000, "CHF", "SNDB")
    assert (frn["interest_type"], frn["coupon_pct"], frn["floating_ref"], frn["floating_spread_bps"]) == ("floating", None, "EU0000000016", 25.0)
    assert (zero["interest_type"], zero["coupon_pct"]) == ("zero", 0.0)
    assert all(r["as_of"] == "2026-01-05" for r in recs)
    assert recs[3]["maturity_date"] is None and recs[3]["coupon_pct"] is None


def _instruments(tmp_path):
    from openbasket.datasets import connection

    paths = [str(firds.xml_to_parquet(p, tmp_path / (p.stem + ".parquet"))["parquet_path"]) for p in (FULINS, DLTINS)]
    rel = connection().read_parquet(paths)
    rel.create_view("_test_firds_records", replace=True)
    return connection().sql(firds.instruments_sql("_test_firds_records")).df()


def test_snapshot_plus_delta(tmp_path):
    df = _instruments(tmp_path).set_index("isin")
    assert list(df.reset_index().columns) == firds.INSTRUMENT_COLUMNS
    assert sorted(df.index) == ["CH0000000015", "CH0000000023", "IE0000000012", "IE0000000020", "LU0000000017", "US0000000010", "XS0000000017", "XS0000000025"]
    # a cancelled venue disappears
    assert df.loc["CH0000000015", "n_venues"] == 1
    assert df.loc["CH0000000015", "full_name"] == "Example SMI ETF (CH) Inhaber-Anteile"
    # a later delta overrides the snapshot
    assert df.loc["IE0000000012", "full_name"].endswith("renamed")
    assert df.loc["IE0000000012", "as_of"] == "2026-01-05"
    # terminated everywhere: no active venue, termination date kept
    assert df.loc["IE0000000020", "n_active_venues"] == 0 and df.loc["IE0000000020", "termination_date"] == "2025-12-31"
    assert df.loc["XS0000000025", "termination_date"] == "2026-01-02"
    # a future termination (bond maturity) is still active
    assert df.loc["CH0000000023", "n_active_venues"] == 1 and pd.isna(df.loc["CH0000000023", "termination_date"])
    assert df.loc["LU0000000017", "asset_class"] == "equity"  # REIT
    assert df["asset_class"].to_dict() == {
        "CH0000000015": "fund",
        "CH0000000023": "debt",
        "IE0000000012": "fund",
        "IE0000000020": "fund",
        "LU0000000017": "equity",
        "US0000000010": "equity",
        "XS0000000017": "debt",
        "XS0000000025": "debt",
    }
    assert df.loc["US0000000010", "relevant_venue"] == "XFRA" and df.loc["US0000000010", "venue"] == "XGAT"
    assert all(u == firds.solr_url(i) for i, u in df["url"].items())  # a direct link per ISIN


def test_parse_file_name_and_select_files():
    assert firds.parse_file_name("FULINS_D_20261031_01of04.zip") == {"file_type": "FULINS", "letter": "D", "date": "2026-10-31", "part": 1, "parts": 4}
    assert firds.parse_file_name("DLTINS_20261102_01of01")["letter"] is None
    assert firds.parse_file_name("something.zip") is None
    index = firds.files_frame(json.loads((FIXTURES / "files_select.json").read_text())["response"]["docs"])
    assert "" not in set(index["file_name"])
    chosen = firds.select_files(index, ["C", "D", "E"])
    assert chosen["file_name"].tolist() == [
        "FULINS_C_20260103_01of01.zip",
        "FULINS_D_20260103_01of02.zip",
        "FULINS_D_20260103_02of02.zip",
        "FULINS_E_20260103_01of01.zip",
        "DLTINS_20260106_01of02.zip",
        "DLTINS_20260106_02of02.zip",
    ]  # same-day delta is already in the snapshot; R and the older snapshot are left out
    assert len(firds.select_files(index, ["C"], include_deltas=False)) == 1
    # an incomplete set (D part 2 missing) falls back to the deltas only
    partial = index[index["file_name"] != "FULINS_D_20260103_02of02.zip"]
    assert firds.select_files(partial, ["C", "D"])["file_name"].str.startswith("DLTINS").all()


def test_download_file_checks_md5(monkeypatch, tmp_path):
    import hashlib

    body = b"zip bytes"
    monkeypatch.setattr(firds.http, "get", lambda url, **kw: FakeResponse(content=body))
    good = hashlib.md5(body).hexdigest()
    p = firds.download_file("https://firds.esma.europa.eu/firds/X.zip", tmp_path / "X.zip", checksum=good)
    assert p.read_bytes() == body
    with pytest.raises(ValueError):
        firds.download_file("https://firds.esma.europa.eu/firds/Y.zip", tmp_path / "Y.zip", checksum="0" * 32)
    assert not (tmp_path / "Y.zip").exists() and not (tmp_path / "Y.zip.part").exists()


# Pipeline --------------------------------------------------------------------


def test_reference_pipeline_nodes_offline(tmp_path):
    bulk = {
        "enabled": False,
        "cfi_letters": ["C", "D", "E"],
        "raw_dir": str(tmp_path / "raw"),
        "parquet_dir": str(tmp_path / "parquet"),
    }
    files = nodes.firds_file_list(bulk)
    assert files.empty and list(files.columns) == firds.FILE_COLUMNS
    assert nodes.firds_download(files, bulk).empty

    zips = [_zip(p, tmp_path) for p in (FULINS, DLTINS)]
    raw = pd.DataFrame({"file_name": [z.name for z in zips], "publication_date": ["2026-01-03", "2026-01-05"], "zip_path": [str(z) for z in zips], "bytes": [1, 1]})
    manifest = nodes.firds_to_parquet(raw, bulk)
    assert manifest["file_name"].tolist() == ["FULINS_C_20260103_01of01", "DLTINS_20260105_01of01"]
    assert manifest["rows"].tolist() == [5, 6]

    ds = DuckDBParquetDataset(str(tmp_path / "firds_instruments.parquet"), order_by="isin")
    ds.save(nodes.firds_instruments(manifest))
    out = ds.load().df()
    assert len(out) == 8 and list(out.columns) == firds.INSTRUMENT_COLUMNS

    # Download off and nothing new: rebuilt from the Parquet files on disk.
    again = nodes.firds_to_parquet(pd.DataFrame(columns=nodes.RAW_MANIFEST_COLUMNS), bulk)
    pd.testing.assert_frame_equal(again, manifest)

    # No files at all: an empty table with the right columns.
    empty = nodes.firds_instruments(pd.DataFrame(columns=nodes.PARQUET_MANIFEST_COLUMNS)).df()
    assert empty.empty and list(empty.columns) == firds.INSTRUMENT_COLUMNS


def test_current_files_drops_old_snapshot():
    m = pd.DataFrame(
        {
            "file_name": ["FULINS_C_20251227_01of01", "DLTINS_20251229_01of01", "FULINS_C_20260103_01of01", "DLTINS_20260105_01of01"],
            "file_type": ["FULINS", "DLTINS", "FULINS", "DLTINS"],
            "as_of": ["2025-12-27", "2025-12-29", "2026-01-03", "2026-01-05"],
            "rows": [1, 1, 1, 1],
            "parquet_path": ["a", "b", "c", "d"],
        }
    )
    assert nodes.current_files(m)["file_name"].tolist() == ["FULINS_C_20260103_01of01", "DLTINS_20260105_01of01"]


def test_isin_param_parsing():
    assert nodes._isins("CH0008899764; ie00b5bmr087") == ["CH0008899764", "IE00B5BMR087"]
    assert nodes._isins(["CH0008899764"]) == ["CH0008899764"]
    assert nodes._isins(None) == []


def test_kedro_config_and_pipeline_registered():
    from kedro.config import OmegaConfigLoader
    from kedro.framework.project import find_pipelines
    from kedro.framework.startup import bootstrap_project
    from kedro.io import DataCatalog

    bootstrap_project(ROOT)
    loader = OmegaConfigLoader(str(ROOT / "conf"), base_env="base", default_run_env="local")
    catalog_conf = loader["catalog"]
    for name in ["firds_lookup", "firds_files", "firds_raw_manifest", "firds_parquet_manifest", "firds_instruments"]:
        assert name in catalog_conf
    DataCatalog.from_config({k: v for k, v in catalog_conf.items() if k.startswith("firds_")})
    params = loader["parameters"]
    assert params["firds"]["bulk"]["enabled"] is False
    assert "isin" in params
    pipes = find_pipelines(raise_errors=True)
    assert "reference" in pipes
    assert {n.name for n in pipes["reference"].nodes} == {"firds_lookup", "firds_file_list", "firds_download", "firds_to_parquet", "firds_instruments"}


# Network ---------------------------------------------------------------------


@pytest.mark.network
def test_live_lookup_swiss_etf_and_bond(tmp_path):
    df = firds.lookup_many(["CH0008899764", "CH1619473973"], cache_dir=tmp_path)
    df = df.set_index("isin")
    etf, bond = df.loc["CH0008899764"], df.loc["CH1619473973"]
    assert etf["asset_class"] == "fund" and etf["cfi"].startswith("CE")
    assert etf["issuer_lei"] == "549300513WH1XYPSM312" and etf["currency"] == "CHF"
    assert bond["asset_class"] == "debt" and bond["maturity_date"] == "2035-11-23"
    assert bond["coupon_pct"] == pytest.approx(1.4125) and bond["currency"] == "CHF"
    assert df["as_of"].notna().all()


def test_interest_type_of_undefined_debt_cfi():
    """A zero fixed rate on a CFI whose interest letter is X (undefined) is not called zero-coupon."""
    assert firds._interest("DBXXXX", "0", None) == (None, None)
    assert firds._interest("DBZUFB", "0", None) == ("zero", 0.0)
    assert firds._interest("DBFTFB", "1.5", None) == ("fixed", 1.5)
