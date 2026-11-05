"""Who a fund is, without its holdings: for the public build.

The public build never reads a ``local_only`` source's holdings (see
:func:`openbasket.sources.redistribution`), but the funds those sources
cover still belong in the public index, so a reader can find them and learn
where their holdings are. This module gives each such fund its identity,
and nothing more: ISIN, name and issuer.

Where a fact comes from, best first:

1. an open registry that the project has built locally, when its file is
   there: the fund directory (``fund_directory``), the GLEIF and ESMA fund
   registry (``fund_registry``) or ESMA FIRDS (``firds``);
2. otherwise the issuer's own public fund list, as cached by an earlier full
   run (``data/01_raw/issuers/issuer_products.parquet``), read for those
   three columns only (``issuer_list``).

Which funds an issuer covers comes from the fund directory's ``adapter``
column or from the cached list. Nothing here downloads anything.

Every identity row names the source of its name in ``identity_source`` and
a link in ``identity_url``: the registry's own link for the fund, or the
issuer's home page.

A US fund's ticker is not taken from the issuer either: :func:`add_us_tickers`
asks OpenFIGI (a public source) for the US listing of its ISIN, so the
universe can still tie the fund to its N-PORT series by ticker.

The scheduled public build has none of those local files. A local full run
therefore writes the same identity rows to ``public_aggregates/fund_identity.csv``
(:func:`write_identity_file`), committed with the coarse splits, and the
public build reads them back (:func:`read_identity_file`). The file is
checked by :func:`validate_identity` both ways: identity columns only.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from ..identifiers import is_valid_isin

logger = logging.getLogger(__name__)

#: Redistribution class (see :func:`openbasket.sources.redistribution`):
#: fund names, ISINs and issuers are facts; no holdings come from here.
REDISTRIBUTION = "public"

#: Source id of a fact taken from an issuer's public fund list.
ISSUER_LIST = "issuer_list"

#: Open registries, in order of preference, and where the project writes them.
DEFAULT_REGISTRIES = {
    "fund_directory": "data/03_primary/fund_directory.parquet",
    "fund_registry": "data/03_primary/fund_registry.parquet",
    "firds": "data/03_primary/firds_instruments.parquet",
}

#: Columns tried, in order, for a fund's name and its link in a registry file.
_NAME_COLUMNS = ("name", "full_name", "fund_name", "short_name")
_URL_COLUMNS = ("source_url", "url")
_DATE_COLUMNS = ("as_of", "listed_on")

#: The only columns read from a cached issuer fund list.
ISSUER_LIST_COLUMNS = ["isin", "fund_name", "issuer", "listed_on"]

#: Columns of an identity row; ``site`` and ``portfolio_id`` only make it a
#: valid ``issuer_products`` row (``<issuer>-isin:<isin>``), and are not issuer facts.
IDENTITY_COLUMNS = [
    "issuer", "site", "portfolio_id", "isin", "fund_name", "ticker", "ticker_source", "identity_source",
    "identity_url", "identity_only", "listed_on",
]


def _columns(path: Path) -> list[str]:
    return list(pq.read_schema(path).names)


def _text(v: object) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def read_registry(path: str | Path) -> pd.DataFrame:
    """``isin``, ``name``, ``adapter``, ``url`` and ``as_of`` from one registry file; empty if it is not there."""
    out_cols = ["isin", "name", "adapter", "url", "as_of"]
    p = Path(path)
    if not p.is_file():
        return pd.DataFrame(columns=out_cols)
    try:
        cols = _columns(p)
    except Exception as exc:  # a half-written file is skipped, not fatal
        logger.warning("Registry %s cannot be read: %s", p, exc)
        return pd.DataFrame(columns=out_cols)
    if "isin" not in cols:
        return pd.DataFrame(columns=out_cols)
    pick = {"name": next((c for c in _NAME_COLUMNS if c in cols), None),
            "adapter": "adapter" if "adapter" in cols else None,
            "url": next((c for c in _URL_COLUMNS if c in cols), None),
            "as_of": next((c for c in _DATE_COLUMNS if c in cols), None)}
    df = pd.read_parquet(p, columns=["isin", *[c for c in pick.values() if c]])
    out = pd.DataFrame({"isin": df["isin"]})
    for key, col in pick.items():
        out[key] = df[col].map(_text) if col else None
    out = out[out["isin"].map(is_valid_isin)].drop_duplicates("isin")
    return out.reset_index(drop=True)


def read_issuer_list(path: str | Path) -> pd.DataFrame:
    """ISIN, fund name, issuer and the day it was read from a cached ``issuer_products`` file; nothing else."""
    p = Path(path)
    if not p.is_file():
        return pd.DataFrame(columns=ISSUER_LIST_COLUMNS)
    try:
        cols = _columns(p)
    except Exception as exc:
        logger.warning("Issuer fund list %s cannot be read: %s", p, exc)
        return pd.DataFrame(columns=ISSUER_LIST_COLUMNS)
    df = pd.read_parquet(p, columns=[c for c in ISSUER_LIST_COLUMNS if c in cols])
    for c in ISSUER_LIST_COLUMNS:
        if c not in df:
            df[c] = None
    if "issuer" not in cols:
        df["issuer"] = "ishares"  # lists from before adapters had no issuer column
    df["issuer"] = df["issuer"].fillna("ishares").astype(str).str.lower()
    return df[df["isin"].map(is_valid_isin)][ISSUER_LIST_COLUMNS].reset_index(drop=True)


def identity_rows(
    local_only: dict[str, dict],
    registries: dict[str, str] | None = None,
    issuer_lists: list[str] | None = None,
    today: str | None = None,
) -> pd.DataFrame:
    """One identity row per fund ISIN that a ``local_only`` issuer covers.

    ``local_only`` maps an issuer id (``ishares``, an adapter's name) to
    ``{"title": ..., "homepage": ...}``. ``registries`` maps a registry id to
    its file (default :data:`DEFAULT_REGISTRIES`); ``issuer_lists`` are
    cached issuer fund lists. A fund's name comes from the first registry
    that has its ISIN, else from the issuer list.
    """
    registries = DEFAULT_REGISTRIES if registries is None else registries
    today = today or date.today().isoformat()
    covered: dict[str, str] = {}
    list_facts: dict[str, dict] = {}
    names: dict[str, tuple[str, str, str | None, str | None]] = {}
    for rid, path in registries.items():
        reg = read_registry(path)
        if reg.empty:
            continue
        logger.info("Fund identity: %d ISINs in %s (%s)", len(reg), rid, path)
        for r in reg.itertuples(index=False):
            adapter = (r.adapter or "").lower()
            if adapter in local_only:
                covered.setdefault(r.isin, adapter)
            if r.name and r.isin not in names:
                names[r.isin] = (r.name, rid, r.url, r.as_of)
    for path in issuer_lists or []:
        lst = read_issuer_list(path)
        lst = lst[lst["issuer"].isin(list(local_only))]
        if lst.empty:
            continue
        logger.info("Fund identity: %d local-only funds in the cached issuer list %s", len(lst), path)
        for r in lst.itertuples(index=False):
            covered.setdefault(r.isin, r.issuer)
            list_facts.setdefault(r.isin, {"name": _text(r.fund_name), "listed_on": _text(r.listed_on)})
    rows = []
    for isin, issuer in covered.items():
        info = local_only.get(issuer) or {}
        if isin in names:
            name, src, url, as_of = names[isin]
        else:
            fact = list_facts.get(isin) or {}
            name, src, url, as_of = fact.get("name"), ISSUER_LIST, None, fact.get("listed_on")
        rows.append({
            "issuer": issuer, "site": "isin", "portfolio_id": isin, "isin": isin, "fund_name": name,
            "ticker": None, "ticker_source": None, "identity_source": src, "identity_url": url or info.get("homepage"), "identity_only": True,
            "listed_on": as_of or today,
        })
    out = pd.DataFrame(rows, columns=IDENTITY_COLUMNS)
    if len(out):
        logger.info("Fund identity: %d funds of local-only issuers (%s), names from %s", len(out),
                    out["issuer"].value_counts().to_dict(), out["identity_source"].value_counts().to_dict())
    return out


def add_us_tickers(rows: pd.DataFrame, figi) -> pd.DataFrame:
    """Fill ``ticker`` for US ISINs with their US listing from OpenFIGI (``ticker_source`` ``openfigi``).

    ``figi`` is an :class:`~openbasket.sources.openfigi.OpenFIGI` client;
    cached answers cost no request. A lookup that fails leaves the tickers
    empty.
    """
    if rows.empty:
        return rows
    us = [i for i in rows["isin"] if isinstance(i, str) and i.startswith("US")]
    if not us:
        return rows
    try:
        found = figi.map("ID_ISIN", us)
    except Exception as exc:  # tickers only help matching; never lose ingest over them
        logger.warning("Fund identity: OpenFIGI tickers skipped: %s", exc)
        return rows
    tickers = {}
    for isin, listings in found.items():
        t = next((r.get("ticker") for r in listings or [] if r.get("exchCode") == "US" and r.get("ticker")), None)
        if t:
            tickers[isin] = t
    rows = rows.copy()
    rows["ticker"] = rows["isin"].map(tickers)
    rows["ticker_source"] = rows["ticker"].map(lambda t: "openfigi" if isinstance(t, str) else None)
    logger.info("Fund identity: US tickers for %d of %d US ISINs from OpenFIGI", len(tickers), len(us))
    return rows


#: Where the name of an identity row may come from.
IDENTITY_SOURCES = (*DEFAULT_REGISTRIES, ISSUER_LIST)


class IdentityError(ValueError):
    """An identity file holds something other than fund identities."""


def validate_identity(df: pd.DataFrame) -> pd.DataFrame:
    """The rows if they are identity rows only (:data:`IDENTITY_COLUMNS`, valid ISINs, known name sources); else raise."""
    if list(df.columns) != IDENTITY_COLUMNS:
        raise IdentityError(f"identity file columns {list(df.columns)} are not {IDENTITY_COLUMNS}")
    bad = df[~df["isin"].map(is_valid_isin)]
    if len(bad):
        raise IdentityError(f"{len(bad)} rows without a valid ISIN, e.g. {bad['isin'].iloc[0]!r}")
    src = set(df["identity_source"].dropna()) - set(IDENTITY_SOURCES)
    if src:
        raise IdentityError(f"unknown name sources {sorted(src)}")
    if df["isin"].duplicated().any():
        raise IdentityError("an ISIN appears twice")
    return df


def write_identity_file(rows: pd.DataFrame, path: str | Path) -> pd.DataFrame:
    """Write identity rows, sorted by ISIN, for the public build; returns what was written."""
    out = validate_identity(rows[IDENTITY_COLUMNS].drop_duplicates("isin").sort_values("isin").reset_index(drop=True))
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(p, index=False)
    return out


def read_identity_file(path: str | Path, issuers: set[str] | None = None) -> pd.DataFrame:
    """Identity rows from a file a local run wrote; only those of ``issuers`` when given. Empty if absent."""
    p = Path(path)
    if not p.is_file():
        return pd.DataFrame(columns=IDENTITY_COLUMNS)
    df = pd.read_csv(p, dtype=str, keep_default_na=False).replace({"": None})
    df["identity_only"] = True
    df = validate_identity(df[IDENTITY_COLUMNS] if set(IDENTITY_COLUMNS) <= set(df.columns) else df)
    if issuers is not None:
        df = df[df["issuer"].isin(issuers)]
    logger.info("Fund identity: %d funds from %s", len(df), p)
    return df.reset_index(drop=True)
