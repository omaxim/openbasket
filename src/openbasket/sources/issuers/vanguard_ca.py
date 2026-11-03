"""Vanguard Canada (vanguard.ca): Canadian-domiciled Vanguard ETFs and mutual funds.

Not a registered adapter: the ``vanguard`` adapter lists the Canadian funds
too and reads their direct holdings from Vanguard's GPX service, where a
fund of funds such as VFV shows the Vanguard ETF it holds with that ETF's
ISIN (the look-through expands it). This module is its fallback for a
Canadian fund whose GPX holdings fail or come back empty. Its rows are
credited to Vanguard and are ``local_only`` like the rest.

Fund list
    ``https://www.vanguard.ca/en/product`` embeds the site configuration, whose
    ``portIds`` string lists every product the Canadian site shows (ETFs and
    Series F/M mutual funds). The site's own ``FundFinderFunds`` GraphQL query
    then returns, per ``portId``, the share-class ISIN, the TSX ticker and
    SEDOL of each listing, the asset class and the currency.
Holdings
    ``POST https://www.vanguard.ca/gpx/graphql`` with the site's own
    ``borHoldings`` queries. The product page uses the first non-empty of
    ``dailyHoldings``, ``delayeredHoldings`` (the issuer's look-through of
    funds that hold other Vanguard funds, such as VEQT or VFV) and
    ``holdings`` (the fund's direct month-end positions); this module does the
    same. Each page holds at most 1,500 lines and is followed with
    ``lastItemKey``.

``marketValuePercentage`` is already percent of net assets. Month-end lines
identify a security by ``sedol1`` and ticker only (no ISIN or CUSIP); daily
lines, when published, carry ``isin``. A held Vanguard Canada ETF is given
its ISIN from the fund list through its listing SEDOL.

The GraphQL gateway requires an ``x-consumer-id`` header. Its value ``ca0``
is the public application id the site's own front end sends with every
request (``consumerId`` in ``main.<hash>.js``); it is not a credential.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd

from ... import http
from ...identifiers import is_valid_isin
from . import (
    HOLDING_COLUMNS,
    PRODUCT_COLUMNS,
    FetchContext,
    IssuerHoldings,
)

logger = logging.getLogger(__name__)

NAME = "vanguard_ca"
#: Vanguard Canada's terms license content for personal, non-commercial use only.
REDISTRIBUTION = "local_only"
HOST = "https://www.vanguard.ca"
PRODUCT_LIST_URL = HOST + "/en/product"
GRAPHQL_URL = HOST + "/gpx/graphql"
CONSUMER_ID = "ca0"
PAGE_SIZE = 1500
MAX_PAGES = 100

#: Subset of the site's ``FundFinderFunds`` query (same fields and arguments).
FUND_FINDER_QUERY = """
query FundFinderFunds($portIds: [String!]!) {
  funds(portIds: $portIds) {
    portId
    profile {
      portId
      fundCurrency
      fundFullName
      shareClassName
      assetClassificationLevel1
      identifiers(altIds: ["ISIN", "Ticker", "FundServ Code"]) { altId altIdValue }
      listings {
        portId
        exchange
        stockExchangeMarketIdentifierCode
        identifiers(altIds: ["Ticker - Canada", "Ticker", "SEDOL"]) { altId altIdValue }
      }
      polarisPdtTypeIndicator
      currencyHedgingStrategy
      marketOfDomicile
    }
  }
}
"""

_MONTH_END_ITEMS = """
        items {
          issuerName
          securityLongDescription
          marketValuePercentage
          sedol1
          ticker
          securityType
          finalMaturity
          effectiveDate
          bloombergIsoCountry
          couponRate
        }
        totalHoldings
        lastItemKey"""

#: The site's three holdings queries, keyed by the ``borHoldings`` field they read.
HOLDINGS_QUERIES = {
    "dailyHoldings": """
query FundsDailyHoldingsQuery($portIds: [String!], $lastItemKey: String) {
  borHoldings(portIds: $portIds) {
    dailyHoldings(limit: %d, lastItemKey: $lastItemKey) {
      items {
        effectiveDate
        securityLongDescription
        securityType
        isin
        marketValuePercentage
      }
      totalHoldings
      lastItemKey
    }
  }
}""" % PAGE_SIZE,
    "delayeredHoldings": """
query FundsHoldingsQuery($portIds: [String!], $securityTypes: [String!], $lastItemKey: String) {
  borHoldings(portIds: $portIds) {
    delayeredHoldings(limit: %d, securityTypes: $securityTypes, lastItemKey: $lastItemKey) {%s
    }
  }
}""" % (PAGE_SIZE, _MONTH_END_ITEMS),
    "holdings": """
query FundsHoldingsQuery($portIds: [String!], $securityTypes: [String!], $lastItemKey: String) {
  borHoldings(portIds: $portIds) {
    holdings(limit: %d, securityTypes: $securityTypes, lastItemKey: $lastItemKey) {%s
    }
  }
}""" % (PAGE_SIZE, _MONTH_END_ITEMS),
}

#: Order the product page tries them in.
LAYERS = ("dailyHoldings", "delayeredHoldings", "holdings")

LAYER_NOTES = {
    "dailyHoldings": "Daily holdings published by Vanguard Canada.",
    "delayeredHoldings": (
        "Vanguard Canada's own look-through: positions of the underlying Vanguard "
        "funds, apportioned to this fund (month-end)."
    ),
    "holdings": "Direct month-end positions of the fund.",
}

_ASSET_URI = {"Equity": "equity", "Bond": "fixed-income", "Balanced/Diversified": "asset-allocation"}
_PORT_IDS_RE = re.compile(r'"portIds"\s*:\s*"([A-Z0-9,]+)"')


# --------------------------------------------------------------------------- HTTP


def _graphql(query: str, variables: dict) -> dict:
    r = http.post(
        GRAPHQL_URL,
        data=json.dumps({"query": query, "variables": variables}),
        headers={"Content-Type": "application/json", "Accept": "application/json", "x-consumer-id": CONSUMER_ID},
        timeout=90,
    )
    r.raise_for_status()
    payload = r.json()
    if payload.get("errors"):
        raise RuntimeError(f"Vanguard Canada GraphQL error: {payload['errors'][0].get('message', '')[:200]}")
    return payload["data"]


# ----------------------------------------------------------------------- products


def parse_port_ids(page_html: str) -> list[str]:
    """``portIds`` from the site configuration embedded in the product list page."""
    m = _PORT_IDS_RE.search(page_html)
    if not m:
        raise ValueError("Vanguard Canada product page has no portIds in its site configuration")
    return list(dict.fromkeys(p for p in m.group(1).split(",") if p))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("&", "")).strip("-")


def _ids(entries: list[dict] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in entries or []:
        if e.get("altIdValue") and e.get("altId") not in out:
            out[e["altId"]] = str(e["altIdValue"]).replace(" ", "")
    return out


def _portfolio_key(name: str) -> str:
    """Share classes of one mutual fund differ only in their series suffix."""
    base = re.sub(r"\s*-?\s*Series [A-Z]+\s*$", "", name or "")
    return _slug(base)


def parse_products(payload: dict) -> pd.DataFrame:
    """``FundFinderFunds`` response to :data:`PRODUCT_COLUMNS` rows (plus ``listing_sedols``)."""
    rows = []
    for fund in payload.get("funds") or []:
        p = fund.get("profile") or {}
        ids = _ids(p.get("identifiers"))
        isin = ids.get("ISIN")
        if not is_valid_isin(isin):
            continue
        kind = "mf" if (p.get("polarisPdtTypeIndicator") or "").upper() == "FUND" else "etf"
        ticker, sedols = None, []
        for listing in p.get("listings") or []:
            lid = _ids(listing.get("identifiers"))
            ticker = ticker or lid.get("Ticker - Canada") or lid.get("Ticker")
            if lid.get("SEDOL"):
                sedols.append(lid["SEDOL"].upper())
        ticker = ticker or ids.get("Ticker") or ids.get("FundServ Code")
        asset = p.get("assetClassificationLevel1")
        name = p.get("fundFullName") or ""
        dom = (p.get("marketOfDomicile") or "").upper()
        rows.append(
            {
                "issuer": NAME,
                "site": "ca",
                "portfolio_id": _portfolio_key(name) or p.get("portId"),
                "isin": isin,
                "ticker": ticker,
                "fund_name": name,
                "domicile": "CA" if dom == "CANADA" else (dom or None),
                "currency": p.get("fundCurrency"),
                "asset_class": asset,
                # Vanguard Canada funds hold securities or other Vanguard funds;
                # none of them uses swaps for its index exposure.
                "replication": "physical",
                "product_page_url": f"{HOST}/en/product/{kind}/{_ASSET_URI.get(asset, 'equity')}/{p.get('portId')}/{_slug(name)}",
                "holdings_url": GRAPHQL_URL,
                "port_id": p.get("portId"),
                "product_type": kind,
                "currency_hedging": p.get("currencyHedgingStrategy"),
                "listing_sedols": ",".join(sedols) or None,
            }
        )
    df = pd.DataFrame(rows, columns=PRODUCT_COLUMNS + ["port_id", "product_type", "currency_hedging", "listing_sedols"])
    return df.drop_duplicates("isin").reset_index(drop=True)


def products() -> pd.DataFrame:
    """Every product on the Canadian site, one row per share-class ISIN."""
    port_ids = parse_port_ids(http.get(PRODUCT_LIST_URL, timeout=60).text)
    return parse_products(_graphql(FUND_FINDER_QUERY, {"portIds": port_ids}))


@lru_cache(maxsize=1)
def _own_sedol_isin() -> dict[str, str]:
    """Listing SEDOL to ISIN for Vanguard Canada's own ETFs (to identify fund-of-fund lines)."""
    df = products()
    out = {}
    for isin, sedols in zip(df["isin"], df["listing_sedols"]):
        for s in (_clean(sedols) or "").split(","):
            if s:
                out[s] = isin
    return out


# ----------------------------------------------------------------------- holdings


def is_valid_sedol(value: object) -> bool:
    """Seven characters, no vowels, with a correct check digit."""
    if not isinstance(value, str) or len(value.strip()) != 7:
        return False
    v = value.strip().upper()
    if not re.fullmatch(r"[0-9B-DF-HJ-NP-TV-Z]{6}[0-9]", v):
        return False
    total = sum((int(c) if c.isdigit() else ord(c) - 55) * w for c, w in zip(v[:6], (1, 3, 1, 7, 3, 9)))
    return (10 - total % 10) % 10 == int(v[6])


def asset_class(security_type: object) -> str:
    """Vanguard security type codes (``EQ.STOCK``, ``FI.CORP``, ``CT.FOREX`` ...) to output labels."""
    t = str(security_type or "").upper()
    if t in ("EQ.ETF",) or t.startswith("MF."):
        return "fund"
    if t.startswith("EQ."):
        return "equity"
    if t.startswith("FI."):
        return "debt"
    if t in ("CRNY", "MM.CASH") or t.startswith("CASH"):
        return "cash"
    if t in ("CT.FOREX", "CT.SPOT"):
        return "derivative (foreign exchange)"
    if t.startswith("DE."):
        return "derivative (futures)"
    if "SWAP" in t:
        return "derivative (swap)"
    return "other"


def _clean(v: object) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def _bond_name(item: dict) -> str | None:
    name = _clean(item.get("securityLongDescription")) or _clean(item.get("issuerName"))
    extra = []
    if item.get("couponRate") not in (None, ""):
        extra.append(f"{float(item['couponRate']):g}%")
    if item.get("finalMaturity"):
        extra.append(str(item["finalMaturity"])[:10])
    return " ".join([name or ""] + extra).strip() or None


def parse_holdings(items: list[dict], own_sedol_isin: dict[str, str] | None = None) -> tuple[str | None, pd.DataFrame]:
    """Holdings items (any of the three layers) to ``(as_of, HOLDING_COLUMNS frame)``."""
    own = own_sedol_isin or {}
    rows, dates = [], []
    for it in items:
        if it.get("effectiveDate"):
            dates.append(str(it["effectiveDate"])[:10])
        stype = it.get("securityType")
        ac = asset_class(stype)
        sedol = _clean(it.get("sedol1"))
        sedol = sedol.upper() if sedol and is_valid_sedol(sedol) else None
        isin = _clean(it.get("isin"))
        isin = isin.upper() if isin and is_valid_isin(isin) else None
        if isin is None and sedol in own:
            isin = own[sedol]
        ticker = _clean(it.get("ticker"))
        rows.append(
            {
                "isin": isin,
                "cusip": None,
                "sedol": sedol,
                "ticker": ticker,
                "name": _bond_name(it) if ac == "debt" else (_clean(it.get("securityLongDescription")) or _clean(it.get("issuerName"))),
                "weight_pct": it.get("marketValuePercentage"),
                "asset_class": ac,
                "currency": ticker if ac == "cash" and ticker and len(ticker) == 3 else None,
                "country": _clean(it.get("bloombergIsoCountry")),
            }
        )
    df = pd.DataFrame(rows, columns=HOLDING_COLUMNS)
    df["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce")
    as_of = max(set(dates), key=dates.count) if dates else None
    return as_of, df


def fetch_layer(port_id: str, layer: str) -> list[dict]:
    """All items of one holdings layer for one ``portId``, following ``lastItemKey``."""
    items: list[dict] = []
    key = None
    for _ in range(MAX_PAGES):
        variables = {"portIds": [port_id]}
        if key:
            variables["lastItemKey"] = key
        data = _graphql(HOLDINGS_QUERIES[layer], variables)
        block = ((data.get("borHoldings") or [{}])[0] or {}).get(layer) or {}
        page = block.get("items") or []
        items += page
        key = block.get("lastItemKey")
        if not key or not page:
            break
    else:
        raise RuntimeError(f"Vanguard Canada {layer} for {port_id}: more than {MAX_PAGES} pages")
    return items


def _port_id(product: dict) -> str:
    pid = _clean(product.get("port_id"))
    if not pid:
        m = re.search(r"/(\w+)/[^/]*$", _clean(product.get("product_page_url")) or "")
        pid = m.group(1) if m else None
    if not pid:
        raise ValueError("Vanguard Canada product row has no port_id")
    return str(pid)


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    """Holdings of one product: the first non-empty layer, as the product page shows it."""
    ctx = ctx or FetchContext()
    pid = _port_id(product)
    cache = Path(ctx.cache_dir) / f"{NAME}_{pid}_{date.today().isoformat()}.json" if ctx.cache_dir else None
    payload = json.loads(cache.read_text()) if cache is not None and cache.exists() else None
    if payload is None:
        payload = {"port_id": pid, "layer": None, "items": []}
        for layer in LAYERS:
            items = fetch_layer(pid, layer)
            if items:
                payload = {"port_id": pid, "layer": layer, "items": items}
                break
    own = {}
    if any(asset_class(i.get("securityType")) == "fund" and not i.get("isin") for i in payload["items"]):
        try:
            own = _own_sedol_isin()
        except Exception as e:  # the holdings are still usable without it
            logger.warning("Vanguard Canada fund list unavailable for SEDOL lookup: %s", e)
    as_of, df = parse_holdings(payload["items"], own)
    if cache is not None and payload["items"]:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload))
    if payload["layer"]:
        notes = [LAYER_NOTES[payload["layer"]], f"Fetched from POST {GRAPHQL_URL} (borHoldings.{payload['layer']}, portId {pid})."]
    else:
        notes = ["Vanguard Canada publishes no holdings for this product."]
    url = _clean(product.get("product_page_url")) or GRAPHQL_URL
    return IssuerHoldings(as_of=as_of, holdings=df, url=url, notes=notes)

