"""Download raw source files."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from ...datasets import connection
from ...identifiers import is_valid_isin
from ...sources import FULL_MODE, fund_identity, issuers, nport, publish_mode, redistribution, sec, sec_nport_history, spdr

logger = logging.getLogger(__name__)


def download_nport(quarters: list[str], raw_dir: str, overwrite: bool = False, history: dict | None = None) -> pd.DataFrame:
    """Download the N-PORT ZIP for each quarter (``2026q3`` style) unless cached.

    ``history`` (``params:nport_history``) adds older quarters, see
    :func:`openbasket.sources.sec_nport_history.resolve_quarters`.
    """
    rows = []
    for q in sec_nport_history.resolve_quarters(quarters, history):
        q = q.lower()
        dest = Path(raw_dir) / f"{q}_nport.zip"
        sec.download(sec.NPORT_URL.format(quarter=q), dest, overwrite=overwrite)
        rows.append({"quarter": q, "zip_path": str(dest), "bytes": dest.stat().st_size})
    return pd.DataFrame(rows)


def nport_to_parquet(raw_manifest: pd.DataFrame, parquet_dir: str) -> pd.DataFrame:
    """Convert the tables the pipeline needs to Parquet, one folder per quarter."""
    rows = []
    for r in raw_manifest.itertuples(index=False):
        rows += nport.zip_to_parquet(connection(), Path(r.zip_path), r.quarter, Path(parquet_dir))
    return pd.DataFrame(rows)


def download_fund_tickers() -> pd.DataFrame:
    """SEC's fund and ETF share-class tickers with their series IDs."""
    return sec.fund_tickers()


#: Issuers whose fund lists are read by code in this package, not by an adapter.
_BUILTIN = ("spdr",)


def products_options(ishares_sites: list[str] | None, issuers_params: dict | None) -> dict[str, dict]:
    """Keyword arguments for each adapter's ``products()``, by adapter name.

    ``issuers.options.<adapter>`` gives them; ``issuers.ishares_sites`` is the
    ``sites`` option of the iShares adapter unless that sets its own.
    """
    options = {k: dict(v or {}) for k, v in ((issuers_params or {}).get("options") or {}).items()}
    if ishares_sites is not None:
        options.setdefault("ishares", {}).setdefault("sites", [str(x) for x in ishares_sites])
    return options


def download_issuer_products(
    ishares_sites: list[str], spdr_regions: list[str], issuers_params: dict | None = None,
    publish_params: dict | None = None,
) -> pd.DataFrame:
    """Every fund on the issuer lists, keyed by share-class ISIN.

    SPDR comes first, then every adapter registered in
    :mod:`openbasket.sources.issuers` in name order (iShares among them, over
    ``issuers.ishares_sites``; ``issuers.enabled`` limits them,
    ``issuers.disabled`` drops some), each row with ``issuer`` set to the
    adapter's name. An adapter that fails, or returns rows that break the
    product contract, is logged and skipped or cleaned; it never stops the
    others. An ISIN two lists share stays with the first; stand-in adapters
    (a peer's holdings for a fund, :attr:`IssuerAdapter.stand_in`) come last,
    so they never take a fund a direct adapter lists. ``listed_on`` is the
    date the list was read.

    In the public build (``publish.mode: public``) nothing is asked of a
    ``local_only`` source: not the iShares and SPDR lists, not a
    ``local_only`` adapter's ``products()``. Their funds are added as
    identity rows instead (ISIN, name, issuer; see
    :mod:`openbasket.sources.fund_identity`), so they are still listed.
    """
    from ...pipelines.lookthrough.nodes import issuer_adapters

    mode = publish_mode(publish_params)
    adapters = issuer_adapters(issuers_params)
    frames = []
    withheld: list[str] = []
    if mode == FULL_MODE:
        for r in spdr_regions or []:
            try:
                frames.append(spdr.products(r))
            except Exception as exc:  # one fund finder down must not stop the others
                logger.warning("SPDR %s: product list failed: %s: %s", r, type(exc).__name__, exc)
    else:
        withheld = [n for n in _BUILTIN if redistribution(n) != issuers.PUBLIC]
        withheld += [n for n, a in adapters.items() if a.redistribution != issuers.PUBLIC]
        adapters = {n: a for n, a in adapters.items() if a.redistribution == issuers.PUBLIC}
        logger.info("Public build: not reading the fund lists of %s (local_only)", sorted(withheld))
    options = products_options(ishares_sites, issuers_params)
    for name, adapter in sorted(adapters.items(), key=lambda kv: (kv[1].stand_in, kv[0])):
        try:
            df = adapter.products(**options.get(name, {}))
        except Exception as exc:  # one issuer's site must not stop the others
            logger.warning("Issuer %s: product list failed: %s: %s", name, type(exc).__name__, exc)
            continue
        problems = issuers.validate_products(df)
        if problems:
            logger.warning("Issuer %s: %s", name, "; ".join(problems))
        if "isin" not in df or df.empty:
            continue
        df = df.assign(issuer=name)
        df = df[df["isin"].map(is_valid_isin)].drop_duplicates("isin")
        logger.info("Issuer %s: %d share classes", name, len(df))
        frames.append(df)
    out = pd.concat([f for f in frames if len(f)], ignore_index=True) if any(len(f) for f in frames) else pd.DataFrame(columns=issuers.PRODUCT_COLUMNS)
    out["listed_on"] = date.today().isoformat()
    if mode != FULL_MODE:
        identity = local_only_identity(withheld, issuer_adapters(issuers_params), publish_params or {})
        if len(identity):
            out = pd.concat([out, identity], ignore_index=True) if len(out) else identity
    out = out.drop_duplicates("isin").reset_index(drop=True)
    return parquet_safe(out)


def local_only_identity(withheld: list[str], adapters: dict, publish_params: dict) -> pd.DataFrame:
    """Identity rows (ISIN, name, issuer) for the funds of the ``withheld`` issuers, from local files only.

    ``publish.identity.registries`` names the open registry files and
    ``publish.identity.issuer_lists`` the cached issuer fund lists; see
    :func:`openbasket.sources.fund_identity.identity_rows`. With
    ``publish.identity.openfigi_cache`` set, US funds get their US ticker
    from OpenFIGI.
    """
    cfg = publish_params.get("identity") or {}
    sources = publish_params.get("sources") or {}
    info = {}
    for name in withheld:
        a = adapters.get(name)
        src = sources.get(name) or {}
        info[name] = {"title": a.title if a else src.get("listed_by") or name,
                      "homepage": a.homepage if a else src.get("url")}
    rows = fund_identity.identity_rows(info, cfg.get("registries"), cfg.get("issuer_lists") or [])
    if cfg.get("openfigi_cache") and len(rows):
        from ...sources.openfigi import OpenFIGI

        try:
            rows = fund_identity.add_us_tickers(rows, OpenFIGI(cfg["openfigi_cache"]))
        except Exception as exc:  # a broken cache file costs the tickers, nothing more
            logger.warning("Fund identity: OpenFIGI tickers skipped: %s", exc)
    return rows


def _kind(v: object) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    return "str" if isinstance(v, str) else "other"


def parquet_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Columns that mix value types (adapters add their own) become text, so the frame saves as Parquet."""
    df = df.copy()
    for col in df.columns:
        if df[col].dtype == object:
            kinds = {_kind(v) for v in df[col] if v is not None and not (isinstance(v, float) and pd.isna(v))}
            if kinds == {"number"}:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            elif len(kinds) > 1 or "other" in kinds:
                df[col] = [None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v) for v in df[col]]
    if "portfolio_id" in df:
        df["portfolio_id"] = df["portfolio_id"].map(lambda v: None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    return df
