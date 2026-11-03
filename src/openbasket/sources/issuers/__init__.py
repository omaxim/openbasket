"""Plug-in registry for issuer holdings sources.

Each issuer lives in its own module in this package and registers one
:class:`IssuerAdapter`. An adapter knows two things: which funds the issuer
publishes (``products``) and how to read one fund's full holdings
(``holdings``). Everything else (matching an ISIN to a fund, look-through,
publishing) is shared.

Adding a source means adding one module here, one test file and one docs page
under ``docs/source/sources/``. Nothing else needs editing:
:func:`safe_discover` imports every module in the package, and a module that
fails to import is logged and skipped, so one broken adapter cannot stop a run.

Contracts
---------

``products()`` returns a DataFrame with :data:`PRODUCT_COLUMNS`, one row per
share-class ISIN. ``portfolio_id`` identifies the portfolio, so share classes
of one portfolio share it.

``holdings(product)`` takes one row of that frame (as a dict) and returns an
:class:`IssuerHoldings` whose frame has :data:`HOLDING_COLUMNS`. ``weight_pct``
is percent of the fund's net assets as the issuer reports it: never rescale.
Identifiers that fail their check digit must be ``None``.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import sys
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from ...identifiers import is_valid_cusip, is_valid_isin

logger = logging.getLogger(__name__)

PRODUCT_COLUMNS = [
    "issuer",            # adapter name, e.g. "xtrackers"
    "site",              # regional site or culture the row came from, e.g. "ch"
    "portfolio_id",      # issuer's id for the portfolio (shared by its share classes)
    "isin",              # share-class ISIN (required)
    "ticker",            # primary listing ticker, if any
    "fund_name",
    "domicile",          # ISO country code or name as the issuer gives it
    "currency",          # share-class currency
    "asset_class",       # issuer's label: Equity, Fixed Income, ...
    "replication",       # "physical", "swap", "sampled" or None if unknown
    "product_page_url",  # human page for this share class (shown as the source link)
    "holdings_url",      # machine URL the holdings come from, if fixed
]

HOLDING_COLUMNS = [
    "isin",          # constituent ISIN, None if absent or invalid
    "cusip",         # 9-character CUSIP if given and valid, else None
    "sedol",         # 7-character SEDOL if given, else None
    "ticker",
    "name",
    "weight_pct",    # percent of net assets, as reported
    "asset_class",   # normalised: equity, debt, cash, derivative (...), fund, other
    "currency",
    "country",
]

#: Columns a holdings frame may add; the pipeline uses them when present and
#: nothing requires them. ``sector`` is the issuer's own sector label.
OPTIONAL_HOLDING_COLUMNS = [
    "sector",
    "issuer_name",
]


@dataclass
class IssuerHoldings:
    as_of: str | None          # ISO date of the positions, from the file itself
    holdings: pd.DataFrame     # HOLDING_COLUMNS
    url: str                   # where they were fetched from (cited as the source)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IssuerAdapter:
    name: str                  # short id used in data and config, e.g. "xtrackers"
    title: str                 # human name, e.g. "Xtrackers (DWS)"
    homepage: str              # cited as the source home
    credit: str                # credit line shown next to every row from this source
    licence: str               # what we know about the terms, honestly
    regions: tuple[str, ...]   # where its funds are domiciled or sold, for docs
    products: Callable[[], pd.DataFrame]
    holdings: Callable[[dict, "FetchContext"], IssuerHoldings]
    frequency: str = "daily"   # how often the issuer updates holdings


@dataclass
class FetchContext:
    """Per-run settings passed to ``holdings``."""

    cache_dir: str | None = None


_REGISTRY: dict[str, IssuerAdapter] = {}

#: Adapter modules that failed to import, by module name, with the error.
_FAILED: dict[str, str] = {}


def register(adapter: IssuerAdapter) -> IssuerAdapter:
    if adapter.name in _REGISTRY:
        raise ValueError(f"Issuer adapter {adapter.name!r} registered twice")
    _REGISTRY[adapter.name] = adapter
    return adapter


def discover() -> dict[str, IssuerAdapter]:
    """Import every adapter module in this package and return the registry.

    Raises on the first module that fails to import; pipelines use
    :func:`safe_discover` instead.
    """
    for mod in pkgutil.iter_modules(__path__):
        if not mod.name.startswith("_"):
            importlib.import_module(f"{__name__}.{mod.name}")
    return dict(_REGISTRY)


def safe_discover() -> dict[str, IssuerAdapter]:
    """Like :func:`discover`, but each module is imported on its own.

    A module that fails to import (a missing optional dependency, a syntax
    error, a name registered twice) is logged, recorded in
    :func:`failed_modules` and skipped; the other adapters are still returned.
    A module that failed is not retried in the same process.
    """
    for mod in pkgutil.iter_modules(__path__):
        name = f"{__name__}.{mod.name}"
        if mod.name.startswith("_") or name in sys.modules or mod.name in _FAILED:
            continue
        try:
            importlib.import_module(name)
        except Exception as exc:  # one broken adapter must not stop the build
            _FAILED[mod.name] = f"{type(exc).__name__}: {exc}"[:500]
            sys.modules.pop(name, None)
            logger.warning("Issuer adapter module %s failed to import and is skipped: %s", mod.name, _FAILED[mod.name])
    return dict(_REGISTRY)


def failed_modules() -> dict[str, str]:
    """Adapter modules :func:`safe_discover` skipped, with their errors."""
    return dict(_FAILED)


def select(
    adapters: dict[str, IssuerAdapter], enabled: list[str] | None = None, disabled: list[str] | None = None
) -> dict[str, IssuerAdapter]:
    """The adapters to use: all of ``adapters`` (or only those named in ``enabled``) minus ``disabled``.

    A name in ``enabled`` that no module registers is logged and skipped.
    """
    names = list(adapters) if enabled is None else list(enabled)
    unknown = [n for n in names if n not in adapters]
    if unknown:
        logger.warning("issuers.enabled names unknown adapters %s; known: %s", unknown, sorted(adapters))
    skip = set(disabled or ())
    return {n: adapters[n] for n in names if n in adapters and n not in skip}


def validate_products(df: pd.DataFrame) -> list[str]:
    """Problems with a products frame; empty when it meets the contract."""
    problems = [f"missing column {c}" for c in PRODUCT_COLUMNS if c not in df.columns]
    if "isin" in df:
        bad = df["isin"].map(lambda v: not is_valid_isin(v))
        if bad.any():
            problems.append(f"{int(bad.sum())} rows with an invalid fund ISIN")
        if df["isin"].duplicated().any():
            problems.append("duplicate fund ISINs")
    return problems


def validate_holdings(df: pd.DataFrame) -> list[str]:
    """Problems with a holdings frame; empty when it meets the contract."""
    problems = [f"missing column {c}" for c in HOLDING_COLUMNS if c not in df.columns]
    if "isin" in df:
        bad = df["isin"].dropna().map(lambda v: not is_valid_isin(v))
        if bad.any():
            problems.append(f"{int(bad.sum())} constituent ISINs fail their check digit")
    if "cusip" in df:
        bad = df["cusip"].dropna().map(lambda v: not is_valid_cusip(v))
        if bad.any():
            problems.append(f"{int(bad.sum())} CUSIPs fail their check digit")
    if "weight_pct" in df and not pd.api.types.is_numeric_dtype(df["weight_pct"]):
        problems.append("weight_pct is not numeric")
    return problems
