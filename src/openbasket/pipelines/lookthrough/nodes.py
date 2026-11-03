"""Look through the requested ISINs and shape the output."""

from __future__ import annotations

import logging
import re
import time

import duckdb
import pandas as pd

from ...lookthrough import look_through
from ...providers import DataProviders
from ...sources import issuers as issuer_registry
from ...sources import publish_mode
from ...sources.openfigi import OpenFIGI, best_listing

logger = logging.getLogger(__name__)

OUTPUT_COLUMNS = [
    "input_isin", "constituent_isin", "name", "weight_pct", "asset_class", "source", "as_of",
    "issuer_name", "issuer_lei", "title", "ticker", "cusip", "isin_source",
    "figi_name", "figi_ticker", "security_type", "market_sector", "composite_figi",
    "country", "sector", "depth", "n_paths", "via_fund", "source_ref", "note",
]


def parse_isins(isin: str | list[str]) -> list[str]:
    """Accept one ISIN, a list, or a string separated by ``;``, ``,`` or spaces.

    Kedro's ``--params`` splits on commas, so on the command line use ``;``.
    """
    items = isin if isinstance(isin, (list, tuple)) else re.split(r"[;,\s]+", str(isin))
    return list(dict.fromkeys(i.strip().upper() for i in items if i and i.strip()))


def run_lookthrough(
    isins: list[str],
    nport_holdings: duckdb.DuckDBPyRelation,
    security_master: duckdb.DuckDBPyRelation,
    fund_master: pd.DataFrame,
    ticker_isin_map: pd.DataFrame,
    issuer_products: pd.DataFrame,
    cusip_isin_map: duckdb.DuckDBPyRelation,
    sedol_isin_map: pd.DataFrame,
    params: dict,
    issuers_params: dict | None = None,
    publish_params: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Expand each ISIN. Returns position-level constituents, unidentified lines and a summary.

    An ISIN whose look-through raises gets a summary row with ``status``
    ``error`` and the exception in ``error``; the others carry on. In the
    public build (``publish.mode: public``) a ``local_only`` issuer's
    holdings are not read, and a fund that only such an issuer covers has
    ``status`` ``local_only``.
    """
    figi = OpenFIGI(params["openfigi_cache"]) if params.get("use_openfigi", True) else None
    providers = make_providers(
        nport_holdings, security_master, fund_master, ticker_isin_map, issuer_products, cusip_isin_map,
        sedol_isin_map, params, figi, issuers_params=issuers_params, publish_params=publish_params,
    )
    leaves, unresolved, summaries = [], [], []
    for isin in isins:
        t0 = time.perf_counter()
        try:
            res = look_through(isin, providers, max_depth=params["max_depth"], min_resolved_pct=params["min_resolved_pct"],
                               look_through_derivatives=params.get("look_through_derivatives", True))
        except Exception as exc:  # one bad input must not lose the batch
            logger.warning("%s failed: %s: %s", isin, type(exc).__name__, exc)
            summaries.append({"input_isin": isin, "matched": None, "status": "error",
                              "error": f"{type(exc).__name__}: {exc}"[:500], "seconds": round(time.perf_counter() - t0, 3)})
            continue
        leaves += res.leaves
        unresolved += res.unresolved
        s = res.summary
        s["error"] = None
        s["seconds"] = round(time.perf_counter() - t0, 3)
        summaries.append(s)
        logger.info(
            "%s -> %s via %s: %.2f%% resolved, %.2f%% unresolved (%s, %s)",
            isin, s["fund_name"], s["matched_via"], s["resolved_pct"], s["unresolved_pct"], s["source"], s["status"],
        )
    if figi is not None and figi.failures:
        logger.warning("OpenFIGI: %s", figi.stats())
    return pd.DataFrame(leaves), pd.DataFrame(unresolved), pd.DataFrame(summaries)


def issuer_adapters(issuers_params: dict | None) -> dict:
    """The registered issuer adapters to use, after ``issuers.enabled`` and ``issuers.disabled``."""
    p = issuers_params or {}
    return issuer_registry.select(issuer_registry.safe_discover(), p.get("enabled"), p.get("disabled"))


def make_providers(
    nport_holdings: duckdb.DuckDBPyRelation,
    security_master: duckdb.DuckDBPyRelation,
    fund_master: pd.DataFrame,
    ticker_isin_map: pd.DataFrame,
    issuer_products: pd.DataFrame,
    cusip_isin_map: duckdb.DuckDBPyRelation,
    sedol_isin_map: pd.DataFrame,
    params: dict,
    figi: OpenFIGI | None,
    issuers_params: dict | None = None,
    publish_params: dict | None = None,
    **kwargs,
) -> DataProviders:
    """The providers both look-through pipelines use; ``kwargs`` go to :class:`DataProviders`.

    ``publish_params`` gives ``mode`` (``publish.mode``) and ``run_locally_url``.
    """
    pp = publish_params or {}
    return DataProviders(
        nport_holdings, security_master, fund_master, ticker_isin_map, figi,
        source_order=params["source_order"], proxies=params.get("proxies"),
        issuer_products=issuer_products, issuer_cache_dir=params.get("issuer_cache_dir"),
        cusip_isin={c: (i, src) for c, i, src in cusip_isin_map.select("cusip, isin, isin_source").fetchall()},
        sedol_isin=dict(zip(sedol_isin_map["sedol"], sedol_isin_map["isin"])),
        adapters=issuer_adapters(issuers_params),
        max_issuer_leis=int(params.get("max_issuer_leis_per_isin", 3)),
        mode=publish_mode(pp), run_locally_url=pp.get("run_locally_url"),
        **kwargs,
    )


def aggregate(constituents: pd.DataFrame, by: tuple[str, ...] = ("input_isin", "constituent_isin")) -> pd.DataFrame:
    """One row per value of ``by``: weights summed over every route to it.

    The other columns come from the heaviest route; ``depth`` is the
    shallowest and ``n_paths`` counts the routes.
    """
    c = constituents.sort_values("weight_pct", ascending=False, key=abs)
    groups = c.groupby(list(by), sort=False, dropna=False)
    first = groups.first()
    first["weight_pct"] = groups["weight_pct"].sum()
    first["n_paths"] = groups.size()
    first["depth"] = groups["depth"].min()
    return first.reset_index()


def build_output(constituents: pd.DataFrame, summary: pd.DataFrame, params: dict) -> pd.DataFrame:
    """One row per constituent ISIN, plus one unresolved-weight row per input.

    The same ISIN reached through several sub-funds is summed. Names and
    security types from OpenFIGI are added when enabled.
    """
    if constituents.empty:
        agg = pd.DataFrame(columns=OUTPUT_COLUMNS)
    else:
        agg = aggregate(constituents)
        if params.get("use_openfigi", True) and params.get("enrich_openfigi", True):
            try:
                agg = _enrich(agg, params["openfigi_cache"])
            except Exception as exc:  # names and types are a bonus; the output stands without them
                logger.warning("OpenFIGI enrichment skipped: %s: %s", type(exc).__name__, exc)
    if "status" in summary:
        summary = summary[summary["status"] != "error"].copy()
    for col in ("input_isin", "unresolved_pct", "source", "as_of", "note"):
        if col not in summary:
            summary[col] = None
    unresolved_rows = pd.DataFrame(
        {
            "input_isin": summary["input_isin"],
            "constituent_isin": None,
            "name": "Unresolved weight",
            "weight_pct": summary["unresolved_pct"],
            "asset_class": "unresolved",
            "source": summary["source"],
            "as_of": summary["as_of"],
            "note": summary["note"],
        }
    )
    out = pd.concat([agg, unresolved_rows], ignore_index=True)
    for col in OUTPUT_COLUMNS:
        if col not in out:
            out[col] = None
    out = out[OUTPUT_COLUMNS]
    out["_unres"] = out["constituent_isin"].isna()
    out = out.sort_values(["input_isin", "_unres", "weight_pct"], ascending=[True, True, False]).drop(columns="_unres")
    return out.reset_index(drop=True)


def _enrich(df: pd.DataFrame, cache_path: str) -> pd.DataFrame:
    figi = OpenFIGI(cache_path)
    found = figi.map("ID_ISIN", df["constituent_isin"].dropna().unique().tolist())
    picks = {isin: best_listing(rows) or {} for isin, rows in found.items()}
    df = df.copy()
    df["figi_name"] = df["constituent_isin"].map(lambda i: picks.get(i, {}).get("name"))
    df["figi_ticker"] = df["constituent_isin"].map(lambda i: picks.get(i, {}).get("ticker"))
    df["security_type"] = df["constituent_isin"].map(lambda i: picks.get(i, {}).get("securityType"))
    df["market_sector"] = df["constituent_isin"].map(lambda i: picks.get(i, {}).get("marketSector"))
    df["composite_figi"] = df["constituent_isin"].map(lambda i: picks.get(i, {}).get("compositeFIGI"))
    df["name"] = df["name"].fillna(df["figi_name"])
    return df
