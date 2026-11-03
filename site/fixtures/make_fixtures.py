"""Rebuild the fixture API in ``site/fixtures/api/v1`` from cached source files.

The fixtures stand in for the real API (``docs/source/data-api.md``, v1.1)
while the landing page is developed and tested. They are cut from the
project's own cached source data, so names, ISINs, weights, countries,
sectors, dates and links are real but not maintained.

Each fund keeps the identity it already has in its fixture file (id, ISINs,
name, ticker, source link, as-of date); everything derived from holdings is
recomputed from the cached files:

- iShares UK and US: ``data/01_raw/issuers/holdings/{site}_{portfolio}_*.json``.
  AOR (``US4642898674``) is expanded one level through the iShares funds it
  holds, so its rows carry ``depth`` 1 and ``via``.
- SPDR US and UCITS: ``data/01_raw/issuers/holdings/spdr_*.xlsx``.
- SEC N-PORT: ``data/03_primary/nport_holdings.parquet``; the issuer is the
  registrant name in the quarter's ``REGISTRANT.tsv``.
- TickerInside (QQQ): the rows already in the fixture, which list every
  holding.

Usage (from the repository root, with the cached data in ``data/``)::

    .venv/bin/python site/fixtures/make_fixtures.py [--data DIR] [--cap 500]

This script is a development tool. It is not deployed (``assemble_site.py``
leaves ``site/fixtures/`` out) and the real files come from the ``publish``
pipeline.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openbasket.identifiers import cusip_to_isin, is_valid_isin  # noqa: E402
from openbasket.sources import ishares, nport  # noqa: E402

API = HERE / "api" / "v1"
FIELDS = ["isin", "name", "weight_pct", "asset_class", "source", "as_of", "depth", "via", "country", "sector"]
FUND_KEYS = [
    "id", "isin", "isins", "name", "ticker", "issuer", "source", "source_url", "credit", "as_of", "matched_via",
    "note", "reported_total_pct", "resolved_pct", "unresolved_pct", "unidentified_lines_pct", "n_constituents",
    "n_rows", "truncated", "listed_pct", "breakdowns", "fields", "constituents", "unresolved", "full_data_url",
]
UNRESOLVED = "Unresolved"
BUILT_AT = "2026-11-03T12:00:00Z"
PRODUCT_LIST_DATE = "2026-11-03"

SOURCES = {
    "ishares": {
        "name": "iShares (BlackRock) fund holdings",
        "url": "https://www.ishares.com/",
        "licence": "Public website data; terms not reviewed",
        "credit": "Holdings data from iShares by BlackRock",
    },
    "spdr": {
        "name": "SPDR (State Street) daily fund holdings",
        "url": "https://www.ssga.com/",
        "licence": "Public website data; terms not reviewed",
        "credit": "Holdings data from SPDR ETFs by State Street Global Advisors",
    },
    "tickerinside": {
        "name": "TickerInside",
        "url": "https://tickerinside.com/",
        "licence": "CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)",
        "credit": (
            "TickerInside (https://tickerinside.com/), licensed under CC BY 4.0 "
            "(https://creativecommons.org/licenses/by/4.0/). Changed by openbasket: tickers mapped to ISINs, "
            "names taken from SEC Form N-PORT."
        ),
    },
    "nport": {
        "name": "SEC Form N-PORT",
        "url": "https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets",
        "licence": "Public US government data",
        "credit": "Holdings from SEC Form N-PORT filings, U.S. Securities and Exchange Commission",
    },
    "openfigi": {
        "name": "OpenFIGI",
        "url": "https://www.openfigi.com/",
        "licence": "OpenFIGI API terms; terms not reviewed",
        "credit": "Names and security types from OpenFIGI",
    },
}

#: Country names as issuers write them, to ISO 3166-1 alpha-2. Anything else keeps the source's label.
COUNTRY = {
    "Andorra": "AD", "Angola": "AO", "Argentina": "AR", "Armenia": "AM", "Australia": "AU", "Austria": "AT",
    "Azerbaijan": "AZ", "BRITISH VIRGIN": "VG", "Bahamas": "BS", "Bahrain": "BH", "Barbados": "BB", "Belgium": "BE",
    "Benin": "BJ", "Bermuda": "BM", "Bolivia": "BO", "Brazil": "BR", "Bulgaria": "BG", "Burkina Faso": "BF",
    "Cameroon": "CM", "Canada": "CA", "Cayman Islands": "KY", "Chile": "CL", "China": "CN", "Colombia": "CO",
    "Costa Rica": "CR", "Cote D'Ivoire (Ivory Coast)": "CI", "Croatia (Hrvatska)": "HR", "Cyprus": "CY",
    "Czech Republic": "CZ", "DOMINICAN REPB.": "DO", "Democratic Rep of Congo": "CD", "Denmark": "DK",
    "Dominican Republic": "DO", "Ecuador": "EC", "Egypt": "EG", "El Salvador": "SV", "Estonia": "EE",
    "Finland": "FI", "France": "FR", "Gabon": "GA", "Georgia": "GE", "Germany": "DE", "Ghana": "GH",
    "Gibraltar": "GI", "Greece": "GR", "Guatemala": "GT", "Guernsey": "GG", "Honduras": "HN", "Hong Kong": "HK",
    "Hungary": "HU", "Iceland": "IS", "India": "IN", "Indonesia": "ID", "Iraq": "IQ", "Ireland": "IE",
    "Isle Of Man": "IM", "Isle of Man": "IM", "Israel": "IL", "Italy": "IT", "Jamaica": "JM", "Japan": "JP",
    "Jersey": "JE", "Jordan": "JO", "Kazakhstan": "KZ", "Kenya": "KE", "Korea": "KR", "Korea (South)": "KR",
    "Kuwait": "KW", "Kyrgyzstan": "KG", "Laos": "LA", "Latvia": "LV", "Lebanon": "LB", "Lithuania": "LT",
    "Luxembourg": "LU", "MARSHALL ISLAND": "MH", "Macau": "MO", "Madagascar": "MG", "Malaysia": "MY", "Malta": "MT",
    "Mauritius": "MU", "Mexico": "MX", "Mongolia": "MN", "Montenegro": "ME", "Morocco": "MA", "Mozambique": "MZ",
    "Netherlands": "NL", "New Zealand": "NZ", "Nigeria": "NG", "Norway": "NO", "Oman": "OM", "Pakistan": "PK",
    "Panama": "PA", "Papua New Guinea": "PG", "Paraguay": "PY", "Peru": "PE", "Philippines": "PH", "Poland": "PL",
    "Portugal": "PT", "Qatar": "QA", "Romania": "RO", "Russian Federation": "RU", "Rwanda": "RW",
    "Saudi Arabia": "SA", "Senegal": "SN", "Serbia": "RS", "Singapore": "SG", "Slovak Republic": "SK",
    "Slovenia": "SI", "South Africa": "ZA", "Spain": "ES", "Sri Lanka": "LK", "Suriname": "SR", "Sweden": "SE",
    "Switzerland": "CH", "TRINIDAD&TOBAGO": "TT", "Taiwan": "TW", "Tanzania": "TZ", "Thailand": "TH", "Togo": "TG",
    "Trinidad and Tobago": "TT", "Turkey": "TR", "UAE": "AE", "Ukraine": "UA", "United Arab Emirates": "AE",
    "United Kingdom": "GB", "United States": "US", "Uruguay": "UY", "Uzbekistan": "UZ", "VIRGIN ISLANDS": "VG",
    "Zambia": "ZM",
}

#: The no-holdings fund added to the fixtures (an iShares index mutual fund whose page shows only its top ten).
NO_HOLDINGS = {
    "id": "IE000TOBHQ26", "isin": "IE000TOBHQ26", "isins": ["IE000TOBHQ26"],
    "name": "iShares Japan Index Fund (IE)", "ticker": None, "issuer": "iShares", "source": "ishares",
    "source_url": "https://www.ishares.com/uk/individual/en/products/332375/ishares-japan-index-fund-ie",
    "matched_via": "ishares",
    "note": "iShares publishes only the ten largest holdings for this fund; see the source link.",
}


def _blank(v: object) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or str(v).strip() in ("", "-", "nan", "<NA>")


def _label(v: object) -> str | None:
    return None if _blank(v) else str(v).strip()


def _country(v: object) -> str | None:
    s = _label(v)
    if s is None:
        return None
    if re.fullmatch(r"[A-Z]{2}", s) and s not in ("XX", "ZZ"):
        return s
    return COUNTRY.get(s, s)


def _w(v: object) -> float:
    """A line's weight; missing or not a number counts as 0."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(f) else f


def _r(v: float | None, digits: int = 5) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    out = round(float(v), digits)
    return 0.0 if out == 0 else out


# ---------- reading holdings: lists of line dicts ----------
# A line: isin (or None), name, weight, asset_class, country, sector.


def _ishares_lines(path: Path) -> tuple[str | None, list[dict]]:
    as_of, df = ishares.parse_holdings(json.loads(path.read_text()))
    weight = "holdingPercent"
    out = []
    for r in df.to_dict("records"):
        isin = r.get("isin")
        out.append({
            "isin": isin if isinstance(isin, str) and is_valid_isin(isin) else None,
            "name": _label(r.get("issueName")) or "",
            "weight": _w(r.get(weight)),
            "asset_class": ishares.ASSET_CLASS.get(r.get("assetClass"), _label(r.get("assetClass"))),
            "country": _country(r.get("countryOfRisk")),
            "sector": _label(r.get("sectorName")),
        })
    return as_of, out


_DATE_RE = re.compile(r"(\d{2}-[A-Za-z]{3}-\d{4})")


def _spdr_isin(maps: dict, isin: str | None, cusip: str | None, sedol: str | None) -> str | None:
    """As the SPDR provider does: reported ISIN, then N-PORT's CUSIP and SEDOL maps, then a derived
    ISIN only if OpenFIGI knows it (SPDR uses CUSIP-shaped internal codes for some securities)."""
    if isin and is_valid_isin(isin.upper()):
        return isin.upper()
    cusip = cusip.upper() if cusip else None
    if cusip and cusip in maps["cusip"]:
        return maps["cusip"][cusip]
    if sedol and sedol.upper() in maps["sedol"]:
        return maps["sedol"][sedol.upper()]
    derived = cusip_to_isin(cusip) if cusip else None
    return derived if derived and derived in maps["figi"] else None


def _maps(data: Path) -> dict:
    import duckdb

    primary = data / "03_primary"
    return {
        "cusip": dict(duckdb.sql(f"select cusip, first(isin) from read_parquet('{primary / 'cusip_isin_map.parquet'}') group by 1").fetchall()),
        "sedol": dict(duckdb.sql(f"select upper(sedol), first(isin) from read_parquet('{primary / 'sedol_isin_map.parquet'}') group by 1").fetchall()),
        "figi": {x[0] for x in duckdb.sql(
            f"select id_value from read_parquet('{primary / 'openfigi_cache.parquet'}') "
            "where id_type = 'ID_ISIN' and result is not null and result <> '[]'").fetchall()},
    }


def _spdr_lines(path: Path, maps: dict) -> tuple[str | None, list[dict]]:
    raw = pd.read_excel(path, header=None, dtype=object)
    first = raw.iloc[:, 0].map(lambda v: str(v).strip() if v is not None else "")
    hdr = first.index[first.isin(["Name", "ISIN"])][0]
    as_of = None
    for _, row in raw.iloc[:hdr].iterrows():
        text = " ".join(str(v) for v in row if not _blank(v))
        m = _DATE_RE.search(text)
        if "as of" in text.lower() and m:
            as_of = pd.to_datetime(m.group(1), format="%d-%b-%Y").date().isoformat()
    body = raw.iloc[hdr + 1 :].copy()
    body.columns = [str(c).strip() for c in raw.iloc[hdr]]
    blank = body.isna().all(axis=1)
    if blank.any():
        body = body.loc[: blank.idxmax()].iloc[:-1]
    weight_col = next(c for c in ("Weight", "Percent of Fund") if c in body.columns)
    name_col = next(c for c in ("Name", "Security Name") if c in body.columns)
    is_debt = any(c in body.columns for c in ("Coupon", "Maturity", "Maturity Date", "Interest Rate"))
    country_col = next((c for c in ("Country of Issue", "Trade Country Name") if c in body.columns), None)
    sector_col = next((c for c in ("Sector Classification", "Sector") if c in body.columns), None)
    out = []
    for r in body.to_dict("records"):
        name = _label(r.get(name_col))
        w = pd.to_numeric(r.get(weight_col), errors="coerce")
        if name is None and pd.isna(w):
            continue
        # US files have no ISIN column; their Identifier holds an ISIN for bonds and a CUSIP for shares.
        reported = _label(r.get("ISIN")) if "ISIN" in body.columns else _label(r.get("Identifier"))
        isin = _spdr_isin(maps, reported, _label(r.get("Identifier")), _label(r.get("SEDOL")))
        out.append({
            "isin": isin,
            "name": name or "",
            "weight": 0.0 if pd.isna(w) else float(w),
            "asset_class": "debt" if is_debt else "equity",
            "country": _country(r.get(country_col)) if country_col else None,
            "sector": _label(r.get(sector_col)) if sector_col else None,
        })
    return as_of, out


def _nport_lines(holdings: Path, series_id: str) -> tuple[str | None, str | None, list[dict]]:
    import duckdb

    df = duckdb.execute(f"select * from read_parquet('{holdings}') where series_id = ?", [series_id]).df()
    df = df.astype(object).where(df.notna(), None)
    as_of = pd.Timestamp(df["report_date"].iloc[0]).date().isoformat()
    accession = df["accession_number"].iloc[0]
    out = []
    for r in df.itertuples(index=False):
        out.append({
            "isin": r.isin if r.isin and is_valid_isin(r.isin) else None,
            "name": (r.issuer_title if r.asset_cat not in ("EC", "EP") else r.issuer_name) or "",
            "weight": _w(r.weight_pct),
            "asset_class": nport.ASSET_CLASS.get(r.asset_cat, r.asset_cat),
            "country": _country(r.country),
            "sector": None,
        })
    return as_of, accession, out


def _registrants(zip_path: Path) -> dict[str, str]:
    with zipfile.ZipFile(zip_path) as z, z.open("REGISTRANT.tsv") as f:
        rows = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"), delimiter="\t")
        return {r["ACCESSION_NUMBER"]: r["REGISTRANT_NAME"].strip() for r in rows}


# ---------- building documents ----------


def _rows(lines: list[dict], source: str, as_of: str, via: str, depth: int = 0) -> tuple[list[list], float]:
    """Constituent rows, one per (isin, source, as_of, via), and the weight of lines without an ISIN."""
    keyed: dict[tuple, list] = {}
    unidentified = 0.0
    for ln in lines:
        if not ln["isin"]:
            unidentified += ln["weight"]
            continue
        key = (ln["isin"], source, as_of, via)
        if key in keyed:
            keyed[key][2] += ln["weight"]
        else:
            keyed[key] = [ln["isin"], ln["name"], ln["weight"], ln["asset_class"], source, as_of, depth, via,
                          ln["country"], ln["sector"]]
    return list(keyed.values()), unidentified


def _breakdown(rows: list[list], col: int, unresolved: float) -> list[list]:
    acc: dict[object, float] = defaultdict(float)
    for r in rows:
        acc[r[col]] += r[2]
    items = sorted(acc.items(), key=lambda kv: (-kv[1], kv[0] is None, str(kv[0])))
    return [[k, _r(v)] for k, v in items] + [[UNRESOLVED, _r(unresolved)]]


def breakdowns(rows: list[list], unresolved: float) -> dict:
    """``breakdowns`` over the full list of rows, as data-api.md v1.1 defines it."""
    by_isin: dict[str, float] = defaultdict(float)
    for r in rows:
        by_isin[r[0]] += r[2]
    top = sorted(by_isin.values(), key=lambda w: -abs(w))[:10]
    return {
        "asset_class": _breakdown(rows, 3, unresolved),
        "country": _breakdown(rows, 8, unresolved),
        "sector": _breakdown(rows, 9, unresolved),
        "source": _breakdown(rows, 4, unresolved),
        "top10_pct": _r(sum(top)),
    }


def document(meta: dict, rows: list[list], reported: float | None, unidentified: float | None, credit: str,
             as_of: str | None, cap: int, full_data_url: str, note: str | None = None) -> dict:
    rows = sorted(rows, key=lambda r: (-abs(r[2]), r[0], r[7] or ""))
    resolved = sum(r[2] for r in rows)
    unresolved = 100.0 - resolved
    listed = rows[:cap]
    for r in rows:
        r[2] = _r(r[2])
    unresolved_note = note
    if unresolved_note is None and unidentified:
        unresolved_note = f"{unidentified:.2f}% is in lines with no ISIN (cash, futures, FX, swaps or pools)."
    doc = {
        "id": meta["id"], "isin": meta["isin"], "isins": meta["isins"], "name": meta["name"],
        "ticker": meta["ticker"], "issuer": meta["issuer"], "source": meta["source"],
        "source_url": meta["source_url"], "credit": credit, "as_of": as_of,
        "matched_via": meta["matched_via"], "note": note if note is not None else meta.get("note"),
        "reported_total_pct": _r(reported), "resolved_pct": _r(resolved), "unresolved_pct": _r(unresolved),
        "unidentified_lines_pct": _r(unidentified), "n_constituents": len({r[0] for r in rows}),
        "n_rows": len(rows), "truncated": len(rows) > len(listed), "listed_pct": _r(sum(r[2] for r in listed)),
        "breakdowns": breakdowns(rows, unresolved), "fields": FIELDS, "constituents": listed,
        "unresolved": {"weight_pct": _r(unresolved), "note": unresolved_note, "source": meta["source"],
                       "source_url": meta["source_url"]},
        "full_data_url": full_data_url,
    }
    assert list(doc) == FUND_KEYS
    return doc


def _credit(sources: list[str], own: str, extra: str | None = None) -> str:
    seen = [own] + [s for s in sources if s != own]
    parts = [extra or SOURCES[own]["credit"]] + [SOURCES[s]["credit"] for s in seen[1:]]
    return "; ".join(dict.fromkeys(parts))


def build(data: Path, cap: int) -> None:
    holdings_dir = data / "01_raw" / "issuers" / "holdings"
    nport_parquet = data / "03_primary" / "nport_holdings.parquet"
    registrants = _registrants(next((data / "01_raw" / "nport").glob("*_nport.zip")))
    products = pd.read_parquet(data / "01_raw" / "issuers" / "issuer_products.parquet")
    maps = _maps(data)

    old = {}
    for p in sorted((API / "funds").glob("*.json")):
        d = json.loads(p.read_text())
        old[p.stem] = d
    meta_old = json.loads((API / "meta.json").read_text())
    full_data_url = meta_old["full_data_url"]

    def ishares_file(url: str) -> Path:
        site = "uk" if "/uk/" in url else "us"
        pid = re.search(r"/products/(\d+)/", url).group(1)
        return next(holdings_dir.glob(f"{site}_{pid}_*.json"))

    ishares_us = products[(products["issuer"].str.lower() == "ishares") & (products["site"] == "us")]
    us_pages = dict(zip(ishares_us["isin"], ishares_us["product_page_url"]))
    us_names = dict(zip(ishares_us["isin"], ishares_us["fund_name"]))

    docs = {}
    for fid, d in old.items():
        if "alias_of" in d or fid == NO_HOLDINGS["id"]:
            continue
        meta = {k: d[k] for k in ("id", "isin", "isins", "name", "ticker", "issuer", "source", "source_url",
                                  "matched_via", "note")}
        src = d["source"]
        if src == "ishares":
            as_of, lines = _ishares_lines(ishares_file(d["source_url"]))
            reported = sum(ln["weight"] for ln in lines)
            if fid == "US4642898674":  # AOR: one level through the iShares funds it holds
                rows, unidentified = [], 0.0
                for ln in lines:
                    page = us_pages.get(ln["isin"]) if ln["isin"] else None
                    try:
                        sub_file = ishares_file(page) if page else None
                    except StopIteration:
                        sub_file = None
                    if sub_file is None:
                        r, u = _rows([ln], src, as_of, d["name"], 0)
                        rows += r
                        unidentified += u
                        continue
                    sub_as_of, sub_lines = _ishares_lines(sub_file)
                    scaled = [{**s, "weight": s["weight"] * ln["weight"] / 100.0} for s in sub_lines]
                    r, u = _rows(scaled, src, sub_as_of, us_names.get(ln["isin"], ln["name"]), 1)
                    rows += r
                    unidentified += u
            else:
                rows, unidentified = _rows(lines, src, as_of, d["name"])
            credit = _credit([src], src)
        elif src == "spdr":
            tick = (d["ticker"] or "").lower()
            path = next(p for p in holdings_dir.glob("spdr_*.xlsx")
                        if re.search(rf"-{re.escape(tick)}(-[a-z]{{2}})?_", p.name))
            as_of, lines = _spdr_lines(path, maps)
            reported = sum(ln["weight"] for ln in lines)
            rows, unidentified = _rows(lines, src, as_of, d["name"])
            credit = _credit([src], src)
        elif src == "nport":
            series = d["id"] if d["id"].startswith("S0") else _series_for(nport_parquet, d)
            as_of, accession, lines = _nport_lines(nport_parquet, series)
            reported = sum(ln["weight"] for ln in lines)
            rows, unidentified = _rows(lines, src, as_of, d["name"])
            meta["issuer"] = registrants.get(accession) or meta["issuer"]
            meta["matched_via"] = "series_id" if d["id"].startswith("S0") else "nport_lei"
            credit = _credit([src], src)
        elif src == "tickerinside":
            as_of = d["as_of"]
            fields = d["fields"]
            ix = {n: i for i, n in enumerate(fields)}
            rows = [[r[ix["isin"]], r[ix["name"]], r[ix["weight_pct"]], r[ix["asset_class"]], r[ix["source"]],
                     r[ix["as_of"]], r[ix["depth"]], r[ix["via"]],
                     r[ix["country"]] if "country" in ix else None, r[ix["sector"]] if "sector" in ix else None]
                    for r in d["constituents"]]
            unidentified = d["unidentified_lines_pct"]
            reported = d["reported_total_pct"]
            meta["matched_via"] = "openfigi_ticker"
            credit = (
                f"TickerInside, {d['name']} ({d['ticker']}), holdings from the issuer's file of {as_of}, "
                f"{d['source_url']}, licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). "
                "Changed by openbasket: tickers mapped to ISINs, names taken from SEC Form N-PORT."
            )
        else:
            raise ValueError(f"no fixture rule for source {src!r}")
        docs[fid] = document(meta, rows, reported, unidentified, credit, as_of, cap, full_data_url)

    nh = NO_HOLDINGS
    listed_by = {"ishares": "iShares (BlackRock)"}[nh["source"]]
    docs[nh["id"]] = document(
        nh, [], None, None,
        f"Fund listed by {listed_by}; no holdings were available from it.",
        PRODUCT_LIST_DATE, cap, full_data_url, note=nh["note"],
    )

    out = API / "funds"
    for p in out.glob("*.json"):
        if "alias_of" not in json.loads(p.read_text()):
            p.unlink()
    for fid, doc in docs.items():
        _write(out / f"{fid}.json", doc)

    aliases = {}
    for fid, doc in docs.items():
        for isin in doc["isins"]:
            aliases[isin] = fid
        if doc["isin"]:
            aliases[doc["isin"]] = fid
    for p in out.glob("*.json"):
        d = json.loads(p.read_text())
        if "alias_of" in d:
            aliases[p.stem] = d["alias_of"]

    order = sorted(docs.values(), key=lambda x: (["ishares", "spdr", "nport", "tickerinside"].index(x["source"]), x["id"]))
    index = {
        "fields": ["id", "isin", "name", "ticker", "issuer", "kind", "source", "as_of", "resolved_pct", "n_constituents"],
        "rows": [[d["id"], d["isin"], d["name"], d["ticker"], d["issuer"], "fund", d["source"], d["as_of"],
                  _r(d["resolved_pct"], 2), d["n_constituents"]] for d in order],
        "aliases": dict(sorted(aliases.items())),
    }
    _write(API / "index.json", index)

    per_source: dict[str, dict] = {}
    for d in docs.values():
        ids = {r[4] for r in d["constituents"]} | {d["source"]}
        for s in ids:
            e = per_source.setdefault(s, {"funds": set(), "dates": set()})
            e["funds"].add(d["id"])
        for r in d["constituents"]:
            per_source[r[4]]["dates"].add(r[5])
        if not d["constituents"]:
            per_source[d["source"]]["dates"].add(d["as_of"])
    per_source["openfigi"] = {"funds": {d["id"] for d in docs.values() if d["matched_via"] == "openfigi_ticker"},
                              "dates": set()}
    sources = []
    for sid in SOURCES:
        if sid not in per_source:
            continue
        e = per_source[sid]
        dates = sorted(x for x in e["dates"] if x)
        sources.append({"id": sid, **SOURCES[sid], "n_funds": len(e["funds"]),
                        "as_of_min": dates[0] if dates else None, "as_of_max": dates[-1] if dates else None})
    meta = {
        "api_version": 1, "built_at": BUILT_AT, "release_tag": meta_old["release_tag"],
        "release_url": meta_old["release_url"], "full_data_url": full_data_url,
        "n_funds": len(docs), "n_isins": len(aliases), "sources": sources,
    }
    _write(API / "meta.json", meta)
    print(f"Wrote {len(docs)} fund files, {len(aliases)} aliases, {len(sources)} sources to {API}")


def _series_for(holdings: Path, d: dict) -> str:
    import duckdb

    found = duckdb.execute(
        f"""select series_id, count(*) n from read_parquet('{holdings}')
            where upper(series_name) = upper(?) and cast(report_date as date) = cast(? as date) group by 1""",
        [d["name"], d["as_of"]],
    ).fetchall()
    if not found:
        raise LookupError(f"no N-PORT series named {d['name']!r} on {d['as_of']}")
    return min(found, key=lambda x: abs(x[1] - d["n_constituents"]))[0]


def _write(path: Path, doc: object) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, default=ROOT / "data", help="the project's data/ folder")
    parser.add_argument("--cap", type=int, default=500, help="rows kept per fund file (default 500)")
    args = parser.parse_args(argv)
    build(args.data, args.cap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
