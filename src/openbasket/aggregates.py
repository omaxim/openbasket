"""Coarse splits for funds whose holdings may not be republished.

A local (full) run reads issuer files whose terms do not allow openbasket to
republish their holdings. For those funds this module computes openbasket's
own coarse aggregates over the full holdings: the share of the fund in each
sector, country, region and asset class, rounded to whole percent. They are
written to ``public_aggregates/aggregates.json`` so the public site can show a
local_only fund's shape without any of what is behind it.

What the file may contain is fixed by :func:`validate`: fund ids, dates,
source ids, and lists of ``[label, whole percent]`` whose labels come from
fixed vocabularies (our sector list, ISO country codes, regions, asset
classes, and Other / Unknown / Unresolved). No names, ISINs, row weights or
issuer labels. The public build checks the file with the same function.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import duckdb
import pandas as pd

from .countries import country_code
from .datasets import connection
from .sources import redistribution

VERSION = 1
DIMENSIONS = ("sector", "country", "region", "asset_class")
OTHER, UNKNOWN, UNRESOLVED = "Other", "Unknown", "Unresolved"
#: A label below this share of the fund is folded into "Other".
MIN_PCT = 1

#: openbasket's sector list: the eleven GICS sector names, plus two for bonds.
SECTORS = (
    "Information Technology", "Health Care", "Financials", "Consumer Discretionary", "Consumer Staples",
    "Communication Services", "Industrials", "Energy", "Materials", "Utilities", "Real Estate",
    "Government Bonds", "Corporate Bonds",
)
#: Keywords in an issuer's sector label, mapped to openbasket's sector list. Checked in order.
_SECTOR_RULES = (
    (r"real estate|reit|immobil", "Real Estate"),
    (r"information tech|technolog|software|semiconduct|\bit\b|tech hardware", "Information Technology"),
    (r"health|pharma|biotech|medical|life science", "Health Care"),
    (r"communicat|telecom|media|interactive", "Communication Services"),
    (r"consumer (disc|cycl)|discretionary|cyclical|retail|automob|leisure", "Consumer Discretionary"),
    (r"consumer stap|staples|non.?cyclical|defensive|food|beverage|household", "Consumer Staples"),
    (r"financ|bank|insur|capital market", "Financials"),
    (r"industr|aerospace|defen[cs]e|transport|machinery|capital goods", "Industrials"),
    (r"energy|oil|gas", "Energy"),
    (r"material|chemical|metal|mining|basic", "Materials"),
    (r"utilit", "Utilities"),
    (r"treasur|government|sovereign|agency|municipal|supranational", "Government Bonds"),
    (r"corporate|credit", "Corporate Bonds"),
)
REGIONS = ("North America", "Europe", "Asia-Pacific", "Latin America", "Middle East & Africa")
_REGION_OF = {}
for _region, _codes in {
    "North America": "US CA BM",
    "Europe": "GB IE FR DE NL BE LU CH AT IT ES PT SE NO DK FI IS PL CZ HU SK SI HR RO BG GR CY MT EE LV LT JE GG IM LI MC",
    "Asia-Pacific": "JP CN HK TW KR SG IN ID TH MY PH VN AU NZ PK BD LK MO KY",
    "Latin America": "BR MX CL CO PE AR UY PA VG BS",
    "Middle East & Africa": "IL SA AE QA KW BH OM TR EG ZA NG KE MA",
}.items():
    for _c in _codes.split():
        _REGION_OF[_c] = _region
ASSET_CLASSES = ("Equity", "Bonds", "Cash", "Derivatives", "Funds", "Other assets")
_ISIN_LIKE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b")


def sector_of(label: object, asset_class: object = None) -> str:
    """openbasket's sector for an issuer's sector label (and the row's asset class)."""
    text = str(label or "").strip().lower()
    for pattern, sector in _SECTOR_RULES:
        if text and re.search(pattern, text):
            return sector
    ac = asset_class_of(asset_class)
    if ac == "Bonds":
        return "Corporate Bonds" if "corp" in text else UNKNOWN
    return OTHER if text else UNKNOWN


def asset_class_of(label: object) -> str:
    text = str(label or "").lower()
    if not text:
        return UNKNOWN
    if "derivative" in text or "future" in text or "swap" in text or "forward" in text:
        return "Derivatives"
    if text.startswith(("equity", "preferred")) or "stock" in text or "share" in text:
        return "Equity"
    if any(k in text for k in ("debt", "bond", "loan", "asset-backed", "mortgage", "fixed income", "note")):
        return "Bonds"
    if "cash" in text or "money market" in text or "short-term" in text or "repurchase" in text:
        return "Cash"
    if "fund" in text:
        return "Funds"
    return "Other assets"


def region_of(code: str | None) -> str:
    return _REGION_OF.get(code or "", UNKNOWN if not code else OTHER)


def _split(sums: dict[str, float], unresolved_pct: float) -> list[list]:
    """Whole-percent ``[label, pct]`` list, largest first; small labels folded into Other, Unresolved last."""
    kept, other = {}, 0.0
    for label, pct in sums.items():
        if abs(pct) >= MIN_PCT - 0.5 and label not in (OTHER,):
            kept[label] = pct
        else:
            other += pct
    out = [[k, int(round(v))] for k, v in sorted(kept.items(), key=lambda kv: -kv[1]) if int(round(v)) != 0]
    if int(round(other)):
        out.append([OTHER, int(round(other))])
    out.append([UNRESOLVED, int(round(unresolved_pct or 0.0))])
    return out


def fund_aggregates(rows: pd.DataFrame, unresolved_pct: float | None) -> dict[str, list[list]]:
    """Splits for one fund from its full constituent rows (weight_pct, sector, country, asset_class)."""
    sums: dict[str, dict[str, float]] = {d: {} for d in DIMENSIONS}
    for r in rows.itertuples(index=False):
        w = float(r.weight_pct or 0.0)
        code = country_code(r.country) or None
        labels = {
            "sector": sector_of(r.sector, r.asset_class),
            "country": code or UNKNOWN,
            "region": region_of(code),
            "asset_class": asset_class_of(r.asset_class),
        }
        for d, label in labels.items():
            sums[d][label] = sums[d].get(label, 0.0) + w
    return {d: _split(sums[d], unresolved_pct or 0.0) for d in DIMENSIONS}


def build(universe_funds: pd.DataFrame, universe_constituents: duckdb.DuckDBPyRelation, params: dict,
          sector_map: pd.DataFrame | None = None) -> dict:
    """The aggregates document for every fund whose holdings come from a ``local_only`` source.

    A row whose issuer file gives no sector takes its company's sector from
    ``sector_map`` (company ISIN -> GICS sector, see
    :func:`openbasket.sources.financedatabase.sector_map`), as the public build does.
    """
    funds = universe_funds[universe_funds["source"].map(lambda s: bool(s) and redistribution(str(s)) == "local_only")]
    funds = funds[funds["n_constituents"].fillna(0) > 0]
    con = connection()
    con.register("_agg_rel", universe_constituents)
    has_map = sector_map is not None and len(sector_map) > 0
    if has_map:
        con.register("_agg_sectors", sector_map[["isin", "sector"]])
    sector = "coalesce(nullif(trim(r.sector), ''), m.sector)" if has_map else "r.sector"
    join = "left join _agg_sectors m on r.constituent_isin = m.isin" if has_map else ""
    try:
        rows = con.execute(
            f"select r.fund_id, r.weight_pct, {sector} as sector, r.country, r.asset_class from _agg_rel r {join} "
            "where r.fund_id in (select unnest(?))", [funds["id"].tolist()]
        ).df()
    finally:
        con.unregister("_agg_rel")
        if has_map:
            con.unregister("_agg_sectors")
    by_fund = dict(tuple(rows.groupby("fund_id"))) if len(rows) else {}
    out = {}
    for f in funds.to_dict("records"):
        r = by_fund.get(f["id"])
        if r is None or r.empty:
            continue
        out[f["id"]] = {
            "isins": [i for i in (f.get("isins") if f.get("isins") is not None else []) if i],
            "as_of": f.get("as_of"),
            "holdings_source": f.get("source"),
            **fund_aggregates(r, f.get("unresolved_pct")),
        }
    doc = {
        "version": VERSION,
        "computed_at": params.get("computed_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": ("openbasket's own aggregates over the fund's full holdings: shares of net assets by "
                   "openbasket's sector list, ISO country, region and asset class, rounded to whole percent; "
                   "labels under 1% are folded into Other. Rows whose issuer gives no sector take their "
                   "company's GICS sector from FinanceDatabase (MIT) by ISIN. No holdings are included."),
        "n_funds": len(out),
        "funds": out,
    }
    validate(doc)
    return doc


class AggregatesError(ValueError):
    """The aggregates file holds something other than coarse splits."""


def validate(doc: dict) -> None:
    """Raise :class:`AggregatesError` unless ``doc`` holds only coarse splits from fixed vocabularies."""
    if set(doc) - {"version", "computed_at", "method", "n_funds", "funds"}:
        raise AggregatesError(f"unexpected top-level keys {sorted(set(doc) - {'version', 'computed_at', 'method', 'n_funds', 'funds'})}")
    allowed = {
        "sector": set(SECTORS),
        "region": set(REGIONS),
        "asset_class": set(ASSET_CLASSES),
    }
    specials = {OTHER, UNKNOWN, UNRESOLVED}
    for fid, entry in (doc.get("funds") or {}).items():
        if set(entry) - {"isins", "as_of", "holdings_source", *DIMENSIONS}:
            raise AggregatesError(f"{fid}: unexpected keys {sorted(set(entry) - {'isins', 'as_of', 'holdings_source', *DIMENSIONS})}")
        for d in DIMENSIONS:
            for item in entry.get(d) or []:
                if not (isinstance(item, list) and len(item) == 2 and isinstance(item[1], int)):
                    raise AggregatesError(f"{fid}.{d}: {item!r} is not [label, whole percent]")
                label = item[0]
                if label in specials:
                    continue
                ok = re.fullmatch(r"[A-Z]{2}", label) is not None if d == "country" else label in allowed[d]
                if not ok:
                    raise AggregatesError(f"{fid}.{d}: label {label!r} is not in openbasket's vocabulary")
        if not isinstance(entry.get("holdings_source"), (str, type(None))):
            raise AggregatesError(f"{fid}: holdings_source must be a source id")
    # Fund ids and their own ISINs name the fund and may appear; nothing else may look like an ISIN.
    text = " ".join(str(doc.get(k) or "") for k in ("computed_at", "method")) + " " + " ".join(
        str(item[0]) for e in (doc.get("funds") or {}).values() for d in DIMENSIONS for item in (e.get(d) or []))
    if _ISIN_LIKE.search(text):
        raise AggregatesError("an ISIN-like string appears in the splits")
