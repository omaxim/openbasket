"""Fund and bond directory: who issued an ISIN, where it is domiciled and listed.

The directory does not hold any holdings. It answers, for an ISIN nobody has
seen yet, "which issuer adapter should look this up?", and it lets coverage
be reported honestly: *fund known, no holdings source*.

It is built from public exchange and regulator lists, each with its own
credit line and as-of date:

``six_funds`` / ``six_bonds``
    SIX Swiss Exchange reference data API (``/fqs/ref.json``): every fund,
    ETF and bond listed in Zurich, with issuer, domicile and replication.
``xetra``
    Deutsche Boerse "all tradable instruments" CSV for Xetra (ETFs, ETNs, ETCs).
``euronext_etfs`` / ``euronext_bonds``
    Euronext product directory CSV downloads (all Euronext markets).
``ecb_eligible``
    ECB list of eligible marketable assets (EUR bonds, with issuer residence).
``esma_firds_funds`` / ``esma_firds_debt``
    ESMA FIRDS weekly full files ``FULINS_C`` (fund units) and ``FULINS_D``
    (debt): every instrument traded on an EU venue, with issuer LEI and venues.
``fca_firds_funds``
    The UK FCA's FIRDS copy (London and other UK venues). Off by default: see
    the FCA terms in the docs.
``gleif``
    GLEIF ISIN-to-LEI file, the level 2 relationship file (fund manager and
    umbrella fund of every fund LEI) and the LEI records API for names (CC0).

Every parser returns a frame with :data:`FUND_LISTING_COLUMNS` or
:data:`BOND_LISTING_COLUMNS`. :func:`build_fund_directory` and
:func:`build_bond_directory` merge them, taking each field from the best
source that has it and recording which one in ``<field>_source``.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from .. import http
from ..identifiers import is_valid_isin

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DirectorySource:
    key: str
    title: str
    homepage: str
    credit: str
    licence: str
    frequency: str
    #: "public": the licence allows republishing the fields kept; "local_only":
    #: the list is read for the person running openbasket only (see
    #: docs/source/publishing.md). The public build reads public sources only.
    redistribution: str = "local_only"


SIX_FQS = "https://www.six-group.com/fqs/ref.json"
SIX_FUND_FIELDS = (
    "ISIN,ValorNumber,ValorSymbol,FundLongName,IssuerNameFull,LegalStructureCountryCode,"
    "AssetClassCode,AssetClassDesc,ReplicationMethodDesc,FundUnderlyingDescription,"
    "FundCurrency,TradingBaseCurrency,ProductLine,ProductLineDesc,ManagementFee,FirstTradingDate"
)
SIX_BOND_FIELDS = (
    "ISIN,ValorNumber,ShortName,SecDescriptionFull,IssuerNameFull,IssueCurrencyCode,"
    "MaturityDate,CouponRate,AmountInIssue,IndustrySectorDesc,ProductLine"
)
SIX_FUNDS_URL = f"{SIX_FQS}?select={SIX_FUND_FIELDS}&where=PortalSegment=FU&orderby=ISIN&page=1&pagesize=99999"
SIX_BONDS_URL = f"{SIX_FQS}?select={SIX_BOND_FIELDS}&where=PortalSegment=BO&orderby=ISIN&page=1&pagesize=99999"

XETRA_PAGE = "https://www.cashmarket.deutsche-boerse.com/cash-en/trading/Tradable-Instruments-Xetra/"
XETRA_HOST = "https://www.cashmarket.deutsche-boerse.com"

EURONEXT_HOST = "https://live.euronext.com"
EURONEXT_PAGES = {
    "etfs": EURONEXT_HOST + "/en/products/etfs/list",
    "bonds": EURONEXT_HOST + "/en/products/fixed-income/list",
}
EURONEXT_BODY = {
    "args[fe_type]": "csv",
    "args[fe_decimal]": ".",
    "args[fe_date_format]": "d/m/Y",
    "args[fe_layout]": "hor",
}

ECB_PAGE = "https://www.ecb.europa.eu/mopo/coll/assets/html/list-MID.en.html"
ECB_HOST = "https://www.ecb.europa.eu"

ESMA_FIRDS_INDEX = "https://registers.esma.europa.eu/solr/esma_registers_firds_files/select"
FCA_FIRDS_INDEX = "https://api.data.fca.org.uk/fca_data_firds_files"

GLEIF_ISIN_LEI = "https://mapping.gleif.org/api/v2/isin-lei/latest"
GLEIF_GOLDEN_COPY = "https://goldencopy.gleif.org/api/v2/golden-copies/publishes/latest"
GLEIF_API = "https://api.gleif.org/api/v1/lei-records"

SOURCES: dict[str, DirectorySource] = {
    s.key: s
    for s in [
        DirectorySource(
            "six_funds", "SIX Swiss Exchange: funds and ETFs", "https://www.six-group.com/en/market-data/etf/etf-explorer.html",
            "Reference data: SIX Swiss Exchange, (c) SIX Group Ltd",
            "Public website data API, no login. Responses carry '(c) Copyright by SIX Group Ltd. All rights reserved.' "
            "Only reference fields are kept (no prices).",
            "daily",
        ),
        DirectorySource(
            "six_bonds", "SIX Swiss Exchange: bonds", "https://www.six-group.com/en/market-data/bonds/bond-explorer.html",
            "Reference data: SIX Swiss Exchange, (c) SIX Group Ltd",
            "Public website data API, no login. Responses carry '(c) Copyright by SIX Group Ltd. All rights reserved.' "
            "Only reference fields are kept (no prices).",
            "daily",
        ),
        DirectorySource(
            "xetra", "Deutsche Boerse Xetra: all tradable instruments", XETRA_PAGE,
            "Instrument list: Deutsche Boerse AG (Xetra)",
            "Public CSV download, no login; no explicit licence stated. Deutsche Boerse website terms apply.",
            "daily",
        ),
        DirectorySource(
            "euronext_etfs", "Euronext: ETF and ETP list", EURONEXT_PAGES["etfs"],
            "Product directory: Euronext N.V.",
            "Public CSV download, no login; Euronext website terms apply. Price columns are dropped.",
            "daily",
        ),
        DirectorySource(
            "euronext_bonds", "Euronext: bond list", EURONEXT_PAGES["bonds"],
            "Product directory: Euronext N.V.",
            "Public CSV download, no login; Euronext website terms apply. Price columns are dropped.",
            "daily",
        ),
        DirectorySource(
            "ecb_eligible", "ECB eligible marketable assets", ECB_PAGE,
            "Source: European Central Bank, list of eligible marketable assets",
            "Public ECB download, no login. ECB copyright notice: reproduction is permitted provided the source is "
            "acknowledged; cite the ECB as the source.",
            "daily",
            "public",
        ),
        DirectorySource(
            "esma_firds_funds", "ESMA FIRDS: fund units (FULINS_C)", "https://registers.esma.europa.eu/publication/searchRegister?core=esma_registers_firds",
            "Source: ESMA Financial Instruments Reference Data System (FIRDS)",
            "Public ESMA bulk files. ESMA legal notice: reproduction is authorised provided the source is acknowledged.",
            "weekly",
            "public",
        ),
        DirectorySource(
            "esma_firds_debt", "ESMA FIRDS: debt instruments (FULINS_D)", "https://registers.esma.europa.eu/publication/searchRegister?core=esma_registers_firds",
            "Source: ESMA Financial Instruments Reference Data System (FIRDS)",
            "Public ESMA bulk files. ESMA legal notice: reproduction is authorised provided the source is acknowledged.",
            "weekly",
            "public",
        ),
        DirectorySource(
            "fca_firds_funds", "FCA FIRDS: fund units (FULINS_C)", "https://data.fca.org.uk/#/firds",
            "Source: Financial Conduct Authority (FCA) FIRDS, (c) The Financial Conduct Authority",
            "Public download, no login. The FCA website terms (clauses 3.3, 3.4) restrict storing its material in other "
            "websites or data feeds without written permission, so this source is off by default.",
            "weekly",
        ),
        DirectorySource(
            "gleif", "GLEIF: ISIN-LEI, relationships, LEI records", "https://www.gleif.org/en/lei-data/gleif-golden-copy",
            "LEI data: Global Legal Entity Identifier Foundation (GLEIF), CC0",
            "CC0 1.0 (GLEIF LEI data terms of use).",
            "daily",
            "public",
        ),
    ]
}


# --------------------------------------------------------------------------
# Output contracts
# --------------------------------------------------------------------------

FUND_LISTING_COLUMNS = [
    "isin", "name", "issuer_name", "domicile", "currency", "asset_class", "replication",
    "instrument_type", "management", "ticker", "venues", "fund_lei", "as_of", "source", "source_url",
]

BOND_LISTING_COLUMNS = [
    "isin", "name", "issuer_name", "issuer_lei", "issuer_country", "currency", "maturity",
    "coupon_pct", "amount_issued", "bond_type", "venues", "as_of", "source", "source_url",
]

FUND_DIRECTORY_COLUMNS = [
    "isin", "name", "issuer", "issuer_name", "domicile", "currency", "asset_class", "replication",
    "instrument_type", "management", "ticker", "venues", "fund_lei", "manager_lei", "manager_name",
    "umbrella_lei", "umbrella_name", "adapter", "holdings_route",
    "name_source", "issuer_source", "issuer_name_source", "domicile_source", "currency_source",
    "asset_class_source", "replication_source", "instrument_type_source", "fund_lei_source", "manager_source",
    "listed_in", "source", "source_url", "credit", "as_of",
]

BOND_DIRECTORY_COLUMNS = [
    "isin", "name", "issuer_name", "issuer_lei", "issuer_country", "currency", "maturity", "coupon_pct",
    "amount_issued", "bond_type", "venues",
    "name_source", "issuer_name_source", "issuer_lei_source", "currency_source", "maturity_source", "coupon_source",
    "listed_in", "source", "source_url", "credit", "as_of",
]


def _frame(rows: list[dict] | pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows) if not isinstance(rows, pd.DataFrame) else rows.copy()
    for c in columns:
        if c not in df.columns:
            df[c] = None
    df = df[columns]
    df = df[df["isin"].map(is_valid_isin)]
    for c in ("coupon_pct", "amount_issued"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.astype(object).where(df.notna(), None).reset_index(drop=True)


def _clean(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        if value in ("", "-", "Unknown", "unknown", "n/a", "N/A"):
            return None
    return value


def _join(values: Iterable[object]) -> str | None:
    out: list[str] = []
    for v in values:
        for part in str(v).split(";") if v is not None else []:
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return ";".join(sorted(out)) or None


def _yyyymmdd(value: object) -> str | None:
    value = _clean(value)
    if value is None:
        return None
    s = str(int(value)) if isinstance(value, (int, float)) else str(value)
    try:
        return datetime.strptime(s[:8], "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _dmy(value: object) -> str | None:
    value = _clean(value)
    if value is None:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%Y %H:%M:%S", "%d.%m.%Y", "%d %b %Y"):
        try:
            return datetime.strptime(str(value), fmt).date().isoformat()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# Cache: a raw response is written only after it parsed
# --------------------------------------------------------------------------


def cached(
    cache_dir: str | Path | None,
    key: str,
    suffix: str,
    fetch: Callable[[], bytes],
    parse: Callable[[bytes], pd.DataFrame],
    day: date | None = None,
    state: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Parse today's cached copy of a download, or fetch, parse and then cache it.

    ``state`` is what ``fetch`` learned along the way (the resolved file URL,
    which carries the publication date). It is saved next to the cached file
    and restored on a cache hit, so ``parse`` sees the same values either way.
    """
    path = Path(cache_dir) / key / f"{(day or date.today()).isoformat()}{suffix}" if cache_dir else None
    meta = path.with_name(path.name + ".json") if path is not None else None
    if path is not None and path.exists():
        if state is not None and meta.exists():
            state.update(json.loads(meta.read_text()))
        return parse(path.read_bytes())
    content = fetch()
    out = parse(content)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if state:
            meta.write_text(json.dumps(state))
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(path)
    return out


def _get_bytes(url: str, **kw) -> bytes:
    r = http.get(url, timeout=kw.pop("timeout", 300), **kw)
    r.raise_for_status()
    return r.content


# --------------------------------------------------------------------------
# SIX Swiss Exchange
# --------------------------------------------------------------------------

SIX_REPLICATION = {"physical": "physical", "synthetic": "swap"}
SIX_PRODUCT_LINE = {"ET": "ETF", "PF": "fund", "IF": "fund"}
SIX_ASSET_CLASS = {
    "FIXED_INCOME": "fixed_income",
    "MONEY_MARKET": "money_market",
    "COMMODITIES": "commodity",
    "REAL_ESTATE": "real_estate",
    "LOANS": "fixed_income",
    "VOLATILITY": "alternative",
    "FUNDS": "multi_asset",
    "OTHER": "other",
}


def _six_rows(payload: bytes | dict) -> tuple[list[dict], str | None]:
    j = json.loads(payload) if isinstance(payload, (bytes, str)) else payload
    if "colNames" not in j or "rowData" not in j:
        raise ValueError("SIX response has no colNames/rowData")
    cols = j["colNames"]
    as_of = _yyyymmdd(str(j.get("delayedDateTime") or "")[:8])
    return [dict(zip(cols, r)) for r in j["rowData"]], as_of


def parse_six_funds(payload: bytes | dict, url: str = SIX_FUNDS_URL) -> pd.DataFrame:
    rows, as_of = _six_rows(payload)
    out = []
    for r in rows:
        code = str(r.get("AssetClassCode") or "")
        asset = "equity" if code.startswith("EQUITY") else SIX_ASSET_CLASS.get(code)
        repl = str(_clean(r.get("ReplicationMethodDesc")) or "").lower()
        out.append(
            {
                "isin": r.get("ISIN"),
                "name": _clean(r.get("FundLongName")),
                "issuer_name": _clean(r.get("IssuerNameFull")),
                "domicile": _clean(r.get("LegalStructureCountryCode")),
                "currency": _clean(r.get("FundCurrency")),
                "asset_class": asset,
                "replication": SIX_REPLICATION.get(repl),
                "instrument_type": SIX_PRODUCT_LINE.get(r.get("ProductLine"), "fund"),
                "ticker": _clean(r.get("ValorSymbol")),
                "venues": "XSWX",
                "as_of": as_of,
                "source": "six_funds",
                "source_url": url,
            }
        )
    # A fund traded in two currencies on SIX appears twice; the fields kept are the same.
    return _frame(out, FUND_LISTING_COLUMNS).drop_duplicates("isin").reset_index(drop=True)


def parse_six_bonds(payload: bytes | dict, url: str = SIX_BONDS_URL) -> pd.DataFrame:
    rows, as_of = _six_rows(payload)
    out = [
        {
            "isin": r.get("ISIN"),
            "name": _clean(r.get("ShortName")),
            "issuer_name": _clean(r.get("IssuerNameFull")) or _clean(r.get("SecDescriptionFull")),
            "currency": _clean(r.get("IssueCurrencyCode")),
            "maturity": _yyyymmdd(r.get("MaturityDate")),
            "coupon_pct": _clean(r.get("CouponRate")),
            "amount_issued": _clean(r.get("AmountInIssue")),
            "bond_type": _clean(r.get("IndustrySectorDesc")),
            "venues": "XSWX",
            "as_of": as_of,
            "source": "six_bonds",
            "source_url": url,
        }
        for r in rows
    ]
    return _frame(out, BOND_LISTING_COLUMNS).drop_duplicates("isin").reset_index(drop=True)


def six_funds(cache_dir: str | Path | None = None) -> pd.DataFrame:
    return cached(cache_dir, "six_funds", ".json", lambda: _get_bytes(SIX_FUNDS_URL), parse_six_funds)


def six_bonds(cache_dir: str | Path | None = None) -> pd.DataFrame:
    return cached(cache_dir, "six_bonds", ".json", lambda: _get_bytes(SIX_BONDS_URL), parse_six_bonds)


# --------------------------------------------------------------------------
# Deutsche Boerse Xetra
# --------------------------------------------------------------------------

XETRA_TYPES = {"ETF": "ETF", "ETN": "ETN", "ETC": "ETC"}


def xetra_csv_url(page_html: str) -> str:
    m = re.search(r'href="([^"]*t7-xetr-allTradableInstruments\.csv)"', page_html)
    if not m:
        raise ValueError("no allTradableInstruments CSV link on the Xetra page")
    href = m.group(1)
    return href if href.startswith("http") else XETRA_HOST + href


def parse_xetra(content: bytes, url: str = XETRA_PAGE) -> pd.DataFrame:
    text = content.decode("utf-8-sig")
    head = text.splitlines()[:2]
    as_of = _dmy(head[1].split(";")[1]) if len(head) > 1 and ";" in head[1] else None
    df = pd.read_csv(io.StringIO(text), sep=";", skiprows=2, dtype=str)
    if "ISIN" not in df.columns or "Instrument Type" not in df.columns:
        raise ValueError("Xetra CSV is missing ISIN / Instrument Type")
    df = df[df["Instrument Type"].isin(list(XETRA_TYPES))]
    group = df.get("Product Assignment Group Description", pd.Series("", index=df.index)).fillna("").str.upper()
    out = pd.DataFrame(
        {
            "isin": df["ISIN"],
            "name": df["Instrument"].map(_clean),
            "currency": df.get("Currency"),
            "asset_class": group.map(lambda g: "fixed_income" if "RENTEN" in g else None),
            "management": group.map(lambda g: "active" if "AKTIV" in g else ("passive" if "PASSIV" in g else None)),
            "instrument_type": df["Instrument Type"].map(XETRA_TYPES),
            "ticker": df.get("Mnemonic"),
            "venues": "XETR",
            "as_of": as_of,
            "source": "xetra",
            "source_url": url,
        }
    )
    return _frame(out, FUND_LISTING_COLUMNS).drop_duplicates("isin").reset_index(drop=True)


def xetra(cache_dir: str | Path | None = None) -> pd.DataFrame:
    state: dict[str, str] = {}

    def fetch() -> bytes:
        page = http.get(XETRA_PAGE)
        page.raise_for_status()
        state["url"] = xetra_csv_url(page.text)
        return _get_bytes(state["url"])

    return cached(cache_dir, "xetra", ".csv", fetch, lambda b: parse_xetra(b, state.get("url", XETRA_PAGE)), state=state)


# --------------------------------------------------------------------------
# Euronext
# --------------------------------------------------------------------------

EURONEXT_MIC = {
    "ETF Plus": "ETFP",
    "Euronext Paris": "XPAR",
    "Euronext Amsterdam": "XAMS",
    "Euronext Brussels": "XBRU",
    "Euronext Lisbon": "XLIS",
    "Euronext Dublin": "XMSM",
    "Oslo Børs": "XOSL",
    "EuroTLX": "ETLX",
    "MOT": "MOTX",
    "Euronext Access Milan": "XMOT",
    "Euronext Growth Paris": "ALXP",
    "Euronext Access Paris": "XMLI",
    "Euronext Growth Brussels": "ALXB",
    "Euronext Access Brussels": "MLXB",
    "Euronext Access Lisbon": "ENXL",
    "Euronext Growth Milan": "EXGM",
    "Nordic Alternative Bond Market": "XOAM",
    "Euronext Expert Market": "VPXB",
}


def euronext_venues(market: object) -> str | None:
    """'Euronext Amsterdam, Paris' -> 'XAMS;XPAR'. Unknown market names are kept as text."""
    market = _clean(market)
    if market is None:
        return None
    market = str(market).replace(" - Multi-currency Trading", "")
    parts = [p.strip() for p in market.split(",")]
    out = []
    for i, p in enumerate(parts):
        if i and p and not p.startswith("Euronext") and f"Euronext {p}" in EURONEXT_MIC:
            p = f"Euronext {p}"
        out.append(EURONEXT_MIC.get(p, p))
    return _join(out)


def euronext_download_url(page_html: str) -> str:
    m = re.search(r'"jsongateway_download"\s*:\s*"([^"]+)"', page_html)
    if not m:
        raise ValueError("no jsongateway_download link on the Euronext list page")
    return EURONEXT_HOST + "/en" + json.loads(f'"{m.group(1)}"')


def _euronext_frame(content: bytes) -> tuple[pd.DataFrame, str | None]:
    text = content.decode("utf-8-sig")
    lines = text.splitlines()
    as_of = _dmy(lines[2].strip().strip('"')) if len(lines) > 2 else None
    df = pd.read_csv(io.StringIO(text), sep=";", skiprows=[1, 2, 3], dtype=str)
    if "ISIN" not in df.columns:
        raise ValueError("Euronext CSV has no ISIN column")
    return df, as_of


def _etp_type(name: object) -> str:
    n = str(name or "").upper()
    if re.search(r"\bETF\b|\bUCITS\b|\bFUND\b|\bTRACKER\b", n):
        return "ETF"
    if re.search(r"\bETC\b", n):
        return "ETC"
    if re.search(r"\bETN\b|\bETP\b|SECURITIES|\bNOTES?\b", n):
        return "ETN"
    return "ETP"


def parse_euronext_etfs(content: bytes, url: str = EURONEXT_PAGES["etfs"]) -> pd.DataFrame:
    df, as_of = _euronext_frame(content)
    full = df["Instrument Fullname"] if "Instrument Fullname" in df else df.get("Name")
    df = pd.DataFrame(
        {
            "isin": df["ISIN"],
            "name": full.map(_clean),
            "currency": df.get("Currency"),
            "ticker": df.get("Symbol"),
            "venues": df.get("Market").map(euronext_venues),
        }
    )
    g = df.groupby("isin", sort=False)
    out = g.agg({"name": "first", "currency": "first", "ticker": "first", "venues": _join}).reset_index()
    out["instrument_type"] = out["name"].map(_etp_type)
    out["as_of"], out["source"], out["source_url"] = as_of, "euronext_etfs", url
    return _frame(out, FUND_LISTING_COLUMNS)


def parse_euronext_bonds(content: bytes, url: str = EURONEXT_PAGES["bonds"]) -> pd.DataFrame:
    df, as_of = _euronext_frame(content)
    df = pd.DataFrame(
        {
            "isin": df["ISIN"],
            "name": df["Name"].map(_clean),
            "issuer_name": df.get("Issuer").map(_clean),
            "maturity": df.get("Maturity").map(_dmy),
            "venues": df.get("Market").map(euronext_venues),
        }
    )
    g = df.groupby("isin", sort=False)
    out = g.agg({"name": "first", "issuer_name": "first", "maturity": "first", "venues": _join}).reset_index()
    out["as_of"], out["source"], out["source_url"] = as_of, "euronext_bonds", url
    return _frame(out, BOND_LISTING_COLUMNS)


def _euronext(kind: str, cache_dir: str | Path | None, parse: Callable[[bytes, str], pd.DataFrame]) -> pd.DataFrame:
    page_url = EURONEXT_PAGES[kind]

    def fetch() -> bytes:
        page = http.get(page_url)
        page.raise_for_status()
        r = http.post(euronext_download_url(page.text), data=EURONEXT_BODY, timeout=300)
        r.raise_for_status()
        return r.content

    return cached(cache_dir, f"euronext_{kind}", ".csv", fetch, lambda b: parse(b, page_url))


def euronext_etfs(cache_dir: str | Path | None = None) -> pd.DataFrame:
    return _euronext("etfs", cache_dir, parse_euronext_etfs)


def euronext_bonds(cache_dir: str | Path | None = None) -> pd.DataFrame:
    return _euronext("bonds", cache_dir, parse_euronext_bonds)


# --------------------------------------------------------------------------
# ECB eligible marketable assets
# --------------------------------------------------------------------------


def ecb_csv_url(page_html: str) -> str:
    links = sorted(set(re.findall(r'href="([^"]*ea_MID/ea_csv_(\d{6})\.csv\.gz)"', page_html)), key=lambda t: t[1])
    if not links:
        raise ValueError("no ea_csv_<date>.csv.gz link on the ECB page")
    href = links[-1][0]
    return href if href.startswith("http") else ECB_HOST + href


def parse_ecb_eligible(content: bytes, url: str = ECB_PAGE) -> pd.DataFrame:
    raw = gzip.decompress(content) if content[:2] == b"\x1f\x8b" else content
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
    df = pd.read_csv(io.StringIO(text), sep="\t", dtype=str)
    if "ISIN_CODE" not in df.columns:
        raise ValueError("ECB file has no ISIN_CODE column")
    m = re.search(r"ea_csv_(\d{6})", url)
    as_of = datetime.strptime(m.group(1), "%y%m%d").date().isoformat() if m else None
    residence = df.get("ISSUER_RESIDENCE", pd.Series(None, index=df.index)).fillna("")
    out = pd.DataFrame(
        {
            "isin": df["ISIN_CODE"].str.strip(),
            "issuer_name": df.get("ISSUER_NAME").map(_clean),
            "issuer_country": residence.map(lambda v: v[2:4] if v.startswith("IR") and len(v) >= 4 else None),
            "currency": df.get("DENOMINATION").map(_clean),
            "maturity": df.get("MATURITY_DATE").map(lambda v: _dmy(str(v)[:10]) if isinstance(v, str) else None),
            "coupon_pct": pd.to_numeric(df.get("COUPON_RATE (%)"), errors="coerce"),
            "bond_type": df.get("TYPE").map(_clean),
            "as_of": as_of,
            "source": "ecb_eligible",
            "source_url": url,
        }
    )
    return _frame(out, BOND_LISTING_COLUMNS).drop_duplicates("isin").reset_index(drop=True)


def ecb_eligible(cache_dir: str | Path | None = None) -> pd.DataFrame:
    # ecb.europa.eu asks for Crawl-delay: 5 in robots.txt.
    http.set_min_interval("www.ecb.europa.eu", 5.0)
    state: dict[str, str] = {}

    def fetch() -> bytes:
        page = http.get(ECB_PAGE)
        page.raise_for_status()
        state["url"] = ecb_csv_url(page.text)
        return _get_bytes(state["url"])

    return cached(
        cache_dir, "ecb_eligible", ".csv.gz", fetch, lambda b: parse_ecb_eligible(b, state.get("url", ECB_PAGE)), state=state
    )


# --------------------------------------------------------------------------
# FIRDS (ESMA and FCA)
# --------------------------------------------------------------------------

#: ISO 10962 (CFI) category of collective investment vehicles.
CFI_FUND_TYPE = {
    "CI": "fund", "CE": "ETF", "CB": "REIT", "CH": "hedge_fund", "CF": "fund_of_funds",
    "CS": "pension_fund", "CP": "private_equity_fund", "CM": "other_fund",
}
#: Debt CFI groups kept as bonds: structured products (DE, DS) are left out.
CFI_BOND_GROUPS = {"DB", "DC", "DW", "DT", "DY", "DG", "DA", "DN", "DD", "DM"}


def firds_files(index_url: str, kind: str, prefix: str, today: date | None = None, days: int = 14) -> list[dict]:
    """Latest weekly full-file set (``FULINS_<prefix>_*``) listed by the ESMA or FCA index."""
    today = today or date.today()
    start = (today - timedelta(days=days)).isoformat()
    if kind == "esma":
        r = http.get(
            index_url,
            params={
                "q": "*",
                "fq": f"publication_date:[{start}T00:00:00Z TO {today.isoformat()}T23:59:59Z]",
                "wt": "json",
                "rows": 500,
            },
        )
        r.raise_for_status()
        docs = r.json()["response"]["docs"]
    else:
        q = f"((file_type:FULINS) AND (publication_date:[{start} TO {today.isoformat()}]))"
        r = http.get(index_url, params={"q": q, "from": 0, "size": 500})
        r.raise_for_status()
        docs = [h["_source"] for h in r.json()["hits"]["hits"]]
    docs = [d for d in docs if str(d.get("file_name", "")).startswith(f"FULINS_{prefix}_")]
    if not docs:
        return []
    latest = max(str(d["publication_date"])[:10] for d in docs)
    return sorted((d for d in docs if str(d["publication_date"])[:10] == latest), key=lambda d: d["file_name"])


def _firds_records(content: bytes) -> Iterable[dict]:
    """Stream one FIRDS full-file zip, yielding one dict per RefData record."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
        with z.open(name) as f:
            ns = ""
            for _, el in ET.iterparse(f, events=("end",)):
                if not el.tag.endswith("RefData"):
                    continue
                if not ns and "}" in el.tag:
                    ns = el.tag.split("}")[0] + "}"
                g = el.find(f"{ns}FinInstrmGnlAttrbts")
                if g is None:
                    el.clear()
                    continue
                rec = {
                    "isin": g.findtext(f"{ns}Id"),
                    "name": g.findtext(f"{ns}FullNm"),
                    "cfi": g.findtext(f"{ns}ClssfctnTp") or "",
                    "currency": g.findtext(f"{ns}NtnlCcy"),
                    "lei": el.findtext(f"{ns}Issr"),
                    "mic": el.findtext(f"{ns}TradgVnRltdAttrbts/{ns}Id"),
                }
                d = el.find(f"{ns}DebtInstrmAttrbts")
                if d is not None:
                    rec["maturity"] = d.findtext(f"{ns}MtrtyDt")
                    rec["amount_issued"] = d.findtext(f"{ns}TtlIssdNmnlAmt")
                    rec["coupon_pct"] = d.findtext(f"{ns}IntrstRate/{ns}Fxd")
                    rec["seniority"] = d.findtext(f"{ns}DebtSnrty")
                yield rec
                el.clear()


def parse_firds_funds(content: bytes, source: str, url: str, as_of: str | None) -> pd.DataFrame:
    """One row per fund ISIN from a ``FULINS_C`` file, with every venue it trades on."""
    by_isin: dict[str, dict] = {}
    for rec in _firds_records(content):
        cfi = rec["cfi"]
        if not cfi.startswith("C"):
            continue
        row = by_isin.get(rec["isin"])
        if row is None:
            asset = {"C": "commodity", "R": "real_estate"}.get(cfi[4:5]) if cfi[:2] in ("CI", "CE") else None
            row = by_isin[rec["isin"]] = {
                "isin": rec["isin"],
                "name": _clean(rec["name"]),
                "currency": rec["currency"],
                "asset_class": "real_estate" if cfi[:2] == "CB" else asset,
                "instrument_type": CFI_FUND_TYPE.get(cfi[:2], "other_fund"),
                "fund_lei": rec["lei"],
                "venues": set(),
            }
        if rec["mic"]:
            row["venues"].add(rec["mic"])
    rows = list(by_isin.values())
    for r in rows:
        r["venues"] = ";".join(sorted(r["venues"])) or None
    out = pd.DataFrame(rows)
    out["as_of"], out["source"], out["source_url"] = as_of, source, url
    return _frame(out, FUND_LISTING_COLUMNS)


def parse_firds_debt(content: bytes, source: str, url: str, as_of: str | None) -> pd.DataFrame:
    """One row per bond ISIN from a ``FULINS_D`` file (structured products left out)."""
    by_isin: dict[str, dict] = {}
    for rec in _firds_records(content):
        if rec["cfi"][:2] not in CFI_BOND_GROUPS:
            continue
        row = by_isin.get(rec["isin"])
        if row is None:
            row = by_isin[rec["isin"]] = {
                "isin": rec["isin"],
                "name": _clean(rec["name"]),
                "issuer_lei": rec["lei"],
                "currency": rec["currency"],
                "maturity": (rec.get("maturity") or "")[:10] or None,
                "coupon_pct": pd.to_numeric(rec.get("coupon_pct"), errors="coerce"),
                "amount_issued": pd.to_numeric(rec.get("amount_issued"), errors="coerce"),
                "bond_type": rec["cfi"],
                "venues": set(),
            }
        if rec["mic"]:
            row["venues"].add(rec["mic"])
    rows = list(by_isin.values())
    for r in rows:
        r["venues"] = ";".join(sorted(r["venues"])) or None
    out = pd.DataFrame(rows, columns=["isin", "name", "issuer_lei", "currency", "maturity", "coupon_pct", "amount_issued", "bond_type", "venues"])
    out["as_of"], out["source"], out["source_url"] = as_of, source, url
    return _frame(out, BOND_LISTING_COLUMNS)


def firds(
    regulator: str,
    prefix: str,
    cache_dir: str | Path | None = None,
    today: date | None = None,
) -> pd.DataFrame:
    """Fund units (prefix ``C``) or debt (``D``) from the latest ESMA or FCA full files."""
    index = ESMA_FIRDS_INDEX if regulator == "esma" else FCA_FIRDS_INDEX
    source = f"{regulator}_firds_{'funds' if prefix == 'C' else 'debt'}"
    parse = parse_firds_funds if prefix == "C" else parse_firds_debt
    files = firds_files(index, regulator, prefix, today=today)
    if not files:
        raise RuntimeError(f"no FULINS_{prefix} files listed by the {regulator.upper()} FIRDS index")
    parts = []
    for f in files:
        as_of = str(f["publication_date"])[:10]
        url = f["download_link"]
        key = f"{source}/{f['file_name'].removesuffix('.zip')}"
        parts.append(
            cached(cache_dir, key, ".zip", lambda u=url: _get_bytes(u, timeout=600),
                   lambda b, u=url, a=as_of: parse(b, source, u, a), day=date.fromisoformat(as_of))
        )
    out = pd.concat(parts, ignore_index=True)
    if prefix == "C":
        # A fund split over two parts keeps its first row, with every venue.
        venues = out.groupby("isin")["venues"].agg(_join)
        out = out.drop_duplicates("isin").set_index("isin")
        out["venues"] = venues
        out = out.reset_index()
    else:
        out = out.drop_duplicates("isin")
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------
# GLEIF
# --------------------------------------------------------------------------

GLEIF_RELATIONSHIPS = {"IS_FUND-MANAGED_BY": "manager_lei", "IS_SUBFUND_OF": "umbrella_lei"}


def parse_gleif_isin_lei(content: bytes, isins: set[str]) -> pd.DataFrame:
    """ISIN -> LEI pairs for the given ISINs from the GLEIF mapping zip."""
    parts = []
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(name) as f:
            for chunk in pd.read_csv(f, dtype=str, chunksize=1_000_000):
                if "ISIN" not in chunk or "LEI" not in chunk:
                    raise ValueError("GLEIF ISIN-LEI file needs LEI and ISIN columns")
                parts.append(chunk[chunk["ISIN"].isin(isins)])
    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["LEI", "ISIN"])
    return out.rename(columns={"ISIN": "isin", "LEI": "lei"})[["isin", "lei"]].drop_duplicates("isin").reset_index(drop=True)


def parse_gleif_relationships(content: bytes, leis: set[str] | None = None) -> pd.DataFrame:
    """Active fund-manager and umbrella-fund links from the GLEIF level 2 (RR) file."""
    cols = {
        "Relationship.StartNode.NodeID": "lei",
        "Relationship.EndNode.NodeID": "parent_lei",
        "Relationship.RelationshipType": "relationship",
        "Relationship.RelationshipStatus": "status",
    }
    parts = []
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(name) as f:
            for chunk in pd.read_csv(f, dtype=str, usecols=list(cols), chunksize=500_000):
                chunk = chunk.rename(columns=cols)
                keep = chunk["relationship"].isin(list(GLEIF_RELATIONSHIPS)) & (chunk["status"] == "ACTIVE")
                if leis is not None:
                    keep &= chunk["lei"].isin(leis)
                parts.append(chunk[keep])
    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=list(cols.values()))
    return out[["lei", "relationship", "parent_lei"]].drop_duplicates(["lei", "relationship"]).reset_index(drop=True)


def gleif_isin_lei(isins: set[str], cache_dir: str | Path | None = None) -> pd.DataFrame:
    def fetch() -> bytes:
        meta = http.get(GLEIF_ISIN_LEI)
        meta.raise_for_status()
        return _get_bytes(meta.json()["data"]["attributes"]["downloadLink"], timeout=900)

    return cached(cache_dir, "gleif_isin_lei", ".zip", fetch, lambda b: parse_gleif_isin_lei(b, isins))


def gleif_relationships(cache_dir: str | Path | None = None) -> pd.DataFrame:
    def fetch() -> bytes:
        meta = http.get(GLEIF_GOLDEN_COPY)
        meta.raise_for_status()
        return _get_bytes(meta.json()["data"]["rr"]["full_file"]["csv"]["url"], timeout=900)

    return cached(cache_dir, "gleif_rr", ".zip", fetch, parse_gleif_relationships)


def parse_gleif_records(payload: dict) -> list[dict]:
    rows = []
    for d in payload.get("data", []):
        a = d.get("attributes", {})
        e = a.get("entity", {})
        rows.append(
            {
                "lei": a.get("lei") or d.get("id"),
                "legal_name": (e.get("legalName") or {}).get("name"),
                "jurisdiction": e.get("jurisdiction"),
                "category": e.get("category"),
            }
        )
    return rows


def gleif_entities(
    leis: Iterable[str],
    cache_dir: str | Path | None = None,
    batch: int = 100,
    max_leis: int | None = None,
    first: Iterable[str] = (),
) -> pd.DataFrame:
    """Legal name, jurisdiction and category for LEIs, from the GLEIF API (batched).

    Results are kept in one Parquet file per day under ``cache_dir``, so a
    rerun only asks for LEIs it has not seen today. When there are more than
    ``max_leis``, the LEIs in ``first`` (managers and umbrellas, which name
    many funds each) are kept before the others, and the number left out is
    logged.
    """
    http.set_min_interval("api.gleif.org", 1.1)  # GLEIF allows about 60 requests a minute
    priority = {l for l in first if isinstance(l, str)}
    want = sorted({l for l in leis if isinstance(l, str) and len(l) == 20}, key=lambda l: (l not in priority, l))
    if max_leis is not None and len(want) > max_leis:
        logger.warning("GLEIF API: %d LEIs wanted, only the first %d are looked up (gleif_api_max_leis)", len(want), max_leis)
        want = want[:max_leis]
    path = Path(cache_dir) / "gleif_entities" / f"{date.today().isoformat()}.parquet" if cache_dir else None
    have = pd.read_parquet(path) if path is not None and path.exists() else pd.DataFrame(columns=["lei", "legal_name", "jurisdiction", "category"])
    todo = [l for l in want if l not in set(have["lei"])]
    rows: list[dict] = []
    failed = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i : i + batch]
        r = http.get(GLEIF_API, params={"filter[lei]": ",".join(chunk), "page[size]": batch, "fields[lei-records]": "lei,entity"})
        if r.status_code != 200:
            logger.warning("GLEIF API %s for a batch of %d LEIs; skipped", r.status_code, len(chunk))
            failed += len(chunk)
            continue
        rows.extend(parse_gleif_records(r.json()))
    if failed:
        logger.warning("GLEIF API: %d of %d LEIs not looked up because their batch failed", failed, len(todo))
    out = pd.concat([have, pd.DataFrame(rows, columns=have.columns)], ignore_index=True).drop_duplicates("lei")
    if path is not None and rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(path, index=False)
    return out[out["lei"].isin(want)].reset_index(drop=True)


# --------------------------------------------------------------------------
# Issuer normalisation
# --------------------------------------------------------------------------

#: (pattern, issuer key). Keys are adapter names where an adapter exists or
#: is planned; other brands get a stable lowercase key so they can be counted.
ISSUER_PATTERNS: list[tuple[str, str]] = [
    (r"\bi\s?shares\b|\bishs\w*|\bblack\s?rock\b|\bbgf\b|\bbsf\b", "ishares"),
    (r"\bspdr\b|\bstate street\b|\bssga\b|\bss\s?spdr\b", "spdr"),
    (r"\bx\s?trackers\b|\bxtr\b|\bdb x-trackers\b|\bdws\b", "xtrackers"),
    (r"\bamundi\b|\bamndi\b|\blyxor\b|\bmulti units (luxembourg|france)\b|\bcpr\b", "amundi"),
    (r"\bvanguard\b|\bvang\b", "vanguard"),
    (r"\bubs\b|\bcredit suisse\b|\bcsif\b", "ubs"),
    (r"\bswisscanto\b|\bz(ü|ue|u)rcher kantonalbank\b|\bzkb\b", "swisscanto"),
    (r"\bj\.?\s?p\.?\s?morgan\b|\bjpmorgan\b|\bjpm\b", "jpmorgan"),
    (r"\bhsbc\b", "hsbc"),
    (r"\binvesco\w*\b|\bpowershares\b|\bivz\b", "invesco"),
    (r"\bwisdom\s?tree\b|\bwitr\b", "wisdomtree"),
    (r"\bvan\s?eck\b|\bvaneck\b", "vaneck"),
    (r"\bfranklin\b|\btempleton\b", "franklin"),
    (r"\bbnp\s?paribas\b|\bbnpp\w*\b|\bbnp\b", "bnpparibas"),
    (r"\blegal\s?(&|and)\s?general\b|\bl\s?[&+]\s?g\b|\blgim\b", "lgim"),
    (r"\bglobal x\b", "globalx"),
    (r"\bdeka\b", "deka"),
    (r"\bfidelity\b|\bfil investment\b", "fidelity"),
    (r"\bpimco\b", "pimco"),
    (r"\bpictet\b", "pictet"),
    (r"\ballianz\b|\bagif\b", "allianz"),
    (r"\bschroders?\b", "schroders"),
    (r"\brobeco\b", "robeco"),
    (r"\bgoldman sachs\b|\bgsam\b", "goldman_sachs"),
    (r"\bfirst trust\b", "first_trust"),
    (r"\bhanetf\b", "hanetf"),
    (r"\bossiam\b", "ossiam"),
    (r"\bvontobel\b", "vontobel"),
    # Raiffeisen Switzerland's funds are run by Vontobel (see build_fund_directory);
    # Raiffeisen Kapitalanlage (Austria) and the German Raiffeisen banks are not.
    (r"\braiffeisen\b", "raiffeisen"),
    (r"\bswiss life\b|\bswisslife\b", "swisslife"),
    (r"\bjulius b(ä|ae|a)r\b", "juliusbaer"),
    (r"\blombard odier\b", "lombard_odier"),
    (r"\bgam\b", "gam"),
    (r"\bbanque cantonale vaudoise\b|\bbcv\b|\bgerifonds\b", "bcv"),
    (r"\bllb\b|\bliechtensteinische landesbank\b", "llb"),
    (r"\b21\s?shares\b", "21shares"),
    (r"\bcoinshares\b", "coinshares"),
    (r"\bgranite\s?shares\b", "graniteshares"),
    (r"\bleverage shares\b", "leverage_shares"),
    (r"\bark\b", "ark"),
    (r"\bproshares\b", "proshares"),
    (r"\bdirexion\b", "direxion"),
    (r"\bschwab\b", "schwab"),
    (r"\bnuveen\b", "nuveen"),
    (r"\bjanus henderson\b", "janus_henderson"),
    (r"\bnordea\b", "nordea"),
    (r"\baxa\b", "axa"),
    (r"\bbarings\b", "barings"),
    (r"\bpgim\b", "pgim"),
    (r"\babrdn|\baberdeen\b", "abrdn"),
    (r"\bfineco\b", "fineco"),
    (r"\bsociete generale\b|\bsg issuer\b", "societe_generale"),
    (r"\bbitwise\b", "bitwise"),
    (r"\bdimensional\b", "dimensional"),
    (r"\berste\b", "erste"),
    (r"\bdanske\b", "danske"),
    (r"\bcolumbia threadneedle\b|\bthreadneedle\b", "columbia_threadneedle"),
    (r"\bjan(us)?\.?\s?hend(erson)?\b", "janus_henderson"),
    (r"\bswissc\.", "swisscanto"),
    (r"\bftif\b|\bftgf\b", "franklin"),
    (r"\bbny\b|\bbny mellon\b", "bny_mellon"),
    (r"\bcandriam\b", "candriam"),
    (r"\bmfs\b", "mfs"),
    (r"\bmetzl(er)?\b", "metzler"),
    (r"\bpolar capital\b", "polar_capital"),
    (r"\bdnb\b", "dnb"),
    (r"\bj\.? safra sarasin\b|\bjss\b", "jsafra_sarasin"),
    (r"\bunion investment\b|\buni(global|rak|rent|fonds|zins|europa|asia|nordamerika|deutschland|institutional)\b", "union_investment"),
    (r"\bnn \(l\)|\bnn investment\b", "nn"),
    (r"\bm&g\b", "mg"),
    (r"\bjupiter\b", "jupiter"),
    (r"\bninety one\b", "ninety_one"),
    (r"\bcarmignac\b", "carmignac"),
    (r"\bcomgest\b", "comgest"),
    (r"\beurizon\b", "eurizon"),
    (r"\bkempen\b", "kempen"),
    (r"\bmirabaud\b", "mirabaud"),
    (r"\bedmond de rothschild\b|\bedr\b", "edmond_de_rothschild"),
    (r"\blazard\b", "lazard"),
    (r"\bnatixis\b|\bostrum\b|\bmirova\b|\bdnca\b", "natixis"),
    (r"\bt\.?\s?rowe\b", "t_rowe_price"),
    (r"\bmorgan stanley\b|\bms invf\b", "morgan_stanley"),
    (r"\bneuberger\b", "neuberger_berman"),
    (r"\bwellington\b", "wellington"),
    (r"\bbaillie gifford\b", "baillie_gifford"),
    (r"\bflossbach\b", "flossbach"),
    (r"\bdje\b", "dje"),
    (r"\bacatis\b", "acatis"),
    (r"\boddo\b", "oddo"),
    (r"\bunion bancaire priv(e|é)e\b|\bubp\b", "ubp"),
    (r"\befg\b", "efg"),
    (r"\bmigros bank\b", "migros_bank"),
    (r"\bsanlam\b", "sanlam"),
    (r"\bcanaccord\b", "canaccord"),
    (r"\bmeag\b", "meag"),
    (r"\blbbw\b", "lbbw"),
    (r"\bsauren\b", "sauren"),
    (r"\bneos\b", "neos"),
    (r"\bpurpose\b", "purpose"),
    (r"\bmuzinich\b", "muzinich"),
    (r"\bnomura\b", "nomura"),
    (r"\bpayden\b", "payden"),
    (r"\bab\s?sicav\b|\balliancebernstein\b", "alliancebernstein"),
    (r"\bwealth\s?shares\b", "wealthshares"),
    (r"\bvalour\b", "valour"),
    (r"\bvirtune\b", "virtune"),
    (r"\bincome\s?shares\b", "incomeshares"),
    (r"\bdefiance\b", "defiance"),
    (r"\binnovator\b", "innovator"),
    (r"\btradr\b", "tradr"),
    (r"\bbankinvest\b", "bankinvest"),
    (r"\bkrane\s?shares\b|\bkrane funds\b", "kraneshares"),
    (r"\baviva\b", "aviva"),
    (r"\bsydinvest\b|\bsyd fund\b", "sydinvest"),
    (r"\bkepler\b", "kepler"),
    (r"\broundhill\b", "roundhill"),
    (r"\byieldmax\b", "yieldmax"),
    (r"\bsparinv(est)?\b|\bsparindex\b", "sparinvest"),
    (r"\bmatthews\b", "matthews"),
    (r"\bamplify\b", "amplify"),
    (r"\bsprott\b", "sprott"),
    (r"\bpacer\b", "pacer"),
    (r"\bsimplify\b", "simplify"),
    (r"\bseb\b", "seb"),
    (r"\bbmo\b", "bmo"),
    (r"\bavantis\b", "avantis"),
    (r"\bmercer\b", "mercer"),
    (r"\bdpam\b", "dpam"),
    (r"\bbellevue\b", "bellevue"),
    (r"\bnykredit\b", "nykredit"),
    (r"\bman (ahl|group|glg)\b", "man_group"),
    (r"\bpartners group\b", "partners_group"),
]
#: Abbreviations at the start of exchange short names (Xetra, FIRDS), e.g.
#: "XT MSCI WORLD", "AIS-AM.MSCI EM", "WT ...". Tried only at the start of a
#: text, and only when no full brand name matched.
ABBREVIATION_PATTERNS: list[tuple[str, str]] = [
    (r"^(ish\b|is\s?(ii|iii|iv|v|vi|vii)\b)", "ishares"),
    (r"^(xt\b|x\(ie\)|x\s)", "xtrackers"),
    (r"^(ais\b|mul\b|muf\b|am\.|am\s)", "amundi"),
    (r"^wt\b", "wisdomtree"),
    (r"^dk\b", "deka"),
    (r"^fid\d?\b", "fidelity"),
    (r"^oss\b", "ossiam"),
    (r"^(ss\b|ssspdr)", "spdr"),
    (r"^(im\d?\b|imii\b)", "invesco"),
    (r"^gs\b", "goldman_sachs"),
    (r"^lg\b", "lgim"),
    (r"^(va\b|van\b)", "vanguard"),
    (r"^sg\b", "societe_generale"),
]
#: Brand keys short or common enough to match other words in a fund name
#: ("EFG-Hermes", "Amova ARK"); a name match is checked against GLEIF's manager.
AMBIGUOUS_KEYS = frozenset({"ark", "gam", "seb", "purpose", "efg", "axa", "neos", "dje", "edmond_de_rothschild", "mfs", "dnb"})
_ISSUER_RES = [(re.compile(p, re.IGNORECASE), key) for p, key in ISSUER_PATTERNS]
_ABBREV_RES = [(re.compile(p, re.IGNORECASE), key) for p, key in ABBREVIATION_PATTERNS]


def issuer_key(*texts: object) -> str | None:
    """Adapter-style issuer key from the first text that names a known brand.

    Within one text the leftmost brand wins, so "iShares J.P. Morgan USD EM
    Bond" is iShares, not J.P. Morgan.
    """
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            continue
        best: tuple[int, str] | None = None
        for rx, key in _ISSUER_RES:
            m = rx.search(text)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), key)
        if best:
            return best[1]
        for rx, key in _ABBREV_RES:
            if rx.search(text.strip()):
                return key
    return None


_SWAP_RE = re.compile(r"\bswap\b|\bsynthetic\b", re.IGNORECASE)
_BOND_RE = re.compile(
    r"\bbonds?\b|\btreasury\b|\bgovt\b|\bgov(ernmen)?t bond|\bcorp(orate)?\b|\bhigh yield\b|\baggregate\b|"
    r"\bfixed income\b|\bbd\b|\bt-bill|\bgilts?\b|\bsovereign\b|\btips\b|\bclo\b",
    re.IGNORECASE,
)


def _name_replication(name: object) -> str | None:
    return "swap" if isinstance(name, str) and _SWAP_RE.search(name) else None


def _name_asset_class(name: object) -> str | None:
    return "fixed_income" if isinstance(name, str) and _BOND_RE.search(name) else None


def _isin_country(isin: str) -> str | None:
    cc = isin[:2]
    return None if cc in ("XS", "XD", "EU", "XC", "QS") else cc


# --------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------

#: Source preference per field, best first.
FUND_PRIORITY = {
    "name": ["six_funds", "euronext_etfs", "esma_firds_funds", "fca_firds_funds", "xetra"],
    "issuer_name": ["six_funds"],
    "domicile": ["six_funds"],
    "currency": ["six_funds", "esma_firds_funds", "fca_firds_funds", "euronext_etfs", "xetra"],
    "asset_class": ["six_funds", "xetra", "esma_firds_funds", "fca_firds_funds"],
    "replication": ["six_funds"],
    "instrument_type": ["six_funds", "xetra", "esma_firds_funds", "fca_firds_funds", "euronext_etfs"],
    "management": ["xetra"],
    "ticker": ["six_funds", "xetra", "euronext_etfs"],
    "fund_lei": ["esma_firds_funds", "fca_firds_funds"],
}

BOND_PRIORITY = {
    "name": ["six_bonds", "euronext_bonds", "esma_firds_debt"],
    "issuer_name": ["six_bonds", "ecb_eligible", "euronext_bonds"],
    "issuer_lei": ["esma_firds_debt"],
    "issuer_country": ["ecb_eligible"],
    "currency": ["six_bonds", "ecb_eligible", "esma_firds_debt"],
    "maturity": ["six_bonds", "ecb_eligible", "euronext_bonds", "esma_firds_debt"],
    "coupon_pct": ["six_bonds", "ecb_eligible", "esma_firds_debt"],
    "amount_issued": ["six_bonds", "esma_firds_debt"],
    "bond_type": ["six_bonds", "ecb_eligible", "esma_firds_debt"],
}


def _pick(listings: pd.DataFrame, field: str, order: list[str]) -> pd.DataFrame:
    """Best value of ``field`` per ISIN, with the source it came from."""
    rank = {s: i for i, s in enumerate(order)}
    sub = listings[listings["source"].isin(rank) & listings[field].notna()][["isin", field, "source"]]
    sub = sub.assign(_r=sub["source"].map(rank)).sort_values(["isin", "_r"]).drop_duplicates("isin")
    return sub.set_index("isin")[[field, "source"]].rename(columns={"source": f"{field}_source"})


def _join_by_isin(listings: pd.DataFrame, column: str) -> pd.Series:
    """Sorted, de-duplicated ';'-joined values of ``column`` per ISIN."""
    s = listings[["isin", column]].dropna()
    s = s.assign(v=s[column].astype(str).str.split(";")).explode("v")
    s["v"] = s["v"].str.strip()
    s = s[s["v"] != ""].drop_duplicates(["isin", "v"]).sort_values(["isin", "v"])
    return s.groupby("isin")["v"].agg(";".join)


def _summary(listings: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=pd.Index(sorted(listings["isin"].unique()), name="isin"))
    out["venues"] = _join_by_isin(listings, "venues")
    out["listed_in"] = _join_by_isin(listings, "source")
    out["as_of"] = listings.groupby("isin")["as_of"].max()
    return out


def _source_urls(out: pd.DataFrame, listings: pd.DataFrame) -> pd.Series:
    urls = listings[["isin", "source", "source_url"]].drop_duplicates(["isin", "source"])
    keyed = out[["source"]].reset_index().merge(urls, on=["isin", "source"], how="left")
    return pd.Series(keyed["source_url"].values, index=out.index)


def _credit(listed_in: str | None) -> str | None:
    if not listed_in:
        return None
    seen: list[str] = []
    for key in listed_in.split(";"):
        c = SOURCES[key].credit if key in SOURCES else key
        if c not in seen:
            seen.append(c)
    return "; ".join(seen)


def build_fund_directory(
    listings: list[pd.DataFrame],
    isin_lei: pd.DataFrame | None = None,
    relationships: pd.DataFrame | None = None,
    entities: pd.DataFrame | None = None,
    adapters: Iterable[str] = (),
    etf_only_adapters: Iterable[str] = (),
    adapter_isins: dict[str, Iterable[str]] | None = None,
) -> pd.DataFrame:
    """Merge fund listings into one row per ISIN with :data:`FUND_DIRECTORY_COLUMNS`.

    ``adapters`` are the registered issuer adapters. ``adapter_isins`` maps an
    adapter to the ISINs it lists (its ``products()``); ``etf_only_adapters``
    are adapters that read exchange-traded funds only.
    """
    frames = [l for l in listings if l is not None and len(l)]
    if not frames:
        return pd.DataFrame(columns=FUND_DIRECTORY_COLUMNS)
    all_ = pd.concat(frames, ignore_index=True)
    all_ = all_[all_["isin"].map(is_valid_isin)]
    out = _summary(all_)
    for field, order in FUND_PRIORITY.items():
        out = out.join(_pick(all_, field, order))

    # Fund LEI: FIRDS reports it as the issuer of the units; GLEIF's ISIN map fills gaps.
    if isin_lei is not None and len(isin_lei):
        gl = isin_lei.drop_duplicates("isin").set_index("isin")["lei"]
        fill = out["fund_lei"].isna() & out.index.isin(gl.index)
        out.loc[fill, "fund_lei"] = gl.reindex(out.index[fill]).values
        out.loc[fill, "fund_lei_source"] = "gleif"

    out["manager_lei"] = out["umbrella_lei"] = out["manager_source"] = None
    if relationships is not None and len(relationships):
        rel = relationships.pivot_table(index="lei", columns="relationship", values="parent_lei", aggfunc="first")
        man = rel.get("IS_FUND-MANAGED_BY", pd.Series(dtype=object))
        umb = rel.get("IS_SUBFUND_OF", pd.Series(dtype=object))
        out["manager_lei"] = out["fund_lei"].map(man)
        out["umbrella_lei"] = out["fund_lei"].map(umb)
        out.loc[out["manager_lei"].notna(), "manager_source"] = "gleif"
        # A sub-fund without its own manager link takes its umbrella's manager.
        via = out["manager_lei"].isna() & out["umbrella_lei"].notna()
        out.loc[via, "manager_lei"] = out.loc[via, "umbrella_lei"].map(man)
        out.loc[via & out["manager_lei"].notna(), "manager_source"] = "gleif_umbrella"

    names = juris = pd.Series(dtype=object)
    if entities is not None and len(entities):
        ent = entities.drop_duplicates("lei").set_index("lei")
        names, juris = ent["legal_name"], ent["jurisdiction"]
    out["manager_name"] = out["manager_lei"].map(names)
    out["umbrella_name"] = out["umbrella_lei"].map(names)
    fund_legal = out["fund_lei"].map(names)
    fund_juris = out["fund_lei"].map(juris)

    # Issuer name: SIX names the umbrella or management company; else GLEIF's manager.
    for col, src in (("manager_name", "gleif"), ("umbrella_name", "gleif")):
        fill = out["issuer_name"].isna() & out[col].notna()
        out.loc[fill, "issuer_name"] = out.loc[fill, col]
        out.loc[fill, "issuer_name_source"] = src

    # Issuer key: brand in the fund's name first (third-party management companies
    # run many brands), then the issuer, manager, umbrella and legal names.
    keys, key_src = [], []
    for name, iname, src_iname, mname, uname, lname in zip(
        out["name"], out["issuer_name"], out["issuer_name_source"], out["manager_name"], out["umbrella_name"], fund_legal
    ):
        for text, src in ((name, "name"), (iname, src_iname), (mname, "gleif"), (uname, "gleif"), (lname, "gleif")):
            k = issuer_key(text)
            if k:
                keys.append(k)
                key_src.append(src)
                break
        else:
            keys.append(None)
            key_src.append(None)
    out["issuer"], out["issuer_source"] = keys, key_src

    # Domicile: SIX, then the fund LEI's legal jurisdiction, then the ISIN prefix.
    gj = fund_juris.map(lambda v: v[:2] if isinstance(v, str) else None)
    fill = out["domicile"].isna() & gj.notna()
    out.loc[fill, "domicile"] = gj[fill]
    out.loc[fill, "domicile_source"] = "gleif"
    pref = pd.Series(out.index.map(_isin_country), index=out.index)
    fill = out["domicile"].isna() & pref.notna()
    out.loc[fill, "domicile"] = pref[fill]
    out.loc[fill, "domicile_source"] = "isin_prefix"

    # Short brand keys can match unrelated words in a fund's name: when GLEIF
    # names the manager, a name match must agree with it.
    for i, (key, src, mname) in enumerate(zip(out["issuer"], out["issuer_source"], out["manager_name"])):
        if key in AMBIGUOUS_KEYS and src == "name" and isinstance(mname, str) and issuer_key(mname) != key:
            out.iat[i, out.columns.get_loc("issuer")] = issuer_key(mname)
            out.iat[i, out.columns.get_loc("issuer_source")] = "gleif" if issuer_key(mname) else None
    # Raiffeisen Switzerland's funds are run by Vontobel; other Raiffeisen funds keep their own key.
    vont = out["issuer"].eq("raiffeisen") & (
        out["domicile"].eq("CH") | out["manager_name"].map(lambda v: issuer_key(v) == "vontobel")
    )
    out.loc[vont, "issuer"] = "vontobel"

    # Replication and asset class from the name where no source states them.
    for field, fn in (("replication", _name_replication), ("asset_class", _name_asset_class)):
        guess = out["name"].map(fn)
        fill = out[field].isna() & guess.notna()
        out.loc[fill, field] = guess[fill]
        out.loc[fill, f"{field}_source"] = "name"

    # The issuer key is the brand; the route is the adapter that can read this
    # ISIN's holdings. An adapter that lists its ISINs routes only those; an
    # ETF-only adapter routes only exchange-traded funds, so a brand's mutual
    # funds count as "fund known, no holdings source".
    adapters = set(adapters)
    etf_only = set(etf_only_adapters)
    lists = {k: set(v) for k, v in (adapter_isins or {}).items()}

    def routed(isin: str, key: object, kind: object) -> str | None:
        if key not in adapters:
            return None
        if key in lists:
            return key if isin in lists[key] else None
        if key in etf_only and kind != "ETF":
            return None
        return key

    out["adapter"] = [routed(i, k, t) for i, k, t in zip(out.index, out["issuer"], out["instrument_type"])]
    out["holdings_route"] = out["adapter"].fillna("no_holdings_source")
    out["source"] = out["name_source"].fillna(out["listed_in"].str.split(";").str[0])
    out["source_url"] = _source_urls(out, all_)
    out["credit"] = out["listed_in"].map(_credit)
    out = out.reset_index()
    return out[FUND_DIRECTORY_COLUMNS].astype(object).where(out[FUND_DIRECTORY_COLUMNS].notna(), None).sort_values("isin").reset_index(drop=True)


def build_bond_directory(listings: list[pd.DataFrame], isin_lei: pd.DataFrame | None = None) -> pd.DataFrame:
    """Merge bond listings into one row per ISIN with :data:`BOND_DIRECTORY_COLUMNS`."""
    frames = [l for l in listings if l is not None and len(l)]
    if not frames:
        return pd.DataFrame(columns=BOND_DIRECTORY_COLUMNS)
    all_ = pd.concat(frames, ignore_index=True)
    all_ = all_[all_["isin"].map(is_valid_isin)]
    out = _summary(all_)
    for field, order in BOND_PRIORITY.items():
        out = out.join(_pick(all_, field, order))
    if isin_lei is not None and len(isin_lei):
        gl = isin_lei.drop_duplicates("isin").set_index("isin")["lei"]
        fill = out["issuer_lei"].isna() & out.index.isin(gl.index)
        out.loc[fill, "issuer_lei"] = gl.reindex(out.index[fill]).values
        out.loc[fill, "issuer_lei_source"] = "gleif"
    out = out.rename(columns={"coupon_pct_source": "coupon_source"})
    out["source"] = out["name_source"].fillna(out["issuer_name_source"]).fillna(out["listed_in"].str.split(";").str[0])
    out["source_url"] = _source_urls(out, all_)
    out["credit"] = out["listed_in"].map(_credit)
    out = out.reset_index()
    for c in BOND_DIRECTORY_COLUMNS:
        if c not in out:
            out[c] = None
    out = out[BOND_DIRECTORY_COLUMNS]
    out = out.astype(object).where(out.notna(), None)
    for c in ("coupon_pct", "amount_issued"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.sort_values("isin").reset_index(drop=True)


def coverage(fund_directory: pd.DataFrame) -> pd.DataFrame:
    """Funds known per issuer and domicile, and how many have a holdings adapter."""
    df = fund_directory.assign(
        issuer=fund_directory["issuer"].fillna("(unknown)"),
        domicile=fund_directory["domicile"].fillna("(unknown)"),
        with_adapter=fund_directory["adapter"].notna(),
    )
    g = df.groupby(["issuer", "domicile"])
    out = pd.DataFrame({"isins": g.size(), "with_adapter": g["with_adapter"].sum()}).reset_index()
    out["without_holdings_source"] = out["isins"] - out["with_adapter"]
    return out.sort_values(["isins", "issuer"], ascending=[False, True]).reset_index(drop=True)


def route(isin: str, fund_directory: pd.DataFrame) -> dict | None:
    """Directory row for a fund ISIN (issuer, adapter, domicile ...), or None if unknown."""
    hit = fund_directory[fund_directory["isin"] == isin]
    return None if hit.empty else hit.iloc[0].to_dict()
