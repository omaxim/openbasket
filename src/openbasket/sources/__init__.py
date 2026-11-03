"""Clients for the free data sources the pipeline combines.

Every source has a redistribution class. ``"public"``: its licence allows
the holdings to be republished (SEC N-PORT, TickerInside under CC BY 4.0,
CVM, CNMV ...). ``"local_only"``: its terms restrict republication (issuer
websites), so openbasket reads it only for the person running it and the
public build never fetches or publishes it. :func:`redistribution` gives the
class for any source id that appears in the data.

A source module declares its class in a module-level ``REDISTRIBUTION``
constant; an issuer adapter in :attr:`IssuerAdapter.redistribution
<openbasket.sources.issuers.IssuerAdapter.redistribution>`.
"""

from __future__ import annotations

import importlib
import re

PUBLIC = "public"
LOCAL_ONLY = "local_only"

#: Source ids whose module has another name, or that are built from another source.
_MODULE_FOR = {
    "tickerinside": "tickerinside_source",
    # The security master and the fund master are built from N-PORT and SEC's ticker list.
    "security_master": "nport",
    "fund_master": "nport",
    "sec_tickers": "sec",
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
    module = _MODULE_FOR.get(sid, sid)
    if module == "issuers":
        return LOCAL_ONLY
    try:
        mod = importlib.import_module(f"{__name__}.{module}")
    except Exception:  # no such module, or one that does not import
        return LOCAL_ONLY
    value = getattr(mod, "REDISTRIBUTION", LOCAL_ONLY)
    return value if value in (PUBLIC, LOCAL_ONLY) else LOCAL_ONLY
