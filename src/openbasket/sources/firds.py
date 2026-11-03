"""ESMA FIRDS: reference data for instruments traded in the EU and EEA.

FIRDS (Financial Instruments Reference Data System) holds one record per
instrument and trading venue for everything admitted to or traded on an EU or
EEA venue: shares, bonds, ETFs and other fund units, and derivatives. Many
Swiss, US and other non-EU securities are in it because German, Dutch or
Italian venues list them. It is not a holdings source; it tells openbasket
what an ISIN *is* (name, CFI code, issuer LEI, currency, maturity, coupon).

Two ways in:

* :func:`lookup` / :func:`lookup_many` query ESMA's public Solr index,
  ``registers.esma.europa.eu/solr/esma_registers_firds/select``, one request
  per batch of ISINs. Records are per venue, so they are reduced to one row
  per ISIN with :data:`INSTRUMENT_COLUMNS`.
* :func:`list_files`, :func:`download_file` and :func:`xml_to_parquet` read
  the published bulk files (weekly ``FULINS`` snapshots split by CFI letter,
  daily ``DLTINS`` deltas). The XML is streamed with ``iterparse`` and only
  debt (``D``), fund (``C``) and equity (``E``) records are kept.
  :func:`instruments_sql` reduces the parsed venue records to one row per
  ISIN inside DuckDB.

:func:`classify` and :func:`classify_cfi` turn a CFI code into ``fund``,
``debt``, ``equity``, ``other`` or ``unknown``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlencode

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .. import http
from ..datasets import connection
from ..identifiers import is_valid_isin

logger = logging.getLogger(__name__)

#: Redistribution class (see :func:`openbasket.sources.redistribution`).
#: ESMA's legal notice (https://www.esma.europa.eu/about-esma/legal-notice-and-data-protection,
#: read 2026-11-03): "Reproduction is authorised provided the source is
#: acknowledged"; material transformed from it carries the disclaimer in
#: :data:`CREDIT`.
REDISTRIBUTION = "public"

SOLR_URL = "https://registers.esma.europa.eu/solr/esma_registers_firds/select"
FILES_URL = "https://registers.esma.europa.eu/solr/esma_registers_firds_files/select"
SEARCH_PAGE = "https://registers.esma.europa.eu/publication/searchRegister?core=esma_registers_firds"
HOMEPAGE = "https://www.esma.europa.eu/data-reporting/mifir-reporting/financial-instruments-reference-data-system"
SOURCE = "esma_firds"
CREDIT = (
    "Source: ESMA Financial Instruments Reference Data System (FIRDS), registers.esma.europa.eu. "
    "Drafted using material downloaded from ESMA's website; ESMA does not endorse this publication."
)
LICENCE = (
    "ESMA legal notice: reproduction is authorised provided the source is acknowledged "
    "(https://www.esma.europa.eu/about-esma/legal-notice-and-data-protection)."
)
FREQUENCY = "daily"

#: One row per ISIN, from either route.
INSTRUMENT_COLUMNS = [
    "isin",
    "full_name",            # the name most venues report (names differ by venue)
    "short_name",           # FISN-style short name
    "cfi",                  # ISO 10962 CFI code
    "asset_class",          # classify_cfi(cfi)
    "issuer_lei",
    "currency",             # notional currency
    "maturity_date",        # YYYY-MM-DD, debt only
    "coupon_pct",           # fixed rate in percent; empty for floating-rate debt
    "interest_type",        # fixed / zero / floating, debt only
    "floating_ref",         # reference rate ISIN, index code or name, floating-rate debt only
    "floating_spread_bps",  # spread over the reference rate in basis points
    "nominal_total",        # total issued nominal amount, debt only
    "nominal_currency",
    "seniority",            # SNDB senior, SBOD subordinated, MZZD mezzanine, JUND junior
    "relevant_venue",       # MIC of the most relevant market (RlvntTradgVn / rca_mic)
    "venue",                # MIC of the record the descriptive fields come from
    "n_venues",
    "n_active_venues",
    "first_trade_date",     # earliest first-trade date across venues
    "termination_date",     # latest termination date, only when no venue is active
    "as_of",                # YYYY-MM-DD of the newest record used (publication date)
    "source",
    "url",
    "credit",
]

#: One row per instrument and venue, as parsed from a bulk file or the Solr API.
RECORD_COLUMNS = [
    "file_name",
    "as_of",
    "seq",
    "record_type",
    "isin",
    "full_name",
    "short_name",
    "cfi",
    "issuer_lei",
    "currency",
    "mic",
    "rca_mic",
    "first_trade_date",
    "termination_date",
    "published_from",
    "maturity_date",
    "coupon_pct",
    "interest_type",
    "floating_ref",
    "floating_spread_bps",
    "nominal_total",
    "nominal_currency",
    "seniority",
]

_FLOAT_RECORD_COLUMNS = {"coupon_pct", "floating_spread_bps", "nominal_total"}
RECORD_SCHEMA = pa.schema(
    [
        (c, pa.int64() if c == "seq" else pa.float64() if c in _FLOAT_RECORD_COLUMNS else pa.string())
        for c in RECORD_COLUMNS
    ]
)

#: Bulk record elements: FULINS uses RefData, DLTINS the four change types.
RECORD_TAGS = frozenset({"RefData", "NewRcrd", "ModfdRcrd", "TermntdRcrd", "CancRcrd"})
_REPORT_TAGS = frozenset({"FinInstrmRptgRefDataRpt", "FinInstrmRptgRefDataDltaRpt"})

DEFAULT_LETTERS = ("C", "D", "E")

# --------------------------------------------------------------------------
# CFI classification
# --------------------------------------------------------------------------

#: First two CFI letters that do not follow their category letter.
_PREFIX_CLASS = {"CB": "equity"}  # REITs are listed property companies, not baskets of securities
_LETTER_CLASS = {"C": "fund", "D": "debt", "E": "equity"}

#: Human label for the first two CFI letters (ISO 10962:2021).
CFI_KINDS = {
    "CI": "standard fund",
    "CH": "hedge fund",
    "CB": "real estate investment trust",
    "CE": "exchange-traded fund",
    "CS": "pension fund",
    "CF": "fund of funds",
    "CP": "private equity fund",
    "CM": "other collective investment vehicle",
    "DB": "bond",
    "DC": "convertible bond",
    "DW": "bond with warrants",
    "DT": "medium-term note",
    "DS": "structured product with capital protection",
    "DE": "structured product without capital protection",
    "DG": "mortgage-backed security",
    "DA": "asset-backed security",
    "DN": "municipal bond",
    "DD": "depositary receipt on debt",
    "DY": "money market instrument",
    "DM": "other debt",
    "ES": "common share",
    "EP": "preferred share",
    "EC": "common convertible share",
    "EF": "preferred convertible share",
    "EL": "limited partnership unit",
    "ED": "depositary receipt",
    "EY": "structured participation product",
    "EM": "other equity",
}

_CFI_RE = re.compile(r"^[A-Z]{6}$")


def classify_cfi(cfi: object) -> str:
    """``fund``, ``debt``, ``equity`` or ``other`` for a CFI code; ``unknown`` if it is not one.

    ``CE`` (ETFs), ``CI`` (standard funds) and the other ``C`` groups are
    funds, except ``CB`` (REITs), which is equity. Every ``D`` code is debt
    and every ``E`` code equity. Anything else (rights, options, futures,
    warrants, certificates) is ``other``.
    """
    if not isinstance(cfi, str) or not _CFI_RE.match(cfi.strip().upper()):
        return "unknown"
    cfi = cfi.strip().upper()
    return _PREFIX_CLASS.get(cfi[:2]) or _LETTER_CLASS.get(cfi[0], "other")


def cfi_kind(cfi: object) -> str | None:
    """Label for the CFI group, such as ``exchange-traded fund`` for ``CE....``."""
    return CFI_KINDS.get(cfi.strip().upper()[:2]) if isinstance(cfi, str) else None


def classify_sql(column: str = "cfi") -> str:
    """The rule of :func:`classify_cfi` as a DuckDB ``CASE`` expression."""
    parts = [f"when {column} is null or not regexp_full_match({column}, '[A-Z]{{6}}') then 'unknown'"]
    parts += [f"when left({column}, 2) = '{p}' then '{c}'" for p, c in _PREFIX_CLASS.items()]
    parts += [f"when left({column}, 1) = '{p}' then '{c}'" for p, c in _LETTER_CLASS.items()]
    return "case " + " ".join(parts) + " else 'other' end"


def classify(isin: str, instruments: pd.DataFrame | Mapping[str, str] | None = None, **lookup_kw) -> str:
    """Asset class of an ISIN from its FIRDS CFI code.

    ``instruments`` may be a frame with ``isin`` and ``cfi`` columns (such as
    the ``firds_instruments`` table) or a mapping of ISIN to CFI. Without it,
    the ISIN is looked up through the Solr API. An ISIN FIRDS does not know is
    ``unknown``.
    """
    isin = str(isin).strip().upper()
    if instruments is None:
        rec = lookup(isin, **lookup_kw)
        return classify_cfi(rec["cfi"]) if rec else "unknown"
    if isinstance(instruments, pd.DataFrame):
        hit = instruments.loc[instruments["isin"] == isin, "cfi"]
        return classify_cfi(hit.iloc[0]) if len(hit) else "unknown"
    return classify_cfi(instruments.get(isin))


# --------------------------------------------------------------------------
# Per-ISIN lookup (Solr)
# --------------------------------------------------------------------------

#: Solr fields read for a lookup. Unknown names in ``fl`` are ignored by Solr.
SOLR_FIELDS = [
    "id",
    "isin",
    "mic",
    "rca_mic",
    "gnr_full_name",
    "gnr_short_name",
    "gnr_cfi_code",
    "gnr_notional_curr_code",
    "lei",
    "mrkt_trdng_start_date",
    "mrkt_trdng_trmination_date",
    "bnd_maturity_date",
    "bnd_fixed_rate",
    "bnd_fltng_rt_ndx_isin",
    "bnd_fltng_rt_ndx_code",
    "bnd_fltng_rt_ndx_name",
    "bnd_fltng_rt_ndx_trm_value",
    "bnd_fltng_rt_ndx_trm_unit",
    "bnd_fltng_rate_bs_pnt_sprd",
    "bnd_nmnl_value_total",
    "bnd_nmnl_value_curr_code",
    "bnd_seniority",
    "status",
    "valid_from_date",
    "publication_date",
]

_SOLR_STATUS = {"NEWS": "NewRcrd", "MODF": "ModfdRcrd", "TERM": "TermntdRcrd", "UNCH": "RefData", "CANC": "CancRcrd"}


def solr_query(isins: Iterable[str]) -> str:
    """The Solr ``q`` for the latest record of each ISIN on every venue."""
    return "isin:(" + " OR ".join(isins) + ") AND latest_received_flag:1"


def solr_url(isin: str) -> str:
    """A link to the raw FIRDS records of one ISIN (JSON)."""
    return SOLR_URL + "?" + urlencode({"q": solr_query([isin]), "wt": "json", "rows": 1000})


def _day(value: object) -> str | None:
    """``YYYY-MM-DD`` from an ISO date or timestamp, else ``None``."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    return value[:10]


def _num(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _interest(cfi: str | None, fixed: object, floating_ref: object) -> tuple[str | None, float | None]:
    """Interest type and coupon in percent.

    FIRDS gives the fixed rate in percent (``1.4125`` is 1.4125%). Solr shows
    ``0`` as the fixed rate of a floating-rate note, so a floating reference
    rate (or a ``V`` in the CFI) wins over the fixed rate.
    """
    letter = cfi[2] if isinstance(cfi, str) and len(cfi) == 6 and cfi[0] == "D" else None
    if floating_ref or letter == "V":
        return "floating", None
    rate = _num(fixed)
    if letter == "X" and not rate:
        # CFI "not applicable/undefined" with a zero rate: may be an FRN without reference-rate fields.
        return None, None
    if letter == "Z" or (rate == 0 and letter not in ("F", None)):
        return "zero", 0.0 if rate is None else rate
    if rate is not None:
        return "fixed", rate
    return None, None


def solr_records(docs: list[dict]) -> pd.DataFrame:
    """Solr documents as venue records with :data:`RECORD_COLUMNS`."""
    rows = []
    for i, d in enumerate(docs):
        cfi = d.get("gnr_cfi_code")
        ref = d.get("bnd_fltng_rt_ndx_isin") or d.get("bnd_fltng_rt_ndx_code") or d.get("bnd_fltng_rt_ndx_name")
        itype, coupon = _interest(cfi, d.get("bnd_fixed_rate"), ref) if (cfi or "").startswith("D") else (None, None)
        rows.append(
            {
                "file_name": None,
                "as_of": _day(d.get("publication_date")) or _day(d.get("valid_from_date")),
                "seq": i,
                "record_type": _SOLR_STATUS.get(d.get("status"), d.get("status")),
                "isin": d.get("isin"),
                "full_name": d.get("gnr_full_name"),
                "short_name": d.get("gnr_short_name"),
                "cfi": cfi,
                "issuer_lei": d.get("lei"),
                "currency": d.get("gnr_notional_curr_code"),
                "mic": d.get("mic"),
                "rca_mic": d.get("rca_mic"),
                "first_trade_date": _day(d.get("mrkt_trdng_start_date")),
                "termination_date": _day(d.get("mrkt_trdng_trmination_date")),
                "published_from": _day(d.get("valid_from_date")),
                "maturity_date": _day(d.get("bnd_maturity_date")),
                "coupon_pct": coupon,
                "interest_type": itype,
                "floating_ref": ref,
                "floating_spread_bps": _num(d.get("bnd_fltng_rate_bs_pnt_sprd")),
                "nominal_total": _num(d.get("bnd_nmnl_value_total")),
                "nominal_currency": d.get("bnd_nmnl_value_curr_code"),
                "seniority": d.get("bnd_seniority"),
            }
        )
    return _records_frame(rows)


def _records_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=RECORD_COLUMNS)
    for c in _FLOAT_RECORD_COLUMNS:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["seq"] = pd.to_numeric(df["seq"], errors="coerce").fillna(0).astype("int64")
    return df


def _fetch_solr(isins: list[str], rows: int = 1000) -> list[dict]:
    """Every latest venue record for the ISINs, paging through the result."""
    docs: list[dict] = []
    start = 0
    while True:
        r = http.get(
            SOLR_URL,
            params={
                "q": solr_query(isins),
                "fl": ",".join(SOLR_FIELDS),
                "wt": "json",
                "rows": rows,
                "start": start,
                "sort": "id asc",
            },
        )
        r.raise_for_status()
        body = r.json()["response"]
        docs += body["docs"]
        start += len(body["docs"])
        if not body["docs"] or start >= body["numFound"]:
            return docs


def _cache_path(cache_dir: str | Path, isin: str, day: str) -> Path:
    return Path(cache_dir) / "firds" / day / f"{isin}.json"


def lookup_many(
    isins: Iterable[str], *, cache_dir: str | Path | None = None, batch_size: int = 20, today: str | None = None
) -> pd.DataFrame:
    """One row per ISIN that FIRDS knows, with :data:`INSTRUMENT_COLUMNS`.

    ISINs are sent in batches of ``batch_size`` (an ``OR`` query). With
    ``cache_dir``, each ISIN's records are cached for the day in
    ``<cache_dir>/firds/<date>/<ISIN>.json``, including an empty list for an
    ISIN FIRDS does not know, and only after the response parsed.
    """
    wanted = list(dict.fromkeys(str(i).strip().upper() for i in isins))
    bad = [i for i in wanted if not is_valid_isin(i)]
    if bad:
        logger.warning("FIRDS: skipping %d invalid ISINs, e.g. %s", len(bad), bad[:3])
    wanted = [i for i in wanted if is_valid_isin(i)]
    day = today or dt.date.today().isoformat()
    docs: list[dict] = []
    todo = []
    for isin in wanted:
        path = _cache_path(cache_dir, isin, day) if cache_dir else None
        if path and path.exists():
            docs += json.loads(path.read_text())
        else:
            todo.append(isin)
    for k in range(0, len(todo), batch_size):
        batch = todo[k : k + batch_size]
        got = _fetch_solr(batch)
        frame = instruments_from_solr(got)  # parse before caching
        logger.info("FIRDS: %d of %d ISINs found (%d venue records)", len(frame), len(batch), len(got))
        docs += got
        if cache_dir:
            by_isin: dict[str, list[dict]] = {i: [] for i in batch}
            for d in got:
                by_isin.setdefault(d.get("isin"), []).append(d)
            for isin in batch:
                path = _cache_path(cache_dir, isin, day)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(by_isin[isin]))
    return instruments_from_solr(docs)


def lookup(isin: str, **kw) -> dict[str, Any] | None:
    """FIRDS reference data for one ISIN as a dict, or ``None`` if FIRDS does not have it."""
    df = lookup_many([isin], **kw)
    if df.empty:
        return None
    return {k: (None if pd.isna(v) else v) for k, v in df.iloc[0].items()}


def instruments_from_solr(docs: list[dict]) -> pd.DataFrame:
    """Reduce Solr documents to one row per ISIN (see :func:`instruments_sql`)."""
    con = connection()
    con.register("_firds_solr_records", solr_records(docs))
    try:
        out = con.sql(instruments_sql("_firds_solr_records")).df()
    finally:
        con.unregister("_firds_solr_records")
    out["url"] = [solr_url(i) for i in out["isin"]]
    return out[INSTRUMENT_COLUMNS].reset_index(drop=True)


# --------------------------------------------------------------------------
# One row per ISIN
# --------------------------------------------------------------------------


def instruments_sql(records: str) -> str:
    """DuckDB SQL that turns venue records into one row per ISIN.

    1. For each ISIN and venue the newest record wins (newest ``as_of``, then
       the later record in the file). A cancelled record removes that venue.
    2. A venue is active when it has no termination date or one on or after
       its ``as_of``.
    3. The descriptive fields come from one venue: the most relevant market
       (``rca_mic``) if listed, else an active venue, else the newest.
       ``full_name`` is the exception: venues spell names differently, so
       the name most venues use is taken (ties: the one more active venues use).

    ``records`` names a table or relation with :data:`RECORD_COLUMNS`.
    """
    return f"""
        with latest as (
            select * from {records}
            where isin is not null
            qualify row_number() over (partition by isin, mic order by as_of desc nulls last, seq desc) = 1
        ),
        v as (
            select *, (termination_date is null or termination_date >= as_of) as active
            from latest where record_type is distinct from 'CancRcrd'
        ),
        agg as (
            select isin,
                   count(*) as n_venues,
                   count(*) filter (where active) as n_active_venues,
                   min(first_trade_date) as first_trade_date,
                   case when bool_or(active) then null else max(termination_date) end as termination_date,
                   max(as_of) as as_of
            from v group by isin
        ),
        pick as (
            select * from v
            qualify row_number() over (
                partition by isin
                order by (mic = rca_mic) desc nulls last, active desc, as_of desc nulls last, mic
            ) = 1
        ),
        names as (
            select isin, full_name from v where full_name is not null
            group by isin, full_name
            qualify row_number() over (
                partition by isin
                order by count(*) desc, count(*) filter (where active) desc, length(full_name) desc, full_name
            ) = 1
        )
        select
            p.isin,
            coalesce(n.full_name, p.full_name) as full_name,
            p.short_name,
            p.cfi,
            {classify_sql('p.cfi')} as asset_class,
            p.issuer_lei,
            p.currency,
            p.maturity_date,
            p.coupon_pct,
            p.interest_type,
            p.floating_ref,
            p.floating_spread_bps,
            p.nominal_total,
            p.nominal_currency,
            p.seniority,
            p.rca_mic as relevant_venue,
            p.mic as venue,
            a.n_venues,
            a.n_active_venues,
            a.first_trade_date,
            a.termination_date,
            a.as_of,
            '{SOURCE}' as source,
            '{SOLR_URL}?q=isin%3A%28' || p.isin || '%29+AND+latest_received_flag%3A1&wt=json&rows=1000' as url,
            '{CREDIT.replace("'", "''")}' as credit
        from pick p join agg a using (isin) left join names n using (isin)
        order by p.isin
    """


# --------------------------------------------------------------------------
# Bulk files
# --------------------------------------------------------------------------

FILE_COLUMNS = ["file_name", "file_type", "publication_date", "download_link", "checksum"]
_FILE_RE = re.compile(r"^(?P<type>FULINS|DLTINS|FULCAN)_(?:(?P<letter>[A-Z])_)?(?P<date>\d{8})_(?P<part>\d+)of(?P<parts>\d+)$")


def parse_file_name(name: str) -> dict[str, Any] | None:
    """Type, CFI letter (FULINS only), date and part of a FIRDS file name."""
    m = _FILE_RE.match(Path(name).name.removesuffix(".zip").removesuffix(".xml"))
    if not m:
        return None
    d = m.group("date")
    return {
        "file_type": m.group("type"),
        "letter": m.group("letter"),
        "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
        "part": int(m.group("part")),
        "parts": int(m.group("parts")),
    }


def list_files(date_from: str, date_to: str, file_type: str | None = None, rows: int = 500) -> pd.DataFrame:
    """Files published between two dates (``YYYY-MM-DD``), from ESMA's file index."""
    fq = [f"publication_date:[{date_from}T00:00:00Z TO {date_to}T23:59:59Z]"]
    if file_type:
        fq.append(f"file_type:{file_type}")
    docs: list[dict] = []
    start = 0
    while True:
        r = http.get(
            FILES_URL, params={"q": "*", "fq": fq, "wt": "json", "rows": rows, "start": start, "sort": "publication_date asc"}
        )
        r.raise_for_status()
        body = r.json()["response"]
        docs += body["docs"]
        start += len(body["docs"])
        if not body["docs"] or start >= body["numFound"]:
            break
    return files_frame(docs)


def files_frame(docs: list[dict]) -> pd.DataFrame:
    """File index documents as a frame, without the index's empty marker rows."""
    df = pd.DataFrame(docs, columns=FILE_COLUMNS)
    df = df[df["file_name"].fillna("").str.len() > 0].copy()
    df["publication_date"] = df["publication_date"].map(_day)
    df = df.drop_duplicates("file_name")
    return df.sort_values(["publication_date", "file_name"]).reset_index(drop=True)


def select_files(index: pd.DataFrame, letters: Iterable[str] = DEFAULT_LETTERS, include_deltas: bool = True) -> pd.DataFrame:
    """The newest complete FULINS set for ``letters``, plus the DLTINS files after it.

    A set is complete when every part of every letter is listed. With no
    complete FULINS set in ``index`` only the deltas are returned.
    """
    letters = [str(x).upper() for x in letters]
    meta = index["file_name"].map(parse_file_name)
    df = index.assign(
        _type=[m["file_type"] if m else None for m in meta],
        _letter=[m["letter"] if m else None for m in meta],
        _date=[m["date"] if m else None for m in meta],
        _part=[m["part"] if m else None for m in meta],
        _parts=[m["parts"] if m else None for m in meta],
    )
    full = df[(df["_type"] == "FULINS") & df["_letter"].isin(letters)]

    def complete(files: pd.DataFrame) -> bool:
        return len(files) > 0 and set(files["_part"]) == set(range(1, int(files["_parts"].iloc[0]) + 1))

    snapshot = None
    for date in sorted(full["_date"].unique(), reverse=True):
        s = full[full["_date"] == date]
        if all(complete(s[s["_letter"] == letter]) for letter in letters):
            snapshot = date
            break
    parts = []
    if snapshot:
        parts.append(full[full["_date"] == snapshot])
    if include_deltas:
        deltas = df[df["_type"] == "DLTINS"]
        parts.append(deltas[deltas["_date"] > snapshot] if snapshot else deltas)
    if not parts:
        return pd.DataFrame(columns=FILE_COLUMNS)
    out = pd.concat(parts).sort_values(["_date", "file_name"])
    return out[FILE_COLUMNS].reset_index(drop=True)


def download_file(url: str, dest: str | Path, checksum: str | None = None, overwrite: bool = False) -> Path:
    """Stream a FIRDS ZIP to ``dest`` and check its MD5 against the index's ``checksum``."""
    dest = Path(dest)
    if dest.exists() and not overwrite and (not checksum or _md5(dest) == checksum):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with http.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    if checksum and _md5(tmp) != checksum:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"{url}: MD5 does not match the FIRDS index checksum {checksum}")
    tmp.replace(dest)
    return dest


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _open_xml(path: str | Path) -> tuple[IO[bytes], str]:
    path = Path(path)
    if path.suffix.lower() == ".zip":
        z = zipfile.ZipFile(path)
        member = next(n for n in z.namelist() if n.lower().endswith(".xml"))
        return z.open(member), Path(member).stem
    return open(path, "rb"), path.stem


def iter_records(path: str | Path, letters: Iterable[str] | None = DEFAULT_LETTERS) -> Iterator[dict[str, Any]]:
    """Stream the instrument records of a FULINS or DLTINS file (ZIP or XML).

    Yields one dict per record with :data:`RECORD_COLUMNS`, keeping only CFI
    codes that start with one of ``letters`` (``None`` keeps everything).
    Each processed element is removed from the tree, so memory stays flat on
    multi-gigabyte files.
    """
    keep = tuple(x.upper() for x in letters) if letters else None
    fh, file_name = _open_xml(path)
    as_of = None
    seq = 0
    report: ET.Element | None = None
    records: dict[str, str] = {}  # namespaced record tag -> local name
    children: frozenset[str] = frozenset()  # namespaced tags of the report's direct children
    with fh:
        for event, el in ET.iterparse(fh, events=("start", "end")):
            if report is None:
                if event == "start" and _local(el.tag) in _REPORT_TAGS:
                    report = el
                    ns = el.tag[: el.tag.index("}") + 1] if el.tag.startswith("{") else ""
                    records = {ns + t: t for t in RECORD_TAGS}
                    children = frozenset({ns + "RptHdr", ns + "FinInstrm", ns + "RefData"})
                continue
            if event == "start":
                continue
            tag = el.tag
            if tag in records:
                rec = _record(el, records[tag])
                seq += 1
                if keep is None or (rec["cfi"] or "").startswith(keep):
                    rec.update(file_name=file_name, as_of=as_of or rec["published_from"], seq=seq)
                    yield rec
            if tag in children:
                if as_of is None and tag.endswith("RptHdr"):
                    as_of = _day(_find_text(el, "RptgPrd/Dt"))
                report.clear()  # drop the finished child, so memory stays flat


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _flatten(el: ET.Element) -> dict[str, str]:
    """Text of every descendant keyed by its local path (``FinInstrmGnlAttrbts/Id``).

    A ``Ccy`` attribute is stored under ``<path>@Ccy``. One walk per record is
    much cheaper than a ``find`` per field.
    """
    out: dict[str, str] = {}

    def walk(node: ET.Element, prefix: str) -> None:
        for child in node:
            path = prefix + child.tag.rsplit("}", 1)[-1]
            if len(child):
                walk(child, path + "/")
            else:
                text = (child.text or "").strip()
                if text:
                    out[path] = text
            ccy = child.get("Ccy")
            if ccy:
                out[path + "@Ccy"] = ccy

    walk(el, "")
    return out


def _find_text(el: ET.Element, path: str) -> str | None:
    return _flatten(el).get(path)


def _record(el: ET.Element, tag: str) -> dict[str, Any]:
    flat = _flatten(el)
    f = flat.get
    cfi = f("FinInstrmGnlAttrbts/ClssfctnTp")
    ref = spread = None
    itype = coupon = None
    if any(k.startswith("DebtInstrmAttrbts/") for k in flat):
        ref = (
            f("DebtInstrmAttrbts/IntrstRate/Fltg/RefRate/ISIN")
            or f("DebtInstrmAttrbts/IntrstRate/Fltg/RefRate/Indx")
            or f("DebtInstrmAttrbts/IntrstRate/Fltg/RefRate/Nm")
        )
        spread = f("DebtInstrmAttrbts/IntrstRate/Fltg/BsisPtSprd")
        itype, coupon = _interest(cfi, f("DebtInstrmAttrbts/IntrstRate/Fxd"), ref)
        if itype is None and (spread is not None or f("DebtInstrmAttrbts/IntrstRate/Fltg/Term/Val")):
            itype = "floating"
    return {
        "file_name": None,
        "as_of": None,
        "seq": 0,
        "record_type": tag,
        "isin": f("FinInstrmGnlAttrbts/Id"),
        "full_name": f("FinInstrmGnlAttrbts/FullNm"),
        "short_name": f("FinInstrmGnlAttrbts/ShrtNm"),
        "cfi": cfi,
        "issuer_lei": f("Issr"),
        "currency": f("FinInstrmGnlAttrbts/NtnlCcy"),
        "mic": f("TradgVnRltdAttrbts/Id"),
        "rca_mic": f("TechAttrbts/RlvntTradgVn"),
        "first_trade_date": _day(f("TradgVnRltdAttrbts/FrstTradDt")),
        "termination_date": _day(f("TradgVnRltdAttrbts/TermntnDt")),
        "published_from": _day(f("TechAttrbts/PblctnPrd/FrDt")) or _day(f("TechAttrbts/PblctnPrd/FrDtToDt/FrDt")),
        "maturity_date": _day(f("DebtInstrmAttrbts/MtrtyDt")),
        "coupon_pct": coupon,
        "interest_type": itype,
        "floating_ref": ref,
        "floating_spread_bps": _num(spread),
        "nominal_total": _num(f("DebtInstrmAttrbts/TtlIssdNmnlAmt")),
        "nominal_currency": f("DebtInstrmAttrbts/TtlIssdNmnlAmt@Ccy"),
        "seniority": f("DebtInstrmAttrbts/DebtSnrty"),
    }


def xml_to_parquet(
    path: str | Path, out: str | Path, letters: Iterable[str] | None = DEFAULT_LETTERS, batch_rows: int = 100_000
) -> dict[str, Any]:
    """Stream one FIRDS file into a Parquet file of venue records, ``batch_rows`` at a time."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.parquet")
    n = 0
    as_of = file_name = None
    with pq.ParquetWriter(str(tmp), RECORD_SCHEMA, compression="zstd") as writer:
        batch: list[dict] = []
        for rec in iter_records(path, letters):
            batch.append(rec)
            if len(batch) >= batch_rows:
                writer.write_table(pa.Table.from_pylist(batch, schema=RECORD_SCHEMA))
                n += len(batch)
                as_of, file_name = rec["as_of"], rec["file_name"]
                batch = []
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=RECORD_SCHEMA))
            n += len(batch)
            as_of, file_name = batch[-1]["as_of"], batch[-1]["file_name"]
    tmp.replace(out)
    meta = parse_file_name(Path(path).name) or {}
    return {
        "file_name": file_name or Path(path).stem.removesuffix(".xml"),
        "file_type": meta.get("file_type"),
        "as_of": as_of or meta.get("date"),
        "rows": n,
        "parquet_path": str(out),
    }


def empty_records() -> pa.Table:
    """An empty table with :data:`RECORD_SCHEMA`."""
    return RECORD_SCHEMA.empty_table()
