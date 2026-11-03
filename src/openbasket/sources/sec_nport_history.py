"""SEC Form N-PORT across several quarterly data sets.

The SEC's Division of Economic and Risk Analysis (DERA) publishes one ZIP per
calendar quarter of *filing* date (``{YYYY}q{N}_nport.zip``). A fund files a
public N-PORT report once a quarter, up to 60 days after the period, so most
series appear in every quarter's file. Some do not appear in the newest one:
the 60th day fell just inside the previous quarter, the fund changed its
fiscal quarter, it was late, or it liquidated. Loading older quarters as well
brings those series back with the portfolio of their last public filing; the
existing :func:`openbasket.sources.nport.latest_filings` still keeps only
each series' latest report date (and the latest amendment of it).

This module

- works out which quarters to load (:func:`resolve_quarters`) from
  ``nport.quarters`` and ``nport_history`` in the parameters;
- lists the quarters the SEC publishes (:func:`available_quarters`);
- reads single tables out of a quarter's ZIP on sec.gov with HTTP range
  requests (:func:`remote_tables_to_parquet`), so the small tables of an older
  quarter can be checked without downloading 450 MB;
- measures how many series each older quarter adds (:func:`quarter_coverage`).

Terms: "Information presented on sec.gov is considered public information and
may be copied or further distributed by users of the web site without the
SEC's permission." (https://www.sec.gov/privacy, *Website Dissemination*).
N-PORT data is a work of the US government and in the public domain.
"""

from __future__ import annotations

import io
import logging
import re
import shutil
import zipfile
from pathlib import Path

import duckdb
import pandas as pd

from .. import http
from ..datasets import connection
from . import nport, sec

logger = logging.getLogger(__name__)

SEC_HOST = "www.sec.gov"
INDEX_URL = "https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets"
NPORT_URL = sec.NPORT_URL
PRIVACY_URL = "https://www.sec.gov/privacy"

LICENCE = (
    "US government public domain. SEC Website Dissemination policy: \"Information presented on sec.gov is "
    "considered public information and may be copied or further distributed by users of the web site without "
    "the SEC's permission.\" (https://www.sec.gov/privacy)"
)
CREDIT = "Holdings from SEC Form N-PORT filings, U.S. Securities and Exchange Commission"

#: The SEC asks for at most ten requests a second; one a second is plenty.
http.set_min_interval(SEC_HOST, 1.0)

#: Tables small enough to read remotely for measuring coverage.
SMALL_TABLES = ("SUBMISSION", "FUND_REPORTED_INFO")

_QUARTER_RE = re.compile(r"^(\d{4})q([1-4])$")
_INDEX_RE = re.compile(r"form-n-port-data-sets/(\d{4}q[1-4])_nport\.zip")


# Quarters ---------------------------------------------------------------------------------------------------------


def parse_quarter(quarter: str) -> tuple[int, int]:
    """``"2026q3"`` -> ``(2026, 3)``; raises ``ValueError`` for anything else."""
    m = _QUARTER_RE.match(str(quarter).strip().lower())
    if not m:
        raise ValueError(f"Not an N-PORT quarter (expected e.g. 2026q3): {quarter!r}")
    return int(m.group(1)), int(m.group(2))


def format_quarter(year: int, q: int) -> str:
    return f"{year}q{q}"


def previous_quarters(quarter: str, n: int) -> list[str]:
    """The ``n`` quarters before ``quarter``, newest first."""
    year, q = parse_quarter(quarter)
    out = []
    for _ in range(max(int(n), 0)):
        year, q = (year - 1, 4) if q == 1 else (year, q - 1)
        out.append(format_quarter(year, q))
    return out


def sort_quarters(quarters) -> list[str]:
    """Unique quarters, oldest first (the order ``nport.quarters`` uses)."""
    seen = {format_quarter(*parse_quarter(q)) for q in quarters}
    return sorted(seen, key=parse_quarter)


def resolve_quarters(base: list[str], history: dict | None = None) -> list[str]:
    """Quarters the ingest loads: ``nport.quarters`` plus the older ones ``nport_history`` asks for.

    ``history`` is the ``nport_history`` parameter block:

    ``lookback``
        Number of quarters before the newest of ``base`` to add (``2`` with
        ``base = [2026q3]`` gives ``2026q1, 2026q2, 2026q3``).
    ``quarters``
        Further quarters to add by name, e.g. ``[2025q4]``.

    Returns the union, oldest first. With no ``history`` (or ``lookback: 0``
    and no ``quarters``), ``base`` is returned unchanged apart from sorting.
    """
    history = history or {}
    base = list(base or [])
    extra = list(history.get("quarters") or [])
    if not base and not extra:
        raise ValueError("No N-PORT quarters configured (nport.quarters and nport_history.quarters are empty)")
    lookback = int(history.get("lookback") or 0)
    newest = sort_quarters(base or extra)[-1]
    return sort_quarters(base + extra + previous_quarters(newest, lookback))


def available_quarters(index_html: str | None = None) -> list[str]:
    """Quarters linked from the SEC's N-PORT data set page, oldest first."""
    if index_html is None:
        r = http.get(INDEX_URL, headers={"User-Agent": sec.user_agent()})
        r.raise_for_status()
        index_html = r.text
    return sort_quarters(set(_INDEX_RE.findall(index_html)))


# Reading single tables from a ZIP on sec.gov -----------------------------------------------------------------------


class RangeFile(io.RawIOBase):
    """A read-only, seekable file over HTTP range requests.

    ``zipfile`` only needs ``seek``, ``tell`` and ``read``: it reads the
    central directory at the end of the archive, then each member it is asked
    for. Wrap it in :class:`io.BufferedReader` so small reads are batched.
    Every request goes through :func:`openbasket.http.request`, which keeps to
    the per-host interval.
    """

    def __init__(self, url: str, headers: dict | None = None, size: int | None = None):
        super().__init__()
        self.url = url
        self.headers = dict(headers or {})
        self.requests = 0
        if size is None:
            r = http.request("HEAD", url, headers=self.headers)
            r.raise_for_status()
            if r.headers.get("Accept-Ranges", "").lower() != "bytes":
                raise OSError(f"{url} does not accept range requests")
            size = int(r.headers["Content-Length"])
            self.requests += 1
        self.size = size
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            pos = offset
        elif whence == io.SEEK_CUR:
            pos = self._pos + offset
        elif whence == io.SEEK_END:
            pos = self.size + offset
        else:
            raise ValueError(f"bad whence {whence}")
        self._pos = max(0, pos)
        return self._pos

    def readinto(self, b) -> int:
        n = min(len(b), self.size - self._pos)
        if n <= 0:
            return 0
        end = self._pos + n - 1
        r = http.request("GET", self.url, headers={**self.headers, "Range": f"bytes={self._pos}-{end}"}, timeout=300)
        self.requests += 1
        if r.status_code != 206:
            raise OSError(f"Range request to {self.url} returned HTTP {r.status_code}")
        data = r.content
        b[: len(data)] = data
        self._pos += len(data)
        return len(data)


def open_remote_zip(url: str, headers: dict | None = None, buffer_mb: int = 8) -> tuple[zipfile.ZipFile, RangeFile]:
    raw = RangeFile(url, headers=headers)
    return zipfile.ZipFile(io.BufferedReader(raw, buffer_size=buffer_mb << 20)), raw


def remote_tables_to_parquet(
    quarter: str,
    out_dir: str | Path,
    tables=SMALL_TABLES,
    url: str | None = None,
    headers: dict | None = None,
) -> list[dict]:
    """Copy ``tables`` of one quarter's ZIP on sec.gov to Parquet without downloading the whole ZIP.

    Writes ``out_dir/<quarter>/<TABLE>.parquet`` in the same shape as
    :func:`openbasket.sources.nport.zip_to_parquet` (all text, plus
    ``QUARTER``) and returns the same manifest rows, so
    :func:`openbasket.sources.nport.table` and :func:`quarter_coverage` read
    them like any other quarter.
    """
    quarter = format_quarter(*parse_quarter(quarter))
    url = url or NPORT_URL.format(quarter=quarter)
    headers = headers if headers is not None else {"User-Agent": sec.user_agent()}
    qdir = Path(out_dir) / quarter
    qdir.mkdir(parents=True, exist_ok=True)
    zf, raw = open_remote_zip(url, headers=headers)
    con = connection()
    out = []
    with zf:
        names = {Path(n).stem.upper(): n for n in zf.namelist()}
        for table in tables:
            member = names.get(table)
            if member is None:
                raise KeyError(f"{table} missing from {url}")
            tsv = qdir / f"{table}.tsv"
            with zf.open(member) as src, open(tsv, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            pq = qdir / f"{table}.parquet"
            con.execute(
                f"copy (select *, '{quarter}' as QUARTER from {nport._read_tsv(str(tsv))}) "
                f"to '{pq}' (format parquet, compression zstd)"
            )
            tsv.unlink()
            rows = con.execute(f"select count(*) from '{pq}'").fetchall()[0][0]
            logger.info("%s %s: %s rows (remote)", quarter, table, rows)
            out.append({"quarter": quarter, "table": table, "path": str(pq), "rows": rows})
    logger.info("%s: %d range requests", url, raw.requests)
    return out


# Coverage ---------------------------------------------------------------------------------------------------------


def _latest(con: duckdb.DuckDBPyConnection, manifest: pd.DataFrame) -> pd.DataFrame:
    submission = nport.table(con, manifest, "SUBMISSION")
    info = nport.table(con, manifest, "FUND_REPORTED_INFO")
    lf = nport.latest_filings(submission, info).df()
    last = submission.select("ACCESSION_NUMBER, IS_LAST_FILING").df()
    last = last.drop_duplicates("ACCESSION_NUMBER").rename(
        columns={"ACCESSION_NUMBER": "accession_number", "IS_LAST_FILING": "is_last_filing"}
    )
    lf = lf.merge(last, on="accession_number", how="left")
    lf["is_last_filing"] = lf["is_last_filing"].eq("Y")
    return lf


def next_report_due(report_date: pd.Series) -> pd.Series:
    """When a fund's next public N-PORT report is due: three months after the period, plus 60 days."""
    return pd.to_datetime(report_date) + pd.DateOffset(months=3) + pd.Timedelta(days=60)


def quarter_coverage(manifest: pd.DataFrame, fund_tickers: pd.DataFrame | None = None) -> pd.DataFrame:
    """How many fund series each loaded quarter adds, newest quarter first.

    Row ``k`` is the result of loading the ``k`` newest quarters and keeping
    each series' latest filing. ``series_added`` are series with no filing in
    any newer quarter: their holdings come from this quarter. Of those,
    ``added_with_ticker`` still have a share class in SEC's
    ``company_tickers_mf.json``, ``added_final_filing`` were flagged as the
    fund's last filing (liquidated or merged), ``added_between_filings`` are
    not yet due for their next public report (:func:`next_report_due` after
    the newest filing date loaded), so they are live funds whose filing
    simply fell in an older quarter, and ``added_net_assets_usd`` is their
    reported net assets.
    """
    con = connection()
    quarters = sort_quarters(manifest["quarter"].unique())[::-1]
    live = set(fund_tickers["series_id"].dropna()) if fund_tickers is not None else None
    full = _latest(con, manifest)
    newest_filing = full["filing_date"].max()
    seen: set[str] = set()
    rows = []
    for k, q in enumerate(quarters, start=1):
        lf = full if k == len(quarters) else _latest(con, manifest[manifest["quarter"].isin(quarters[:k])])
        added = lf[~lf["series_id"].isin(seen) & lf["quarter"].eq(q)]
        seen |= set(lf["series_id"])
        between = next_report_due(added["report_date"]) > newest_filing
        rows.append(
            {
                "quarter": q,
                "quarters_loaded": k,
                "series_total": len(lf),
                "series_added": len(added),
                "added_with_ticker": int(added["series_id"].isin(live).sum()) if live is not None else None,
                "added_final_filing": int(added["is_last_filing"].sum()),
                "added_between_filings": int((between & ~added["is_last_filing"]).sum()),
                "added_net_assets_usd": float(added["net_assets"].sum()),
                "added_report_date_min": added["report_date"].min() if len(added) else None,
                "added_report_date_max": added["report_date"].max() if len(added) else None,
            }
        )
    return pd.DataFrame(rows)


def duplicate_accessions(manifest: pd.DataFrame) -> int:
    """Filings that appear in more than one loaded quarter (expected: 0)."""
    con = connection()
    sub = nport.table(con, manifest, "SUBMISSION")  # noqa: F841 - read by DuckDB below
    return int(
        con.sql("select count(*) from (select ACCESSION_NUMBER from sub group by 1 having count(distinct QUARTER) > 1)")
        .fetchall()[0][0]
    )


def duplicate_holding_ids(manifest: pd.DataFrame) -> int:
    """``HOLDING_ID`` values used in more than one loaded quarter (expected: 0).

    ``IDENTIFIERS`` and ``DESC_REF_OTHER`` join holdings on ``HOLDING_ID``
    alone, so the ids must not repeat across quarters. The SEC numbers them
    across all data sets (2026q1 to 2026q3: 16.9 million ids, none repeated).
    """
    if not (manifest["table"] == "FUND_REPORTED_HOLDING").any():
        return 0
    con = connection()
    hold = nport.table(con, manifest, "FUND_REPORTED_HOLDING")  # noqa: F841 - read by DuckDB below
    return int(
        con.sql("select count(*) from (select HOLDING_ID from hold group by 1 having count(distinct QUARTER) > 1)")
        .fetchall()[0][0]
    )


# Command line -----------------------------------------------------------------------------------------------------


def configured_quarters(conf_dir: str | Path = "conf/base") -> list[str]:
    """The quarters ``ingest`` will load with the parameters in ``conf_dir``.

    ``python -m openbasket.sources.sec_nport_history`` prints them joined by
    ``-`` (``2026q1-2026q2-2026q3``), e.g. for a download cache key.
    """
    import yaml

    params: dict = {}
    for f in sorted(Path(conf_dir).glob("parameters*.yml")):
        params.update(yaml.safe_load(f.read_text()) or {})
    return resolve_quarters(params.get("nport", {}).get("quarters") or [], params.get("nport_history"))


if __name__ == "__main__":  # pragma: no cover
    import sys

    print("-".join(configured_quarters(sys.argv[1] if len(sys.argv) > 1 else "conf/base")))
