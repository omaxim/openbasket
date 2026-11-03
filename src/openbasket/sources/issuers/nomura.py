"""Nomura Asset Management NEXT FUNDS ETFs (Japan), month-end full holdings.

Fund list
    ``https://nextfunds.jp/monthly_holdings/`` lists every NEXT FUNDS ETF that
    publishes a month-end holdings workbook, grouped by type (Japanese equity,
    foreign equity, bonds, REIT, commodity, leveraged/inverse), with the link
    ``//www.nomura-am.co.jp/fund/monthly_holdings/<code>_brd_data.xlsx``.
Fund ISIN
    Each fund page ``https://nextfunds.jp/lineup/<code>/`` prints the ISIN
    (Japanese ISINs cannot be derived from the four-character exchange code,
    and the Securities Identification Code Committee's code list is a paid
    service), and the workbook repeats it next to ``ISINコード：``.
Holdings
    Sheet ``保有明細``: fund code and name, ``YYYY年M月D日現在`` (the month-end
    as-of date), then one row per position with ``銘柄コード`` (local code),
    ``ISINコード``, Japanese and English name, quantity, value and
    ``純資産比率 % of NAV``. That ratio is a **fraction** of net assets
    (0.117392 = 11.7392%) and is multiplied by 100 here. The ``合計``
    (total) and ``純資産総額`` (net assets) rows after the table are dropped.
"""

from __future__ import annotations

import re

import pandas as pd

from ... import http
from . import FetchContext, IssuerAdapter, IssuerHoldings, register
from ._apac import clean, fetch, holdings_frame, number, products_frame, read_sheets, valid_isin

SITE = "https://nextfunds.jp"
LIST_URL = SITE + "/monthly_holdings/"
FUND_URL = SITE + "/lineup/{code}/"

#: Section headings on the list page -> (asset class label, replication).
#: Leveraged/inverse and commodity funds hold futures, not the index, so
#: their replication is left unknown rather than called physical.
SECTIONS = {
    "国内株式": ("Equity (Japan)", "physical"),
    "外国株式": ("Equity (foreign)", "physical"),
    "債券": ("Fixed Income", "physical"),
    "REIT": ("Real Estate", "physical"),
    "コモディティ": ("Commodity", None),
    "レバレッジ": ("Leveraged / Inverse", None),
}

_SECTION_RE = re.compile(r'<h2 id="fundtype_\d+"[^>]*>([^<]+)</h2>')
_ROW_RE = re.compile(
    r'<td class="col-code">(\w+)</td>\s*<td class="col-name"><a href="/lineup/\w+/">\s*([^<]+?)\s*</a></td>'
    r'.*?href="(//www\.nomura-am\.co\.jp/fund/monthly_holdings/\w+_brd_data\.xlsx)"',
    re.S,
)
_FUND_ISIN_RE = re.compile(r"<th>ISIN</th>\s*<td>(?:<span[^>]*>)?\s*([A-Z]{2}[A-Z0-9]{9}[0-9])")
_DATE_JA = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def parse_list(html: str) -> list[dict]:
    """The monthly-holdings page to product rows without ISINs."""
    sections = [(m.start(), m.group(1).strip()) for m in _SECTION_RE.finditer(html)]
    rows = []
    for m in _ROW_RE.finditer(html):
        before = [s for pos, s in sections if pos < m.start()]
        label, replication = SECTIONS.get(before[-1] if before else "", (None, None))
        code = m.group(1)
        if re.search(r"レバ|インバ|ブル|ベア", m.group(2)):
            replication = None  # e.g. 2251 JGB futures double inverse, listed under bonds
        rows.append(
            {
                "issuer": "nomura",
                "site": "jp",
                "portfolio_id": code,
                "isin": None,
                "ticker": code,
                "fund_name": m.group(2),
                "domicile": "JP",
                "currency": "JPY",
                "asset_class": label,
                "replication": replication,
                "product_page_url": FUND_URL.format(code=code),
                "holdings_url": "https:" + m.group(3),
            }
        )
    return rows


def parse_fund_isin(html: str) -> str | None:
    m = _FUND_ISIN_RE.search(html)
    return valid_isin(m.group(1)) if m else None


def _text(url: str) -> str:
    """A nextfunds.jp page as text; the server sends no charset, the pages are UTF-8."""
    r = http.get(url)
    r.raise_for_status()
    return r.content.decode("utf-8", "replace")


def products() -> pd.DataFrame:
    """Every NEXT FUNDS ETF with a month-end holdings workbook (one fund page per ETF for its ISIN)."""
    rows = parse_list(_text(LIST_URL))
    for row in rows:
        row["isin"] = parse_fund_isin(_text(row["product_page_url"]))
    return products_frame(rows)


def _find(cols: list[str], *needles: str) -> int | None:
    for needle in needles:
        for j, c in enumerate(cols):
            if needle in c:
                return j
    return None


def parse_holdings(content: bytes) -> tuple[str | None, str | None, pd.DataFrame]:
    """A ``<code>_brd_data.xlsx`` workbook to ``(as_of, fund_isin, HOLDING_COLUMNS frame)``."""
    sheets = read_sheets(content)
    raw = sheets.get("保有明細")
    if raw is None:
        raw = next(df for name, df in sheets.items() if not name.startswith("$"))
    first = raw.iloc[:, 0].map(lambda v: clean(v) or "")
    hdr_rows = first.index[first == "No."]
    if len(hdr_rows) == 0:
        raise ValueError("Nomura holdings workbook has no 'No.' header row")
    hdr = hdr_rows[0]

    as_of = fund_isin = None
    for _, row in raw.iloc[:hdr].iterrows():
        cells = [clean(v) or "" for v in row]
        for j, c in enumerate(cells):
            m = _DATE_JA.search(c)
            if m and as_of is None:
                as_of = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            if c.startswith("ISIN") and j + 1 < len(cells):
                fund_isin = valid_isin(cells[j + 1]) or fund_isin
    cols = [(clean(c) or "").replace("\r", "").replace("\n", " ") for c in raw.iloc[hdr]]
    i_code = _find(cols, "銘柄コード")
    i_isin = _find(cols, "ISIN")
    i_weight = _find(cols, "% of NAV", "純資産比率")
    # Equity sheets have "銘柄 （Name）" in Japanese plus an English "Name"
    # column; bond sheets only the first, in English.
    i_name_en = next((j for j, c in enumerate(cols) if c == "Name"), None)
    i_name_ja = next((j for j, c in enumerate(cols) if c.startswith("銘柄") and "Name" in c), None)
    i_sector = _find(cols, "種別", "Sector")
    i_cur = next((j for j, c in enumerate(cols) if c == "Currency"), None)
    i_country = _find(cols, "Country")
    if i_isin is None or i_weight is None:
        raise ValueError(f"Nomura holdings workbook has unexpected columns {cols}")

    rows = []
    for _, r in raw.iloc[hdr + 1 :].iterrows():
        if pd.isna(r.iloc[0]) or number(r.iloc[0]) is None:
            break  # the table ends at the first row without a running number
        name = clean(r.iloc[i_name_en]) if i_name_en is not None else None
        name = name or (clean(r.iloc[i_name_ja]) if i_name_ja is not None else None)
        weight = number(r.iloc[i_weight])
        sector = clean(r.iloc[i_sector]) if i_sector is not None else None
        rows.append(
            {
                "isin": r.iloc[i_isin],
                "ticker": clean(r.iloc[i_code]) if i_code is not None else None,
                "name": name,
                "weight_pct": weight * 100 if weight is not None else None,
                "asset_class": _asset_class(name, sector),
                "currency": r.iloc[i_cur] if i_cur is not None else None,
                "country": r.iloc[i_country] if i_country is not None else None,
            }
        )
    return as_of, fund_isin, holdings_frame(rows)


def _asset_class(name: str | None, sector: str | None) -> str:
    n = (name or "").lower()
    s = sector or ""
    if "futures" in n or "先物" in n or ("gold" in n and re.search(r"\b\d{4}$", n)):
        return "derivative (futures)"
    if "国債" in s or "債" in s:
        return "debt"
    if "reit" in n or "投資法人" in s or "REIT" in s:
        return "equity"
    if "cash" in n or "コール" in n:
        return "cash"
    if "etf" in n or "fund" in n or "投資信託" in s:
        return "fund"
    return "equity"


def holdings(product: dict, ctx: FetchContext) -> IssuerHoldings:
    url = product["holdings_url"]
    as_of, fund_isin, df = fetch(url, parse_holdings, ctx=ctx, prefix="nomura", key=str(product["portfolio_id"]), ext="xlsx")
    notes = ["Month-end holdings; Nomura gives weights as fractions of net assets, converted to percent."]
    if fund_isin and fund_isin != product.get("isin"):
        notes.append(f"Workbook gives fund ISIN {fund_isin}, product list gives {product.get('isin')}.")
    return IssuerHoldings(as_of, df, url, notes)


register(
    IssuerAdapter(
        name="nomura",
        title="NEXT FUNDS (Nomura Asset Management)",
        homepage="https://nextfunds.jp/",
        credit="Holdings: Nomura Asset Management Co., Ltd., NEXT FUNDS monthly holdings (nextfunds.jp).",
        licence=(
            "Public month-end holdings workbooks on Nomura Asset Management's websites, free to "
            "download without a log-in. Terms of reuse have not been reviewed; files are fetched "
            "at most once per fund per day."
        ),
        regions=("JP",),
        products=products,
        holdings=holdings,
        frequency="monthly",
    )
)
