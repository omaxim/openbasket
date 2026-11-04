"""SEC Form N-MFP data sets: monthly full portfolios of US money market funds.

US money market funds file Form N-MFP (now N-MFP3) every month instead of
N-PORT, so the N-PORT source misses them. The SEC's Division of Economic and
Risk Analysis (DERA) republishes every filing as a monthly ZIP of
tab-delimited tables.

Index
    ``https://www.sec.gov/data-research/sec-markets-data/dera-form-n-mfp-data-sets``
    links one ZIP per filing window, e.g.
    ``/files/dera/data/form-n-mfp-data-sets/20260909-20261007_nmfp.zip``
    (filings accepted between the two dates, i.e. the month-end report of
    September plus late amendments).
Tables used
    ``NMFP_SUBMISSION``
        One row per filing: ``ACCESSION_NUMBER``, ``SUBMISSIONTYPE``
        (``N-MFP3`` or the amendment ``N-MFP3/A``), ``REPORTDATE``,
        ``FILING_DATE``, ``CIK``, ``SERIESID``, ``NAMEOFSERIES``,
        ``LEIOFSERIES``.
    ``NMFP_SERIESLEVELINFO``
        ``NETASSETOFSERIES``, ``MONEYMARKETFUNDCATEGORY``, feeder/master flags.
    ``NMFP_CLASSLEVELINFO``
        One row per share class: ``CLASSESID``, ``CLASS_NAME``,
        ``NETASSETSOFCLASS``.
    ``NMFP_SCHPORTFOLIOSECURITIES``
        One row per position: ``NAMEOFISSUER``, ``TITLEOFISSUER``,
        ``CUSIP_NUMBER``, ``ISIN``, ``LEI``, ``INVESTMENTCATEGORY`` and
        ``PERCENTAGEOFMONEYMARKETFUNDNET``, a **fraction** of net assets
        rounded to four decimals (the form asks for the nearest hundredth of
        a percent), which is multiplied by 100 here.
    ``NMFP_COLLATERALISSUERS``
        The collateral behind each repurchase agreement, linked by
        ``SECURITY_ID`` (see :func:`repo_collateral`).

Share classes are identified by SEC class IDs, not ISINs, and N-MFP carries
no share-class CUSIP. :func:`products` therefore lists the share-class ISINs
that can be tied to a series through its LEI: ISINs GLEIF records for the
series LEI, and ISINs other money market funds report when they hold the
fund. Every series can still be read by its SEC series ID through
:func:`holdings` (``portfolio_id``), and :func:`classes` gives all share
classes with their tickers from ``company_tickers_mf.json``.

Every request goes through :mod:`openbasket.http` with the ``SEC_USER_AGENT``
from ``.env``; the SEC answers a generic User-Agent with HTTP 403.
"""

from __future__ import annotations

import csv
import json
import os
import html
import io
import logging
import re
import threading
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from ... import http
from ...identifiers import cusip_to_isin, is_valid_cusip, is_valid_isin
from ..sec import FUND_TICKERS_URL, user_agent
from . import FetchContext, IssuerAdapter, IssuerHoldings, PRODUCT_COLUMNS, register

logger = logging.getLogger(__name__)

NAME = "sec_nmfp"

#: Redistribution class (see :func:`openbasket.sources.redistribution`).
#: US federal government data is not subject to copyright in the United
#: States, and https://www.sec.gov/about/privacy-information (read 2026-11-03)
#: says information on sec.gov "may be copied or further distributed ...
#: without the SEC's permission", asking only for a citation.
REDISTRIBUTION = "public"

#: Where the registered adapter keeps the monthly ZIP and GLEIF answers.
DEFAULT_CACHE_DIR = "data/01_raw/sec_nmfp"
HOST = "https://www.sec.gov"
INDEX_URL = HOST + "/data-research/sec-markets-data/dera-form-n-mfp-data-sets"
ZIP_RE = re.compile(r"/files/dera/data/form-n-mfp-data-sets/(\d{8})-(\d{8})_nmfp\.zip")
GLEIF_ISINS_URL = "https://api.gleif.org/api/v1/lei-records/{lei}/isins"
FILING_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{nodash}/{accession}-index.htm"

# GLEIF allows 60 requests a minute.
http.set_min_interval("api.gleif.org", 1.1)

#: N-MFP investment categories (by prefix) to the normalised asset classes.
_CATEGORY = (
    ("U.S. Treasury Repurchase Agreement", "repurchase agreement"),
    ("U.S. Government Agency Repurchase Agreement", "repurchase agreement"),
    ("Other Repurchase Agreement", "repurchase agreement"),
    ("Investment Company", "fund"),
    ("Non-Negotiable Time Deposit", "cash"),
    ("Other Instrument", "other"),
)

_MISSING = {"", "N/A", "NA", "NONE", "NULL"}


def _clean(v: object) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = html.unescape(html.unescape(str(v))).strip()
    return None if s.upper() in _MISSING else s


def asset_class(category: object) -> str:
    """Normalised asset class for an N-MFP ``INVESTMENTCATEGORY``."""
    c = _clean(category) or ""
    for prefix, label in _CATEGORY:
        if c.startswith(prefix):
            return label
    return "debt" if c else "other"


def _date(v: object) -> date | None:
    s = _clean(v)
    if not s:
        return None
    try:
        return pd.to_datetime(s, format="%d-%b-%Y").date()
    except ValueError:
        return None


def _sec_get(url: str, **kw):
    r = http.get(url, headers={"User-Agent": user_agent(), "Accept-Encoding": "gzip, deflate"}, **kw)
    r.raise_for_status()
    return r


# -- data sets ------------------------------------------------------------------


@dataclass(frozen=True)
class DatasetRef:
    start: str  # YYYYMMDD, first filing date in the window
    end: str    # YYYYMMDD, last filing date in the window

    @property
    def period(self) -> str:
        return f"{self.start}-{self.end}"

    @property
    def url(self) -> str:
        return f"{HOST}/files/dera/data/form-n-mfp-data-sets/{self.period}_nmfp.zip"


def parse_index(page: str) -> list[DatasetRef]:
    """Monthly data sets linked from the index page, newest first.

    The quarterly files from before 2023 (``2010q4_nmfp.zip``) are ignored.
    """
    refs = {DatasetRef(a, b) for a, b in ZIP_RE.findall(page)}
    return sorted(refs, key=lambda r: r.end, reverse=True)


_index_memo: dict[str, list[DatasetRef]] = {}
_datasets: dict[str, "Dataset"] = {}
_lock = threading.Lock()


def datasets() -> list[DatasetRef]:
    """The monthly data sets on the index page today, newest first (one request per day)."""
    today = date.today().isoformat()
    with _lock:
        if today not in _index_memo:
            refs = parse_index(_sec_get(INDEX_URL, timeout=60).text)
            if not refs:
                raise RuntimeError(f"No monthly N-MFP data sets linked from {INDEX_URL}")
            _index_memo.clear()
            _index_memo[today] = refs
        return list(_index_memo[today])


class Dataset:
    """One monthly ZIP, read table by table on demand."""

    def __init__(self, content: bytes, url: str):
        self.url = url
        self.zf = zipfile.ZipFile(io.BytesIO(content))
        self._members = {Path(n).stem.upper(): n for n in self.zf.namelist()}
        self._tables: dict[str, pd.DataFrame] = {}
        self._lock = threading.Lock()

    def table(self, name: str, usecols: list[str] | None = None) -> pd.DataFrame:
        """A table as strings, with ``usecols`` only (all columns when ``None``)."""
        key = name.upper() if name.upper().startswith("NMFP_") else "NMFP_" + name.upper()
        cache_key = key + ("|" + ",".join(usecols) if usecols else "")
        with self._lock:
            if cache_key not in self._tables:
                member = self._members.get(key)
                if member is None:
                    raise KeyError(f"{key} missing from {self.url}")
                with self.zf.open(member) as fh:
                    # The files are not quoted: a stray double quote inside a
                    # name must not swallow the rest of the file.
                    self._tables[cache_key] = pd.read_csv(
                        fh, sep="\t", dtype=str, quoting=csv.QUOTE_NONE, keep_default_na=False,
                        usecols=usecols, encoding="utf-8", encoding_errors="replace",
                    )
            return self._tables[cache_key]

    def filings(self) -> pd.DataFrame:
        """One filing per series: the latest report date, then the latest filing.

        An amendment (``N-MFP3/A``) filed for the same report date replaces
        the original.
        """
        sub = self.table("SUBMISSION").copy()
        info = self.table("SERIESLEVELINFO", [
            "ACCESSION_NUMBER", "NETASSETOFSERIES", "MONEYMARKETFUNDCATEGORY", "FEEDERFUNDFLAG", "MASTERFUNDFLAG",
        ])
        f = sub.merge(info, on="ACCESSION_NUMBER", how="left")
        out = pd.DataFrame({
            "accession_number": f["ACCESSION_NUMBER"].str.strip(),
            "submission_type": f["SUBMISSIONTYPE"].map(_clean),
            "report_date": f["REPORTDATE"].map(_date),
            "filing_date": f["FILING_DATE"].map(_date),
            "cik": f["CIK"].map(_clean),
            "registrant": f["REGISTRANTFULLNAME"].map(_clean),
            "series_id": f["SERIESID"].map(_clean),
            "series_name": f["NAMEOFSERIES"].map(_clean),
            "series_lei": f["LEIOFSERIES"].map(_clean),
            "net_assets": pd.to_numeric(f["NETASSETOFSERIES"].str.strip(), errors="coerce"),
            "category": f["MONEYMARKETFUNDCATEGORY"].map(_clean),
            "feeder": f["FEEDERFUNDFLAG"].map(_clean).eq("Y"),
            "master": f["MASTERFUNDFLAG"].map(_clean).eq("Y"),
        })
        out = out.dropna(subset=["series_id", "report_date"])
        out["amendment"] = out["submission_type"].fillna("").str.endswith("/A")
        out = out.sort_values(
            ["series_id", "report_date", "filing_date", "amendment", "accession_number"],
            ascending=[True, False, False, False, False],
        ).drop_duplicates("series_id")
        out["filing_url"] = [filing_url(c, a) for c, a in zip(out["cik"], out["accession_number"])]
        return out.drop(columns="amendment").reset_index(drop=True)

    def securities(self) -> pd.DataFrame:
        return self.table("SCHPORTFOLIOSECURITIES", [
            "ACCESSION_NUMBER", "SECURITY_ID", "NAMEOFISSUER", "TITLEOFISSUER", "CUSIP_NUMBER", "LEI", "ISIN",
            "INVESTMENTCATEGORY", "FINALLEGALINVESTMENTMATURITYDA", "INCLUDINGVALUEOFANYSPONSORSUPP",
            "PERCENTAGEOFMONEYMARKETFUNDNET",
        ])


def filing_url(cik: object, accession: object) -> str | None:
    """The filing's index page on EDGAR."""
    cik, accession = _clean(cik), _clean(accession)
    if not cik or not accession or not cik.isdigit():
        return None
    return FILING_URL.format(cik=int(cik), nodash=accession.replace("-", ""), accession=accession)


def _check(ds: Dataset) -> None:
    """Raise unless the tables this module needs parse."""
    f = ds.filings()
    if f.empty:
        raise ValueError(f"No filings in {ds.url}")
    ds.securities()


def load(ref: DatasetRef, cache_dir: str | Path | None = None) -> Dataset:
    """One monthly data set, from the cache when present, else downloaded.

    A download is cached (``sec_nmfp_<period>.zip``, one file per monthly
    data set, since every fund of the month is in it) only after its tables
    parse.
    """
    with _lock:
        if ref.url in _datasets:
            return _datasets[ref.url]
    cache = Path(cache_dir) / f"{NAME}_{ref.period}.zip" if cache_dir else None
    ds = None
    if cache is not None and cache.exists():
        try:
            ds = Dataset(cache.read_bytes(), ref.url)
            _check(ds)
        except (zipfile.BadZipFile, KeyError, ValueError) as exc:
            logger.warning("Ignoring unreadable cache %s: %s", cache, exc)
            ds = None
    if ds is None:
        logger.info("Fetching N-MFP data set %s", ref.url)
        content = _sec_get(ref.url, timeout=900).content
        ds = Dataset(content, ref.url)
        _check(ds)
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".part")
            tmp.write_bytes(content)
            tmp.replace(cache)
    with _lock:
        _datasets[ref.url] = ds
    return ds


# -- products -------------------------------------------------------------------


def fund_tickers() -> pd.DataFrame:
    """SEC's share-class tickers: ``series_id``, ``class_id``, ``ticker``."""
    doc = _sec_get(FUND_TICKERS_URL, timeout=60).json()
    df = pd.DataFrame(doc["data"], columns=doc["fields"])
    df = df.rename(columns={"seriesId": "series_id", "classId": "class_id", "symbol": "ticker"})
    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    return df[["series_id", "class_id", "ticker"]]


def classes(ds: Dataset, tickers: pd.DataFrame | None = None) -> pd.DataFrame:
    """Share classes of each series' latest filing, with their SEC tickers."""
    f = ds.filings()[["accession_number", "series_id", "series_name"]]
    c = ds.table("CLASSLEVELINFO", ["ACCESSION_NUMBER", "CLASSESID", "CLASS_NAME", "NETASSETSOFCLASS"])
    c = pd.DataFrame({
        "accession_number": c["ACCESSION_NUMBER"].str.strip(),
        "class_id": c["CLASSESID"].map(_clean),
        "class_name": c["CLASS_NAME"].map(_clean),
        "class_net_assets": pd.to_numeric(c["NETASSETSOFCLASS"].str.strip(), errors="coerce"),
    })
    out = f.merge(c, on="accession_number", how="inner").dropna(subset=["class_id"])
    if tickers is not None:
        t = tickers.drop_duplicates("class_id")[["class_id", "ticker"]]
        out = out.merge(t, on="class_id", how="left")
    else:
        out["ticker"] = None
    return out.drop_duplicates("class_id").reset_index(drop=True)


def gleif_isins(lei: str) -> list[str]:
    """ISINs GLEIF links to an LEI (CC0 data); empty on any error."""
    try:
        r = http.get(GLEIF_ISINS_URL.format(lei=lei), params={"page[size]": 200}, timeout=60)
        if r.status_code != 200:
            return []
        return [d["attributes"]["isin"] for d in r.json().get("data", [])]
    except (ValueError, KeyError, OSError) as exc:
        logger.warning("GLEIF ISINs for %s: %s", lei, exc)
        return []


def isin_evidence(ds: Dataset, series: pd.DataFrame, gleif: bool = True, gleif_cache: str | Path | None = None) -> pd.DataFrame:
    """Share ISINs tied to a series by its LEI: ``isin``, ``series_id``, ``via``.

    Two kinds of evidence: positions in other money market funds whose issuer
    LEI is the series' LEI (feeders holding their master, funds holding a
    cash fund), and GLEIF's ISIN to LEI relationships.
    """
    by_lei = series.dropna(subset=["series_lei"]).drop_duplicates("series_lei").set_index("series_lei")["series_id"]
    rows = []
    sec = ds.securities()
    held = pd.DataFrame({"lei": sec["LEI"].map(_clean), "isin": sec["ISIN"].map(_clean)}).dropna()
    held = held[held["lei"].isin(by_lei.index) & held["isin"].map(is_valid_isin)].drop_duplicates()
    rows += [{"isin": i.upper(), "series_id": by_lei[lei], "via": "N-MFP holding"} for lei, i in zip(held["lei"], held["isin"])]
    if gleif:
        # GLEIF answers are kept per monthly data set, so a second run that month asks again only for new LEIs.
        path = Path(gleif_cache) if gleif_cache else None
        known = json.loads(path.read_text()) if path is not None and path.exists() else {}
        for lei, sid in by_lei.items():
            if lei not in known:
                known[lei] = gleif_isins(lei)
            rows += [{"isin": i, "series_id": sid, "via": "GLEIF"} for i in known[lei] if is_valid_isin(i)]
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(known))
    ev = pd.DataFrame(rows, columns=["isin", "series_id", "via"])
    if ev.empty:
        return ev
    # An ISIN tied to two series is ambiguous: drop it.
    n = ev.groupby("isin")["series_id"].nunique()
    ev = ev[ev["isin"].isin(n[n == 1].index)]
    return ev.groupby(["isin", "series_id"], as_index=False)["via"].agg(lambda v: ", ".join(sorted(set(v))))


def build_products(series: pd.DataFrame, evidence: pd.DataFrame, cls: pd.DataFrame, url: str) -> pd.DataFrame:
    """Product rows (PRODUCT_COLUMNS) for the ISINs in ``evidence``."""
    if evidence.empty:
        return pd.DataFrame(columns=PRODUCT_COLUMNS)
    s = series.set_index("series_id")
    tick = cls.dropna(subset=["ticker"]).groupby("series_id")["ticker"].agg(list)
    n_classes = cls.groupby("series_id")["class_id"].nunique()
    rows = []
    for e in evidence.itertuples(index=False):
        if e.series_id not in s.index:
            continue
        r = s.loc[e.series_id]
        tickers = tick.get(e.series_id, [])
        category = r["category"]
        rows.append({
            "issuer": NAME,
            "site": "us",
            "portfolio_id": e.series_id,
            "isin": e.isin,
            # Only certain when the series has a single share class, with a ticker.
            "ticker": tickers[0] if len(tickers) == 1 and n_classes.get(e.series_id, 0) <= 1 else None,
            "fund_name": r["series_name"],
            "domicile": "US",
            "currency": "USD",
            "asset_class": f"Money Market ({category})" if category else "Money Market",
            # Money market funds hold the securities they report.
            "replication": "physical",
            "product_page_url": r["filing_url"],
            "holdings_url": url,
            "isin_via": e.via,
            "registrant": r["registrant"],
            "series_lei": r["series_lei"],
            "net_assets": r["net_assets"],
            "report_date": r["report_date"].isoformat() if r["report_date"] else None,
            "feeder": bool(r["feeder"]),
        })
    return pd.DataFrame(rows).drop_duplicates("isin").sort_values(["portfolio_id", "isin"]).reset_index(drop=True)


def products(gleif: bool = True, cache_dir: str | Path | None = None) -> pd.DataFrame:
    """Share-class ISINs of US money market funds in the latest monthly data set.

    About one GLEIF request per series (some 330, at most one a second), so
    ``gleif=False`` is much faster but finds only the ISINs other funds hold.
    """
    ref = datasets()[0]
    ds = load(ref, cache_dir)
    series = ds.filings()
    cls = classes(ds, fund_tickers())
    gleif_cache = Path(cache_dir) / f"{NAME}_gleif_{ref.period}.json" if cache_dir else None
    ev = isin_evidence(ds, series, gleif=gleif, gleif_cache=gleif_cache)
    return build_products(series, ev, cls, ref.url)


def _registered_products() -> pd.DataFrame:
    """``products`` as the ingest pipeline calls it: cached under ``$OPENBASKET_SEC_NMFP_DIR``."""
    return products(cache_dir=os.environ.get("OPENBASKET_SEC_NMFP_DIR") or DEFAULT_CACHE_DIR)


# -- holdings -------------------------------------------------------------------


def _isin_and_cusip(isin: object, cusip: object) -> tuple[str | None, str | None, bool]:
    """``(isin, cusip, fixed)``; a reported US/CA ISIN that contradicts the CUSIP is rebuilt from it."""
    i, c = _clean(isin), _clean(cusip)
    i = i.upper() if i and is_valid_isin(i) else None
    c = c.upper() if c and is_valid_cusip(c) else None
    if i and c and i[:2] in ("US", "CA") and i[2:11] != c:
        return cusip_to_isin(c, i[:2]), c, True
    return i, c, False


def parse_positions(sec: pd.DataFrame, accession: str, series_by_lei: dict[str, str] | None = None) -> pd.DataFrame:
    """Positions of one filing in HOLDING_COLUMNS (plus N-MFP detail columns).

    Repurchase agreements often carry a trade-level code in ``CUSIP_NUMBER``
    (``ARP1...``) that is not a security; it is kept in ``reported_cusip`` but
    not in ``cusip``, so it cannot be mapped to a real security.
    ``held_series_id`` is the money market series a line invests in (a
    feeder's master), found by its LEI in ``series_by_lei``.
    """
    p = sec[sec["ACCESSION_NUMBER"].str.strip() == accession]
    ids = [_isin_and_cusip(i, c) for i, c in zip(p["ISIN"], p["CUSIP_NUMBER"])]
    frac = pd.to_numeric(p["PERCENTAGEOFMONEYMARKETFUNDNET"].str.strip(), errors="coerce")
    classes_ = p["INVESTMENTCATEGORY"].map(asset_class).to_numpy()
    repo = classes_ == "repurchase agreement"
    leis = p["LEI"].map(_clean)
    out = pd.DataFrame({
        "isin": [None if r else x[0] for x, r in zip(ids, repo)],
        "cusip": [None if r else x[1] for x, r in zip(ids, repo)],
        "sedol": None,
        "ticker": None,
        "name": p["NAMEOFISSUER"].map(_clean).to_numpy(),
        # N-MFP reports a fraction of net assets; percent here.
        "weight_pct": (frac * 100).round(6).to_numpy(),
        "asset_class": classes_,
        # Rule 2a-7 funds may hold US dollar securities only; N-MFP has no currency field.
        "currency": "USD",
        "country": None,
        "title": p["TITLEOFISSUER"].map(_clean).to_numpy(),
        "lei": leis.to_numpy(),
        "reported_cusip": p["CUSIP_NUMBER"].map(_clean).to_numpy(),
        "held_series_id": leis.map(lambda v: (series_by_lei or {}).get(v) if v else None).to_numpy(),
        "investment_category": p["INVESTMENTCATEGORY"].map(_clean).to_numpy(),
        "final_maturity": p["FINALLEGALINVESTMENTMATURITYDA"].map(_date).to_numpy(),
        "value_usd": pd.to_numeric(p["INCLUDINGVALUEOFANYSPONSORSUPP"].str.strip(), errors="coerce").to_numpy(),
        "security_id": p["SECURITY_ID"].str.strip().to_numpy(),
        "isin_fixed": [x[2] for x in ids],
    })
    return out.reset_index(drop=True)


def find_filing(series_id: str, cache_dir: str | Path | None = None, months: int = 2) -> tuple[Dataset, dict]:
    """The latest filing of ``series_id`` in the newest ``months`` data sets."""
    for ref in datasets()[:months]:
        ds = load(ref, cache_dir)
        f = ds.filings()
        hit = f[f["series_id"] == series_id]
        if not hit.empty:
            return ds, hit.iloc[0].to_dict()
    raise LookupError(f"{series_id} has no N-MFP filing in the latest {months} monthly data sets")


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    """Full portfolio of one money market fund series from its latest N-MFP filing.

    ``product`` needs only ``portfolio_id``, the SEC series ID (``S000007051``).
    """
    ctx = ctx or FetchContext()
    sid = str(product["portfolio_id"]).strip().upper()
    ds, f = find_filing(sid, ctx.cache_dir)
    allf = ds.filings().dropna(subset=["series_lei"])
    by_lei = dict(zip(allf["series_lei"], allf["series_id"]))
    df = parse_positions(ds.securities(), f["accession_number"], by_lei)
    notes = [
        f"{f['series_name']} ({sid}), {f['submission_type']} for {f['report_date'].isoformat()}, "
        f"filed {f['filing_date'].isoformat() if f['filing_date'] else 'n/a'}, accession {f['accession_number']}",
        f"Filing: {f['filing_url']}",
        "Weights are N-MFP's fraction of net assets (four decimals) times 100.",
    ]
    if f["feeder"]:
        masters = sorted(set(df["held_series_id"].dropna()))
        notes.append(
            "Feeder fund: its main line is the master fund it invests in"
            + (f" ({', '.join(masters)}; read that series for the look-through)." if masters else ".")
        )
    zero = int(((df["weight_pct"] == 0) & (df["value_usd"] > 0)).sum())
    if zero:
        notes.append(f"{zero} positions are reported as 0.00% (under half a basis point).")
    fixed = int(df["isin_fixed"].sum())
    if fixed:
        notes.append(f"{fixed} reported ISINs contradicted the reported CUSIP and were rebuilt from it.")
    repo = df["asset_class"].eq("repurchase agreement")
    if repo.any():
        notes.append(
            f"{int(repo.sum())} repurchase agreements ({df.loc[repo, 'weight_pct'].sum():.2f}%) are shown as one "
            "line per counterparty; their collateral is in NMFP_COLLATERALISSUERS."
        )
    return IssuerHoldings(as_of=f["report_date"].isoformat(), holdings=df.drop(columns="isin_fixed"), url=ds.url, notes=notes)


def repo_collateral(product: dict, ctx: FetchContext | None = None) -> pd.DataFrame:
    """Collateral of each repurchase agreement in a series' latest filing.

    ``security_id`` links a collateral row to its repo line in :func:`holdings`.
    Values are in USD; collateral is not part of the fund's net assets, so it
    has no weight.
    """
    ctx = ctx or FetchContext()
    ds, f = find_filing(str(product["portfolio_id"]).strip().upper(), ctx.cache_dir)
    c = ds.table("COLLATERALISSUERS", [
        "ACCESSION_NUMBER", "SECURITY_ID", "NAMEOFCOLLATERALISSUER", "LEI", "CUSIPMEMBER",
        "COLLATERALMATURITYDATE", "COUPON", "PRINCIPALAMOUNTTOTHENEARESTCEN", "VALUEOFCOLLATERALTOTHENEARESTC",
        "CTGRYINVESTMENTSRPRSNTSCOLLATE",
    ])
    c = c[c["ACCESSION_NUMBER"].str.strip() == f["accession_number"]]
    cusip = c["CUSIPMEMBER"].map(_clean).map(lambda v: v.upper() if v and is_valid_cusip(v) else None)
    return pd.DataFrame({
        "security_id": c["SECURITY_ID"].str.strip(),
        "name": c["NAMEOFCOLLATERALISSUER"].map(_clean),
        "lei": c["LEI"].map(_clean),
        "cusip": cusip,
        "maturity": c["COLLATERALMATURITYDATE"].map(_clean),
        "coupon": pd.to_numeric(c["COUPON"].str.strip(), errors="coerce"),
        "principal_usd": pd.to_numeric(c["PRINCIPALAMOUNTTOTHENEARESTCEN"].str.strip(), errors="coerce"),
        "value_usd": pd.to_numeric(c["VALUEOFCOLLATERALTOTHENEARESTC"].str.strip(), errors="coerce"),
        "category": c["CTGRYINVESTMENTSRPRSNTSCOLLATE"].map(_clean),
    }).reset_index(drop=True)


ADAPTER = register(
    IssuerAdapter(
        name=NAME,
        title="SEC Form N-MFP (US money market funds)",
        homepage=INDEX_URL,
        credit="U.S. Securities and Exchange Commission, Form N-MFP data sets (Division of Economic and Risk Analysis)",
        licence=(
            "U.S. federal government data, not subject to copyright in the United States; the SEC states that "
            "information on sec.gov is public and may be copied or redistributed. The data sets are filings as "
            "submitted by the funds, not checked by the SEC. Automated access must send a User-Agent naming the "
            "requester and stay under ten requests a second. Share ISINs come from GLEIF (CC0)."
        ),
        regions=("us",),
        products=_registered_products,
        holdings=holdings,
        frequency="monthly",
        redistribution=REDISTRIBUTION,
    )
)
