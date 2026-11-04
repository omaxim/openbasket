"""Fill the issuer cache before the universe run; see :mod:`openbasket.prefetch`."""

from __future__ import annotations

import pandas as pd

from ... import throttle
from ...prefetch import prefetch
from ..lookthrough.nodes import issuer_adapters


def prefetch_issuer_holdings(
    issuer_products: pd.DataFrame,
    params: dict,
    universe_params: dict,
    issuers_params: dict | None,
    lookthrough_params: dict,
    publish_params: dict | None,
) -> dict:
    """Read the issuer files of the funds the next universe run selects; returns the prefetch report.

    The throttle gets the intervals the universe run uses (``universe.http``),
    and files go to ``lookthrough.issuer_cache_dir``, where that run looks.
    """
    http = (universe_params or {}).get("http") or {}
    throttle.configure(http.get("default_interval_s"), http.get("per_host_interval_s"))
    return prefetch(
        issuer_products,
        issuer_adapters(issuers_params),
        cache_dir=lookthrough_params.get("issuer_cache_dir"),
        params=params,
        universe_params=universe_params,
        publish_params=publish_params,
    )
