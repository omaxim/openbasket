"""Nodes of the ``directory`` pipeline.

Each source node downloads one public list (cached per day) and returns it
parsed. A source that is switched off in ``parameters_directory.yml``, or
that fails while ``strict`` is false, returns an empty frame and logs why, so
one unreachable exchange does not stop the rest of the directory. With
``public_only`` (set by ``conf/public``), sources whose redistribution class
is ``local_only`` are not read at all. :func:`directory_sources` reports what
each source gave (rows, as-of date, or why it gave nothing).
"""

from __future__ import annotations

import logging
from typing import Callable

import pandas as pd

from ...sources import directory as d

logger = logging.getLogger(__name__)

#: What each source gave in this run: ``{key: {"status": ..., "error": ...}}``.
_STATUS: dict[str, dict] = {}


def _run(params: dict, key: str, fn: Callable[[], pd.DataFrame], columns: list[str]) -> pd.DataFrame:
    source = d.SOURCES.get(key)
    if not params.get("sources", {}).get(key, False):
        logger.info("directory source %s is switched off", key)
        _STATUS[key] = {"status": "off"}
        return pd.DataFrame(columns=columns)
    if params.get("public_only", False) and (source is None or source.redistribution != "public"):
        logger.info("directory source %s is not read: its terms do not allow republishing (public build)", key)
        _STATUS[key] = {"status": "local_only"}
        return pd.DataFrame(columns=columns)
    try:
        out = fn()
    except Exception as exc:
        if params.get("strict", False):
            raise
        logger.exception("directory source %s failed; continuing without it", key)
        _STATUS[key] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:300]}
        return pd.DataFrame(columns=columns)
    logger.info("directory source %s: %d rows", key, len(out))
    _STATUS[key] = {"status": "ok" if len(out) else "empty"}
    return out


def _cache(params: dict) -> str | None:
    return params.get("cache_dir")


def dir_six_funds(params: dict) -> pd.DataFrame:
    return _run(params, "six_funds", lambda: d.six_funds(_cache(params)), d.FUND_LISTING_COLUMNS)


def dir_six_bonds(params: dict) -> pd.DataFrame:
    return _run(params, "six_bonds", lambda: d.six_bonds(_cache(params)), d.BOND_LISTING_COLUMNS)


def dir_xetra(params: dict) -> pd.DataFrame:
    return _run(params, "xetra", lambda: d.xetra(_cache(params)), d.FUND_LISTING_COLUMNS)


def dir_euronext_etfs(params: dict) -> pd.DataFrame:
    return _run(params, "euronext_etfs", lambda: d.euronext_etfs(_cache(params)), d.FUND_LISTING_COLUMNS)


def dir_euronext_bonds(params: dict) -> pd.DataFrame:
    return _run(params, "euronext_bonds", lambda: d.euronext_bonds(_cache(params)), d.BOND_LISTING_COLUMNS)


def dir_ecb_eligible(params: dict) -> pd.DataFrame:
    return _run(params, "ecb_eligible", lambda: d.ecb_eligible(_cache(params)), d.BOND_LISTING_COLUMNS)


def dir_esma_firds_funds(params: dict) -> pd.DataFrame:
    return _run(params, "esma_firds_funds", lambda: d.firds("esma", "C", _cache(params)), d.FUND_LISTING_COLUMNS)


def dir_fca_firds_funds(params: dict) -> pd.DataFrame:
    return _run(params, "fca_firds_funds", lambda: d.firds("fca", "C", _cache(params)), d.FUND_LISTING_COLUMNS)


def dir_esma_firds_debt(params: dict) -> pd.DataFrame:
    return _run(params, "esma_firds_debt", lambda: d.firds("esma", "D", _cache(params)), d.BOND_LISTING_COLUMNS)


def dir_gleif_isin_lei(params: dict, *listings: pd.DataFrame) -> pd.DataFrame:
    """GLEIF ISIN -> LEI pairs for every ISIN in the fund and bond listings."""
    isins = set().union(*(set(l["isin"].dropna()) for l in listings if len(l)))
    return _run(params, "gleif", lambda: d.gleif_isin_lei(isins, _cache(params)), ["isin", "lei"])


def dir_gleif_relationships(params: dict) -> pd.DataFrame:
    """Active fund-manager and umbrella-fund links of every fund LEI."""
    return _run(params, "gleif", lambda: d.gleif_relationships(_cache(params)), ["lei", "relationship", "parent_lei"])


def dir_gleif_entities(
    params: dict,
    relationships: pd.DataFrame,
    isin_lei: pd.DataFrame,
    *fund_listings: pd.DataFrame,
) -> pd.DataFrame:
    """Names and jurisdictions of managers, umbrellas and unbranded funds (GLEIF API)."""
    funds = pd.concat([l for l in fund_listings if len(l)], ignore_index=True) if any(len(l) for l in fund_listings) else pd.DataFrame(columns=d.FUND_LISTING_COLUMNS)
    lei = funds.dropna(subset=["fund_lei"]).drop_duplicates("isin").set_index("isin")["fund_lei"]
    if len(isin_lei):
        lei = lei.combine_first(isin_lei.drop_duplicates("isin").set_index("isin")["lei"])
    fund_leis = set(lei.dropna())
    unbranded = set(funds.loc[funds["name"].map(lambda n: d.issuer_key(n) is None), "isin"])
    wanted = {lei[i] for i in unbranded if i in lei.index and isinstance(lei[i], str)}
    if len(relationships):
        rel = relationships[relationships["lei"].isin(fund_leis)]
        wanted |= set(rel["parent_lei"])
        # The umbrella's own manager, for sub-funds that only link to their umbrella.
        umb = set(rel.loc[rel["relationship"] == "IS_SUBFUND_OF", "parent_lei"])
        wanted |= set(relationships.loc[relationships["lei"].isin(umb), "parent_lei"])
    parents = set(relationships["parent_lei"]) if len(relationships) else set()
    return _run(
        params,
        "gleif",
        lambda: d.gleif_entities(wanted, _cache(params), max_leis=params.get("gleif_api_max_leis"), first=parents),
        ["lei", "legal_name", "jurisdiction", "category"],
    )


def fund_directory(
    params: dict,
    isin_lei: pd.DataFrame,
    relationships: pd.DataFrame,
    entities: pd.DataFrame,
    *fund_listings: pd.DataFrame,
) -> pd.DataFrame:
    """One row per fund ISIN: issuer, adapter, domicile, venues, and the source of each field."""
    from ...sources.issuers import discover

    adapters = set(discover())
    adapters |= set(params.get("extra_adapters") or [])
    return d.build_fund_directory(
        list(fund_listings), isin_lei, relationships, entities, adapters,
        etf_only_adapters=params.get("etf_only_adapters") or (),
    )


def bond_directory(isin_lei: pd.DataFrame, *bond_listings: pd.DataFrame) -> pd.DataFrame:
    """One row per bond ISIN: issuer, currency, maturity, coupon, and their sources."""
    return d.build_bond_directory(list(bond_listings), isin_lei)


def directory_sources(params: dict, *listings: pd.DataFrame) -> pd.DataFrame:
    """One row per directory source: status, rows, newest as-of date, redistribution class and error."""
    rows = []
    for key, source in d.SOURCES.items():
        frames = [l for l in listings if len(l) and "source" in l and (l["source"] == key).any()]
        got = pd.concat([l[l["source"] == key] for l in frames]) if frames else pd.DataFrame(columns=["as_of"])
        st = _STATUS.get(key, {"status": "not run"})
        rows.append({
            "source": key, "title": source.title, "status": st["status"], "rows": len(got),
            "as_of": got["as_of"].dropna().max() if len(got) else None,
            "redistribution": source.redistribution, "error": st.get("error"),
        })
    out = pd.DataFrame(rows)
    missing = out[out["source"].isin(params.get("required_sources") or []) & (out["rows"] == 0)]
    if len(missing):
        raise RuntimeError(f"Required directory sources gave no rows: {', '.join(missing['source'])}")
    return out


def directory_coverage(fund_directory: pd.DataFrame) -> pd.DataFrame:
    """Funds known per issuer and domicile, and how many have a holdings adapter."""
    return d.coverage(fund_directory)
