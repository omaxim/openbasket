"""Xtrackers (DWS) UCITS ETFs through their annual and semi-annual reports.

Xtrackers' daily holdings are only served from paths that
``https://etf.dws.com/robots.txt`` disallows (``/api/``, ``/etfdata/*``,
``/export/*``), so openbasket never requests them. What every UCITS fund
must also publish is a statutory annual and semi-annual report with the full
schedule of investments of each sub-fund. This adapter reads those:

1. **Fund list**: the culture sitemaps ``https://etf.dws.com/{culture}/sitemap.xml``
   (allowed by robots.txt) list one product page per share class,
   ``/{culture}/{ISIN}-{slug}/``. Irish and Luxembourg ISINs are kept. The
   slug up to "ucits-etf" names the sub-fund, so share classes of one
   sub-fund share a ``portfolio_id``.
2. **Report**: the AVL Investmentfonds document library
   (:mod:`openbasket.sources.avl_documents`) files each share class's latest
   reports under its ISIN. The newest is read; a share class AVL does not
   list (new classes) uses a sibling class's report, since all classes of a
   sub-fund share one schedule.
3. **Schedule**: the sub-fund's pages are found in the report's contents and
   parsed with :mod:`openbasket.sources.schedule_tables`.
4. **ISINs**: the reports print none, so equity lines are matched by name to
   openbasket's SEC N-PORT security master (:mod:`openbasket.sources.name_isin`)
   when it has been built (``data/03_primary/security_master.parquet`` or
   ``OPENBASKET_SECURITY_MASTER``). Bonds stay unresolved.

What a row is: the fund's holdings on the report date (30 June or
31 December), as printed in its report, not today's holdings. The first
note on every result says so.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

import pandas as pd

from .. import avl_documents as avl
from .. import report_route as route
from .. import robots
from .. import schedule_tables as st
from . import PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

#: Statutory reports are DWS's copyright documents with no open licence, and
#: AVL publishes no reuse terms: run locally, never on the public site.
REDISTRIBUTION = "local_only"

logger = logging.getLogger(__name__)

NAME = "xtrackers_reports"
BRAND = "Xtrackers"
SITEMAP = "https://etf.dws.com/{culture}/sitemap.xml"
CULTURES = ("en-gb", "de-de", "en-ch", "en-lu")
_PRODUCT = re.compile(r"https://etf\.dws\.com/([a-z]{2}-[a-z]{2})/([A-Z]{2}[A-Z0-9]{9}\d)-([^/]+)/?$")
_UPPER = {"msci", "etf", "ucits", "usd", "eur", "gbp", "chf", "jpy", "esg", "sri", "pab", "em", "ii", "uk",
          "usa", "emu", "ftse", "dax", "mdax", "cac", "mib", "csi", "smi", "sli", "spi", "atx", "ac", "gcc",
          "sdg", "fx", "ccf", "ex", "imi", "eurozone"}


# -- products -----------------------------------------------------------------------

def fund_key(slug: str) -> str:
    """Compact sub-fund key from a product slug: everything up to "ucits-etf"."""
    m = re.match(r"^(.*?ucits-(?:etf|ccf))(?:-|$)", slug)
    return st.compact(m.group(1) if m else slug)


def fund_name(slug: str) -> str:
    words = []
    for w in slug.split("-"):
        if w in _UPPER or re.fullmatch(r"\d+[cd]", w):
            words.append(w.upper())
        elif w == "s" or w == "p":
            words.append(w.upper())
        else:
            words.append(w.capitalize())
    name = " ".join(words).replace("S P ", "S&P ")
    return f"Xtrackers {name}"


def parse_sitemap(xml: str) -> list[dict]:
    rows = []
    for url in avl.parse_sitemap_urls(xml):
        m = _PRODUCT.match(url)
        if not m:
            continue
        culture, isin, slug = m.groups()
        if isin[:2] not in ("IE", "LU"):
            continue
        rows.append({
            "issuer": NAME,
            "site": culture,
            "portfolio_id": fund_key(slug),
            "isin": isin,
            "ticker": None,
            "fund_name": fund_name(slug),
            "domicile": isin[:2],
            "currency": None,
            "asset_class": None,
            "replication": "swap" if "swap" in slug.split("-") else None,
            "product_page_url": url,
            "holdings_url": None,
        })
    return rows


def products(cache_dir: str | Path | None = None, fetch=None) -> pd.DataFrame:
    """Every Irish and Luxembourg Xtrackers share class in the etf.dws.com sitemaps."""
    cache = avl.cache_dir(cache_dir) / f"xtrackers_products_{date.today().isoformat()}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    get = fetch or (lambda u: robots.get(u, timeout=60))
    rows: list[dict] = []
    for culture in CULTURES:
        r = get(SITEMAP.format(culture=culture))
        if r.status_code == 200:
            rows.extend(parse_sitemap(r.text))
    df = pd.DataFrame(rows, columns=PRODUCT_COLUMNS).drop_duplicates("isin").reset_index(drop=True)
    df.to_parquet(cache, index=False)
    return df


# -- holdings -----------------------------------------------------------------------

CREDIT = "Holdings: Xtrackers (DWS Investment S.A. / DWS Investment GmbH), annual and semi-annual reports"
FAMILY = route.Family(
    brand=BRAND,
    credit=CREDIT,
    blocked="etf.dws.com serves daily holdings only from /api/ and /etfdata/, which its robots.txt disallows",
)


#: Sub-funds too new to have a report, mapped to an Xtrackers sub-fund on the same
#: index whose report this adapter reads. Only physical (non-swap) funds on both
#: sides; the stand-in's positions are labelled as such, never as the fund's own.
STAND_INS = {
    "stoxxeurope600iiucitsetf": {
        "peer_key": "stoxxeurope600ucitsetf",
        "index": "STOXX Europe 600",
        "evidence": "both are Xtrackers physical (non-swap) ETFs on the STOXX Europe 600; the 'II' fund was "
                    "launched after the last report date, so it has no schedule of its own yet",
    },
}


def stand_in(product: dict, cat: pd.DataFrame, ctx, fetch, name_index) -> IssuerHoldings | None:
    spec = STAND_INS.get(product["portfolio_id"])
    if not spec:
        return None
    peers = cat[(cat["portfolio_id"] == spec["peer_key"])]
    if peers.empty:
        return None
    peer = peers.sort_values("isin").iloc[0].to_dict()
    res = holdings(peer, ctx, fetch=fetch, catalogue=cat, name_index=name_index, _allow_stand_in=False)
    first = (f"holdings of {peer['fund_name']} ({peer['isin']}) on the same index ({spec['index']}), "
             f"used as a stand-in; {res.notes[0]}; not the fund's own holdings")
    notes = [first, f"stand-in evidence: {spec['evidence']}"] + res.notes[1:]
    return IssuerHoldings(as_of=res.as_of, holdings=res.holdings, url=res.url, notes=notes)


def holdings(product: dict, ctx: FetchContext | None = None, *, fetch=None, catalogue: pd.DataFrame | None = None,
             name_index: dict | None = None, _allow_stand_in: bool = True) -> IssuerHoldings:
    """Holdings of one share class from its sub-fund's latest report (or a labelled stand-in)."""
    cache = ctx.cache_dir if ctx and ctx.cache_dir else None
    cat = catalogue if catalogue is not None else products(cache, fetch=fetch)
    try:
        return route.holdings(product, FAMILY, cat, cache, fetch=fetch, index=name_index)
    except LookupError:
        alt = stand_in(product, cat, ctx, fetch, name_index) if _allow_stand_in else None
        if alt is None:
            raise
        return alt


ADAPTER = register(IssuerAdapter(
    name=NAME,
    title="Xtrackers (DWS), statutory reports",
    homepage="https://etf.dws.com",
    credit=CREDIT,
    licence=(
        "Statutory annual and semi-annual reports, published by DWS for investors; DWS's copyright. "
        "Read from the AVL Investmentfonds library, whose robots.txt allows /fds/. No open licence: "
        "local use only (REDISTRIBUTION = local_only)."
    ),
    regions=("IE", "LU"),
    products=products,
    holdings=holdings,
    frequency="semi-annual",
))
