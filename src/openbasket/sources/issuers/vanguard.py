"""Vanguard holdings from the GPX GraphQL service behind Vanguard's websites.

One backend serves Vanguard's sites in Europe, Canada and parts of Latin
America, and it knows US and Australian funds too:

Endpoint
    ``POST https://www.vanguard.co.uk/gpx/graphql`` with the headers
    ``content-type: application/json`` and ``x-consumer-id: uk0`` (what the
    UK site itself sends). The same service answers at
    ``www.ch.vanguard/gpx/graphql``, ``www.de.vanguard/gpx/graphql`` and
    ``www.vanguard.ca/gpx/graphql``. Introspection is off; the query shapes
    below are the ones the sites' own pages send (read from their
    server-rendered Apollo state).
Fund universe
    ``portIdList(country: "<English country name>")`` lists every portId
    registered for sale in a country (``"United Kingdom"``, ``"Switzerland"``,
    ...). ``funds(portIds: [...]) { profile { ... } }`` then gives each share
    class's ISIN (``identifiers``), name, currency, domicile, product type,
    ``etfReplicationMethodology``, exchange listings and
    ``relatedShareClassesPolaris`` (the other share classes of the same
    portfolio). The US list comes from investor.vanguard.com instead
    (``/investment-products/list/funddetail/all``): its ``fundId`` is the
    same portId.
Holdings
    ``borHoldings(portIds: [id]) { holdings(limit: 1500, lastItemKey: k) { totalHoldings lastItemKey items { ... } } }``
    returns the full month-end book of record, 1,500 lines a page, paged by
    passing back ``lastItemKey``. Each line has ``isin``, ``sedol1``,
    ``cusip``, ``ticker``, ``securityType``, ``marketValuePercentage``
    (percent of net assets, five decimals) and ``effectiveDate``.

Holdings are per portfolio: every share class of a portfolio (hedged ones
included) returns the same lines, and currency-hedged portfolios list each
FX forward leg separately, positive and negative.

This is the one Vanguard source in the fund list: it covers the US and
Canadian funds too (with five-decimal weights and ISINs, and a Canadian fund
of funds as the Vanguard ETFs it holds, which the look-through then expands).
For a US or Canadian fund whose GPX holdings fail or come back empty, the
holdings are read instead from investor.vanguard.com
(:mod:`~openbasket.sources.issuers.vanguard_us`, two-decimal weights) or
vanguard.ca (:mod:`~openbasket.sources.issuers.vanguard_ca`, the site's own
look-through, SEDOLs only), and a note says so.

Every holdings request goes to the UK site's GPX endpoint with the header the
UK site sends (``x-consumer-id: uk0``), whatever the fund's region.

Terms: Vanguard's UK/Europe and Canadian website terms license site content
for personal, informational, non-commercial use only, forbid copying,
publishing or redistributing it without Vanguard's written authorisation, and
the UK terms also forbid "repeated automated access" by software other than
what Vanguard makes available. The source is therefore ``local_only``: it runs
only for the person using openbasket, and the public build never fetches or
publishes it.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd

from ... import http
from ...identifiers import cusip_to_isin, is_valid_cusip, is_valid_isin
from . import HOLDING_COLUMNS, PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

logger = logging.getLogger(__name__)

#: Vanguard's terms restrict republication: read only for the person running openbasket.
REDISTRIBUTION = "local_only"
#: On by default; switch it off with ``issuers.disabled: [vanguard]``.
ENABLED_BY_DEFAULT = True

GPX_URL = "https://www.vanguard.co.uk/gpx/graphql"
GPX_HEADERS = {"content-type": "application/json", "x-consumer-id": "uk0"}
US_LIST_URL = "https://investor.vanguard.com/investment-products/list/funddetail/all"
PAGE_LIMIT = 1500       # the service refuses a larger limit
MAX_PAGES = 200         # 300,000 lines; a guard against a key that never ends
PORT_CHUNK = 400        # portIds per funds() call

#: Regional sites. ``countries`` are the portIdList names whose funds the site
#: reads; ``mic`` picks the listing whose ticker is used; ``page`` builds the
#: product page (the site resolves it by portId; the slug is cosmetic).
SITES: dict[str, dict] = {
    "us": {"us_list": True, "page": "us"},
    "au": {"countries": ["Australia", "New Zealand"], "mic": "XASX", "page": "au"},
    "ca": {"countries": ["Canada"], "mic": "XTSE", "page": "https://www.vanguard.ca/en/product/{type}/{asset_ca}/{port}/{slug}"},
    "uk": {"countries": ["United Kingdom"], "mic": "XLON", "page": "https://www.vanguard.co.uk/professional/product/{type}/{asset}/{port}/{slug}"},
    "ch": {"countries": ["Switzerland"], "mic": "XSWX", "page": "https://www.ch.vanguard/en/professional/product/{type}/{asset}/{port}/{slug}"},
    "de": {"countries": ["Germany", "Austria"], "mic": "XETR", "page": "https://www.de.vanguard/professionell/anlageprodukte/{type_de}/{asset_de}/{port}/{slug}"},
    "ie": {"countries": ["Ireland"], "mic": "XLON", "page": "https://www.ie.vanguard/products/{type}/{asset}/{port}/{slug}"},
    "nl": {"countries": ["Netherlands"], "mic": "XAMS", "page": "https://www.nl.vanguard/professional/product/{type}/{asset}/{port}/{slug}"},
}

#: Order in which sites claim a share class that several of them list: home
#: markets first (a US ETF listed in Zurich keeps its US row), then the UK
#: site, which carries every Irish and UK fund.
DEFAULT_SITES = ("us", "au", "ca", "uk", "ch", "de", "ie", "nl")

#: Local ticker codes in a listing's ``identifiers``, by preference.
TICKER_CODES = ("TIDM", "SSET", "DBTK", "XETR", "EETK", "BITK", "ASX", "CATK", "TICK", "BMV")

DOMICILE = {
    "IRELAND": "IE", "UNITED KINGDOM": "GB", "UNITED STATES": "US", "AUSTRALIA": "AU",
    "CANADA": "CA", "HONG KONG": "HK", "MEXICO": "MX", "JAPAN": "JP",
}

PROFILE_FIELDS = """portId fundFullName shareClassName fundCurrency marketOfDomicile polarisPdtTypeIndicator
assetClassificationLevel1 etfReplicationMethodology activityStatusCode
identifiers { altIdCode altIdValue }
listings { stockExchangeMarketIdentifierCode fundCurrency activityStatusCode identifiers { altIdCode altIdValue } }
relatedShareClassesPolaris { portId }"""

PORT_LIST_QUERY = "query($c: String!) { portIdList(country: $c) { count portIds { portId } } }"
FUNDS_QUERY = "query($p: [String!]!) { funds(portIds: $p) { profile { %s } } }" % PROFILE_FIELDS
HOLDINGS_QUERY = """query($p: [String!]!, $k: String) { borHoldings(portIds: $p) { portId
holdings(limit: %d, lastItemKey: $k) { totalHoldings lastItemKey items {
issuerName securityLongDescription securityShortDescription isin sedol1 cusip ticker securityType
marketValuePercentage quantity effectiveDate couponRate maturityDate } } } }""" % PAGE_LIMIT

#: ``securityType`` prefixes mapped to the labels used elsewhere in the output.
SECURITY_TYPES = {
    "EQ.ETF": "fund",
    "EQ": "equity",
    "FI": "debt",
    "MM.TBILL": "debt",
    "MM.CASH": "cash",
    "MM": "money market",
    "CRNY": "cash",
    "CT.FOREX": "derivative (foreign exchange)",
    "CT.SPOT": "derivative (foreign exchange)",
    "CT.PORTSWAP": "derivative (swap)",
    "DE.IND": "derivative (futures)",
    "DE.COMM": "derivative (commodity)",
    "DE": "derivative (other)",
    "MF": "fund",
}

_SEDOL_RE = re.compile(r"^[0-9B-DF-HJ-NP-TV-Z]{6}[0-9]$")


class GraphQLError(RuntimeError):
    pass


def gpx(query: str, variables: dict) -> dict:
    """Run one GPX query and return its ``data``."""
    r = http.post(GPX_URL, json={"query": query, "variables": variables}, headers=GPX_HEADERS)
    try:
        payload = r.json()
    except ValueError as e:
        raise GraphQLError(f"GPX returned HTTP {r.status_code} with no JSON") from e
    if payload.get("errors"):
        raise GraphQLError(f"GPX HTTP {r.status_code}: {payload['errors'][0].get('message', '')[:300]}")
    r.raise_for_status()
    return payload["data"]


# --------------------------------------------------------------------------
# Fund universe
# --------------------------------------------------------------------------


def _ids(profile: dict) -> dict[str, str]:
    return {i["altIdCode"]: i["altIdValue"] for i in profile.get("identifiers") or [] if i.get("altIdValue")}


def _slug(name: str) -> str:
    name = re.sub(r"^Vanguard\s+", "", name or "")
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "fund"


def _ticker(profile: dict, mic: str | None, us_ticker: str | None = None) -> str | None:
    if us_ticker:
        return us_ticker
    listings = [l for l in profile.get("listings") or [] if l.get("activityStatusCode", "ACTIVE") == "ACTIVE"]
    # The site's home exchange first, and there the line in the fund's own
    # currency (VWRA, not VWRP, for the USD class in London).
    listings.sort(key=lambda l: (l.get("stockExchangeMarketIdentifierCode") != mic, l.get("fundCurrency") != profile.get("fundCurrency")))
    for l in listings:
        ids = _ids(l)
        for code in TICKER_CODES:
            if ids.get(code):
                return ids[code]
        if ids.get("BLMB"):
            return ids["BLMB"].split()[0]
    return _ids(profile).get("TICK")


def _replication(value: str | None) -> str | None:
    v = (value or "").lower()
    if "synthetic" in v or "swap" in v:
        return "swap"
    if "sampl" in v or "optimi" in v:
        return "sampled"
    if "physical" in v or "full" in v:
        return "physical"
    return None


def _product_page(site: str, profile: dict, ticker: str | None) -> str | None:
    tmpl = SITES[site]["page"]
    etf = profile.get("polarisPdtTypeIndicator") == "ETF"
    port = profile["portId"]
    if tmpl == "us":
        if not ticker:
            return None
        kind = "etfs" if etf else "mutual-funds"
        return f"https://investor.vanguard.com/investment-products/{kind}/profile/{ticker.lower()}"
    if tmpl == "au":
        return f"https://www.vanguard.com.au/personal/invest-with-us/{'etf' if etf else 'fund'}?portId={port}"
    bond = (profile.get("assetClassificationLevel1") or "").lower() in ("bond", "fixed income", "short-term reserves", "money market")
    return tmpl.format(
        type="etf" if etf else "fund",
        type_de="etf" if etf else "fonds",
        asset="bond" if bond else "equity",
        asset_de="anleihen" if bond else "aktien",
        asset_ca="fixed-income" if bond else "equity",
        port=port,
        slug=_slug(profile.get("fundFullName")),
    )


def fetch_profiles(port_ids: list[str]) -> dict[str, dict]:
    """GPX profiles for portIds, keyed by portId."""
    out: dict[str, dict] = {}
    ids = list(dict.fromkeys(port_ids))
    for i in range(0, len(ids), PORT_CHUNK):
        for f in gpx(FUNDS_QUERY, {"p": ids[i : i + PORT_CHUNK]}).get("funds") or []:
            p = (f or {}).get("profile")
            if p and p.get("portId"):
                out.setdefault(p["portId"], p)
    return out


def country_port_ids(country: str) -> list[str]:
    data = gpx(PORT_LIST_QUERY, {"c": country})["portIdList"]
    return [p["portId"] for p in data.get("portIds") or []]


def fetch_us_list() -> list[dict]:
    """investor.vanguard.com's list of every US Vanguard ETF and mutual fund."""
    r = http.get(US_LIST_URL, headers={"Accept": "application/json"})
    r.raise_for_status()
    return [e["profile"] for e in r.json()["fund"]["entity"] if e.get("profile")]


def _portfolio_ids(profiles: dict[str, dict], extra_links: dict[str, set[str]]) -> dict[str, str]:
    """portId -> smallest portId among the share classes of its portfolio."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for pid, p in profiles.items():
        for rel in p.get("relatedShareClassesPolaris") or []:
            if rel.get("portId"):
                union(pid, rel["portId"])
    for pid, links in extra_links.items():
        for other in links:
            union(pid, other)
    return {pid: find(pid) for pid in profiles}


def product_rows(site: str, profiles: dict[str, dict], portfolio: dict[str, str], us_meta: dict[str, dict] | None = None) -> list[dict]:
    """PRODUCT_COLUMNS rows for the active share classes among ``profiles``."""
    cfg = SITES[site]
    rows = []
    for pid, p in profiles.items():
        if p.get("activityStatusCode") != "ACTIVE" or p.get("polarisPdtTypeIndicator") not in ("ETF", "FUND"):
            continue
        ids = _ids(p)
        meta = (us_meta or {}).get(pid, {})
        isin = (ids.get("ISIN") or "").strip().upper() or cusip_to_isin(meta.get("cusip") or ids.get("CUSP"))
        if not is_valid_isin(isin):
            continue
        ticker = _ticker(p, cfg.get("mic"), meta.get("ticker"))
        rows.append(
            {
                "issuer": "vanguard",
                "site": site,
                "portfolio_id": portfolio.get(pid, pid),
                "isin": isin,
                "ticker": ticker,
                "fund_name": p.get("fundFullName") or meta.get("longName"),
                "domicile": DOMICILE.get((p.get("marketOfDomicile") or "").upper(), isin[:2]),
                "currency": p.get("fundCurrency"),
                "asset_class": p.get("assetClassificationLevel1") or meta.get("style"),
                "replication": _replication(p.get("etfReplicationMethodology")),
                "product_page_url": _product_page(site, p, ticker),
                "holdings_url": GPX_URL,
                "port_id": pid,
                "product_type": p.get("polarisPdtTypeIndicator"),
                "share_class": p.get("shareClassName"),
            }
        )
    return rows


def _site_profiles(site: str) -> tuple[dict[str, dict], dict[str, set[str]], dict[str, dict]]:
    """(profiles, extra share-class links, US list metadata) for one site."""
    cfg = SITES[site]
    if cfg.get("us_list"):
        listed = fetch_us_list()
        meta = {p["fundId"]: p for p in listed}
        links: dict[str, set[str]] = {}
        for p in listed:
            assoc = {v for v in (p.get("associatedFundIds") or {}).values() if v}
            links[p["fundId"]] = assoc
        wanted = list(meta) + sorted({a for s in links.values() for a in s} - set(meta))
        profiles = {k: v for k, v in fetch_profiles(wanted).items() if (v.get("marketOfDomicile") or "").upper() == "UNITED STATES"}
        return profiles, links, meta
    ids: list[str] = []
    for country in cfg["countries"]:
        ids += country_port_ids(country)
    return fetch_profiles(ids), {}, {}


def products(sites: tuple[str, ...] | list[str] = DEFAULT_SITES) -> pd.DataFrame:
    """Every active Vanguard share class on the given sites, one row per ISIN.

    A share class several sites list keeps the row of the first site in
    ``sites``. Extra columns: ``port_id`` (the GPX key for its holdings),
    ``product_type`` (ETF or FUND) and ``share_class``.
    """
    per_site = []
    all_profiles: dict[str, dict] = {}
    all_links: dict[str, set[str]] = {}
    for site in sites:
        profiles, links, meta = _site_profiles(site)
        per_site.append((site, profiles, meta))
        all_profiles.update({k: v for k, v in profiles.items() if k not in all_profiles})
        for k, v in links.items():
            all_links.setdefault(k, set()).update(v)
    portfolio = _portfolio_ids(all_profiles, all_links)
    rows = []
    for site, profiles, meta in per_site:
        rows += product_rows(site, profiles, portfolio, meta)
    cols = PRODUCT_COLUMNS + ["port_id", "product_type", "share_class"]
    df = pd.DataFrame(rows, columns=cols)
    return df.drop_duplicates("isin").reset_index(drop=True)


# --------------------------------------------------------------------------
# Holdings
# --------------------------------------------------------------------------


def _str(v: object) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def _asset_class(security_type: str | None) -> str:
    t = (security_type or "").upper()
    for key in sorted(SECURITY_TYPES, key=len, reverse=True):
        if t == key or t.startswith(key + "."):
            return SECURITY_TYPES[key]
    return "other"


def parse_holdings(items: list[dict]) -> tuple[str | None, pd.DataFrame]:
    """GPX ``borHoldings`` items to ``(as_of, DataFrame)`` with HOLDING_COLUMNS.

    ``marketValuePercentage`` is already percent of net assets and is kept
    as reported. ``as_of`` is the ``effectiveDate`` most lines carry.
    """
    rows = []
    for it in items:
        isin = (_str(it.get("isin")) or "").upper()
        cusip = (_str(it.get("cusip")) or "").upper()
        sedol = (_str(it.get("sedol1")) or "").upper()
        stype = _str(it.get("securityType"))
        ticker = _str(it.get("ticker"))
        asset = _asset_class(stype)
        rows.append(
            {
                "isin": isin if is_valid_isin(isin) else None,
                "cusip": cusip if is_valid_cusip(cusip) else None,
                "sedol": sedol if _SEDOL_RE.match(sedol) else None,
                "ticker": ticker,
                "name": _str(it.get("securityLongDescription")) or _str(it.get("issuerName")) or _str(it.get("securityShortDescription")),
                "weight_pct": it.get("marketValuePercentage"),
                "asset_class": asset,
                # Cash lines are named by their currency code; nothing else
                # in the feed gives a currency.
                "currency": ticker if stype == "CRNY" and ticker and len(ticker) == 3 else None,
                "country": None,
                "issuer_name": _str(it.get("issuerName")),
                "security_type": stype,
                "coupon_pct": it.get("couponRate") if asset == "debt" else None,
                "maturity_date": _str(it.get("maturityDate")) if asset == "debt" else None,
                "quantity": it.get("quantity"),
            }
        )
    df = pd.DataFrame(rows, columns=HOLDING_COLUMNS + ["issuer_name", "security_type", "coupon_pct", "maturity_date", "quantity"])
    df["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce").astype(float)
    dates = Counter(d for d in (_str(it.get("effectiveDate")) for it in items) if d)
    as_of = dates.most_common(1)[0][0][:10] if dates else None
    return as_of, df


def fetch_holdings_items(port_id: str) -> tuple[int | None, list[dict]]:
    """All pages of one portfolio's ``borHoldings``: ``(totalHoldings, items)``."""
    items: list[dict] = []
    key = None
    total = None
    for _ in range(MAX_PAGES):
        data = gpx(HOLDINGS_QUERY, {"p": [str(port_id)], "k": key}).get("borHoldings") or []
        if not data or not data[0].get("holdings"):
            break
        page = data[0]["holdings"]
        total = page.get("totalHoldings", total)
        items += page.get("items") or []
        key = page.get("lastItemKey")
        if not key or not page.get("items"):
            break
    else:
        raise GraphQLError(f"borHoldings for {port_id} did not finish after {MAX_PAGES} pages")
    return total, items


#: Region-specific readers used when GPX gives a fund of that site nothing.
FALLBACK_SITES = ("us", "ca")


def _fallback(product: dict, ctx: FetchContext, why: Exception) -> IssuerHoldings:
    """Holdings of a US or Canadian fund from its own site's reader."""
    site = str(product.get("site") or "")
    if site == "us" and product.get("ticker"):
        from . import vanguard_us as reader

        row = {**product, "holdings_url": None}
    elif site == "ca":
        from . import vanguard_ca as reader

        row = dict(product)
    else:
        raise why
    logger.info("Vanguard GPX for %s failed (%s); reading %s instead", product.get("isin"), why, reader.NAME)
    got = reader.holdings(row, ctx)
    if got.holdings.empty:
        raise why
    got.notes = [f"GPX holdings unavailable ({type(why).__name__}: {why}); read from {reader.HOST} instead."] + list(got.notes)
    return got


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    """Full month-end holdings of one share class's portfolio.

    The raw items are cached in ``ctx.cache_dir`` per portId per day, written
    only after they parse. A US or Canadian fund that GPX cannot give falls
    back to its own site (see the module docs).
    """
    ctx = ctx or FetchContext()
    try:
        return _gpx_holdings(product, ctx)
    except (GraphQLError, LookupError, OSError) as exc:
        if str(product.get("site") or "") not in FALLBACK_SITES:
            raise
        return _fallback(product, ctx, exc)


def _gpx_holdings(product: dict, ctx: FetchContext) -> IssuerHoldings:
    port_id = str(product.get("port_id") or product["portfolio_id"])
    url = product.get("product_page_url") or GPX_URL
    cache = Path(ctx.cache_dir) / f"vanguard_{port_id}_{date.today().isoformat()}.json" if ctx.cache_dir else None
    if cache is not None and cache.exists():
        payload = json.loads(cache.read_text())
    else:
        logger.info("Fetching Vanguard holdings for portId %s", port_id)
        total, items = fetch_holdings_items(port_id)
        payload = {"portId": port_id, "totalHoldings": total, "items": items}
    if not payload["items"]:
        raise LookupError(f"Vanguard publishes no holdings for portId {port_id}")
    as_of, df = parse_holdings(payload["items"])
    if cache is not None and not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload))
    notes = [f"POST {GPX_URL} borHoldings(portIds: [\"{port_id}\"]), {len(df)} lines"]
    if payload.get("totalHoldings") not in (None, len(df)):
        notes.append(f"Vanguard reports {payload['totalHoldings']} holdings but {len(df)} lines were returned")
    return IssuerHoldings(as_of=as_of, holdings=df, url=url, notes=notes)


ADAPTER = register(
    IssuerAdapter(
        name="vanguard",
        title="Vanguard",
        homepage="https://www.vanguard.co.uk",
        credit=(
            "Holdings: Vanguard (The Vanguard Group, Inc. and its regional entities), month-end portfolio "
            "data from Vanguard's GPX fund-data service, reached through vanguard.co.uk; the US fund list "
            "comes from investor.vanguard.com. © Vanguard."
        ),
        licence=(
            "No open licence. Vanguard's website terms (UK/Europe and Canada, read 2026-11-03) license site "
            "content for personal, informational, non-commercial use only and forbid copying, publishing or "
            "redistributing data from the sites without Vanguard's written authorisation; the UK terms also "
            "forbid repeated automated access by software other than what Vanguard makes generally available. "
            "openbasket therefore reads it only for the person running it (redistribution class local_only) "
            "and never publishes it. Fetched politely: about one request a second, one fetch per fund per day."
        ),
        regions=("GB", "IE", "CH", "DE", "AT", "NL", "US", "AU", "NZ", "CA"),
        products=products,
        holdings=holdings,
        frequency="monthly",
        redistribution=REDISTRIBUTION,
        enabled_by_default=ENABLED_BY_DEFAULT,
    )
)
