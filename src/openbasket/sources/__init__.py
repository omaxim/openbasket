"""Clients for the free data sources the pipeline combines.

Every source has a redistribution class. ``"public"``: its licence allows
the holdings to be republished (SEC N-PORT, TickerInside under CC BY 4.0,
CVM, CNMV ...). ``"local_only"``: its terms restrict republication (issuer
websites), so openbasket reads it only for the person running it and the
public build never fetches or publishes it. :func:`redistribution` gives the
class for any source id that appears in the data.

``publish.mode`` says which build is running: ``"full"`` (the default, a
run on someone's own machine, which reads every enabled source) or
``"public"`` (the GitHub Action, which never fetches holdings from a
``local_only`` source and never writes its rows); :func:`publish_mode` reads
it.

A source module declares its class in a module-level ``REDISTRIBUTION``
constant; an issuer adapter in :attr:`IssuerAdapter.redistribution
<openbasket.sources.issuers.IssuerAdapter.redistribution>`.
"""

from __future__ import annotations

import importlib
import re

PUBLIC = "public"
LOCAL_ONLY = "local_only"

#: Build modes (``publish.mode``): the public build, or a full run for one person.
PUBLIC_MODE = "public"
FULL_MODE = "full"
MODES = (PUBLIC_MODE, FULL_MODE)

#: Source ids whose module has another name, or that are built from another source.
_MODULE_FOR = {
    "tickerinside": "tickerinside_source",
    # The security master and the fund master are built from N-PORT and SEC's ticker list.
    "security_master": "nport",
    "fund_master": "nport",
    "sec_tickers": "sec",
    # Fund identity (name, ISIN, issuer) from open registries, or from an
    # issuer's public fund list; never holdings. See openbasket.sources.fund_identity.
    "issuer_list": "fund_identity",
    "fund_directory": "fund_identity",
    "fund_registry": "fund_identity",
    "firds": "fund_identity",
    "gleif": "fund_identity",
    "esma_firds": "firds",
}

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def redistribution(source_id: str | None) -> str:
    """``"public"`` or ``"local_only"`` for a source id such as ``nport`` or ``ishares``.

    A registered issuer adapter answers with its own ``redistribution``;
    otherwise the module ``openbasket.sources.<id>`` answers with its
    ``REDISTRIBUTION`` constant. Anything unknown is ``"local_only"``, so a
    source nobody has classified is never published by mistake.
    """
    sid = (source_id or "").strip().lower()
    if not _ID_RE.match(sid):
        return LOCAL_ONLY
    from . import issuers

    adapter = issuers.safe_discover().get(sid)
    if adapter is not None:
        return adapter.redistribution if adapter.redistribution in (PUBLIC, LOCAL_ONLY) else LOCAL_ONLY
    from .directory import SOURCES as DIRECTORY_SOURCES

    if sid in DIRECTORY_SOURCES:
        value = DIRECTORY_SOURCES[sid].redistribution
        return value if value in (PUBLIC, LOCAL_ONLY) else LOCAL_ONLY
    module = _MODULE_FOR.get(sid, sid)
    if module == "issuers":
        return LOCAL_ONLY
    try:
        mod = importlib.import_module(f"{__name__}.{module}")
    except Exception:  # no such module, or one that does not import
        return LOCAL_ONLY
    value = getattr(mod, "REDISTRIBUTION", LOCAL_ONLY)
    return value if value in (PUBLIC, LOCAL_ONLY) else LOCAL_ONLY


def is_public(source_id: str | None) -> bool:
    """True when ``source_id``'s licence allows its rows to be republished."""
    return redistribution(source_id) == PUBLIC


def publish_mode(value: object = None) -> str:
    """``"public"`` or ``"full"`` from ``publish.mode``, or from the whole ``publish`` dict.

    Missing means ``"full"``; anything else that is not a mode is an error,
    so a typo never turns the public build into a full one.
    """
    if isinstance(value, dict):
        value = value.get("mode")
    if value is None:
        return FULL_MODE
    mode = str(value).strip().lower()
    if mode not in MODES:
        raise ValueError(f"publish.mode must be one of {MODES}, not {value!r}")
    return mode
