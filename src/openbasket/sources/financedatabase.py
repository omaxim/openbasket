"""FinanceDatabase: names, families, categories and listings of ETFs by ISIN.

`FinanceDatabase <https://github.com/JerBouma/FinanceDatabase>`_ by Jeroen
Bouma is a set of CSV files, one per exchange, describing about 300,000
symbols. It is published under the MIT licence. Its README says the data are
"an aggregation of publicly available sources": exchange listing directories
(Nasdaq Trader, HKEX, NSE, JPX, ASX, TSX, Deutsche Börse Xetra, Euronext,
SIX), open reference data (OpenFIGI, SEC EDGAR) and community contributions,
and that nothing behind a paid subscription is included.

Files used
    ``compression/etfs.parquet``, ``compression/funds.parquet`` and
    ``compression/moneymarkets.parquet`` on ``raw.githubusercontent.com``:
    the files the ``financedatabase`` Python package itself downloads
    (``DATA_REPO`` in ``financedatabase/database_controller.py``). They hold
    the same rows and columns as ``database/etfs/*.csv``,
    ``database/funds/*.csv`` and ``database/moneymarkets.csv``, which can be
    read instead (``fmt="csv"``, one request per exchange file).
Columns
    ``symbol, name, currency, summary, category_group, category, family,
    exchange, mic, isin, delisted`` for ETFs. Funds have no ``isin`` or
    ``delisted`` column, money markets also no categories or ``mic``.

What openbasket keeps
    :func:`build_table` makes one row per ISIN with every listing of it
    (symbols, exchanges, MICs, currencies), its name, family, category and
    kind (``etf``, ``fund``, ``moneymarket``) and whether every listing is
    delisted. Rows whose ISIN fails its check digit are dropped and counted.
    The free-text ``summary`` is dropped: it is descriptive prose of unclear
    provenance, so it is kept out of every output.

Since October 2026 the funds and money-market files are no longer maintained
upstream, and neither carries ISINs, so by default only ETFs reach the table.
``isin_from_text=True`` also takes a fund's ISIN when its name or summary
names exactly one valid ISIN; such rows say ``isin_from = "summary_text"``.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

import pandas as pd

from .. import http
from ..identifiers import is_valid_isin

logger = logging.getLogger(__name__)

#: Redistribution class (see :func:`openbasket.sources.redistribution`): MIT.
REDISTRIBUTION = "public"

SOURCE_ID = "financedatabase"
HOMEPAGE = "https://github.com/JerBouma/FinanceDatabase"
RAW = "https://raw.githubusercontent.com/JerBouma/FinanceDatabase/main/"
API = "https://api.github.com/repos/JerBouma/FinanceDatabase"
CREDIT = "FinanceDatabase by Jeroen Bouma (MIT)"
LICENCE = (
    "MIT License, Copyright (c) 2023 Jeroen Bouma "
    "(https://github.com/JerBouma/FinanceDatabase/blob/main/LICENSE). The README describes the data as an "
    "aggregation of exchange listing directories, open reference data and community contributions."
)

#: Kind of instrument -> the database's folder / file name.
KINDS = {"etf": "etfs", "fund": "funds", "moneymarket": "moneymarkets"}

RAW_COLUMNS = [
    "symbol", "name", "currency", "summary", "category_group", "category", "family", "exchange", "mic", "isin",
    "delisted",
]

#: One row per listing, as kept (no ``summary``).
LISTING_COLUMNS = [
    "kind", "symbol", "name", "currency", "category_group", "category", "family", "exchange", "mic", "isin",
    "isin_from", "delisted",
]

#: The ``findb_funds`` table, one row per ISIN.
TABLE_COLUMNS = [
    "isin", "name", "family", "category_group", "category", "kind", "kinds", "symbols", "exchanges", "mics",
    "currencies", "n_listings", "n_active_listings", "delisted", "isin_from", "source", "source_url", "credit",
    "licence", "redistribution", "as_of",
]

_ISIN_IN_TEXT = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")
_KIND_ORDER = {"etf": 0, "fund": 1, "moneymarket": 2}


def compressed_url(kind: str) -> str:
    """The Parquet file the ``financedatabase`` package reads for ``kind``."""
    return f"{RAW}compression/{KINDS[kind]}.parquet"


def browse_url(kind: str) -> str:
    """A page a person can open for ``kind``'s CSV files (cited as the source link)."""
    folder = KINDS[kind]
    return f"{HOMEPAGE}/blob/main/database/{folder}.csv" if kind == "moneymarket" else f"{HOMEPAGE}/tree/main/database/{folder}"


def csv_urls(kind: str) -> list[str]:
    """Raw URLs of ``kind``'s CSV files: one per exchange, or one for money markets."""
    if kind == "moneymarket":
        return [f"{RAW}database/moneymarkets.csv"]
    r = http.get(f"{API}/contents/database/{KINDS[kind]}", headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    return sorted(e["download_url"] for e in r.json() if e.get("type") == "file" and e["name"].endswith(".csv"))


def last_commit_date(path: str) -> str | None:
    """ISO date of the last commit touching ``path`` in the repository, or ``None``."""
    try:
        r = http.get(f"{API}/commits", params={"path": path, "per_page": 1},
                     headers={"Accept": "application/vnd.github+json"}, retries=2, timeout=30)
        if r.ok and r.json():
            return r.json()[0]["commit"]["committer"]["date"][:10]
    except Exception as exc:  # the date is a nicety; the data still load
        logger.info("FinanceDatabase: no commit date for %s: %s", path, exc)
    return None


def _download(url: str, dest: Path) -> Path:
    r = http.get(url, timeout=300)
    r.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(r.content)
    tmp.replace(dest)
    return dest


def fetch(kind: str, root: str | Path, fmt: str = "parquet", overwrite: bool = False) -> dict:
    """Download ``kind`` into ``root`` (reused if fetched today) and describe it.

    Returns ``kind``, ``path`` (a file, or a folder of CSVs), ``url``, ``as_of``
    (date of the upstream file's last commit, else today) and ``bytes``.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown FinanceDatabase kind {kind!r}; choose from {sorted(KINDS)}")
    root = Path(root)
    today = date.today().isoformat()
    if fmt == "parquet":
        url = compressed_url(kind)
        dest = root / f"{KINDS[kind]}.parquet"
        if overwrite or not dest.exists() or date.fromtimestamp(dest.stat().st_mtime).isoformat() != today:
            _download(url, dest)
        size = dest.stat().st_size
        as_of = last_commit_date(f"compression/{KINDS[kind]}.parquet")
    elif fmt == "csv":
        dest = root / KINDS[kind]
        urls = csv_urls(kind)
        for u in urls:
            target = dest / u.rsplit("/", 1)[-1]
            if overwrite or not target.exists() or date.fromtimestamp(target.stat().st_mtime).isoformat() != today:
                _download(u, target)
        url = browse_url(kind)
        size = sum(p.stat().st_size for p in dest.glob("*.csv"))
        as_of = last_commit_date(f"database/{KINDS[kind]}" + (".csv" if kind == "moneymarket" else ""))
    else:
        raise ValueError(f"fmt must be 'parquet' or 'csv', not {fmt!r}")
    return {"kind": kind, "path": str(dest), "url": url, "as_of": as_of or today, "bytes": int(size)}


def read(path: str | Path) -> pd.DataFrame:
    """One kind's rows from a Parquet file, a CSV file or a folder of CSV files."""
    path = Path(path)
    if path.is_dir():
        files = sorted(path.glob("*.csv"))
        frames = [pd.read_csv(f, dtype=str, keep_default_na=False, na_values=[""]) for f in files]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=RAW_COLUMNS)
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])


def _text(v: object) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
        return None
    s = str(v).strip()
    return s or None


def _flag(v: object) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes")


def isin_in_text(*texts: object) -> str | None:
    """The one valid ISIN named in ``texts``, or ``None`` if there are none or several."""
    found = {m for t in texts if isinstance(t, str) for m in _ISIN_IN_TEXT.findall(t) if is_valid_isin(m)}
    return found.pop() if len(found) == 1 else None


def listings(frames: dict[str, pd.DataFrame], isin_from_text: bool = False) -> tuple[pd.DataFrame, dict]:
    """Every listing with a valid ISIN, and counts per kind.

    ``frames`` maps a kind to its raw rows. A row whose ISIN fails its check
    digit is dropped and counted in ``invalid_isin``. The ``summary`` text is
    read only to find an ISIN when ``isin_from_text`` is set, and never kept.
    """
    out, stats = [], {}
    for kind, raw in frames.items():
        df = raw.copy()
        for c in RAW_COLUMNS:
            if c not in df:
                df[c] = None
        df = df.astype(object).where(df.notna(), None)
        isin = df["isin"].map(lambda v: (_text(v) or "").upper() or None)
        has = isin.notna()
        valid = isin.map(is_valid_isin)
        st = {"rows": int(len(df)), "with_isin": int(has.sum()), "invalid_isin": int((has & ~valid).sum()),
              "isin_from_text": 0, "delisted": int(df["delisted"].map(_flag).sum())}
        source = pd.Series(["isin"] * len(df), index=df.index, dtype=object).where(valid, None)
        if isin_from_text and (~has).any():
            found = pd.Series(
                {i: isin_in_text(df.at[i, "name"], df.at[i, "summary"]) for i in df.index[~has]}, dtype=object
            ).dropna()
            isin.loc[found.index] = found
            source.loc[found.index] = "summary_text"
            valid = isin.map(is_valid_isin)
            st["isin_from_text"] = int(len(found))
        keep = df[valid].copy()
        keep["isin"] = isin[valid]
        keep["isin_from"] = source[valid]
        keep["kind"] = kind
        keep["delisted"] = keep["delisted"].map(_flag)
        for c in ("symbol", "name", "currency", "category_group", "category", "family", "exchange", "mic"):
            keep[c] = keep[c].map(_text)
        stats[kind] = st
        out.append(keep[LISTING_COLUMNS])
    df = pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=LISTING_COLUMNS)
    return df, stats


def _joined(values: pd.Series) -> str | None:
    vals = sorted({v for v in values if isinstance(v, str) and v})
    return "; ".join(vals) or None


def _mode(values: pd.Series) -> str | None:
    vals = [v for v in values if isinstance(v, str) and v]
    if not vals:
        return None
    counts = pd.Series(vals).value_counts()
    return sorted(counts[counts == counts.max()].index)[0]


def build_table(listing: pd.DataFrame, as_of: dict[str, str | None] | None = None) -> pd.DataFrame:
    """One row per ISIN from :func:`listings` output: the ``findb_funds`` table.

    The name, family and categories are the most common values among the
    ISIN's active listings (all listings if every one is delisted); ties go to
    the alphabetically first. ``kind`` is ``etf`` if any listing is an ETF.
    ``as_of`` maps a kind to the date of its upstream file.
    """
    as_of = as_of or {}
    if listing.empty:
        return pd.DataFrame(columns=TABLE_COLUMNS)
    listing = listing.assign(delisted=listing["delisted"].map(_flag).astype(bool))
    rows = []
    for isin, g in listing.groupby("isin", sort=True):
        active = g[~g["delisted"]]
        base = active if len(active) else g
        kinds = sorted(set(g["kind"]), key=_KIND_ORDER.get)
        dates = [as_of.get(k) for k in kinds if as_of.get(k)]
        rows.append({
            "isin": isin,
            "name": _mode(base["name"]),
            "family": _mode(base["family"]),
            "category_group": _mode(base["category_group"]),
            "category": _mode(base["category"]),
            "kind": kinds[0],
            "kinds": "; ".join(kinds),
            "symbols": _joined(g["symbol"]),
            "exchanges": _joined(g["exchange"]),
            "mics": _joined(g["mic"]),
            "currencies": _joined(g["currency"]),
            "n_listings": int(len(g)),
            "n_active_listings": int(len(active)),
            "delisted": bool(len(active) == 0),
            "isin_from": "isin" if (g["isin_from"] == "isin").any() else "summary_text",
            "source": SOURCE_ID,
            "source_url": browse_url(kinds[0]),
            "credit": CREDIT,
            "licence": LICENCE,
            "redistribution": REDISTRIBUTION,
            "as_of": max(dates) if dates else None,
        })
    return pd.DataFrame(rows, columns=TABLE_COLUMNS)


def summarise(table: pd.DataFrame, stats: dict, top: int = 15) -> dict:
    """Counts for the report: rows per kind, distinct ISINs, country prefixes, families."""
    t = table
    return {
        "source": SOURCE_ID,
        "credit": CREDIT,
        "as_of": t["as_of"].dropna().max() if len(t) and t["as_of"].notna().any() else None,
        "rows": stats,
        "invalid_isin_rows_dropped": int(sum(s.get("invalid_isin", 0) for s in stats.values())),
        "distinct_isins": int(len(t)),
        "isins_by_kind": {k: int(v) for k, v in t["kind"].value_counts().items()},
        "isins_all_listings_delisted": int(t["delisted"].sum()) if len(t) else 0,
        "isins_by_country_prefix": {k: int(v) for k, v in t["isin"].str[:2].value_counts().head(top).items()},
        "top_families": {k: int(v) for k, v in t["family"].value_counts().head(top).items()},
        "isins_without_family": int(t["family"].isna().sum()),
    }


# -- comparison with what openbasket already has ------------------------------


def load_known(tables: dict[str, dict]) -> dict[str, tuple[set[str], set[str] | None]]:
    """``{name: (ISINs, ISINs with a holdings source or None)}`` for each table that exists.

    Each entry of ``tables`` has ``path`` (a Parquet file), optionally
    ``isin_column`` (default ``isin``) and ``holdings``: ``true`` when every
    row has a holdings source (an issuer's fund list), or the name of a column
    that names the holdings source, with ``none_values`` listing values that
    mean "none". A table whose file is missing is skipped.
    """
    out = {}
    for name, spec in (tables or {}).items():
        path = Path(spec["path"])
        if not path.exists():
            logger.info("Comparison table %s not found at %s; skipped", name, path)
            continue
        col = spec.get("isin_column", "isin")
        h = spec.get("holdings")
        cols = [col] + ([h] if isinstance(h, str) else [])
        df = pd.read_parquet(path, columns=cols)
        isins = set(df[col].dropna().astype(str).str.upper())
        with_h = None
        if h is True:
            with_h = isins
        elif isinstance(h, str):
            none = set(spec.get("none_values") or [])
            m = df[h].notna() & ~df[h].isin(none)
            with_h = set(df.loc[m, col].dropna().astype(str).str.upper())
        out[name] = (isins, with_h)
    return out


def overlap(isins: pd.Series | set, known: dict[str, tuple[set[str], set[str] | None]]) -> pd.DataFrame:
    """How many of ``isins`` each known table has, and how many have holdings there.

    The last row, ``any``, is the union of all tables.
    """
    mine = set(pd.Series(list(isins)).dropna().astype(str).str.upper())
    rows, all_known, all_h = [], set(), set()
    for name, (k, h) in known.items():
        hit = mine & k
        all_known |= hit
        row = {"table": name, "isins": len(mine), "known": len(hit), "known_pct": round(100 * len(hit) / max(len(mine), 1), 2),
               "with_holdings": None, "with_holdings_pct": None}
        if h is not None:
            hh = mine & h
            all_h |= hh
            row["with_holdings"] = len(hh)
            row["with_holdings_pct"] = round(100 * len(hh) / max(len(mine), 1), 2)
        rows.append(row)
    rows.append({"table": "any", "isins": len(mine), "known": len(all_known),
                 "known_pct": round(100 * len(all_known) / max(len(mine), 1), 2),
                 "with_holdings": len(all_h), "with_holdings_pct": round(100 * len(all_h) / max(len(mine), 1), 2)})
    return pd.DataFrame(rows)
