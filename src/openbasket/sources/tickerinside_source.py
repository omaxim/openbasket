"""TickerInside holdings.

TickerInside publishes the holdings of US ETFs under CC BY 4.0; every row it
supplies carries its citation in ``source_ref``. Equity funds read from the
issuer's daily file (``source_layer == "issuer"``) are keyed by ticker, not
ISIN. Other layers are rebuilt from N-PORT, which this package reads
directly, so only the issuer layer is used here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..throttle import THROTTLE

logger = logging.getLogger(__name__)


@dataclass
class TickerInsideFund:
    ticker: str
    name: str | None
    layer: str | None
    holdings_kind: str | None
    as_of: str | None
    holdings: dict[str, float]
    cite_as: str | None
    page_url: str | None = None


SITE = "https://tickerinside.com"


def issuer_tickers() -> set[str] | None:
    """Tickers TickerInside reads from the issuer's own file, or ``None`` if the list cannot be read.

    One read of ``funds.json`` saves a request per ticker it does not cover,
    which for a universe run is most of SEC's fund tickers.
    """
    import tickerinside as ti

    THROTTLE.wait(SITE)
    try:
        return {r["ticker"].upper() for r in ti.funds(layer="issuer").data}
    except ti.TickerInsideError as exc:
        logger.warning("TickerInside fund list: %s", exc)
        return None


def fetch_fund(ticker: str) -> TickerInsideFund | None:
    """Fetch one fund, or ``None`` when TickerInside does not cover it."""
    import tickerinside as ti

    THROTTLE.wait(SITE)
    try:
        d = ti.fund(ticker).data
    except ti.NotCovered:
        return None
    except ti.TickerInsideError as exc:
        logger.warning("TickerInside %s: %s", ticker, exc)
        return None
    holdings = d.get("holdings") or {}
    return TickerInsideFund(
        ticker=d.get("ticker", ticker),
        name=d.get("name"),
        layer=d.get("source_layer"),
        holdings_kind=d.get("holdings_kind"),
        as_of=d.get("holdings_as_of") or d.get("as_of"),
        holdings={str(k): float(v) for k, v in holdings.items() if v is not None},
        cite_as=d.get("cite_as"),
        page_url=d.get("page_url"),
    )
