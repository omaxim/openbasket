"""Nodes for the ``swiss`` pipeline.

Publishing policy: every output row carries ``redistribution`` (``public`` or
``local_only``). ``swiss_fund_registry_public`` keeps only the public rows
(GLEIF, FIRDS). On GitHub Actions (``GITHUB_ACTIONS=true``) the pipeline reads
only the openly licensed registry sources and runs no holdings route, so a
public build never fetches or writes restricted data.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd

from ...sources import swiss_registry, swiss_reports
from ...sources.issuers import FetchContext, discover
from ...sources.swiss_banks import BANKS

logger = logging.getLogger(__name__)

#: The Swiss holdings adapters, best route first.
ADAPTERS = ("swiss_standin", "swiss_vontobel", "swiss_migros")
PUBLIC_SOURCES = ("gleif", "firds")


def _on_ci() -> bool:
    return os.environ.get("GITHUB_ACTIONS", "").lower() == "true"


def _enable(names: list[str] | None) -> None:
    if names:
        current = {s for s in os.environ.get("OPENBASKET_ENABLE", "").split(",") if s}
        os.environ["OPENBASKET_ENABLE"] = ",".join(sorted(current | set(names)))


def build_registry(params: dict[str, Any]) -> pd.DataFrame:
    sources = params.get("registry_sources", ["gleif", "firds", "six", "finma"])
    if _on_ci():
        sources = [s for s in sources if s in PUBLIC_SOURCES]
    return swiss_registry.build_registry(params.get("cache_dir"), sources)


def public_registry(registry: pd.DataFrame) -> pd.DataFrame:
    return registry[registry["redistribution"] == "public"].reset_index(drop=True)


def _modules() -> dict[str, Any]:
    import importlib

    return {name: importlib.import_module(f"openbasket.sources.issuers.{name}") for name in ADAPTERS}


def sample_holdings(params: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Holdings of the sample ISINs through the first adapter that lists each one."""
    columns = ["fund_isin", "adapter", "bank", "as_of", "source_url", "credit", "redistribution", "note",
               "isin", "name", "weight_pct", "asset_class", "currency", "country", "isin_source"]
    if _on_ci():
        logger.info("GitHub Actions: Swiss holdings routes are local only and are skipped")
        return pd.DataFrame(columns=columns), pd.DataFrame()
    _enable(params.get("enable"))
    registry = discover()
    mods = _modules()
    cache_dir = params.get("cache_dir")
    products = {}
    for name in ADAPTERS:
        try:
            products[name] = mods[name].products(cache_dir)
        except Exception as exc:
            logger.warning("%s products: %s", name, exc)
            products[name] = pd.DataFrame()
    rows, summary = [], []
    for isin in params.get("sample", []):
        for name in ADAPTERS:
            df = products[name]
            hit = df[df["isin"] == isin] if not df.empty else df
            if hit.empty:
                continue
            product = hit.iloc[0].to_dict()
            adapter = registry[name]
            try:
                got = adapter.holdings(product, FetchContext(cache_dir=None))
            except Exception as exc:
                logger.warning("%s %s: %s", name, isin, exc)
                summary.append({"fund_isin": isin, "adapter": name, "error": str(exc)})
                break
            h = got.holdings
            bank = product.get("bank") or {"swiss_vontobel": "vontobel", "swiss_migros": "migros"}.get(name)
            if "isin_source" not in h:
                h = h.assign(isin_source=h["isin"].map(lambda v: "reported" if isinstance(v, str) else None))
            rows.append(h.assign(fund_isin=isin, adapter=name, bank=bank, as_of=got.as_of, source_url=got.url,
                                 credit=adapter.credit, redistribution=mods[name].REDISTRIBUTION, note=got.notes[0]))
            summary.append(
                {
                    "fund_isin": isin, "fund_name": product.get("fund_name"), "bank": bank, "adapter": name,
                    "route": "standin" if name == "swiss_standin" else "report", "as_of": got.as_of, "lines": len(h),
                    "weight_sum_pct": round(float(h["weight_pct"].sum()), 4),
                    "isin_weight_pct": round(float(h.loc[h["isin"].notna(), "weight_pct"].sum()), 4),
                    "isin_lines": int(h["isin"].notna().sum()),
                    "name_matched_lines": int((h["isin_source"] == "name_match").sum()),
                    "source_url": got.url, "note": got.notes[0],
                }
            )
            break
        else:
            summary.append({"fund_isin": isin, "error": "no Swiss adapter lists this ISIN (or it is off)"})
    holdings = pd.concat(rows, ignore_index=True)[columns] if rows else pd.DataFrame(columns=columns)
    return holdings, pd.DataFrame(summary)


def bank_coverage(registry: pd.DataFrame, summary: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """Funds known and with holdings, route, licence and switches per bank."""
    _enable(params.get("enable"))
    mods = _modules()
    routes = []
    for b in BANKS:
        adapters = [n for n in ADAPTERS if (n == "swiss_standin" and b.route == "standin") or n == f"swiss_{b.key}"]
        products = 0
        for n in adapters:
            try:
                df = mods[n].products(params.get("cache_dir"))
                products += int((df["bank"] == b.key).sum()) if "bank" in df else len(df)
            except Exception as exc:
                logger.warning("%s products: %s", n, exc)
        sampled = summary[summary.get("bank", pd.Series(dtype=object)) == b.key] if not summary.empty and "bank" in summary else pd.DataFrame()
        routes.append(
            {
                "bank": b.key,
                "adapters": ",".join(adapters),
                "share_classes_with_holdings_route": products,
                "redistribution": ",".join(sorted({mods[n].REDISTRIBUTION for n in adapters})) or None,
                "enabled_by_default": ",".join(str(mods[n].ENABLED_BY_DEFAULT) for n in adapters) or None,
                "enabled_now": ",".join(str(swiss_reports.enabled(n, mods[n].ENABLED_BY_DEFAULT)) for n in adapters) or None,
                "sample_runs": len(sampled),
                "contact": b.contact,
                "legal_url": b.legal_url,
            }
        )
    return swiss_registry.coverage(registry, pd.DataFrame(routes))
