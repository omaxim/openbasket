"""Franklin Templeton ETFs: UCITS (Ireland), US and Canada.

Every Franklin Templeton site is the same Angular app over one public GraphQL
service, the "PDS" API, which the site's own pages call without a login:

Fund list
    ``POST https://<site>/api/pds/price-and-performance?op=IntlIndentifiers``
    (the operation name is spelled that way by Franklin) with the ``PPSS``
    query and ``productType = "etf"`` returns every ETF on the site, each
    with its ``fundid`` and the ISIN, ticker, CUSIP and SEDOL of each share
    class.
Holdings
    The same endpoint with ``op=Holdings`` and the ``Portfolio`` query for one
    ``fundid`` returns ``dailyholdings`` (IE and US sites: every line, with
    ISIN, CUSIP or SEDOL, and ``pctofnetassets`` in percent to two decimals)
    or, on the Canadian site, ``fullholdings`` (quarterly, several quarters at
    once; only the latest is kept).
Product pages
    Each site's product sitemap,
    ``/binaries/content/assets/global/sitemaps/google/<culture>_product.xml``,
    links the human page of each share class by ``/<fundid>/<shclcode>/``.

The ``cusipnbr`` field holds a CUSIP for North American lines and a SEDOL for
the rest; each is kept only if its check digit is right.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from ... import http
from ...identifiers import is_valid_cusip, is_valid_isin
from . import (
    HOLDING_COLUMNS,
    PRODUCT_COLUMNS,
    FetchContext,
    IssuerAdapter,
    IssuerHoldings,
    register,
)

logger = logging.getLogger(__name__)

API_PATH = "/api/pds/price-and-performance"

#: Sites read. ``date`` is how the site writes ``asofdate``.
SITES = {
    "ie": {
        "host": "https://www.franklintempleton.ie",
        "country": "IE",
        "language": "en_GB",
        "date": "%d/%m/%Y",
        "culture": "en-ie",
    },
    "us": {
        "host": "https://www.franklintempleton.com",
        "country": "US",
        "language": "en_US",
        "date": "%m/%d/%Y",
        "culture": "en-us",
    },
    "ca": {
        "host": "https://www.franklintempleton.ca",
        "country": "CA",
        "language": "en_CA",
        "date": "%m/%d/%Y",
        "culture": "en-ca",
    },
}

LIST_QUERY = """query IntlIndentifiers($countrycode: String!, $languagecode: String!, $productType: String!) {
  PPSS(countrycode: $countrycode, languagecode: $languagecode, productType: $productType) {
    fundid fundname assetclass etftype methodology basecurrcode
    shareclass {
      shclname shclcurr etfclass
      identifiers { fundid shclcode ticker isin cusip sedol }
    }
  }
}"""

_HOLDING_FIELDS = (
    "asofdate frequency secticker isinsecnbr cusipnbr secname quantityshrpar "
    "pctofnetassets mktvalue assetclasscatg mktcurr"
)
HOLDINGS_QUERY = """query Holdings($fundid: String!, $countrycode: String!, $languagecode: String!) {
  Portfolio(fundid: $fundid, countrycode: $countrycode, languagecode: $languagecode) {
    fundname producttype assetclass
    portfolio {
      dailyholdings { %s }
      fullholdings { asofdatestd %s }
    }
  }
}""" % (_HOLDING_FIELDS, _HOLDING_FIELDS)

#: Franklin's ``methodology`` labels. Active funds and US index funds give
#: none, so their replication stays unknown. Franklin runs no swap-based ETF.
REPLICATION = {
    "full replication": "physical",
    "optimised": "sampled",
    "optimized": "sampled",
    "sampling": "sampled",
    "synthetic": "swap",
    "swap": "swap",
}

_BLANK = {"", "-", "—", "–", "n/a", "N/A", "null", "None"}


def _clean(v: object) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return None if s in _BLANK else s


def is_valid_sedol(value: object) -> bool:
    """True for a seven-character SEDOL whose check digit is right."""
    if not isinstance(value, str):
        return False
    s = value.strip().upper()
    if not re.fullmatch(r"[0-9BCDFGHJKLMNPQRSTVWXYZ]{6}[0-9]", s):
        return False
    weights = (1, 3, 1, 7, 3, 9)
    total = sum(w * (int(c) if c.isdigit() else ord(c) - 55) for w, c in zip(weights, s[:6]))
    return (10 - total % 10) % 10 == int(s[6])


def _pct(v: object) -> float | None:
    s = _clean(v)
    if s is None:
        return None
    s = s.replace(",", "").replace("%", "")
    neg = s.startswith("(") and s.endswith(")")
    try:
        x = float(s.strip("()"))
    except ValueError:
        return None
    return -x if neg else x


def asset_class(label: object) -> str:
    """Map Franklin's ``assetclasscatg`` to the labels used in the output."""
    s = (_clean(label) or "").lower()
    if not s:
        return "other"
    if any(k in s for k in ("currency contract", "forward", "future", "swap", "option", "warrant")):
        kind = (
            "foreign exchange" if ("currency" in s or "forward" in s)
            else "futures" if "future" in s
            else "swap" if "swap" in s
            else "other"
        )
        return f"derivative ({kind})"
    if any(k in s for k in ("cash", "currency", "money market", "net current assets", "repo")):
        return "cash"
    if any(k in s for k in ("stock", "equit", "right", "reit", "depositary", "share")):
        return "equity"
    if any(k in s for k in ("bond", "note", "mortgage", "municipal", "treasur", "govern", "debt",
                            "loan", "asset backed", "asset-backed", "fixed income", "bill", "paper")):
        return "debt"
    if any(k in s for k in ("fund", "etf")):
        return "fund"
    return "other"


def _post(site: str, op: str, query: str, variables: dict, extra: str = "") -> tuple[str, dict]:
    cfg = SITES[site]
    url = f"{cfg['host']}{API_PATH}?op={op}{extra}"
    r = http.post(
        url,
        json={"operationName": op, "variables": variables, "query": query},
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    r.raise_for_status()
    payload = r.json()
    if payload.get("errors"):
        raise ValueError(f"Franklin PDS {op} on {site} returned errors: {payload['errors'][:2]}")
    return url, payload


def _sitemap_url(site: str) -> str:
    cfg = SITES[site]
    return f"{cfg['host']}/binaries/content/assets/global/sitemaps/google/{cfg['culture']}_product.xml"


def parse_sitemap(xml: str) -> dict[tuple[str, str], str]:
    """Product pages from a product sitemap.

    Keys are ``("isin", <ISIN>)`` when the URL ends in the share-class ISIN
    (IE site), ``(fundid, shclcode)`` and ``(fundid, "")`` for the first page
    of a fund.
    """
    pages: dict[tuple[str, str], str] = {}
    for loc in re.findall(r"<loc>([^<]+)</loc>", xml):
        loc = loc.strip()
        # .../<fundid>/<shclcode>/<slug>[/<ISIN or ticker>]
        m = re.search(r"/(\d+)/([A-Za-z0-9-]+)/[a-z0-9-]+(?:/([A-Za-z0-9]+))?/?$", loc)
        if not m:
            continue
        isin = m.group(3)
        if is_valid_isin(isin):
            pages.setdefault(("isin", isin), loc)
        pages.setdefault((m.group(1), m.group(2)), loc)
        pages.setdefault((m.group(1), ""), loc)
    return pages


def parse_products(payload: dict, site: str, pages: dict[tuple[str, str], str] | None = None) -> pd.DataFrame:
    """PPSS response to one row per share-class ISIN (:data:`PRODUCT_COLUMNS`)."""
    pages = pages or {}
    host = SITES[site]["host"]
    rows = []
    for fund in (payload.get("data") or {}).get("PPSS") or []:
        fid = str(fund.get("fundid") or "")
        method = (_clean(fund.get("methodology")) or "").lower()
        replication = REPLICATION.get(method)
        for sc in fund.get("shareclass") or []:
            ids = sc.get("identifiers") or {}
            isin = (_clean(ids.get("isin")) or "").upper()
            if not is_valid_isin(isin):
                continue
            code = _clean(ids.get("shclcode")) or ""
            page = pages.get(("isin", isin)) or pages.get((fid, code)) or pages.get((fid, ""))
            if page and "/closed/" in page:
                # Liquidated funds stay in the list; their page moves under
                # /investments/closed/ and their holdings are a final cash line.
                continue
            name = fund.get("fundname") or ""
            cls = _clean(sc.get("shclname"))
            if cls and cls not in ("SINGLCLASS", "Fund") and cls not in name:
                name = f"{name} {cls}"
            rows.append(
                {
                    "issuer": "franklin_ca" if site == "ca" else "franklin",
                    "site": site,
                    "portfolio_id": fid,
                    "isin": isin,
                    "ticker": _clean(ids.get("ticker")),
                    "fund_name": name.strip(),
                    "domicile": isin[:2],
                    "currency": _clean(sc.get("shclcurr")),
                    "asset_class": _clean(fund.get("assetclass")),
                    "replication": replication,
                    "product_page_url": page,
                    "holdings_url": f"{host}{API_PATH}?op=Holdings&fundid={fid}",
                }
            )
    df = pd.DataFrame(rows, columns=PRODUCT_COLUMNS).astype(object)
    df = df.where(df.notna(), None)
    return df.drop_duplicates("isin").reset_index(drop=True)


def products_for(sites: tuple[str, ...]) -> pd.DataFrame:
    frames = []
    for site in sites:
        cfg = SITES[site]
        variables = {"countrycode": cfg["country"], "languagecode": cfg["language"], "productType": "etf"}
        _, payload = _post(site, "IntlIndentifiers", LIST_QUERY, variables)
        try:
            pages = parse_sitemap(http.get(_sitemap_url(site)).text)
        except Exception as exc:  # product pages are a nicety; the list is not
            logger.warning("Franklin %s sitemap not read: %s", site, exc)
            pages = {}
        frames.append(parse_products(payload, site, pages))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PRODUCT_COLUMNS)
    return df.drop_duplicates("isin").reset_index(drop=True)


def _row_date(row: dict, fmt: str) -> str | None:
    std = _clean(row.get("asofdatestd"))
    if std:
        return std[:10]
    raw = _clean(row.get("asofdate"))
    if not raw:
        return None
    try:
        return datetime.strptime(raw, fmt).date().isoformat()
    except ValueError:
        return None


def parse_holdings(payload: dict, site: str) -> tuple[str | None, pd.DataFrame, list[str]]:
    """``Portfolio`` response to ``(as_of, HOLDING_COLUMNS frame, notes)``.

    Daily holdings are used when the site has them, else the full (periodic)
    holdings. Only lines of the latest as-of date are kept, since the
    Canadian site returns several quarters at once.
    """
    fmt = SITES[site]["date"]
    port = ((payload.get("data") or {}).get("Portfolio") or {}).get("portfolio") or {}
    rows = port.get("dailyholdings") or []
    kind = "dailyholdings"
    if not rows:
        rows, kind = port.get("fullholdings") or [], "fullholdings"
    notes: list[str] = []
    if not rows:
        return None, pd.DataFrame(columns=HOLDING_COLUMNS), ["no holdings published"]
    dates = [_row_date(r, fmt) for r in rows]
    as_of = max((d for d in dates if d), default=None)
    kept = [r for r, d in zip(rows, dates) if d == as_of]
    freqs = sorted({(_clean(r.get("frequency")) or "").lower() for r in kept} - {""})
    if len(kept) < len(rows):
        notes.append(f"{len(rows) - len(kept)} lines of earlier dates dropped")
    notes.append(f"{kind}, frequency {'/'.join(freqs) or 'unknown'}")

    out = []
    for r in kept:
        isin = (_clean(r.get("isinsecnbr")) or "").upper()
        code = (_clean(r.get("cusipnbr")) or "").upper()
        out.append(
            {
                "isin": isin if is_valid_isin(isin) else None,
                "cusip": code if is_valid_cusip(code) else None,
                "sedol": code if len(code) == 7 and is_valid_sedol(code) else None,
                "ticker": _clean(r.get("secticker")),
                "name": _clean(r.get("secname")),
                "weight_pct": _pct(r.get("pctofnetassets")),
                "asset_class": asset_class(r.get("assetclasscatg")),
                "currency": _clean(r.get("mktcurr")),
                "country": None,
            }
        )
    df = pd.DataFrame(out, columns=HOLDING_COLUMNS).astype(object)
    df = df.where(df.notna(), None)
    df["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce").astype(float)
    return as_of, df, notes


def holdings_for(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    """Full holdings of one Franklin ETF; responses cached per fund per day."""
    site = product.get("site") or "ie"
    cfg = SITES[site]
    fid = str(product["portfolio_id"])
    cache = None
    if ctx is not None and ctx.cache_dir:
        cache = Path(ctx.cache_dir) / "franklin" / f"{site}_{fid}_{date.today().isoformat()}.json"
    url = f"{cfg['host']}{API_PATH}?op=Holdings&fundid={fid}"
    if cache is not None and cache.exists():
        payload = json.loads(cache.read_text())
    else:
        variables = {"fundid": fid, "countrycode": cfg["country"], "languagecode": cfg["language"]}
        url, payload = _post(site, "Holdings", HOLDINGS_QUERY, variables, extra=f"&fundid={fid}")
    as_of, df, notes = parse_holdings(payload, site)
    if cache is not None and not cache.exists() and len(df):
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload))
    return IssuerHoldings(as_of=as_of, holdings=df, url=url, notes=notes)


def products() -> pd.DataFrame:
    """Franklin UCITS ETFs (IE site) and US ETFs."""
    return products_for(("ie", "us"))


def products_ca() -> pd.DataFrame:
    """ETF series of Franklin's Canadian funds."""
    return products_for(("ca",))


_CREDIT = "Holdings data: Franklin Templeton (franklintempleton.com), as published on its website."
_LICENCE = (
    "Public data from Franklin Templeton's websites, read through the same API its fund pages use, "
    "without login. Franklin's site terms have not been reviewed for automated or bulk use; "
    "requests are throttled and each fund is fetched at most once a day."
)

register(
    IssuerAdapter(
        name="franklin",
        title="Franklin Templeton ETFs",
        homepage="https://www.franklintempleton.ie/our-funds/price-and-performance-etfs",
        credit=_CREDIT,
        licence=_LICENCE,
        regions=("IE", "US", "Europe", "Switzerland"),
        products=products,
        holdings=holdings_for,
        frequency="daily",
    )
)

register(
    IssuerAdapter(
        name="franklin_ca",
        title="Franklin Templeton Canada ETF series",
        homepage="https://www.franklintempleton.ca/en-ca/products/price-and-performance",
        credit=_CREDIT,
        licence=_LICENCE,
        regions=("CA",),
        products=products_ca,
        holdings=holdings_for,
        frequency="quarterly",
    )
)
