"""Amundi ETF (including the former Lyxor and ComStage ranges).

Every Amundi ETF country site is served by one JSON API, the same one its
public product pages call from the browser:

``POST https://<site>/mapi/ProductAPI/getProductsData``
    The body carries a ``context`` (``countryCode``, ``languageCode``,
    ``userProfileName``). Only the ``RETAIL`` profile is used, exactly as each
    site embeds it in its public retail pages; professional (``INSTIT``)
    contexts are never sent. Without ``productIds`` the call returns every
    product the site lists, with the characteristics asked for; with
    ``productIds`` and a ``composition`` block it returns each fund's full
    portfolio, one line per position, with ``isin``, ``bbg`` (Bloomberg
    ticker or id), ``name``, ``weight``, ``type``, ``currency``,
    ``countryOfRisk`` and the position ``date``.
``POST https://<site>/mapi/PageContentAPI/getProductPageLinkByPageIdsAndContext``
    Maps ISINs to their product page on that site, used as the human link.

``weight`` is a **fraction** of net assets (0.0775 is 7.75%); it is
multiplied by 100 and not otherwise changed. For swap-based funds
(``Indirect(Swap Based)``) the composition is the fund's substitute basket,
not the index it tracks; such products have ``replication = "swap"`` and
their holdings carry a note saying so.

Each site lists a different subset. :func:`products` reads the retail list of
every site in :data:`SITES` and keeps the first site that lists an ISIN, so
holdings are always fetched in a context where the product exists.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from ... import http
from ...identifiers import is_valid_cusip, is_valid_isin
from . import HOLDING_COLUMNS, PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

logger = logging.getLogger(__name__)

PRODUCTS_PATH = "/mapi/ProductAPI/getProductsData"
PAGE_LINKS_PATH = "/mapi/PageContentAPI/getProductPageLinkByPageIdsAndContext"
HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


@dataclass(frozen=True)
class Site:
    host: str
    country_code: str
    language_code: str
    retail_path: str  # the site's public retail home page, which embeds the context

    @property
    def context(self) -> dict:
        return {"countryCode": self.country_code, "languageCode": self.language_code, "userProfileName": "RETAIL"}

    @property
    def products_url(self) -> str:
        return f"https://{self.host}{PRODUCTS_PATH}"

    @property
    def home(self) -> str:
        return f"https://{self.host}{self.retail_path}"


#: Sites with a public retail profile, in priority order (an ISIN listed by
#: several sites is taken from the first). Switzerland (amundietf.ch), Norway,
#: Finland and Poland offer only a professional profile and are not used; their
#: CHF and other share classes are listed by the sites below. Taiwan's retail
#: context lists no products.
SITES: dict[str, Site] = {
    "de": Site("www.amundietf.de", "DEU", "de", "/de/privatanleger"),
    "fr": Site("www.amundietf.fr", "FRA", "fr", "/fr/particuliers"),
    "it": Site("www.amundietf.it", "ITA", "it", "/it/privati"),
    "at": Site("www.amundietf.at", "AUT", "de", "/de/privatanleger"),
    "es": Site("www.amundietf.es", "ESP", "es", "/es/individual"),
    "nl": Site("www.amundietf.nl", "NLD", "en", "/en/individual"),
    "be": Site("www.amundietf.be", "BEL", "fr", "/fr/particuliers"),
    "lu": Site("www.amundietf.lu", "LUX", "en", "/en/individual"),
    "uk": Site("www.amundietf.co.uk", "GBR", "en", "/en/individual"),
    "se": Site("www.amundietf.se", "SWE", "en", "/en/individual"),
    "dk": Site("www.amundietf.dk", "DNK", "en", "/en/individual"),
    "hk": Site("www.amundietf.com.hk", "HKG", "en", "/en/individual"),
    "sg": Site("www.amundietf.com.sg", "SGP", "en", "/en/individual"),
}

LIST_CHARACTERISTICS = [
    "ISIN",
    "SHARE_MARKETING_NAME",
    "FUND_REFLEX_CODE",  # Amundi's fund code, shared by the share classes of one portfolio
    "FUND_REPLICATION_METHODOLOGY",  # "Direct(Physical)" or "Indirect(Swap Based)"
    "FUND_SAMPLING",
    "FUND_DOMICILIATION_COUNTRY",
    "ASSET_CLASS",
    "SUBASSET_CLASS",
    "CURRENCY",
    "CURRENCY_HEDGE",
    "FUND_ISSUER",  # AMUNDI, LYXOR or COMSTAGE: the range the fund came from
    "MNEMO",
    "MAIN_LISTINGS",  # Bloomberg ticker of the main listing, per country
    "POSITION_AS_OF_DATE",
]

COMPOSITION_FIELDS = ["date", "type", "bbg", "isin", "name", "weight", "quantity", "currency", "sector", "country", "countryOfRisk"]

DOMICILE = {
    "Luxembourg": "LU",
    "Ireland": "IE",
    "France": "FR",
    "Germany": "DE",
    "Hong Kong SAR China": "HK",
}

#: Composition ``type`` values mapped to the normalised asset classes.
ASSET_CLASS = {
    "EQUITY_ORDINARY": "equity",
    "PREFERENCE_SHARES": "equity",
    "DEPOSITORY_RECEIPT": "equity",
    "RIGHT": "other",
    "WARRANT": "other",
    "CORPORATE": "debt",
    "GOVERNMENT": "debt",
    "SECURITIZED": "debt",
    "MUNICIPAL": "debt",
    "TREASURY_BILL": "debt",
    "CASH": "cash",
    "FUTURE": "derivative (futures)",
    "ETF": "fund",
    "MUTUAL_FUND": "fund",
}

SWAP_NOTE = (
    "Swap-based fund: Amundi reports its substitute basket, the securities the fund holds while a "
    "total return swap (not listed) delivers the index return. These lines are not the index "
    "constituents and their weights need not add up to 100%."
)


def _asset_class(kind: object) -> str | None:
    if not isinstance(kind, str) or not kind:
        return None
    if kind in ASSET_CLASS:
        return ASSET_CLASS[kind]
    k = kind.upper()
    if "EQUITY" in k or "SHARE" in k:
        return "equity"
    if any(w in k for w in ("BOND", "DEBT", "NOTE", "BILL")):
        return "debt"
    if "FORWARD" in k or k.startswith("FX"):
        return "derivative (foreign exchange)"
    if "SWAP" in k:
        return "derivative (swap)"
    if "OPTION" in k:
        return "derivative (options)"
    if "FUND" in k:
        return "fund"
    return "other"


def _replication(method: object, sampling: object) -> str | None:
    m = method.lower() if isinstance(method, str) else ""
    if "swap" in m or m.startswith("indirect"):
        return "swap"
    if "physical" in m or m.startswith("direct"):
        return "sampled" if sampling is True else "physical"
    return None


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _absolute(link: object) -> str | None:
    """Page links come back as ``www.host/path``; add the scheme."""
    link = _text(link)
    if link and not link.startswith("http"):
        link = "https://" + link
    return link


def _post(url: str, body: dict, timeout: int = 120) -> dict:
    r = http.post(url, json=body, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


# --------------------------------------------------------------------------- products


def list_body(site: Site) -> dict:
    return {
        "context": site.context,
        "productType": "PRODUCT",
        "characteristics": LIST_CHARACTERISTICS,
        "filters": [],
        "metrics": [],
        "historics": [],
        "sortCriterias": [],
    }


def parse_products(payload: dict, site_key: str, page_links: dict | None = None) -> pd.DataFrame:
    """One row per share-class ISIN from a ``getProductsData`` list response."""
    site = SITES[site_key]
    links = page_links or {}
    rows = []
    for p in payload.get("products") or []:
        c = p.get("characteristics") or {}
        isin = (_text(c.get("ISIN")) or _text(p.get("productId")) or "").upper()
        if not is_valid_isin(isin):
            continue
        listings = c.get("MAIN_LISTINGS") if isinstance(c.get("MAIN_LISTINGS"), dict) else {}
        link = _absolute(links.get(isin))
        dom = _text(c.get("FUND_DOMICILIATION_COUNTRY"))
        reflex = c.get("FUND_REFLEX_CODE")
        rows.append(
            {
                "issuer": "amundi",
                "site": site_key,
                "portfolio_id": str(reflex) if reflex is not None else isin,
                "isin": isin,
                "ticker": _text(listings.get(site.country_code)) or _text(c.get("MNEMO")),
                "fund_name": _text(c.get("SHARE_MARKETING_NAME")),
                "domicile": DOMICILE.get(dom, dom),
                "currency": _text(c.get("CURRENCY")),
                "asset_class": _text(c.get("ASSET_CLASS")),
                "replication": _replication(c.get("FUND_REPLICATION_METHODOLOGY"), c.get("FUND_SAMPLING")),
                "product_page_url": link or site.home,
                "holdings_url": site.products_url,
                "sub_asset_class": _text(c.get("SUBASSET_CLASS")),
                "replication_label": _text(c.get("FUND_REPLICATION_METHODOLOGY")),
                "currency_hedged": c.get("CURRENCY_HEDGE"),
                "range": _text(c.get("FUND_ISSUER")),
                "mnemonic": _text(c.get("MNEMO")),
            }
        )
    cols = PRODUCT_COLUMNS + ["sub_asset_class", "replication_label", "currency_hedged", "range", "mnemonic"]
    return pd.DataFrame(rows, columns=cols).drop_duplicates("isin").reset_index(drop=True)


def _page_links(site: Site, isins: list[str]) -> dict:
    if not isins:
        return {}
    try:
        got = _post(f"https://{site.host}{PAGE_LINKS_PATH}", {"pagesIds": isins, "context": site.context})
        return got if isinstance(got, dict) else {}
    except Exception as exc:  # links are a convenience; the site home is the fallback
        logger.warning("Amundi %s: product page links failed: %s", site.host, exc)
        return {}


def products(sites: list[str] | None = None) -> pd.DataFrame:
    """The union of every retail site's product list, one row per share-class ISIN."""
    frames, seen = [], set()
    for key in sites or list(SITES):
        site = SITES[key]
        try:
            payload = _post(site.products_url, list_body(site))
        except Exception as exc:  # one site down must not lose the others
            logger.warning("Amundi %s: product list failed: %s", site.host, exc)
            continue
        df = parse_products(payload, key)
        df = df[~df["isin"].isin(seen)].reset_index(drop=True)
        if df.empty:
            continue
        links = _page_links(site, df["isin"].tolist())
        df["product_page_url"] = [_absolute(links.get(i)) or u for i, u in zip(df["isin"], df["product_page_url"])]
        seen.update(df["isin"])
        frames.append(df)
    if not frames:
        raise RuntimeError("No Amundi site returned a product list")
    return pd.concat(frames, ignore_index=True)


# --------------------------------------------------------------------------- holdings


def holdings_body(site: Site, isin: str) -> dict:
    return {
        "context": site.context,
        "productIds": [isin],
        "productType": "PRODUCT",
        "characteristics": ["ISIN", "FUND_REPLICATION_METHODOLOGY", "POSITION_AS_OF_DATE"],
        "filters": [],
        "metrics": [],
        "historics": [],
        "composition": {"compositionFields": COMPOSITION_FIELDS},
    }


def _cusip(bbg: str | None, isin: str | None) -> str | None:
    """Bond lines carry a 9-character id in ``bbg``; keep it when it is a valid
    CUSIP that agrees with the line's ISIN, or when there is no ISIN."""
    if not bbg or " " in bbg or not is_valid_cusip(bbg):
        return None
    if isin is None or isin[2:11] == bbg.upper():
        return bbg.upper()
    return None


def parse_holdings(payload: dict, isin: str) -> tuple[str | None, pd.DataFrame, list[str]]:
    """``(as_of, frame, notes)`` from a ``getProductsData`` composition response."""
    product = next((p for p in payload.get("products") or [] if p.get("productId") == isin), None)
    if product is None:
        raise LookupError(f"Amundi returned no product {isin}")
    chars = product.get("characteristics") or {}
    comp = product.get("composition") or {}
    lines = [x.get("compositionCharacteristics") or {} for x in comp.get("compositionData") or []]
    notes: list[str] = []
    rows = []
    for x in lines:
        raw_isin = (_text(x.get("isin")) or "").upper()
        line_isin = raw_isin if is_valid_isin(raw_isin) else None
        bbg = _text(x.get("bbg"))
        weight = x.get("weight")
        rows.append(
            {
                "isin": line_isin,
                "cusip": _cusip(bbg, line_isin),
                "sedol": None,
                "ticker": bbg if bbg and " " in bbg else None,
                "name": _text(x.get("name")),
                "weight_pct": float(weight) * 100 if isinstance(weight, (int, float)) else float("nan"),
                "asset_class": _asset_class(x.get("type")),
                "currency": _text(x.get("currency")),
                "country": _text(x.get("countryOfRisk")) or _text(x.get("country")),
                "issuer_type": _text(x.get("type")),
                "sector": _text(x.get("sector")),
                "quantity": x.get("quantity"),
                "bbg": bbg,
            }
        )
    df = pd.DataFrame(rows, columns=HOLDING_COLUMNS + ["issuer_type", "sector", "quantity", "bbg"])
    df["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce")
    dates = sorted({d for d in (x.get("date") for x in lines) if isinstance(d, str) and d})
    as_of = dates[-1] if dates else None
    if len(dates) > 1:
        notes.append(f"Positions carry {len(dates)} dates ({dates[0]} to {dates[-1]}); the latest is used.")
    if not lines:
        notes.append("Amundi publishes no composition for this product.")
    if _replication(chars.get("FUND_REPLICATION_METHODOLOGY"), None) == "swap":
        notes.append(SWAP_NOTE)
    total = comp.get("totalNumberOfInstruments")
    if isinstance(total, int) and total != len(lines):
        notes.append(f"Amundi reports {total} instruments but returned {len(lines)} lines.")
    return as_of, df, notes


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    """Full composition of one share class, cached per fund per day once parsed."""
    isin = product["isin"]
    site = SITES[product.get("site") or "de"]
    cache_dir = ctx.cache_dir if ctx else None
    cache = Path(cache_dir) / f"amundi_{product.get('site') or 'de'}_{isin}_{date.today().isoformat()}.json" if cache_dir else None
    if cache is not None and cache.exists():
        payload = json.loads(cache.read_text())
        as_of, df, notes = parse_holdings(payload, isin)
    else:
        logger.info("Fetching Amundi holdings for %s from %s", isin, site.host)
        payload = _post(site.products_url, holdings_body(site, isin))
        as_of, df, notes = parse_holdings(payload, isin)
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(payload))
    if product.get("replication") == "swap" and SWAP_NOTE not in notes:
        notes.append(SWAP_NOTE)
    return IssuerHoldings(as_of=as_of, holdings=df, url=site.products_url, notes=notes)


register(
    IssuerAdapter(
        name="amundi",
        title="Amundi ETF (including former Lyxor and ComStage funds)",
        homepage="https://www.amundietf.com",
        credit="Holdings: Amundi Asset Management, Amundi ETF websites (amundietf.de and other country sites).",
        licence=(
            "Public data from the retail pages of Amundi's ETF websites, read through the JSON API those "
            "pages use. Amundi's site terms (legal notice, updated 1 December 2025) reserve all rights and "
            "forbid reproducing or transmitting the site's information, or using it for public or commercial "
            "purposes, without Amundi's prior written permission. No open licence is given; publishing these "
            "rows needs that permission."
        ),
        regions=("LU", "IE", "FR", "DE", "HK"),
        products=products,
        holdings=holdings,
        frequency="daily",
    )
)
