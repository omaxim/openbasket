"""Which funds the Swiss banks run: a registry built from public registers.

No holdings here. For each fund (or share class) the registry records its
ISIN where one is known, name, manager, domicile, legal form and the bank it
belongs to (:func:`openbasket.sources.swiss_banks.attribute`), with the
source of every row.

Sources, all read through :mod:`openbasket.sources.swiss_fetch`:

``gleif`` (CC0, public)
    GLEIF API ``/lei-records/{manager}/managed-funds`` for every fund-manager
    LEI in :data:`openbasket.sources.swiss_banks.BANKS` (fund LEI, legal name,
    jurisdiction, legal form), joined to GLEIF's daily ISIN-to-LEI file
    (``https://mapping.gleif.org/api/v2/isin-lei/latest``) for the share-class
    ISINs.
``firds`` (ESMA, reuse with acknowledgement, public)
    ESMA FIRDS weekly full file ``FULINS_C`` (fund units traded on EU venues):
    ISIN, full name, CFI code and the issuing fund's LEI. Adds share classes
    missing from the GLEIF map and their full names.
``six`` (SIX Group, all rights reserved: local only)
    SIX Swiss Exchange reference data ``/fqs/ref.json`` for funds and ETFs:
    ISIN, name, issuer, legal-structure country, replication method and
    underlying index.
``finma`` (FINMA, reproduction needs written consent: local only)
    FINMA's lists of authorised Swiss collective investment schemes
    (``afch.xlsx``) and of foreign schemes approved for offering in
    Switzerland (``afetr.xlsx``). Names, umbrella, FINMA ID, fund management
    company and custodian, but no ISINs. A Swiss fund is linked to its GLEIF
    record when the normalised sub-fund name is unique for that bank on both
    sides; otherwise it stays a name-only row.
"""

from __future__ import annotations

import io
import json
import logging
import re
import unicodedata
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable
from xml.etree.ElementTree import iterparse

import pandas as pd

from ..identifiers import is_valid_isin
from . import swiss_fetch
from .swiss_banks import BANKS, attribute, bank_for_lei

logger = logging.getLogger(__name__)

#: The registry mixes sources; each row carries its own ``redistribution``.
REDISTRIBUTION = "public"
SOURCE_REDISTRIBUTION = {
    "gleif": "public",      # CC0 1.0
    "firds": "public",      # ESMA register data, reuse with acknowledgement
    "six": "local_only",    # (c) SIX Group Ltd, all rights reserved
    "finma": "local_only",  # FINMA terms: any reproduction needs prior written consent
}
CREDITS = {
    "gleif": "Legal Entity Identifier data: GLEIF, CC0 1.0",
    "firds": "Reference data: ESMA Financial Instruments Reference Data System (FIRDS)",
    "six": "Reference data: SIX Swiss Exchange, (c) SIX Group Ltd",
    "finma": "List of authorised collective investment schemes: Swiss Financial Market Supervisory Authority FINMA",
}

GLEIF_API = "https://api.gleif.org/api/v1"
GLEIF_ISIN_LEI = "https://mapping.gleif.org/api/v2/isin-lei/latest"
ESMA_FIRDS_INDEX = "https://registers.esma.europa.eu/solr/esma_registers_firds_files/select"
SIX_FQS = "https://www.six-group.com/fqs/ref.json"
SIX_FIELDS = ("ISIN,FundLongName,IssuerNameFull,LegalStructureCountryCode,AssetClassDesc,ReplicationMethodDesc,"
              "FundUnderlyingDescription,ProductLine,TradingBaseCurrency,ValorNumber")
SIX_FUNDS_URL = f"{SIX_FQS}?select={SIX_FIELDS}&where=PortalSegment=FU&orderby=ISIN&page=1&pagesize=99999"
FINMA_CH_URL = "https://www.finma.ch/en/~/media/finma/dokumente/bewilligungstraeger/xlsx/afch.xlsx?sc_lang=en"
FINMA_FOREIGN_URL = "https://www.finma.ch/en/~/media/finma/dokumente/bewilligungstraeger/xlsx/afetr.xlsx?sc_lang=en"
FINMA_PAGE = "https://www.finma.ch/en/finma-public/authorised-institutions-individuals-and-products/"

REGISTRY_COLUMNS = [
    "bank", "attributed_by", "isin", "name", "umbrella", "manager", "manager_lei", "fund_lei", "domicile", "currency",
    "fund_type", "legal_form", "product_line", "replication", "underlying", "finma_id", "valor",
    "listed_in", "source", "source_url", "credit", "redistribution", "as_of",
]

#: FINMA's foreign list names countries in German.
COUNTRY_DE = {
    "Luxemburg": "LU", "Irland": "IE", "Frankreich": "FR", "Deutschland": "DE", "Liechtenstein": "LI",
    "Österreich": "AT", "Niederlande": "NL", "Grossbritannien": "GB", "Vereinigtes Königreich": "GB",
    "Belgien": "BE", "Spanien": "ES", "Italien": "IT", "Dänemark": "DK", "Schweden": "SE", "Finnland": "FI",
    "Guernsey": "GG", "Jersey": "JE", "Malta": "MT", "Norwegen": "NO", "Portugal": "PT", "Cayman Islands": "KY",
}


def norm_name(value: object) -> str:
    """Lower-case, accents and punctuation removed, dashes and spaces collapsed."""
    if not isinstance(value, str):
        return ""
    s = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return " ".join(s.split())


# --------------------------------------------------------------------------- GLEIF


def parse_managed_funds(pages: Iterable[dict], manager_lei: str) -> list[dict]:
    rows = []
    for payload in pages:
        for d in payload.get("data", []):
            a = d.get("attributes", {})
            e = a.get("entity", {})
            form = e.get("legalForm") or {}
            rows.append(
                {
                    "fund_lei": a.get("lei") or d.get("id"),
                    "name": (e.get("legalName") or {}).get("name"),
                    "jurisdiction": e.get("jurisdiction"),
                    "legal_form": form.get("other") or form.get("id"),
                    "status": e.get("status"),
                    "manager_lei": manager_lei,
                }
            )
    return rows


def gleif_managed_funds(manager_lei: str, cache_dir: str | Path | None = None, page_size: int = 200) -> list[dict]:
    """Every fund GLEIF lists as managed by ``manager_lei`` (API pages cached per day)."""
    pages = []
    number = 1
    while True:
        url = f"{GLEIF_API}/lei-records/{manager_lei}/managed-funds?page%5Bsize%5D={page_size}&page%5Bnumber%5D={number}"
        body = swiss_fetch.cached(cache_dir, f"gleif_managed/{swiss_fetch.today()}/{manager_lei}_{number}.json",
                                  lambda: swiss_fetch.get_bytes(url, timeout=60))
        payload = json.loads(body)
        pages.append(payload)
        last = (payload.get("meta", {}).get("pagination", {}) or {}).get("lastPage") or 1
        if number >= last:
            break
        number += 1
    return parse_managed_funds(pages, manager_lei)


def parse_isin_lei(content: bytes, leis: set[str]) -> pd.DataFrame:
    """ISIN-LEI pairs of the given LEIs from GLEIF's zipped ``LEI,ISIN`` CSV."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        out = []
        with z.open(name) as f:
            for chunk in pd.read_csv(f, dtype=str, chunksize=1_000_000):
                chunk.columns = [c.strip().upper() for c in chunk.columns]
                out.append(chunk[chunk["LEI"].isin(leis)])
    df = pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["LEI", "ISIN"])
    return df.rename(columns={"LEI": "fund_lei", "ISIN": "isin"})[["fund_lei", "isin"]].drop_duplicates()


def gleif_isin_lei(leis: set[str], cache_dir: str | Path | None = None) -> pd.DataFrame:
    def fetch() -> bytes:
        meta = swiss_fetch.get(GLEIF_ISIN_LEI, timeout=60)
        meta.raise_for_status()
        return swiss_fetch.get_bytes(meta.json()["data"]["attributes"]["downloadLink"], timeout=900)

    content = swiss_fetch.cached(cache_dir, f"gleif_isin_lei/{swiss_fetch.today()}.zip", fetch)
    return parse_isin_lei(content, leis)


# --------------------------------------------------------------------------- FIRDS

_NS = "{urn:iso:std:iso:20022:tech:xsd:auth.017.001.02}"


def parse_firds_funds(content: bytes, leis: set[str]) -> pd.DataFrame:
    """Fund units in a zipped FIRDS ``FULINS_C`` file whose issuer is one of ``leis``."""
    rows: dict[str, dict] = {}
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".xml"))
        with z.open(name) as f:
            for _, el in iterparse(f, events=("end",)):
                if el.tag != _NS + "RefData":
                    continue
                issuer = el.findtext(_NS + "Issr")
                if issuer in leis:
                    g = el.find(_NS + "FinInstrmGnlAttrbts")
                    isin = g.findtext(_NS + "Id") if g is not None else None
                    if isin and isin not in rows:
                        rows[isin] = {
                            "isin": isin,
                            "firds_name": g.findtext(_NS + "FullNm"),
                            "cfi": g.findtext(_NS + "ClssfctnTp"),
                            "currency": g.findtext(_NS + "NtnlCcy"),
                            "fund_lei": issuer,
                        }
                el.clear()
    return pd.DataFrame(list(rows.values()), columns=["isin", "firds_name", "cfi", "currency", "fund_lei"])


def firds_latest_fulins_c(today: date | None = None) -> tuple[str, str] | None:
    """(file name, download link) of the latest ``FULINS_C`` in ESMA's index."""
    today = today or date.today()
    start = (today - timedelta(days=14)).isoformat()
    url = (f"{ESMA_FIRDS_INDEX}?q=*&fq=publication_date:%5B{start}T00:00:00Z%20TO%20{today.isoformat()}T23:59:59Z%5D"
           f"&wt=json&rows=500")
    r = swiss_fetch.get(url, timeout=60)
    r.raise_for_status()
    docs = [d for d in r.json()["response"]["docs"] if str(d.get("file_name", "")).startswith("FULINS_C_")]
    if not docs:
        return None
    d = max(docs, key=lambda d: str(d["publication_date"]))
    return d["file_name"].removesuffix(".zip"), d["download_link"]


def firds_funds(leis: set[str], cache_dir: str | Path | None = None) -> tuple[pd.DataFrame, str | None]:
    latest = firds_latest_fulins_c()
    if latest is None:
        return pd.DataFrame(columns=["isin", "firds_name", "cfi", "currency", "fund_lei"]), None
    name, link = latest
    content = swiss_fetch.cached(cache_dir, f"firds_funds/{name}.zip", lambda: swiss_fetch.get_bytes(link, timeout=600))
    m = re.search(r"_(\d{8})_", name)
    as_of = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else None
    return parse_firds_funds(content, leis), as_of


# --------------------------------------------------------------------------- SIX


def parse_six(payload: bytes | dict) -> tuple[pd.DataFrame, str | None]:
    data = json.loads(payload) if isinstance(payload, (bytes, str)) else payload
    df = pd.DataFrame(data["rowData"], columns=data["colNames"])
    stamp = str(data.get("delayedDateTime") or "")
    as_of = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if len(stamp) >= 8 and stamp[:8].isdigit() else None
    return df, as_of


def six_funds(cache_dir: str | Path | None = None) -> tuple[pd.DataFrame, str | None]:
    body = swiss_fetch.cached(cache_dir, f"six_funds/{swiss_fetch.today()}.json", lambda: swiss_fetch.get_bytes(SIX_FUNDS_URL))
    return parse_six(body)


# --------------------------------------------------------------------------- FINMA

_FINMA_FORM = {9: "contractual", 11: "SICAV", 12: "SICAF", 13: "KmGK"}
_FINMA_TYPE = {14: "securities", 15: "real estate", 16: "other traditional", 17: "other alternative"}


def _blank(v: object) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _rows(content: bytes) -> list[tuple]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    return [tuple(r) for r in wb.worksheets[0].iter_rows(values_only=True)]


def parse_finma_ch(content: bytes) -> pd.DataFrame:
    """FINMA's list of authorised Swiss collective investment schemes."""
    out = []
    section = None
    for row in _rows(content):
        cells = list(row) + [None] * (20 - len(row))
        filled = [c for c in cells if not _blank(c)]
        if not filled:
            continue
        if len(filled) == 1 and isinstance(cells[0], str):
            if "CISA" in cells[0]:
                section = "open-ended" if "Open" in cells[0] else "closed-ended"
            continue
        if cells[0] == "Name of individual fund or sub-fund" or not isinstance(cells[2], str) or not cells[2].startswith("F"):
            continue
        mark = lambda i: isinstance(cells[i], str) and cells[i].strip().upper() == "X"  # noqa: E731
        out.append(
            {
                "name": str(cells[0]).strip(),
                "umbrella": None if _blank(cells[1]) else str(cells[1]).strip(),
                "finma_id": cells[2].strip(),
                "manager": None if _blank(cells[5]) else str(cells[5]).strip(),
                "custodian": None if _blank(cells[6]) else str(cells[6]).strip(),
                "legal_form": next((v for k, v in _FINMA_FORM.items() if mark(k)), None),
                "fund_type": next((v for k, v in _FINMA_TYPE.items() if mark(k)), None),
                "qualified_only": mark(18),
                "section": section,
                "domicile": "CH",
            }
        )
    return pd.DataFrame(out)


def parse_finma_foreign(content: bytes) -> pd.DataFrame:
    """FINMA's list of foreign schemes approved for offering in Switzerland."""
    out = []
    for row in _rows(content):
        cells = list(row) + [None] * (16 - len(row))
        if not isinstance(cells[4], str) or not cells[4].startswith("F") or cells[0] == "Name of individual fund or sub-fund":
            continue
        country = None if _blank(cells[6]) else str(cells[6]).strip()
        out.append(
            {
                "name": str(cells[0]).strip(),
                "umbrella": None if _blank(cells[2]) else str(cells[2]).strip(),
                "finma_id": cells[4].strip(),
                "domicile": COUNTRY_DE.get(country, country),
                "representative": None if _blank(cells[8]) else str(cells[8]).strip(),
                "fund_type": "UCITS" if isinstance(cells[13], str) and cells[13].strip().upper() == "X" else "non-UCITS",
            }
        )
    return pd.DataFrame(out)


def finma_lists(cache_dir: str | Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    ch = swiss_fetch.cached(cache_dir, f"finma/{swiss_fetch.today()}_afch.xlsx", lambda: swiss_fetch.get_bytes(FINMA_CH_URL))
    fo = swiss_fetch.cached(cache_dir, f"finma/{swiss_fetch.today()}_afetr.xlsx", lambda: swiss_fetch.get_bytes(FINMA_FOREIGN_URL))
    return parse_finma_ch(ch), parse_finma_foreign(fo)


# --------------------------------------------------------------------------- merge


def _frame(rows: list[dict] | pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for c in REGISTRY_COLUMNS:
        if c not in df:
            df[c] = None
    return df[REGISTRY_COLUMNS]


def gleif_rows(funds: list[dict], isin_lei: pd.DataFrame, firds: pd.DataFrame, as_of: str,
               firds_as_of: str | None) -> pd.DataFrame:
    """One row per share-class ISIN (or per fund LEI when GLEIF knows no ISIN)."""
    f = pd.DataFrame(funds)
    if f.empty:
        return _frame([])
    f = f[f["status"].fillna("ACTIVE") == "ACTIVE"].drop_duplicates("fund_lei")
    isins = pd.concat([isin_lei.assign(via="gleif"), firds[["fund_lei", "isin"]].assign(via="firds")], ignore_index=True)
    isins = isins[isins["isin"].map(is_valid_isin)]
    isins = isins.groupby(["fund_lei", "isin"], as_index=False)["via"].agg(lambda s: ",".join(sorted(set(s))))
    df = f.merge(isins, on="fund_lei", how="left")
    if not firds.empty:
        df = df.merge(firds[["isin", "firds_name", "currency", "cfi"]], on="isin", how="left")
    else:
        df["firds_name"] = df["currency"] = df["cfi"] = None
    rows = []
    for r in df.to_dict("records"):
        via = r.get("via") if isinstance(r.get("via"), str) else "gleif"
        src = "firds" if via == "firds" else "gleif"
        rows.append(
            {
                "bank": attribute(r["firds_name"] if isinstance(r.get("firds_name"), str) else r["name"], None, None, r["manager_lei"]),
                "isin": r["isin"] if isinstance(r.get("isin"), str) else None,
                "name": r["firds_name"] if isinstance(r.get("firds_name"), str) else r["name"],
                "umbrella": None,
                "manager": None,
                "manager_lei": r["manager_lei"],
                "fund_lei": r["fund_lei"],
                "domicile": r["jurisdiction"],
                "currency": r.get("currency") if isinstance(r.get("currency"), str) else None,
                "legal_form": r["legal_form"],
                "listed_in": via,
                "source": src,
                "source_url": f"https://search.gleif.org/#/record/{r['fund_lei']}",
                "credit": CREDITS[src] if src == "gleif" else f"{CREDITS['gleif']}; {CREDITS['firds']}",
                "redistribution": "public",
                "as_of": as_of if src == "gleif" else (firds_as_of or as_of),
            }
        )
    return _frame(rows)


def six_rows(six: pd.DataFrame, as_of: str | None) -> pd.DataFrame:
    rows = []
    for r in six.drop_duplicates("ISIN").to_dict("records"):
        bank = attribute(r.get("FundLongName"), None, r.get("IssuerNameFull"))
        if bank is None:
            continue
        repl = str(r.get("ReplicationMethodDesc") or "").lower()
        rows.append(
            {
                "bank": bank,
                "isin": r["ISIN"],
                "name": r.get("FundLongName"),
                "manager": r.get("IssuerNameFull"),
                "domicile": r.get("LegalStructureCountryCode"),
                "currency": r.get("TradingBaseCurrency"),
                "fund_type": r.get("AssetClassDesc"),
                "product_line": {"ET": "ETF", "PF": "fund", "IF": "fund"}.get(r.get("ProductLine"), r.get("ProductLine")),
                "replication": {"physical": "physical", "synthetic": "swap"}.get(repl),
                "underlying": r.get("FundUnderlyingDescription"),
                "valor": r.get("ValorNumber"),
                "listed_in": "six",
                "source": "six",
                "source_url": "https://www.six-group.com/en/market-data/etf/etf-explorer.html",
                "credit": CREDITS["six"],
                "redistribution": SOURCE_REDISTRIBUTION["six"],
                "as_of": as_of,
            }
        )
    return _frame(rows)


def finma_rows(ch: pd.DataFrame, foreign: pd.DataFrame, as_of: str) -> pd.DataFrame:
    rows = []
    for r in ch.to_dict("records"):
        bank = attribute(r["name"], r.get("umbrella"), r.get("manager"))
        if bank:
            rows.append({**{k: r.get(k) for k in ("name", "umbrella", "manager", "domicile", "fund_type", "legal_form", "finma_id")},
                         "bank": bank, "listed_in": "finma"})
    for r in foreign.to_dict("records"):
        bank = attribute(r["name"], r.get("umbrella"))
        if bank:
            rows.append({**{k: r.get(k) for k in ("name", "umbrella", "domicile", "fund_type", "finma_id")},
                         "bank": bank, "listed_in": "finma"})
    df = _frame(rows)
    df["source"] = "finma"
    df["source_url"] = FINMA_PAGE
    df["credit"] = CREDITS["finma"]
    df["redistribution"] = SOURCE_REDISTRIBUTION["finma"]
    df["as_of"] = as_of
    return df


def link_finma(finma: pd.DataFrame, gleif: pd.DataFrame) -> pd.DataFrame:
    """Copy FINMA IDs, umbrellas and managers onto GLEIF rows of the same sub-fund.

    Rows are matched on the bank behind the fund manager (FINMA's fund
    management company, GLEIF's manager LEI) and the normalised sub-fund name,
    and only where that pair is unique on both sides. The bank is then
    attributed again with the umbrella, which carries white-label brands
    ("BKB Anlagelösung" run by UBS Fund Management).
    """
    if finma.empty or gleif.empty:
        return gleif
    f = finma[["name", "finma_id", "umbrella", "manager"]]
    f = f.assign(key=f["name"].map(norm_name), mbank=f["manager"].map(lambda m: attribute(None, None, m)))
    f = f[f["mbank"].notna() & ~f.duplicated(["mbank", "key"], keep=False)]
    g = gleif.drop_duplicates("fund_lei")
    g = g.assign(key=g["name"].map(norm_name), mbank=g["manager_lei"].map(bank_for_lei))
    g = g[~g.duplicated(["mbank", "key"], keep=False)][["mbank", "key", "fund_lei"]]
    link = f.merge(g, on=["mbank", "key"])[["fund_lei", "finma_id", "umbrella", "manager"]]
    out = gleif.merge(link, on="fund_lei", how="left", suffixes=("", "_finma"))
    for c in ("finma_id", "umbrella", "manager"):
        out[c] = out[c].where(out[c].notna(), out[f"{c}_finma"])
        out = out.drop(columns=f"{c}_finma")
    has_umbrella = out["umbrella"].notna()
    out.loc[has_umbrella, "bank"] = [
        attribute(n, u, m, lei) for n, u, m, lei in out.loc[has_umbrella, ["name", "umbrella", "manager", "manager_lei"]].itertuples(index=False)
    ]
    return out


def merge_registry(gleif: pd.DataFrame, six: pd.DataFrame, finma: pd.DataFrame) -> pd.DataFrame:
    """Stack the sources: ISIN rows from GLEIF/FIRDS and SIX (deduplicated, open source first), then FINMA-only rows."""
    gleif = link_finma(finma, gleif)
    with_isin = pd.concat([gleif[gleif["isin"].notna()], six], ignore_index=True)
    listed = with_isin.groupby("isin")["listed_in"].agg(lambda s: ",".join(sorted({x for v in s.dropna() for x in str(v).split(",")})))
    # Prefer the openly licensed row; fill gaps (replication, underlying, valor) from SIX.
    with_isin["_rank"] = with_isin["redistribution"].map({"public": 0}).fillna(1)
    with_isin = with_isin.sort_values(["isin", "_rank"])
    first = with_isin.groupby("isin", as_index=False).first()
    first["listed_in"] = first["isin"].map(listed)
    first = first.drop(columns="_rank")
    no_isin = gleif[gleif["isin"].isna()]
    linked = set(gleif["finma_id"].dropna())
    finma_only = finma[~finma["finma_id"].isin(linked)]
    out = pd.concat([first, no_isin, finma_only], ignore_index=True)
    # "brand": the bank's name is in the fund or umbrella name; "manager": the
    # bank's fund management company runs it for someone else or unbranded.
    brand = [attribute(n, u) for n, u in out[["name", "umbrella"]].itertuples(index=False)]
    out["attributed_by"] = ["brand" if b == k else "manager" for b, k in zip(brand, out["bank"])]
    return _frame(out).sort_values(["bank", "isin", "name"], na_position="last").reset_index(drop=True)


def build_registry(cache_dir: str | Path | None = None, sources: Iterable[str] = ("gleif", "firds", "six", "finma"),
                   managers: Iterable[str] | None = None) -> pd.DataFrame:
    """The Swiss bank fund registry from the chosen sources."""
    sources = set(sources)
    today = swiss_fetch.today()
    leis = list(managers) if managers is not None else [lei for b in BANKS for lei in b.manager_leis]
    funds: list[dict] = []
    if "gleif" in sources:
        for lei in leis:
            try:
                funds += gleif_managed_funds(lei, cache_dir)
            except Exception as exc:
                logger.warning("GLEIF managed funds of %s: %s", lei, exc)
    fund_leis = {f["fund_lei"] for f in funds}
    isin_lei = gleif_isin_lei(fund_leis, cache_dir) if "gleif" in sources and fund_leis else pd.DataFrame(columns=["fund_lei", "isin"])
    firds, firds_as_of = pd.DataFrame(columns=["isin", "firds_name", "cfi", "currency", "fund_lei"]), None
    if "firds" in sources and fund_leis:
        try:
            firds, firds_as_of = firds_funds(fund_leis, cache_dir)
        except Exception as exc:  # one registry source failing must not stop the others
            logger.warning("FIRDS fund instruments skipped: %s", exc)
    gleif = gleif_rows(funds, isin_lei, firds, today, firds_as_of)
    six = six_rows(*six_funds(cache_dir)) if "six" in sources else _frame([])
    finma = finma_rows(*finma_lists(cache_dir), today) if "finma" in sources else _frame([])
    return merge_registry(gleif, six, finma)


def coverage(registry: pd.DataFrame, holdings_routes: pd.DataFrame | None = None) -> pd.DataFrame:
    """Funds known per bank: ISINs, portfolios (fund LEIs / FINMA IDs), and by source."""
    rows = []
    for b in BANKS:
        r = registry[registry["bank"] == b.key]
        rows.append(
            {
                "bank": b.key,
                "bank_name": b.name,
                "share_classes_with_isin": int(r["isin"].notna().sum()),
                "portfolios": int(r["fund_lei"].dropna().nunique() + r.loc[r["fund_lei"].isna() & r["isin"].isna(), "finma_id"].nunique()),
                "finma_schemes": int(r["finma_id"].dropna().nunique()),
                "branded_share_classes": int((r["isin"].notna() & (r["attributed_by"] == "brand")).sum()),
                "six_listed": int(r["listed_in"].fillna("").str.contains("six").sum()),
                "domiciles": ",".join(f"{k}:{v}" for k, v in r["domicile"].value_counts().head(4).items()),
                "route": b.route,
                "route_note": b.route_note,
            }
        )
    out = pd.DataFrame(rows)
    if holdings_routes is not None and not holdings_routes.empty:
        out = out.merge(holdings_routes, on="bank", how="left")
    return out
