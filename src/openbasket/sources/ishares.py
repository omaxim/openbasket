"""iShares (BlackRock) holdings from the issuer's own website.

The 2026 site serves two JSON endpoints per region that this module uses:

Product screener
    UK: ``/varnish-api/blk-product-screener-server/api/v1/product-screener/product-data``
    returns a dict keyed by portfolio ID. US: ``/us/product-screener/product-screener-v3.jsn``
    (the varnish screener has no US config) returns ``tableData`` as columns
    plus rows. Both carry each fund's ``isin``, ``cusip`` (US), ticker, asset
    class and ``totalNetAssets``. This is how an ISIN is matched to a fund.
Product data, holdings component
    ``/varnish-api/<data_site>/product-data/api/v2/get-product-data``
    with ``component=holdings`` returns the full holdings table as columns:
    ``isin``, ``cusip`` (US), ``ticker``, ``issueName``, the weight,
    ``assetClass`` and more, plus ``asOfDate``. Fixed income and fund-of-fund
    tables give the weight as ``holdingPercent`` to five decimals; US equity
    tables give ``marketWeight`` to two decimals, which is renamed to
    ``holdingPercent`` here.

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

from ..identifiers import cusip_to_isin
from ..throttle import THROTTLE

logger = logging.getLogger(__name__)

#: Redistribution class (see :func:`openbasket.sources.redistribution`):
#: BlackRock's site terms restrict republication: read only for the person running openbasket.
REDISTRIBUTION = "local_only"

HOST = "https://www.ishares.com"
SCREENER_URL = HOST + "/varnish-api/blk-product-screener-server/api/v1/product-screener/product-data"
PRODUCT_DATA_URL = "https://www.blackrock.com/varnish-api/{data_site}/product-data/api/v2/get-product-data"
USER_AGENT = "Mozilla/5.0 (compatible; openbasket)"

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
    "us": {
        "screener_url": HOST + "/us/product-screener/product-screener-v3.jsn",
        "screener_params": {
            "dcrPath": "/templatedata/config/product-screener-v3/data/en/us-ishares/ishares-product-screener-backend-config",
            "siteEntryPassthrough": "true",
        },
        # BlackRock mutual funds share the US list but their holdings carry no
        # ISINs; N-PORT covers them better.
        "product_types": ["ISHARES_FUND_DATA"],
        "targetSite": "us-ishares",
        "locale": "en_US",
        "data_site": "blk-one01-product-data",
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


class NoFullHoldings(ValueError):
    """The fund page has no full holdings table (index mutual funds publish only the ten largest lines)."""

    plain = "iShares publishes only the ten largest holdings for this fund; see the source link"


def _get(url: str, params: dict, timeout: int = 60) -> requests.Response:
    for attempt in range(4):
        THROTTLE.wait(url)
        r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        if r.status_code in (429, 502, 503, 504):
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


def _screener_rows(site: str) -> list[dict]:
    cfg = SITES[site]
    if "screener_url" in cfg:
        table = _get(cfg["screener_url"], cfg["screener_params"]).json()["data"]["tableData"]
        names = [c["name"] for c in table["columns"]]
        rows = [dict(zip(names, r)) for r in table["data"]]
    else:
        params = {k: cfg[k] for k in ("country", "language", "siteName")} | {"userType": "individual"}
        rows = [p | {"portfolioId": p.get("portfolioId") or pid} for pid, p in _get(SCREENER_URL, params).json().items()]
    if "product_types" in cfg:
        rows = [r for r in rows if r.get("productType") in cfg["product_types"]]
    return rows


def _blank(value: object) -> bool:
    return not isinstance(value, str) or value.strip() in ("", "-")


def parse_products(rows: list[dict], site: str) -> pd.DataFrame:
    """One row per share-class ISIN from screener rows. A missing ISIN is
    derived from the CUSIP where that is safe."""
    out = []
    for p in rows:
        tna = p.get("totalNetAssets")
        isin = None if _blank(p.get("isin")) else p["isin"].strip().upper()
        if isin is None and not _blank(p.get("cusip")):
            isin = cusip_to_isin(p["cusip"])
        out.append(
            {
                "site": site,
                "portfolio_id": str(p.get("portfolioId")),
                "isin": isin,
                "ticker": None if _blank(p.get("localExchangeTicker")) else p["localExchangeTicker"],
                "fund_name": p.get("fundName"),
                "asset_class": p.get("aladdinAssetClass"),
                "sub_asset_class": p.get("aladdinSubAssetClass"),
                "domicile": p.get("domicile"),
                "currency": p.get("seriesBaseCurrencyCode"),
                "product_page_url": HOST + p["productPageUrl"] if p.get("productPageUrl") else None,
                "total_net_assets": pd.to_numeric(tna.get("r") if isinstance(tna, dict) else tna, errors="coerce"),
            }
        )
    return pd.DataFrame(out).dropna(subset=["isin"]).drop_duplicates("isin").reset_index(drop=True)


def products(site: str = "uk") -> pd.DataFrame:
    """Every fund on a regional iShares site, one row per share class ISIN."""
    return parse_products(_screener_rows(site), site)


def _column(dp: dict) -> object:
    value = dp.get("value")
    if isinstance(value, str) and value.startswith("["):
        # Values are Python-style list literals ('...' quotes, None).
        return ast.literal_eval(value)
    return value


def parse_holdings(payload: dict) -> tuple[str | None, pd.DataFrame]:
    """Turn a ``component=holdings`` response into ``(as_of, DataFrame)``."""
    containers = payload["componentsByNameMap"]["holdings"]["containersByNameMap"]
    if "all" not in containers:
        # Some funds (index mutual funds among them) publish only their ten largest lines.
        raise NoFullHoldings(f"no full holdings table, only {sorted(containers)}")
    container = containers["all"]
    cols = {k: _column(v) for k, v in container["dataPointsByNameMap"].items()}
    as_of = cols.pop("asOfDate", None)
    cols.pop("dateList", None)
    lists = {k: v for k, v in cols.items() if isinstance(v, list)}
    lengths = {len(v) for v in lists.values()}
    if len(lengths) > 1:
        raise ValueError(f"iShares holdings columns differ in length: { {k: len(v) for k, v in lists.items()} }")
    df = pd.DataFrame(lists)
    if "holdingPercent" not in df and "marketWeight" in df:
        df = df.rename(columns={"marketWeight": "holdingPercent"})
    if "isin" in df:
        isins = [None if _blank(x) else x.strip().upper() for x in df["isin"]]
        sources = ["reported" if x else None for x in isins]
        # Lines with only a CUSIP are left for the providers, which look the
        # CUSIP up in N-PORT and check any derived ISIN against OpenFIGI.
        df["isin"] = pd.Series(isins, index=df.index, dtype=object)
        df["isin_source"] = pd.Series(sources, index=df.index, dtype=object)
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
        as_of, df = parse_holdings(json.loads(cache.read_text()))
    else:
        logger.info("Fetching iShares %s holdings for portfolio %s", site, portfolio_id)
        payload = _get(url, params).json()
        try:
            as_of, df = parse_holdings(payload)
        except NoFullHoldings:
            # A real answer: keep it, so a rerun the same day does not ask again.
            _write_cache(cache, json.dumps(payload))
            raise
        # Cached only once it parsed: an interstitial page or a changed
        # payload is fetched again next time instead of failing all day.
        _write_cache(cache, json.dumps(payload))
    return IssuerHoldings(as_of, df, requests.Request("GET", url, params=params).prepare().url)


def _write_cache(path: Path | None, text: str) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
