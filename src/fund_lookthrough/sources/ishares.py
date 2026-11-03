"""iShares (BlackRock) holdings from the issuer's own website.

The 2026 site serves two JSON endpoints that this module uses:

Product screener
    ``/varnish-api/blk-product-screener-server/api/v1/product-screener/product-data``
    lists every fund on a regional site, keyed by portfolio ID, with its
    ``isin``, ticker, asset class and ``totalNetAssets``. This is how an ISIN
    is matched to a fund.
Product data, holdings component
    ``/varnish-api/<site>-product-data/product-data/api/v2/get-product-data``
    with ``component=holdings`` returns the full holdings table as columns:
    ``isin``, ``ticker``, ``issueName``, ``holdingPercent`` (to five decimals),
    ``assetClass``, ``marketValue`` and more, plus ``asOfDate``.

The older ``.ajax`` CSV endpoints now return the HTML product page. The
``latest-holdings.csv`` link on each page still works but carries no ISIN and
rounds weights to two decimals, so it is not used.
"""

from __future__ import annotations

import ast
import json
import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

HOST = "https://www.ishares.com"
SCREENER_URL = HOST + "/varnish-api/blk-product-screener-server/api/v1/product-screener/product-data"
PRODUCT_DATA_URL = "https://www.blackrock.com/varnish-api/{data_site}/product-data/api/v2/get-product-data"
USER_AGENT = "Mozilla/5.0 (compatible; fund-lookthrough)"

#: Regional sites. ``data_site`` is the product-data service behind the site.
SITES = {
    "uk": {
        "country": "gb",
        "language": "en",
        "siteName": "ishares-uk",
        "targetSite": "ishares-uk",
        "locale": "en_GB",
        "data_site": "uk-retail01-product-data",
    },
}

#: iShares asset-class labels mapped to the labels used elsewhere in the output.
ASSET_CLASS = {
    "Equity": "equity",
    "Fixed Income": "debt",
    "Cash": "cash",
    "Money Market": "money market",
    "Futures": "derivative (futures)",
    "FX": "derivative (foreign exchange)",
    "Cash Collateral and Margins": "cash",
}


@dataclass
class IssuerHoldings:
    as_of: str | None
    holdings: pd.DataFrame
    url: str


def _get(url: str, params: dict, timeout: int = 60) -> requests.Response:
    for attempt in range(4):
        r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        if r.status_code in (429, 502, 503, 504):
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


def products(site: str = "uk") -> pd.DataFrame:
    """Every fund on a regional iShares site, one row per share class ISIN."""
    cfg = SITES[site]
    params = {k: cfg[k] for k in ("country", "language", "siteName")} | {"userType": "individual"}
    data = _get(SCREENER_URL, params).json()
    rows = []
    for pid, p in data.items():
        tna = p.get("totalNetAssets")
        rows.append(
            {
                "site": site,
                "portfolio_id": str(p.get("portfolioId") or pid),
                "isin": (p.get("isin") or "").strip().upper() or None,
                "ticker": None if p.get("localExchangeTicker") in (None, "-") else p["localExchangeTicker"],
                "fund_name": p.get("fundName"),
                "asset_class": p.get("aladdinAssetClass"),
                "sub_asset_class": p.get("aladdinSubAssetClass"),
                "domicile": p.get("domicile"),
                "currency": p.get("seriesBaseCurrencyCode"),
                "product_page_url": HOST + p["productPageUrl"] if p.get("productPageUrl") else None,
                "total_net_assets": pd.to_numeric(tna.get("r") if isinstance(tna, dict) else tna, errors="coerce"),
            }
        )
    return pd.DataFrame(rows).dropna(subset=["isin"]).drop_duplicates("isin").reset_index(drop=True)


def _column(dp: dict) -> object:
    value = dp.get("value")
    if isinstance(value, str) and value.startswith("["):
        # Values are Python-style list literals ('...' quotes, None).
        return ast.literal_eval(value)
    return value


def parse_holdings(payload: dict) -> tuple[str | None, pd.DataFrame]:
    """Turn a ``component=holdings`` response into ``(as_of, DataFrame)``."""
    container = payload["componentsByNameMap"]["holdings"]["containersByNameMap"]["all"]
    cols = {k: _column(v) for k, v in container["dataPointsByNameMap"].items()}
    as_of = cols.pop("asOfDate", None)
    cols.pop("dateList", None)
    lists = {k: v for k, v in cols.items() if isinstance(v, list)}
    lengths = {len(v) for v in lists.values()}
    if len(lengths) > 1:
        raise ValueError(f"iShares holdings columns differ in length: { {k: len(v) for k, v in lists.items()} }")
    df = pd.DataFrame(lists)
    if "isin" in df:
        df["isin"] = df["isin"].map(lambda x: x.strip().upper() if isinstance(x, str) and x.strip() not in ("", "-") else None)
    if as_of:
        s = str(as_of)
        as_of = f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s
    return as_of, df


def holdings(portfolio_id: str, site: str = "uk", cache_dir: str | Path | None = None) -> IssuerHoldings:
    """Full holdings of one fund. Responses are cached per fund per day."""
    cfg = SITES[site]
    url = PRODUCT_DATA_URL.format(data_site=cfg["data_site"])
    params = {
        "appType": "PRODUCT_PAGE",
        "appSubType": "ISHARES",
        "targetSite": cfg["targetSite"],
        "locale": cfg["locale"],
        "userType": "individual",
        "portfolioId": str(portfolio_id),
        "component": "holdings",
    }
    cache = Path(cache_dir) / f"{site}_{portfolio_id}_{date.today().isoformat()}.json" if cache_dir else None
    if cache is not None and cache.exists():
        payload = json.loads(cache.read_text())
    else:
        logger.info("Fetching iShares %s holdings for portfolio %s", site, portfolio_id)
        payload = _get(url, params).json()
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(payload))
        time.sleep(0.5)
    as_of, df = parse_holdings(payload)
    return IssuerHoldings(as_of, df, requests.Request("GET", url, params=params).prepare().url)
