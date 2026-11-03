"""VanEck UCITS ETFs, ETNs and UCITS funds (Europe): daily holdings workbook.

Fund list
    The European site's product search is fed by
    ``/Main/FundSearchPanelBlock/GetSearchPanelData?blockId=<id>&language=en``,
    with ``<id>`` read from ``<form id="ucitsSearchForm" data-blockid>`` on
    ``/de/en/``. Each row has ``Values`` (ISIN, WKN, name, TER, net assets
    with a currency sign), ``Keywords`` (the fund ticker first) and ``Url``,
    the fund page. Asset classes come from the fund explorer
    (``/de/en/fundlisting/overview/etfs/``), which posts ``filterJson`` to
    ``/Main/FundListingUcits/GetFundData/`` and gets ``FundSet`` rows with
    ``FundID``, ``ISIN``, ``TickerGroup`` (ETF, ETN, MF) and an ``assetClass``
    cell.
Holdings
    ``<fund page>downloads/holdings/`` returns ``<TICKER>_asof_<YYYYMMDD>.xlsx``:
    a title row ``All Holdings  MM/DD/YYYY``, then a table whose header starts
    with ``Number``. Equity files have ``Holding Name, Ticker, ISIN, Shares,
    Market Value, % of Net Assets`` with weights as text such as ``"10.73%"``;
    bond files have ``ISIN, Coupon, Maturity, Composite Rating, % of Net
    Assets, State`` with weights such as ``"4.1025"``. Both are percent of net
    assets. The last line is ``Other/Cash``.

The first request to the site redirects to ``?cken=true`` (a check that the
client keeps cookies) and back; a cookie jar passes it, nothing is accepted.
robots.txt asks for ``Crawl-delay: 25``, so this module waits 25 seconds
between requests to www.vaneck.com.
"""

from __future__ import annotations

import io
import json
import logging
import re
import warnings
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests

from ... import http
from ...identifiers import is_valid_isin
from . import (
    HOLDING_COLUMNS,
    PRODUCT_COLUMNS,
    FetchContext,
    IssuerAdapter,
    IssuerHoldings,
    register,
)

logger = logging.getLogger(__name__)

NAME = "vaneck"
HOST = "https://www.vaneck.com"
HOME_URL = HOST + "/de/en/"
LISTING_URL = HOST + "/de/en/fundlisting/overview/etfs/"
PANEL_URL = HOST + "/Main/FundSearchPanelBlock/GetSearchPanelData"
FUND_DATA_URL = HOST + "/Main/FundListingUcits/GetFundData/"

#: robots.txt on www.vaneck.com sets ``Crawl-delay: 25``.
CRAWL_DELAY = 25.0
http.set_min_interval("www.vaneck.com", CRAWL_DELAY)

_JAR = requests.cookies.RequestsCookieJar()

_PANEL_RE = re.compile(r'<form[^>]*id="ucitsSearchForm"[^>]*data-blockid="(\d+)"')
_PAGE_ID_RE = re.compile(r"currentPageId\s*=\s*(\d+)")
_CHECKBOX_RE = re.compile(r'<input[^>]*type="checkbox"[^>]*value="([a-z]+)"')
_SHEET_DATE_RE = re.compile(r"asof_(\d{8})", re.I)
_TITLE_DATE_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})")
_CURRENCY_SIGNS = {"€": "EUR", "$": "USD", "£": "GBP"}
_CLASS_CCY_RE = re.compile(r"UCITS-(EUR|USD|GBP|CHF)\b")


# ---------------------------------------------------------------- fetching


def _request(method: str, url: str, **kw) -> requests.Response:
    """Polite request through :mod:`openbasket.http` with a shared cookie jar."""
    r = http.request(method, url, cookies=_JAR, **kw)
    for resp in [*r.history, r]:
        _JAR.update(resp.cookies)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------- parsing


def parse_panel_block_id(html: str) -> str:
    m = _PANEL_RE.search(html)
    if not m:
        raise ValueError("VanEck page has no ucitsSearchForm block id")
    return m.group(1)


def parse_listing_page(html: str) -> tuple[str | None, list[str]]:
    """``(currentPageId, asset-class filter codes)`` from the fund explorer page."""
    m = _PAGE_ID_RE.search(html)
    codes = list(dict.fromkeys(_CHECKBOX_RE.findall(html)))
    return (m.group(1) if m else None), codes


def parse_fund_data(payload: dict) -> dict[str, dict]:
    """GetFundData ``FundSet`` to ``{isin: {fund_id, group, asset_class, url}}``."""
    out = {}
    for f in (payload.get("Result") or {}).get("FundSet") or []:
        cells = {c.get("Header"): c for c in f.get("RowData") or []}
        isin = (f.get("ISIN") or "").strip().upper()
        if not is_valid_isin(isin):
            continue
        out[isin] = {
            "fund_id": f.get("FundID"),
            "group": f.get("TickerGroup"),
            "asset_class": (cells.get("assetClass") or {}).get("Value"),
            "url": (cells.get("ticker") or cells.get("name") or {}).get("Link") or None,
        }
    return out


def _currency(net_assets: str | None, name: str) -> str | None:
    m = _CLASS_CCY_RE.search(name or "")
    if m:
        return m.group(1)
    for sign, code in _CURRENCY_SIGNS.items():
        if sign in (net_assets or ""):
            return code
    return None


def product_rows(panel: dict, fund_data: dict[str, dict] | None = None) -> pd.DataFrame:
    """One row per share-class ISIN from the search panel and fund explorer."""
    fund_data = fund_data or {}
    rows = []
    for r in panel.get("Rows") or []:
        values = r.get("Values") or []
        if not values:
            continue
        isin = str(values[0]).strip().upper()
        if not is_valid_isin(isin):
            continue
        name = re.sub(r"\s+", " ", str(values[2] if len(values) > 2 else "")).strip()
        extra = fund_data.get(isin, {})
        path = r.get("Url") or extra.get("url")
        page = urljoin(HOST, path) if path else None
        group = extra.get("group") or ("ETN" if name.endswith(" ETN") else "ETF" if " ETF" in name else "MF")
        keywords = r.get("Keywords") or []
        rows.append(
            {
                "issuer": NAME,
                "site": "de",
                "portfolio_id": path.strip("/").split("/")[-1].lower() if path else isin,
                "isin": isin,
                "ticker": keywords[0].strip() if keywords else None,
                "fund_name": name,
                "domicile": isin[:2],
                "currency": _currency(values[4] if len(values) > 4 else None, name),
                "asset_class": extra.get("asset_class"),
                "replication": None,
                "product_page_url": page,
                # Mutual-fund share classes have no holdings download (404).
                "holdings_url": urljoin(page, "downloads/holdings/") if page and group != "MF" else None,
            }
        )
    df = pd.DataFrame(rows, columns=PRODUCT_COLUMNS)
    # Share classes of one fund share its asset class.
    df["asset_class"] = df.groupby("portfolio_id")["asset_class"].transform(lambda s: s.ffill().bfill())
    df["asset_class"] = df["asset_class"].astype(object).where(df["asset_class"].notna(), None)
    return df.drop_duplicates("isin").reset_index(drop=True)


def _text(v: object) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return None if s in ("", "--", "-", "nan") else s


def _pct(v: object) -> float | None:
    s = _text(v)
    if s is None:
        return None
    try:
        return float(s.replace("%", "").replace(",", ""))
    except ValueError:
        return None


def _as_of(sheet_name: str, title: str | None) -> str | None:
    m = _SHEET_DATE_RE.search(sheet_name or "")
    if m:
        return datetime.strptime(m.group(1), "%Y%m%d").date().isoformat()
    m = _TITLE_DATE_RE.search(title or "")
    if m:  # the title row is US style, MM/DD/YYYY
        return datetime.strptime(m.group(1), "%m/%d/%Y").date().isoformat()
    return None


def parse_holdings(content: bytes, fund_asset_class: str | None = None) -> tuple[str | None, pd.DataFrame]:
    """Read a holdings workbook into ``(as_of, HOLDING_COLUMNS frame)``.

    ``% of Net Assets`` is percent of net assets in both layouts (``"10.73%"``
    and ``"4.1025"``); the sign is dropped and the number kept as is.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # "Workbook contains no default style"
        sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, dtype=object)
    sheet_name, raw = next(iter(sheets.items()))
    first = raw.iloc[:, 0].map(lambda v: (_text(v) or "").lower())
    hdr_rows = first.index[first == "number"]
    if len(hdr_rows) == 0:
        raise ValueError("VanEck holdings file has no header row starting with Number")
    hdr = hdr_rows[0]
    as_of = _as_of(str(sheet_name), _text(raw.iloc[0, 0]))

    body = raw.iloc[hdr + 1:].copy()
    body.columns = [(_text(c) or f"col{i}") for i, c in enumerate(raw.iloc[hdr])]
    body = body[body["Holding Name"].map(_text).notna()]
    weight_col = next(c for c in body.columns if "net assets" in c.lower())
    is_bond_file = any(c in body.columns for c in ("Coupon", "Maturity"))
    income_fund = (fund_asset_class or "").strip().lower() in ("income", "fixed income")
    crypto_fund = "digital asset" in (fund_asset_class or "").lower()

    out = []
    for _, r in body.iterrows():
        name = _text(r["Holding Name"])
        isin = (_text(r.get("ISIN")) or "").upper()
        isin = isin if is_valid_isin(isin) else None
        ticker = _text(r.get("Ticker"))
        if ticker and isin and ticker.upper() == isin:
            ticker = None  # bonds in multi-asset files repeat the ISIN as ticker
            line_class = "debt"
        elif name.lower().startswith("other/cash") or name.lower() == "cash":
            line_class = "cash"
        elif crypto_fund:
            line_class = "other"  # the coin itself
        elif isin or ticker:
            line_class = "debt" if (is_bond_file or income_fund) else "equity"
        else:
            line_class = "other"
        out.append(
            {
                "isin": isin,
                "cusip": None,
                "sedol": None,
                "ticker": ticker,
                "name": name,
                "weight_pct": _pct(r[weight_col]),
                "asset_class": line_class,
                "currency": None,
                "country": _text(r.get("State")) if "State" in body.columns else None,
            }
        )
    df = pd.DataFrame(out, columns=HOLDING_COLUMNS)
    df["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce").astype(float)
    return as_of, df.reset_index(drop=True)


# ---------------------------------------------------------------- adapter


def products() -> pd.DataFrame:
    """All VanEck UCITS ETFs, ETNs and UCITS fund share classes on the European site.

    Four requests, 25 seconds apart: the home page (search block id), the
    search panel data, the fund explorer page and one fund-data query.
    """
    block_id = parse_panel_block_id(_request("GET", HOME_URL).text)
    panel = _request("GET", PANEL_URL, params={"blockId": block_id, "language": "en"}).json()
    if not panel.get("Rows"):
        raise ValueError("VanEck search panel returned no rows")
    fund_data = {}
    try:
        page_id, codes = parse_listing_page(_request("GET", LISTING_URL).text)
        filt = {
            "InvType": "all", "AssetClass": codes, "Funds": [], "ShareClass": [], "TableType": "ov",
            "SortCol": "ticker", "isasc": True, "FilterFunds": [""], "CurrentPageId": page_id or "",
            "CountryIso": "de", "LanguageIso": "en", "InvestorType": "",
        }
        fund_data = parse_fund_data(_request("POST", FUND_DATA_URL, data={"filterJson": json.dumps(filt)}).json())
    except Exception as exc:  # asset classes are a nice-to-have
        logger.warning("VanEck fund explorer failed, asset classes left empty: %s", exc)
    return product_rows(panel, fund_data)


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    """Full holdings of one fund, cached per fund per day after parsing."""
    url = product.get("holdings_url")
    if not isinstance(url, str) or not url:
        raise LookupError(f"VanEck publishes no holdings file for {product.get('isin')}")
    slug = product["portfolio_id"]
    cache = None
    if ctx is not None and ctx.cache_dir:
        cache = Path(ctx.cache_dir) / f"vaneck_{slug}_{date.today().isoformat()}.xlsx"
    content = cache.read_bytes() if cache is not None and cache.exists() else None
    fresh = content is None
    if fresh:
        logger.info("Fetching VanEck holdings %s", url)
        content = _request("GET", url).content
    fund_class = product.get("asset_class")
    as_of, df = parse_holdings(content, fund_class if isinstance(fund_class, str) else None)
    if fresh and cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(content)
    notes = []
    if df["isin"].isna().all() and not df.empty:
        notes.append("no line carries an ISIN (for example a crypto ETN backed by the coin itself)")
    return IssuerHoldings(as_of, df, url, notes)


ADAPTER = register(
    IssuerAdapter(
        name=NAME,
        title="VanEck (Europe)",
        homepage=HOST + "/de/en/",
        credit="Holdings data: VanEck, vaneck.com",
        licence=(
            "Public files on vaneck.com, no login. Not openly licensed: the site terms say no information "
            "on the site may be reproduced, transmitted, displayed, distributed, published or otherwise "
            "used for commercial purposes without VanEck's prior consent; personal use is allowed. "
            "Fetched at most once per fund per day, 25 seconds apart as robots.txt asks."
        ),
        regions=("IE", "NL", "DE", "EU", "CH", "UK"),
        products=products,
        holdings=holdings,
        frequency="daily",
    )
)
