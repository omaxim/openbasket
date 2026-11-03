"""Samsung Asset Management KODEX ETFs (Korea), daily portfolio deposit files.

Korean ETFs publish a daily PDF (portfolio deposit file: the securities in
one creation unit). For a physical ETF that is the whole portfolio scaled to
one unit, so its weights are the fund's weights.

Fund list
    ``https://www.samsungfund.com/api/v1/kodex/product.do?ordrColm=NAV&ordrSort=DESC&pageNo=<n>&srchTerm=w&themeCd=&srchVal=``
    pages of 20 funds with ``fId`` (Samsung's id), ``stkTicker`` (the KRX
    short code), the name and type. The KRX data portal now needs a log-in,
    so the fund ISIN is built from the short code (``KR7`` + code + ``00`` +
    check digit, see :func:`~openbasket.sources.issuers._apac.korea_isin`).
Holdings
    ``/api/v1/kodex/product-pdf/<fId>.do?gijunYMD=<YYYY.MM.DD>`` gives the
    latest PDF date and its spreadsheet link,
    ``/excel_pdf.do?fId=<fId>&gijunYMD=<YYYYMMDD>``: title, date
    (``YYYY/MM/DD``), then ``번호, 종목명, ISIN, 종목코드, 수량, 비중(%), 평가금액(원)``.
    Despite the ``(%)`` header the ``비중`` column is a **fraction**
    (Samsung Electronics 0.344942 in KODEX 200, shown as 34.49% on the
    site); it is multiplied by 100 here.
"""

from __future__ import annotations

import json
import re
from datetime import date

import pandas as pd

from ... import http
from . import FetchContext, IssuerAdapter, IssuerHoldings, register
from ._apac import clean, fetch, holdings_frame, korea_isin, number, products_frame, read_sheets

HOST = "https://www.samsungfund.com"
LIST_URL = HOST + "/api/v1/kodex/product.do"
PDF_JSON_URL = HOST + "/api/v1/kodex/product-pdf/{fid}.do"
PAGE_URL = HOST + "/etf/product/view.do?id={fid}"

#: Samsung's fund types (``typeLnm``) -> asset class label.
TYPES = {
    "국내주식": "Equity (Korea)",
    "해외주식": "Equity (foreign)",
    "채권": "Fixed Income",
    "혼합자산": "Multi-asset",
    "레버리지/인버스": "Leveraged / Inverse",
    "금리": "Money market (rates)",
    "원자재": "Commodity",
    "통화": "Currency",
    "원자재 및 통화": "Commodity / Currency",
    "부동산": "Real Estate",
}


def _replication(name: str, type_l: str | None) -> str | None:
    if "합성" in name:  # "synthetic": the fund holds a total return swap
        return "swap"
    if type_l == "레버리지/인버스":
        return None  # futures-based, not a holding of the index
    return "physical"


def parse_list(payload: list[dict]) -> list[dict]:
    rows = []
    for d in payload:
        name = d.get("fNm") or ""
        code = clean(d.get("stkTicker"))
        rows.append(
            {
                "issuer": "kodex",
                "site": "kr",
                "portfolio_id": d.get("fId"),
                "isin": korea_isin(code),
                "ticker": code,
                "fund_name": name,
                "domicile": "KR",
                "currency": "KRW",
                "asset_class": TYPES.get(d.get("typeLnm"), d.get("typeLnm")),
                "replication": _replication(name, d.get("typeLnm")),
                "product_page_url": PAGE_URL.format(fid=d.get("fId")),
                "holdings_url": None,
            }
        )
    return rows


def products() -> pd.DataFrame:
    """Every KODEX ETF on Samsung Asset Management's site."""
    rows: list[dict] = []
    page, total = 1, None
    while total is None or len(rows) < total:
        params = {"ordrColm": "NAV", "ordrSort": "DESC", "pageNo": page, "srchTerm": "w", "themeCd": "", "srchVal": ""}
        payload = http.get(LIST_URL, params=params).json()
        if not payload:
            break
        total = int(payload[0].get("totalCnt") or 0)
        rows += parse_list(payload)
        page += 1
        if page > 50:
            break
    return products_frame(rows)


def _cash(name: str | None, isin: str | None) -> bool:
    n = name or ""
    return (isin or "").startswith("KRD") or any(w in n for w in ("예금", "현금", "원화", "CASH", "Cash"))


def parse_holdings(content: bytes) -> tuple[str | None, pd.DataFrame]:
    """A KODEX ``excel_pdf.do`` workbook to ``(as_of, HOLDING_COLUMNS frame)``."""
    raw = next(iter(read_sheets(content).values()))
    hdr = None
    as_of = None
    for i, row in raw.iterrows():
        cells = [clean(v) or "" for v in row]
        m = next((re.search(r"(\d{4})[/.-](\d{2})[/.-](\d{2})", c) for c in cells if re.search(r"\d{4}[/.-]\d{2}[/.-]\d{2}", c)), None)
        if m and as_of is None:
            as_of = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        if "ISIN" in cells:
            hdr = i
            break
    if hdr is None:
        raise ValueError("KODEX PDF workbook has no ISIN header row")
    cols = [clean(c) or "" for c in raw.iloc[hdr]]
    i_isin = cols.index("ISIN")
    i_name = next(j for j, c in enumerate(cols) if c.startswith("종목명"))
    i_code = next((j for j, c in enumerate(cols) if c.startswith("종목코드")), None)
    i_weight = next(j for j, c in enumerate(cols) if c.startswith("비중"))
    rows = []
    for _, r in raw.iloc[hdr + 1 :].iterrows():
        name = clean(r.iloc[i_name])
        if name is None:
            continue
        isin = clean(r.iloc[i_isin])
        weight = number(r.iloc[i_weight])
        cash = _cash(name, isin)
        if cash:
            label = "cash"
        elif (isin or "").startswith("KR4"):
            label = "derivative (futures)" if "F " in name or "선물" in name else "derivative"
        elif "스왑" in name or "swap" in name.lower():
            label = "derivative (swap)"
        elif "ETF" in name.upper() or any(b in name for b in ("KODEX", "TIGER", "ACE", "RISE", "SOL")):
            label = "fund"
        elif (isin or "")[:3] in ("KR6", "KR1", "KR2", "KR3") or "채" in name or "국고" in name:
            label = "debt"
        else:
            label = "equity"
        rows.append(
            {
                # KRD... codes are KRX pseudo-identifiers for cash, not ISINs.
                "isin": None if cash else isin,
                "ticker": clean(r.iloc[i_code]) if i_code is not None else None,
                "name": name,
                "weight_pct": weight * 100 if weight is not None else None,
                "asset_class": label,
                "currency": "KRW" if cash and "원화" in name else None,
            }
        )
    return as_of, holdings_frame(rows)


def _latest(fid: str, ctx: FetchContext) -> tuple[str, str]:
    """``(gijunYMD, spreadsheet URL)`` of the latest PDF for a fund."""

    def parse(content: bytes) -> tuple[str, str]:
        pdf = json.loads(content)["pdf"]
        return pdf["gijunYMD"], HOST + pdf["pdfExcelDownloadUrl"]

    today = date.today().strftime("%Y.%m.%d")
    return fetch(PDF_JSON_URL.format(fid=fid) + f"?gijunYMD={today}", parse, ctx=ctx, prefix="kodex", key=f"{fid}-meta", ext="json")


def holdings(product: dict, ctx: FetchContext) -> IssuerHoldings:
    fid = product["portfolio_id"]
    _, url = _latest(fid, ctx)
    as_of, df = fetch(url, parse_holdings, ctx=ctx, prefix="kodex", key=fid, ext="xls")
    notes = ["Daily creation-unit portfolio (PDF); Samsung gives weights as fractions, converted to percent."]
    if product.get("replication") == "swap":
        notes.append("Synthetic fund: the file lists the swap and collateral, not the index.")
    return IssuerHoldings(as_of, df, url, notes)


register(
    IssuerAdapter(
        name="kodex",
        title="KODEX (Samsung Asset Management)",
        homepage="https://www.samsungfund.com/etf/main.do",
        credit="Holdings: Samsung Asset Management, KODEX daily portfolio deposit files (samsungfund.com).",
        licence=(
            "Public daily PDF files on Samsung Asset Management's KODEX site, free without a "
            "log-in; robots.txt allows /etf. Terms of reuse have not been reviewed; files are "
            "fetched at most once per fund per day."
        ),
        regions=("KR",),
        products=products,
        holdings=holdings,
        frequency="daily",
    )
)
