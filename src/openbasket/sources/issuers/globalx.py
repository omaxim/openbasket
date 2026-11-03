"""Global X ETFs Europe: full daily holdings CSV per fund.

Fund list
    ``https://globalxetfs.eu/explore`` (``/funds/`` redirects there) is a
    Next.js page whose embedded flight data holds one record per fund:
    ``ETF_NAME``, ``PRIMARY_TICKER``, ``PRIMARY_ISIN``, ``CLASSNAME``
    (``"USD Acc"``), ``THEME``, ``isCrypto``. Only one ISIN per fund is listed
    there, so each fund page ``https://globalxetfs.eu/funds/<ticker>`` is read
    as well. Its ``ComponentFundsTradingInfo`` block gives the ISIN of the
    accumulating and of the distributing share class with their listings, and
    ``ComponentFundsFundDetails`` gives ``domicile``, ``assetClass`` and
    ``mgmtStyle`` (``"Physical - Full Replication ..."``,
    ``"Synthetic - Passively Managed ..."``) with the swap counterparty.
Holdings
    ``https://globalxetfs.eu/api/funds/<ticker>/topholdingscsv``. Despite the
    name it is the full holdings file (the page button reads "Download Full
    Holdings"). Header ``AS_OF_DATE,SEDOL,NAME,NET_ASSETS,TICKER,
    MARKET_PRICE,SHARES_HELD,MARKET_VALUE,ISIN,COUNTRY``; ``NET_ASSETS`` is
    the weight in percent of net assets (three decimals), ``AS_OF_DATE`` is on
    every row. The file ends with a quoted disclaimer line. Some synthetic
    funds and the crypto ETPs return only that disclaimer.

Synthetic (swap) funds that do list lines list their substitute basket: the
securities held as collateral for the total return swap, not the index the
fund tracks (the Euro Stoxx 50 covered-call fund holds US large caps). Those
lines are labelled :data:`SUBSTITUTE_BASKET` and the result carries a note.

Currency-forward legs (``CURRENCY CONTRACT - JPY/USD``) have a blank
``NET_ASSETS`` in the file; they are kept with no weight.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import date
from pathlib import Path

import pandas as pd

from ... import http
from ...identifiers import is_valid_isin
from . import (
    HOLDING_COLUMNS,
    LOCAL_ONLY,
    PRODUCT_COLUMNS,
    SUBSTITUTE_BASKET,
    FetchContext,
    IssuerAdapter,
    IssuerHoldings,
    mark_substitute_basket,
    register,
)

logger = logging.getLogger(__name__)

NAME = "globalx"
HOST = "https://globalxetfs.eu"
EXPLORE_URL = HOST + "/explore"
FUND_URL = HOST + "/funds/{slug}"
HOLDINGS_URL = HOST + "/api/funds/{slug}/topholdingscsv"

#: Disclaimer at the foot of every holdings CSV, quoted in the licence text.
FOOTER_NOTICE = (
    "The information contained herein may not be reproduced, redistributed or "
    "used to create any derivative works."
)

_FLIGHT_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_SEDOL_RE = re.compile(r"^[0-9BCDFGHJKLMNPQRSTVWXYZ]{6}[0-9]$")
_SEDOL_WEIGHTS = (1, 3, 1, 7, 3, 9)

ASSET_CLASS = {"equity": "equity", "fixed income": "debt", "bond": "debt"}

#: Words that mark a line without an ISIN as cash or a currency balance.
_CASH_WORDS = re.compile(
    r"\b(CASH|PAYABLE|RECEIVABLE|DOLLAR|EURO|POUND|STERLING|YEN|FRANC|KRONE|KRONA|KRONER|RAND|PESO|REAL|"
    r"RUPIAH|RUPEE|BAHT|DIRHAM|RIYAL|DINAR|WON|YUAN|RENMINBI|ZLOTY|FORINT|LIRA|SHEKEL|RINGGIT|KORUNA)\b"
)


# ---------------------------------------------------------------- parsing


def flight_text(html: str) -> str:
    """Concatenate the Next.js flight chunks embedded in a page."""
    return "".join(json.loads('"' + c + '"') for c in _FLIGHT_RE.findall(html))


def _objects(text: str, marker: str) -> list:
    """Every JSON value that starts at an occurrence of ``marker``."""
    dec = json.JSONDecoder()
    out, pos = [], 0
    while (i := text.find(marker, pos)) >= 0:
        try:
            obj, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            pos = i + 1
            continue
        out.append(obj)
        pos = end
    return out


def parse_explore(html: str) -> list[dict]:
    """The fund records embedded in the explore page."""
    text = flight_text(html)
    i = text.find('"ETF_NAME"')
    if i < 0:
        raise ValueError("Global X explore page has no ETF_NAME records")
    arr, _ = json.JSONDecoder().raw_decode(text, text.rfind("[", 0, i))
    return [f for f in arr if isinstance(f, dict) and f.get("PRIMARY_ISIN")]


def parse_fund_page(html: str) -> dict:
    """Share-class ISINs and fund details from one fund page.

    Returns ``{"details": {...}, "classes": [(isin, kind, ticker), ...]}``
    where ``kind`` is ``"acc"`` or ``"dist"``. The page repeats the blocks
    once per locale; the first of each is used.
    """
    text = flight_text(html)
    details = next(iter(_objects(text, '{"__typename":"ComponentFundsFundDetails"')), {})
    trading = next(iter(_objects(text, '{"__typename":"ComponentFundsTradingInfo"')), {})
    classes = []
    for kind in ("acc", "dist"):
        isin = (trading.get(f"{kind}Isin") or "").strip().upper()
        if is_valid_isin(isin):
            listings = trading.get(kind) or []
            ticker = next((x.get("ticker").strip() for x in listings if x.get("ticker")), None)
            classes.append((isin, kind, ticker))
    return {"details": details, "classes": classes}


def replication(mgmt_style: str | None) -> str | None:
    s = (mgmt_style or "").lower()
    if "synthetic" in s or re.search(r"\bswaps?\b", s):  # not "Uniswap"
        return "swap"
    if "sampl" in s or "optimi" in s:
        return "sampled"
    if "physical" in s:
        return "physical"
    return None


def product_rows(explore: list[dict], pages: dict[str, dict]) -> pd.DataFrame:
    """One row per share-class ISIN from the explore records and fund pages."""
    rows = []
    for f in explore:
        ticker = (f.get("PRIMARY_TICKER") or "").strip()
        slug = ticker.lower()
        page = pages.get(slug) or {}
        d = page.get("details") or {}
        name = re.sub(r"\s+", " ", (f.get("ETF_NAME") or "").strip())
        if not name.lower().startswith("global x"):
            name = f"Global X {name}"
        classes = page.get("classes") or [(f["PRIMARY_ISIN"].strip().upper(), None, ticker)]
        if f["PRIMARY_ISIN"].strip().upper() not in {c[0] for c in classes}:
            classes.append((f["PRIMARY_ISIN"].strip().upper(), None, ticker))
        currency = (d.get("Currency") or (f.get("CLASSNAME") or "").split(" ")[0] or "").strip() or None
        for isin, kind, cls_ticker in classes:
            rows.append(
                {
                    "issuer": NAME,
                    "site": "eu",
                    "portfolio_id": slug,
                    "isin": isin,
                    "ticker": cls_ticker or ticker or None,
                    "fund_name": name + {"acc": " (Acc)", "dist": " (Dist)"}.get(kind, ""),
                    "domicile": (d.get("domicile") or "").strip() or None,
                    "currency": currency,
                    "asset_class": (d.get("assetClass") or "").strip() or ("Digital Asset" if f.get("isCrypto") else None),
                    "replication": replication(d.get("mgmtStyle")),
                    "product_page_url": FUND_URL.format(slug=slug),
                    "holdings_url": HOLDINGS_URL.format(slug=slug),
                }
            )
    df = pd.DataFrame(rows, columns=PRODUCT_COLUMNS)
    return df[df["isin"].map(is_valid_isin)].drop_duplicates("isin").reset_index(drop=True)


def _sedol(v: str | None) -> str | None:
    if not v or not _SEDOL_RE.match(v):
        return None
    total = sum(w * (int(c) if c.isdigit() else ord(c) - 55) for w, c in zip(_SEDOL_WEIGHTS, v))
    return v if (10 - total % 10) % 10 == int(v[6]) else None


def _line_class(name: str, has_isin: bool, fund_class: str | None) -> str:
    n = name.upper()
    if n.startswith("CURRENCY CONTRACT"):
        return "derivative (foreign exchange)"
    if n.startswith("TRS ") or " SWAP" in n:
        return "derivative (swap)"
    if has_isin:
        return ASSET_CLASS.get(fund_class.strip().lower() if isinstance(fund_class, str) else "", "other")
    return "cash" if _CASH_WORDS.search(n) else "other"


def parse_holdings(content: bytes, fund_asset_class: str | None = None) -> tuple[str | None, pd.DataFrame]:
    """Read a holdings CSV into ``(as_of, HOLDING_COLUMNS frame)``.

    ``NET_ASSETS`` is already percent of net assets and is kept as is. Lines
    without an ISIN are cash, currency balances, FX contracts, payables and
    swaps; they keep their weight with ``isin = None``. Lines with an ISIN get
    the fund's own asset class, since the file has no per-line class.
    """
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    header = None
    rows = []
    for rec in reader:
        if not rec:
            continue
        if header is None:
            if rec[0].strip().upper() == "AS_OF_DATE":
                header = [c.strip().upper() for c in rec]
            continue
        if not _DATE_RE.match(rec[0].strip()):
            continue  # disclaimer footer
        rows.append(dict(zip(header, (c.strip() for c in rec))))
    if header is None and FOOTER_NOTICE not in text:
        raise ValueError("Global X holdings file has no AS_OF_DATE header")

    def clean(v):
        return v if v not in (None, "", "-") else None

    out = []
    for r in rows:
        isin = (clean(r.get("ISIN")) or "").upper()
        isin = isin if is_valid_isin(isin) else None
        name = clean(r.get("NAME")) or ""
        out.append(
            {
                "isin": isin,
                "cusip": None,
                "sedol": _sedol((clean(r.get("SEDOL")) or "").upper()),
                "ticker": clean(r.get("TICKER")),
                "name": name or None,
                "weight_pct": pd.to_numeric(r.get("NET_ASSETS"), errors="coerce"),
                "asset_class": _line_class(name, isin is not None, fund_asset_class),
                "currency": None,
                "country": clean(r.get("COUNTRY")),
            }
        )
    df = pd.DataFrame(out, columns=HOLDING_COLUMNS)
    df["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce").astype(float)
    dates = sorted({r["AS_OF_DATE"][:10] for r in rows})
    return (dates[-1] if dates else None), df


# ---------------------------------------------------------------- fetching


def _get(url: str) -> bytes:
    r = http.get(url)
    r.raise_for_status()
    return r.content


def products() -> pd.DataFrame:
    """All Global X Europe funds, one row per share-class ISIN.

    One request for the explore page plus one per fund page (about 53), at
    the default one request per second.
    """
    explore = parse_explore(_get(EXPLORE_URL).decode("utf-8"))
    pages = {}
    for f in explore:
        slug = (f.get("PRIMARY_TICKER") or "").strip().lower()
        if not slug:
            continue
        try:
            pages[slug] = parse_fund_page(_get(FUND_URL.format(slug=slug)).decode("utf-8"))
        except Exception as exc:  # keep the primary ISIN from the explore page
            logger.warning("Global X fund page %s failed: %s", slug, exc)
    return product_rows(explore, pages)


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    """Full holdings of one fund, cached per fund per day after parsing."""
    slug = product["portfolio_id"]
    url = product.get("holdings_url")
    url = url if isinstance(url, str) and url else HOLDINGS_URL.format(slug=slug)
    cache = None
    if ctx is not None and ctx.cache_dir:
        cache = Path(ctx.cache_dir) / f"globalx_{slug}_{date.today().isoformat()}.csv"
    content = cache.read_bytes() if cache is not None and cache.exists() else None
    fresh = content is None
    if fresh:
        logger.info("Fetching Global X holdings %s", url)
        content = _get(url)
    fund_class = product.get("asset_class")
    as_of, df = parse_holdings(content, fund_class if isinstance(fund_class, str) else None)
    if fresh and cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(content)
    notes = []
    swap = product.get("replication") == "swap"
    has_trs = bool((df["asset_class"] == "derivative (swap)").any())
    if df.empty:
        notes.append(
            "Global X publishes no constituent list for this product"
            + (" (synthetic: the fund holds a total return swap)" if swap else "")
        )
    elif swap or has_trs:
        df = mark_substitute_basket(df)
        notes.append(
            "synthetic fund: the lines with ISINs are the substitute basket held as collateral for the "
            f"total return swap (asset_class {SUBSTITUTE_BASKET!r}), not the index the fund tracks; "
            "the index exposure comes through the swap"
        )
    if has_trs:
        notes.append("includes a total return swap line without an ISIN")
    return IssuerHoldings(as_of, df, url, notes)


ADAPTER = register(
    IssuerAdapter(
        name=NAME,
        title="Global X ETFs Europe",
        homepage=HOST,
        credit="Holdings data: Global X ETFs Europe (Global X ETFs ICAV), globalxetfs.eu",
        licence=(
            "Public files on globalxetfs.eu, no login. Not openly licensed: every holdings CSV ends with "
            f"\"{FOOTER_NOTICE}\" and the site terms of use allow downloading extracts for personal "
            "reference only and forbid commercial use without a licence from Global X. Republication "
            "needs Global X's permission, so the holdings are read locally only and never republished."
        ),
        regions=("IE", "JE", "EU", "UK", "CH"),
        products=products,
        holdings=holdings,
        frequency="daily",
        redistribution=LOCAL_ONLY,
    )
)
