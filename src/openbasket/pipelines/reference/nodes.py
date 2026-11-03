"""ESMA FIRDS reference data: per-ISIN lookups and an optional bulk table.

``firds_lookup`` asks ESMA's Solr API about the run's input ISINs. The bulk
nodes download FIRDS files only when ``firds.bulk.enabled`` is true. When it
is false they rebuild ``firds_instruments`` from the Parquet files already in
``firds.bulk.parquet_dir``, so turning the download off never empties the
table.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow.parquet as pq

from ...datasets import connection
from ...sources import firds

logger = logging.getLogger(__name__)

RAW_MANIFEST_COLUMNS = ["file_name", "publication_date", "zip_path", "bytes"]
PARQUET_MANIFEST_COLUMNS = ["file_name", "file_type", "as_of", "rows", "parquet_path"]


def _isins(isin: str | list[str] | None) -> list[str]:
    if isin is None:
        return []
    items = isin if isinstance(isin, list) else str(isin).replace(",", ";").split(";")
    return [i.strip().upper() for i in items if str(i).strip()]


def firds_lookup(isin: str | list[str], cache_dir: str | None) -> pd.DataFrame:
    """FIRDS reference data for the input ISINs (``isin`` param), one row per ISIN found."""
    return firds.lookup_many(_isins(isin), cache_dir=cache_dir)


def firds_file_list(bulk: dict[str, Any]) -> pd.DataFrame:
    """The FIRDS files to load: ``bulk.files`` if given, else the newest weekly snapshot plus deltas."""
    if not bulk.get("enabled"):
        return pd.DataFrame(columns=firds.FILE_COLUMNS)
    today = dt.date.today()
    lookback = int(bulk.get("lookback_days", 14))
    index = firds.list_files((today - dt.timedelta(days=lookback)).isoformat(), today.isoformat())
    files = bulk.get("files") or []
    if isinstance(files, str):  # --params=firds.bulk.files=A;B
        files = files.split(";")
    names = [str(n).strip().removesuffix(".zip") for n in files if str(n).strip()]
    if names:
        chosen = index[index["file_name"].str.removesuffix(".zip").isin(names)]
        missing = set(names) - set(chosen["file_name"].str.removesuffix(".zip"))
        if missing:
            raise ValueError(f"FIRDS files not in the last {lookback} days of the index: {sorted(missing)}")
        return chosen.reset_index(drop=True)
    return firds.select_files(index, bulk.get("cfi_letters", firds.DEFAULT_LETTERS), bulk.get("include_deltas", True))


def firds_download(files: pd.DataFrame, bulk: dict[str, Any]) -> pd.DataFrame:
    """Download each listed ZIP into ``bulk.raw_dir`` (skipped when present with the right MD5)."""
    rows = []
    for f in files.itertuples(index=False):
        dest = firds.download_file(f.download_link, Path(bulk["raw_dir"]) / f.file_name, checksum=f.checksum or None)
        rows.append({"file_name": f.file_name, "publication_date": f.publication_date, "zip_path": str(dest), "bytes": dest.stat().st_size})
    return pd.DataFrame(rows, columns=RAW_MANIFEST_COLUMNS)


def firds_to_parquet(raw_manifest: pd.DataFrame, bulk: dict[str, Any]) -> pd.DataFrame:
    """Parse new ZIPs to Parquet, then list the files ``firds_instruments`` should read.

    Every ``*.parquet`` in ``bulk.parquet_dir`` is a candidate. Of those, the
    newest FULINS snapshot and the DLTINS files after it are used; older
    snapshots and deltas are left on disk but not read.
    """
    out_dir = Path(bulk["parquet_dir"])
    letters = bulk.get("cfi_letters", firds.DEFAULT_LETTERS)
    for r in raw_manifest.itertuples(index=False):
        out = out_dir / (Path(r.zip_path).name.removesuffix(".zip") + ".parquet")
        if out.exists() and not bulk.get("overwrite", False):
            continue
        meta = firds.xml_to_parquet(r.zip_path, out, letters)
        logger.info("FIRDS: %s -> %d records", r.file_name, meta["rows"])
    rows = []
    for p in sorted(out_dir.glob("*.parquet")) if out_dir.exists() else []:
        meta = firds.parse_file_name(p.stem)
        if not meta or meta["file_type"] not in ("FULINS", "DLTINS") or p.name.endswith(".tmp.parquet"):
            continue
        n = pq.ParquetFile(p).metadata.num_rows
        rows.append({"file_name": p.stem, "file_type": meta["file_type"], "as_of": meta["date"], "rows": n, "parquet_path": str(p)})
    manifest = pd.DataFrame(rows, columns=PARQUET_MANIFEST_COLUMNS)
    return current_files(manifest)


def current_files(manifest: pd.DataFrame) -> pd.DataFrame:
    """The newest FULINS date's files plus the DLTINS files published after it."""
    full = manifest[manifest["file_type"] == "FULINS"]
    if full.empty:
        keep = manifest[manifest["file_type"] == "DLTINS"]
    else:
        snap = full["as_of"].max()
        keep = manifest[((manifest["file_type"] == "FULINS") & (manifest["as_of"] == snap)) | ((manifest["file_type"] == "DLTINS") & (manifest["as_of"] > snap))]
    return keep.sort_values(["as_of", "file_name"]).reset_index(drop=True)


def firds_instruments(parquet_manifest: pd.DataFrame) -> duckdb.DuckDBPyRelation:
    """One row per ISIN from the loaded FIRDS files (debt, funds and equities)."""
    con = connection()
    paths = [str(p) for p in parquet_manifest.get("parquet_path", [])]
    records = con.read_parquet(paths) if paths else con.from_arrow(firds.empty_records())
    # Named view: the returned relation is evaluated later, when Kedro saves it.
    records.create_view("_firds_records", replace=True)
    return con.sql(firds.instruments_sql("_firds_records"))
