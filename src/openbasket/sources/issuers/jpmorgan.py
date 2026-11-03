"""J.P. Morgan Asset Management ETFs, from the issuer's own fund pages.

Two families, both served by ``am.jpmorgan.com/FundsMarketingHandler``:

UCITS ETFs (JPMorgan ETFs (Ireland) ICAV)
    Fund list: ``fund-explorer?country=<cc>&role=adv&userLoggedIn=false&language=en&fundType=etf``
    returns a JSON list of share classes registered in one country, with the
    share-class ISIN in ``identifier``. No single country lists every share
    class (CHF-hedged classes are only on the Swiss site, MXN-hedged ones only
    on the Dutch one), so several country lists are merged; each share class
    keeps the first country that lists it, because the holdings file is only
    served for a country where the class is registered.

    Holdings: ``excel?type=dailyETFHoldings&cusip=<ISIN>&country=<cc>&...``
    returns a workbook with ``As of Date:dd/mm/yyyy`` above a table whose
    header row starts with ``Name`` (Name, ISIN, Asset class, Country,
    Currency, Weight, Base market value, Price), followed by disclaimer rows.
    ``Weight`` is a **fraction** of net assets (0.029384 = 2.9384%) and is
    multiplied by 100 here.

US ETFs (J.P. Morgan Exchange-Traded Fund Trust)
    Fund list: the same ``fund-explorer`` with ``country=us``; ``identifier``
    is the fund CUSIP, turned into a US ISIN.

    Holdings: ``product-data?cusip=<CUSIP>&country=us&...`` is the JSON behind
    the product page. ``fundData.dailyHoldingsAll`` has every line with
    ``securityId`` (a CUSIP for securities), ``netAssetValuePercent`` (percent,
    two decimals) and ``effectiveDate``. The US holdings workbook carries no
    identifier column, so it is not used.
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from ... import http
from ...identifiers import cusip_to_isin, is_valid_cusip, is_valid_isin
from . import HOLDING_COLUMNS, PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

logger = logging.getLogger(__name__)

HOST = "https://am.jpmorgan.com"
FUND_EXPLORER_URL = HOST + "/FundsMarketingHandler/fund-explorer"
EXCEL_URL = HOST + "/FundsMarketingHandler/excel"
PRODUCT_DATA_URL = HOST + "/FundsMarketingHandler/product-data"

#: Country sites read for UCITS share classes, in order of preference. On
#: 2026-11-03 gb listed 175 classes, de 5 more, ch 9 more (CHF hedged) and
#: nl 6 more (MXN hedged); the rest add none today but are cheap to check.
UCITS_SITES = ("gb", "ch", "de", "nl", "it", "lu", "fr", "es", "at", "se")
US_SITE = "us"
LOCALES = {"gb": "en-GB", "ch": "en-CH", "de": "en-DE", "nl": "en-NL", "it": "en-IT", "lu": "en-LU",
           "fr": "en-FR", "es": "en-ES", "at": "en-AT", "se": "en-SE", "us": "en-US"}

DOMICILES = {"Ireland": "IE", "Luxembourg": "LU", "United States": "US"}

#: Day first; the separator follows the site locale (02/11/2026 on gb, 02.11.2026 on ch).
_AS_OF_RE = re.compile(r"As of Date:\s*(\d{1,2})[./-](\d{1,2})[./-](\d{4})")


# --------------------------------------------------------------------------- products

def _fund_explorer(country: str) -> list[dict]:
    params = {"country": country, "role": "adv", "userLoggedIn": "false", "language": "en", "fundType": "etf"}
    r = http.get(FUND_EXPLORER_URL, params=params)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _product_page(site: str, share_class_name: str | None, identifier: str) -> str:
    slug = _slug(share_class_name or "fund")
    return f"{HOST}/{site}/en/asset-management/adv/products/{slug}-{identifier.lower()}"


def ucits_holdings_url(isin: str, site: str) -> str:
    return (
        f"{EXCEL_URL}?type=dailyETFHoldings&cusip={isin}&country={site}&role=adv&fundType=N_ETF"
        f"&locale={LOCALES.get(site, 'en-GB')}&isUnderlyingHolding=false&isProxyHolding=false"
    )


def us_holdings_url(cusip: str) -> str:
    return f"{PRODUCT_DATA_URL}?cusip={cusip}&country=us&role=adv&language=en&userLoggedIn=false"


def parse_fund_explorer(records: list[dict], site: str) -> pd.DataFrame:
    """One row per share class from one country's ``fund-explorer`` list."""
    rows = []
    for x in records:
        ident = str(x.get("identifier") or "").strip().upper()
        if site == US_SITE:
            cusip = ident if is_valid_cusip(ident) else None
            isin = cusip_to_isin(cusip) if cusip else None
            holdings_url = us_holdings_url(cusip) if cusip else None
        else:
            isin = ident if is_valid_isin(ident) else None
            holdings_url = ucits_holdings_url(isin, site) if isin else None
        if not isin:
            continue
        domicile = DOMICILES.get(x.get("domicile") or "", x.get("domicile")) or isin[:2]
        rows.append(
            {
                "issuer": "jpmorgan",
                "site": site,
                "portfolio_id": x.get("fundIdentifier") or x.get("fundCmsId") or isin,
                "isin": isin,
                "ticker": x.get("ticker") or x.get("displayId"),
                "fund_name": x.get("shareclassName") or x.get("name"),
                "domicile": domicile,
                "currency": x.get("shareclassCurrencyCode") or x.get("currencyCode"),
                "asset_class": x.get("assetClass"),
                # JPM publishes no replication field. Every JPM ETF holds its
                # securities directly (active, research-enhanced or BetaBuilders
                # index funds); none uses a total-return swap for its exposure.
                "replication": "physical",
                "product_page_url": _product_page(site, x.get("shareclassName") or x.get("name"), ident),
                "holdings_url": holdings_url,
            }
        )
    return pd.DataFrame(rows, columns=PRODUCT_COLUMNS)


def products(sites: tuple[str, ...] = UCITS_SITES + (US_SITE,)) -> pd.DataFrame:
    """Every J.P. Morgan ETF share class on the given country sites."""
    frames = []
    for site in sites:
        try:
            frames.append(parse_fund_explorer(_fund_explorer(site), site))
        except Exception as exc:  # one country failing should not lose the rest
            logger.warning("J.P. Morgan fund explorer for %s failed: %s", site, exc)
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=PRODUCT_COLUMNS)
    return pd.concat(frames, ignore_index=True).drop_duplicates("isin").reset_index(drop=True)


# --------------------------------------------------------------------------- holdings

def _clean(v: object) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return None if s in ("", "-", "nan", "N/A") else s


_SEDOL_WEIGHTS = (1, 3, 1, 7, 3, 9)


def is_valid_sedol(value: object) -> bool:
    """True for a 7-character SEDOL (no vowels) with a correct check digit."""
    if not isinstance(value, str) or len(value) != 7 or not value.isalnum() or not value[-1].isdigit():
        return False
    v = value.upper()
    if any(c in "AEIOU" for c in v):
        return False
    total = sum((int(c) if c.isdigit() else ord(c) - 55) * w for c, w in zip(v[:6], _SEDOL_WEIGHTS))
    return (10 - total % 10) % 10 == int(v[6])


def normalise_asset_class(label: object) -> str:
    """Map J.P. Morgan's security-type labels to the shared asset classes."""
    s = (_clean(label) or "").lower()
    if not s:
        return "other"
    if "option" in s:
        return "derivative (options)"
    if "future" in s:
        return "derivative (futures)"
    if "forward" in s or "fx " in s or s.startswith("fx") or "currency contract" in s:
        return "derivative (foreign exchange)"
    if "swap" in s:
        return "derivative (swaps)"
    if "equity linked" in s:
        return "derivative (equity-linked notes)"
    if "money market" in s:
        return "money market"
    if s in ("cash", "currencies", "currency") or "cash" in s or "repurchase" in s or "time deposit" in s:
        return "cash"
    if "real estate investment trust" in s or s == "reit":
        return "equity"
    # UCITS labels are "<sector> - <type>": Corp - Global Bond, Govt - Bond, Mtge - MBS 30yr, ABS - Auto.
    if s.split(" - ")[0] in ("corp", "govt", "mtge", "abs", "muni", "agcy", "agency", "sovr", "supra"):
        return "debt"
    if any(k in s for k in ("bond", "note", "bill", "debt", "paper", "certificate of deposit", "asset backed",
                            "mortgage", "mbs", "cmo", "cdo", "treasury", "sovereign", "govt", "corporate",
                            "municipal", "loan", "agency", "debenture", "frn", "mtn", "private placement")):
        return "debt"
    if any(k in s for k in ("stock", "depository receipt", "depositary receipt", "equity", "right", "warrant",
                            "preferred", "share", "unit")):
        return "equity"
    if "fund" in s or "etf" in s:
        return "fund"
    return "other"


def parse_ucits_workbook(content: bytes) -> tuple[str | None, pd.DataFrame]:
    """Read a UCITS daily holdings workbook into ``(as_of, HOLDING_COLUMNS frame)``."""
    raw = pd.read_excel(io.BytesIO(content), header=None, dtype=object)
    first = raw.iloc[:, 0].map(lambda v: str(v).strip() if _clean(v) else "")
    header_rows = first.index[first == "Name"]
    if len(header_rows) == 0:
        raise ValueError("J.P. Morgan holdings workbook has no header row starting with 'Name'")
    hdr = header_rows[0]

    as_of = None
    for _, row in raw.iloc[:hdr].iterrows():
        m = _AS_OF_RE.search(" ".join(str(v) for v in row if _clean(v)))
        if m:
            as_of = date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()

    body = raw.iloc[hdr + 1 :].copy()
    body.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in raw.iloc[hdr]]
    # The table ends at the first blank row; the disclaimer follows.
    blank = body.isna().all(axis=1)
    if blank.any():
        body = body.loc[: blank.idxmax()].iloc[:-1]
    if "Weight" not in body.columns:
        raise ValueError(f"J.P. Morgan holdings workbook has no Weight column: {list(body.columns)}")

    out = pd.DataFrame(index=body.index)
    reported = body["ISIN"].map(_clean) if "ISIN" in body else pd.Series(None, index=body.index, dtype=object)
    out["isin"] = reported.map(lambda v: v.upper() if is_valid_isin(v) else None)
    out["cusip"] = None
    out["sedol"] = None
    out["ticker"] = None
    out["name"] = body["Name"].map(_clean)
    # Weight is a fraction of net assets in the file; HOLDING_COLUMNS wants percent.
    # Rounded to 10 decimals only to drop float noise from the conversion (0.029384 * 100).
    out["weight_pct"] = (pd.to_numeric(body["Weight"], errors="coerce") * 100).round(10)
    out["asset_class"] = body["Asset class"].map(normalise_asset_class) if "Asset class" in body else "other"
    out["currency"] = body["Currency"].map(_clean) if "Currency" in body else None
    out["country"] = body["Country"].map(_clean) if "Country" in body else None
    out = out[out["name"].notna() | out["weight_pct"].notna()]
    return as_of, out[HOLDING_COLUMNS].reset_index(drop=True)


def parse_us_product_data(payload: dict) -> tuple[str | None, pd.DataFrame]:
    """Read ``fundData.dailyHoldingsAll`` into ``(as_of, HOLDING_COLUMNS frame)``."""
    block = (payload.get("fundData") or {}).get("dailyHoldingsAll") or {}
    rows = block.get("data")
    if not rows:
        raise ValueError("J.P. Morgan product data has no dailyHoldingsAll lines")
    as_of = block.get("effectiveDate") or next((r.get("navDate") for r in rows if r.get("navDate")), None)
    out = []
    for r in rows:
        sec_id = (_clean(r.get("securityId")) or "").upper()
        isin = (_clean(r.get("securityIsin")) or "").upper()
        cusip = (_clean(r.get("securityCusip")) or sec_id).upper()
        asset_class = normalise_asset_class(r.get("securityType"))
        out.append(
            {
                "isin": isin if is_valid_isin(isin) else (sec_id if is_valid_isin(sec_id) else None),
                "cusip": cusip if is_valid_cusip(cusip) else None,
                # Non-US shares are identified by SEDOL in securityId.
                "sedol": next((v for v in ((_clean(r.get("securitySedol")) or "").upper(), sec_id) if is_valid_sedol(v)), None),
                # Bond lines carry the issuer's ticker (T, FNMA); only keep tickers of shares and funds.
                "ticker": _clean(r.get("securityTicker")) if asset_class in ("equity", "fund", "money market") else None,
                "name": _clean(r.get("securityDescription")),
                "weight_pct": r.get("netAssetValuePercent"),
                "asset_class": asset_class,
                "currency": _clean(r.get("currencyCode")),
                "country": _clean(r.get("country")),
            }
        )
    df = pd.DataFrame(out, columns=HOLDING_COLUMNS)
    df["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce")
    return as_of, df


def _trim_product_data(payload: dict) -> dict:
    """The part of a product-data response worth caching (it is ~1 MB in full)."""
    return {"fundData": {"dailyHoldingsAll": (payload.get("fundData") or {}).get("dailyHoldingsAll")}}


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    """Full holdings of one share class. Parsed responses are cached per fund per day."""
    ctx = ctx or FetchContext()
    site = product.get("site") or "gb"
    isin = product["isin"]
    us = site == US_SITE
    url = product.get("holdings_url") or (us_holdings_url(isin[2:11]) if us else ucits_holdings_url(isin, site))
    cache = None
    if ctx.cache_dir:
        cache = Path(ctx.cache_dir) / f"jpmorgan_{site}_{isin}_{date.today().isoformat()}.{'json' if us else 'xlsx'}"

    if us:
        if cache is not None and cache.exists():
            payload = json.loads(cache.read_text())
            as_of, df = parse_us_product_data(payload)
        else:
            logger.info("Fetching J.P. Morgan US holdings %s", url)
            r = http.get(url)
            r.raise_for_status()
            payload = r.json()
            as_of, df = parse_us_product_data(payload)
            if cache is not None:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(_trim_product_data(payload)))
        notes = ["Weights are percent of net assets as published, rounded to two decimals."]
    else:
        if cache is not None and cache.exists():
            as_of, df = parse_ucits_workbook(cache.read_bytes())
        else:
            logger.info("Fetching J.P. Morgan holdings %s", url)
            r = http.get(url)
            r.raise_for_status()
            if "spreadsheet" not in (r.headers.get("content-type") or ""):
                raise ValueError(f"J.P. Morgan returned {r.headers.get('content-type')!r}, not a workbook, for {isin}")
            as_of, df = parse_ucits_workbook(r.content)
            if cache is not None:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(r.content)
        notes = ["Weights were published as fractions of net assets and are multiplied by 100."]
    return IssuerHoldings(as_of=as_of, holdings=df, url=url, notes=notes)


ADAPTER = register(
    IssuerAdapter(
        name="jpmorgan",
        title="J.P. Morgan Asset Management ETFs",
        homepage="https://am.jpmorgan.com",
        credit="Holdings data: J.P. Morgan Asset Management (am.jpmorgan.com), daily ETF holdings.",
        licence=(
            "Public data from J.P. Morgan Asset Management's fund pages, which mark the holdings report as "
            "unofficial and for information only. The site's terms of use have not been reviewed for "
            "automated or bulk use; the client fetches each fund at most once a day at one request per second."
        ),
        regions=("IE", "EU", "CH", "GB", "US"),
        products=products,
        holdings=holdings,
        frequency="daily",
    )
)
