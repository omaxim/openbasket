"""albertored/etfdb: metadata and top holdings of about 4,650 ETFs sold in Europe.

`albertored/etfdb <https://github.com/albertored/etfdb>`_ is a "static ETF
database" in JSON, CSV, XML and XLSX, refreshed about monthly. One file is
read: ``json/etfs.json`` from ``raw.githubusercontent.com``, one record per
ETF with its ISIN, provider, replication, index, domicile, currency, hedging,
number of holdings, strategies, update dates and up to ten top holdings with
name, percentage and ISIN. (``csv/holdings.csv`` is a wide table whose columns
are thirty company names, without ISINs, so it is not used.) No website is
read.

Terms
    The repository has **no licence file** and its README states no terms,
    so all rights are reserved by default. The owner's own notes in the
    repository say the figures come from justETF, a commercial site. The
    adapter is therefore ``local_only``: it runs for the person running
    openbasket, and the public build never fetches or publishes it.

Two uses
    :func:`meta` builds the ``etfdb_meta`` table, which flags swap-based and
    leveraged or short funds and names the index each ETF tracks, so a
    physical ETF on the same index can be proposed as a stand-in
    (:func:`stand_in_candidates`).

    The ``etfdb_top`` adapter returns those top holdings for a fund no other
    source covers. They are the top ten lines only, as percent of the fund as
    given, never rescaled; the rest of the fund stays unresolved. The adapter
    lists only ETFs that have top holdings.

Raw downloads are kept in ``data/01_raw/etfdb`` (or ``$OPENBASKET_ETFDB_DIR``),
one file per day, older ones deleted.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path

import pandas as pd

from ... import http
from ...countries import country_code
from ...identifiers import is_valid_isin
from . import HOLDING_COLUMNS, LOCAL_ONLY, PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

logger = logging.getLogger(__name__)

#: Redistribution class of everything read from albertored/etfdb.
REDISTRIBUTION = LOCAL_ONLY

NAME = "etfdb_top"
SOURCE_ID = "etfdb"
REPO = "https://github.com/albertored/etfdb"
JSON_URL = "https://raw.githubusercontent.com/albertored/etfdb/main/json/etfs.json"
PAGE_URL = REPO + "/blob/main/json/etfs.json"
DEFAULT_ROOT = "data/01_raw/etfdb"
TITLE = "albertored/etfdb top holdings (top 10 only; rest unresolved)"
CREDIT = (
    "Top holdings and fund facts from albertored/etfdb (https://github.com/albertored/etfdb); "
    "no licence stated, data appear to come from justETF; read for private local use only"
)
LICENCE = (
    "No licence: the repository has no LICENSE file and its README states no terms, so all rights are reserved "
    "by default. The owner's notes in the repository say the figures come from justETF, a commercial site. "
    "Read for private local runs only; never republished."
)
FREQUENCY = "about monthly (repository updates); top holdings dated by each fund's holdings_update_date"

#: etfdb ``replication`` -> openbasket's product ``replication``.
REPLICATION = {
    "Full replication": "physical",
    "Physically backed": "physical",
    "Sampling": "sampled",
    "Optimized sampling": "sampled",
    "Swap based": "swap",
    "Swap based Unfunded": "swap",
    "Swap based Funded": "swap",
    "Physical & Synthetic": None,
}

#: Strategy labels that mean a leveraged or short fund (the README says
#: ``Short & Leverage``, the data say ``Short & Leveraged``).
LEVERAGED = {"short & leverage", "short & leveraged"}

#: Fund asset class -> asset class of its top holdings, where it is clear.
HOLDING_CLASS = {"equity": "equity", "realEstate": "equity", "bonds": "debt"}

META_COLUMNS = [
    "isin", "name", "ticker", "wkn", "fund_provider", "asset_class", "replication", "replication_class",
    "uses_swaps", "is_leveraged", "is_active", "strategies", "index", "domicile", "domicile_code", "currency",
    "hedged", "securities_lending", "dividends", "ter_pct", "size_eur_m", "is_sustainable", "number_of_holdings",
    "inception_date", "n_top_holdings", "top_holdings_pct", "update_date", "holdings_update_date", "source",
    "source_url", "credit", "licence", "redistribution",
]


def root_dir(root: str | Path | None = None) -> Path:
    return Path(root or os.environ.get("OPENBASKET_ETFDB_DIR") or DEFAULT_ROOT)


def fetch(root: str | Path | None = None, overwrite: bool = False) -> Path:
    """Today's copy of ``etfs.json`` in ``root``, downloaded if missing; older copies are deleted."""
    folder = root_dir(root)
    dest = folder / f"etfs-{date.today().isoformat()}.json"
    if overwrite or not dest.exists():
        r = http.get(JSON_URL, timeout=300)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            raise ValueError(f"{JSON_URL}: expected a list of ETF records, got {type(data).__name__}")
        folder.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(r.content)
        tmp.replace(dest)
        logger.info("albertored/etfdb: %d ETF records", len(data))
    for old in folder.glob("etfs-*.json"):
        if old != dest:
            old.unlink(missing_ok=True)
    return dest


def latest(root: str | Path | None = None) -> Path | None:
    files = sorted(root_dir(root).glob("etfs-*.json"))
    return files[-1] if files else None


_cache: dict[str, dict[str, dict]] = {}


def records(root: str | Path | None = None, refresh: bool = True) -> dict[str, dict]:
    """ETF records by ISIN, from today's file (fetched when ``refresh``) or the latest one kept."""
    path = fetch(root) if refresh else latest(root)
    if path is None:
        path = fetch(root)
    key = str(path)
    if key not in _cache:
        _cache.clear()
        _cache[key] = index(json.loads(Path(path).read_text("utf-8")))
    return _cache[key]


def index(data: list[dict]) -> dict[str, dict]:
    """Records with a valid ISIN, by ISIN (the first of any duplicate)."""
    out: dict[str, dict] = {}
    for rec in data:
        isin = str(rec.get("isin") or "").strip().upper()
        if is_valid_isin(isin) and isin not in out:
            out[isin] = rec
    return out


def _strategies(rec: dict) -> list[str]:
    s = rec.get("strategies") or []
    return [str(x) for x in s] if isinstance(s, list) else [str(s)]


def _num(v: object) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _top(rec: dict) -> list[dict]:
    h = rec.get("holdings")
    return [x for x in h if isinstance(x, dict)] if isinstance(h, list) else []


def meta_row(isin: str, rec: dict) -> dict:
    """One ``etfdb_meta`` row."""
    strategies = _strategies(rec)
    replication = rec.get("replication") or None
    top = _top(rec)
    weights = [_num(h.get("percentage")) for h in top]
    domicile = rec.get("domicile_country") or None
    return {
        "isin": isin,
        "name": rec.get("name"),
        "ticker": rec.get("ticker") or None,
        "wkn": rec.get("wkn") or None,
        "fund_provider": rec.get("fund_provider") or None,
        "asset_class": rec.get("asset_class") or None,
        "replication": replication,
        "replication_class": REPLICATION.get(replication),
        "uses_swaps": bool(replication and "swap" in replication.lower()) or replication == "Physical & Synthetic",
        "is_leveraged": any(s.strip().lower() in LEVERAGED for s in strategies),
        "is_active": any(s.strip().lower() == "active" for s in strategies),
        "strategies": "; ".join(strategies) or None,
        "index": rec.get("index") or None,
        "domicile": domicile,
        "domicile_code": country_code(domicile),
        "currency": rec.get("currency") or None,
        "hedged": rec.get("hedged"),
        "securities_lending": rec.get("securities_lending"),
        "dividends": rec.get("dividends") or None,
        "ter_pct": _num(rec.get("ter")),
        "size_eur_m": _num(rec.get("size")),
        "is_sustainable": rec.get("is_sustainable"),
        "number_of_holdings": _num(rec.get("number_of_holdings")),
        "inception_date": rec.get("inception_date") or None,
        "n_top_holdings": len(top),
        "top_holdings_pct": round(sum(w for w in weights if w is not None), 4) if top else None,
        "update_date": rec.get("update_date") or None,
        "holdings_update_date": rec.get("holdings_update_date") or None,
        "source": SOURCE_ID,
        "source_url": PAGE_URL,
        "credit": CREDIT,
        "licence": LICENCE,
        "redistribution": REDISTRIBUTION,
    }


def meta(recs: dict[str, dict] | None = None) -> pd.DataFrame:
    """The ``etfdb_meta`` table: one row per ETF."""
    recs = records() if recs is None else recs
    rows = [meta_row(i, r) for i, r in recs.items()]
    df = pd.DataFrame(rows, columns=META_COLUMNS)
    for c in ("hedged", "securities_lending", "is_sustainable"):
        df[c] = df[c].map(lambda v: v if isinstance(v, bool) else None).astype("boolean")
    return df


def _index_key(name: object) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    return " ".join(name.lower().replace("®", "").split())


def stand_in_candidates(meta_df: pd.DataFrame, per_fund: int = 3) -> pd.DataFrame:
    """For each swap-based ETF, physical ETFs in ``meta_df`` on the same index (largest first).

    The index is matched by its name as etfdb gives it (case and spacing
    ignored). Only equity, bond and real-estate funds are paired (a physical
    gold ETC has no securities to look through), leveraged or short funds are
    skipped on both sides, and unhedged candidates come before hedged ones.
    The result is a list to review before adding any of them to
    ``lookthrough.proxies``.
    """
    cols = ["isin", "name", "index", "replication", "candidate_isin", "candidate_name", "candidate_provider",
            "candidate_replication", "candidate_hedged", "candidate_size_eur_m", "candidate_n_top_holdings"]
    m = meta_df.assign(ikey=meta_df["index"].map(_index_key))
    m = m[m["ikey"].notna() & ~m["is_leveraged"].astype(bool) & m["asset_class"].isin(list(HOLDING_CLASS))]
    targets = m[m["replication_class"] == "swap"]
    peers = m[m["replication_class"] == "physical"].copy()
    peers["hedged_flag"] = peers["hedged"].fillna(False).astype(bool)
    peers = peers.sort_values(["ikey", "hedged_flag", "size_eur_m"], ascending=[True, True, False], na_position="last")
    by_key = {k: g for k, g in peers.groupby("ikey", sort=False)}
    rows = []
    for t in targets.itertuples(index=False):
        g = by_key.get(t.ikey)
        if g is None:
            continue
        for p in g.head(per_fund).itertuples(index=False):
            rows.append({
                "isin": t.isin, "name": t.name, "index": t.index, "replication": t.replication,
                "candidate_isin": p.isin, "candidate_name": p.name, "candidate_provider": p.fund_provider,
                "candidate_replication": p.replication, "candidate_hedged": p.hedged_flag,
                "candidate_size_eur_m": p.size_eur_m, "candidate_n_top_holdings": p.n_top_holdings,
            })
    return pd.DataFrame(rows, columns=cols)


def product_rows(recs: dict[str, dict]) -> pd.DataFrame:
    """Product rows for the ETFs that have top holdings."""
    rows = []
    for isin, rec in recs.items():
        if not _top(rec):
            continue
        m = meta_row(isin, rec)
        rows.append({
            "issuer": NAME, "site": "eu", "portfolio_id": isin, "isin": isin, "ticker": m["ticker"],
            "fund_name": m["name"], "domicile": m["domicile_code"] or m["domicile"], "currency": m["currency"],
            "asset_class": m["asset_class"], "replication": m["replication_class"], "product_page_url": REPO,
            "holdings_url": JSON_URL, "fund_provider": m["fund_provider"], "index": m["index"],
            "is_leveraged": m["is_leveraged"], "holdings_update_date": m["holdings_update_date"],
        })
    extra = ["fund_provider", "index", "is_leveraged", "holdings_update_date"]
    return pd.DataFrame(rows, columns=PRODUCT_COLUMNS + extra)


def holdings_of(isin: str, rec: dict) -> IssuerHoldings:
    """The top holdings in one ETF record, labelled as top holdings only."""
    hclass = HOLDING_CLASS.get(rec.get("asset_class") or "")
    rows = []
    for h in _top(rec):
        hi = str(h.get("isin") or "").strip().upper()
        rows.append({
            "isin": hi if is_valid_isin(hi) else None, "cusip": None, "sedol": None, "ticker": None,
            "name": h.get("name"), "weight_pct": _num(h.get("percentage")), "asset_class": hclass,
            "currency": None, "country": None,
        })
    df = pd.DataFrame(rows, columns=HOLDING_COLUMNS)
    df["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce")
    when = rec.get("holdings_update_date") or rec.get("update_date")
    notes = [f"top holdings only, from albertored/etfdb ({when}); the rest of the fund is unresolved"]
    m = meta_row(isin, rec)
    if m["is_leveraged"]:
        notes.append("etfdb lists this fund under Short & Leveraged: its holdings do not show its exposure")
    if m["uses_swaps"]:
        notes.append(f"etfdb gives the replication as {m['replication']}: the holdings may be a substitute basket")
    return IssuerHoldings(as_of=when, holdings=df, url=JSON_URL, notes=notes)


def products() -> pd.DataFrame:
    return product_rows(records())


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    isin = str(product.get("isin") or product.get("portfolio_id") or "").upper()
    recs = records(refresh=False)
    if isin not in recs:
        raise KeyError(f"{isin} is not in albertored/etfdb")
    return holdings_of(isin, recs[isin])


ADAPTER = register(
    IssuerAdapter(
        name=NAME,
        title=TITLE,
        homepage=REPO,
        credit=CREDIT,
        licence=LICENCE,
        regions=("EU", "CH", "GB"),
        products=products,
        holdings=holdings,
        frequency=FREQUENCY,
        redistribution=LOCAL_ONLY,
        enabled_by_default=True,
    )
)
