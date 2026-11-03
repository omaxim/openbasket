"""BNP Paribas Easy (BNP Paribas Asset Management) ETFs and index funds.

Fund list
    ``https://api.bnpparibas-am.com/push/sharesearchv2/<profile>/<language>``
    is the share search behind the fund pages of each country site. It lists
    every share class sold on that site with its ISIN, compartment (portfolio)
    id, currency, asset class, region and flags. The BNP Paribas Easy share
    classes (``compart_name`` contains ``EASY``) of several sites are merged.

    The profile code is the ``defaultVal`` that every public fund page sets in
    its inline JavaScript (``const defaultVal = "IP_UK-FSE;ENG;GBR"``); it is
    the code the page itself uses before a visitor picks an investor type, so
    no cookie or investor-type gate is involved.

Holdings
    ``https://api.bnpparibas-am.com/push/fundsheet/<profile>/<language>/<country>/<ISIN>``
    returns the fund page data, whose ``publications`` list a
    ``DOC_ACCOUNTING_INVENTORY`` workbook per share class on
    ``https://docfinder.bnpparibas-am.com/api/files/<uuid>/<version>``. The
    workbook has a ``Data Date``, a header block with the share's
    ``Replication`` label, then a table ``Asset Class | Name of entity | ISIN |
    Weight`` ending in a ``TOTAL`` row. Weights are **fractions** of net assets
    rounded to four decimals (``TOTAL`` is 1); they are multiplied by 100 here.
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pandas as pd

from ... import http
from ...identifiers import is_valid_isin
from . import HOLDING_COLUMNS, PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

logger = logging.getLogger(__name__)

NAME = "bnp-easy"
API = "https://api.bnpparibas-am.com/push"
WEBSITE = "https://www.bnpparibas-am.com"

#: Country sites read by default: site path -> (profile, language, country), as
#: set in ``defaultVal`` on each site's fund pages (checked 2026-11-03). Sites
#: are read in this order and a share class keeps the first site it appears on,
#: so English-language sites come first. Italy (``PV_IT-FSE;ITA;ITA``), Spain
#: (``IP_ES-FSE;SPA;SPA``), Austria (``IP_AT-FSE;GER;AUT``) and Sweden
#: (``AM_SE-PRO;ENG;SWE``) were also read and added no share class.
SITES: dict[str, tuple[str, str, str]] = {
    "en-gb": ("IP_UK-FSE", "ENG", "GBR"),
    "en-ch": ("IP_CH-FSE", "ENG", "CHE"),
    "en-lu": ("PV_LU-FSE", "ENG", "LUX"),
    "fr-fr": ("PV_FR-FSE", "FRE", "FRA"),
    "de-de": ("PV_DE-FSE", "GER", "DEU"),
    "en-nl": ("PV_NL-FSE", "ENG", "NLD"),
}

_ASSET_SLUG = {"Equity": "equity", "Fixed Income": "fixed-income", "Balanced": "balanced"}

#: The inventory's ``Replication`` label (English and French files).
_REPLICATION = {
    "full": "physical",
    "complete": "physical",
    "complète": "physical",
    "physical": "physical",
    "physique": "physical",
    "alpha enhanced": "physical",
    "optimised": "sampled",
    "optimized": "sampled",
    "optimisée": "sampled",
    "sampling": "sampled",
    "synthetic": "swap",
    "synthétique": "swap",
}

#: Funds whose strategy is reached through total return swaps, as their
#: fund pages say ("Exposure to the Strategy is achieved synthetically by
#: entering TRS"); none of this shows in the share search.
_SWAP_STRATEGIES = re.compile(r"PREMIUM INCOME|EQUITY BUFFER|LONG/SHORT|MANAGED FUTURES|ENHANCED ROLL")
_EUROPEAN_REGIONS = {"Europe", "Eurozone"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def guess_replication(share: dict) -> str | None:
    """Replication from the share-search fields, or None when they do not tell.

    ``swap`` when the fund is eligible for the French PEA (which requires 75%
    EU equities) but tracks a non-European or non-equity index, when its asset
    class is commodities or other, or when it is one of the TRS strategy funds.
    The inventory's own ``Replication`` label is reported in the holdings notes.
    """
    name = (share.get("compart_name") or "").upper()
    flags = share.get("flags") or []
    if share.get("eng_asset_class") == "Other" or share.get("sub_asset_class") == "Commodities":
        return "swap"
    if _SWAP_STRATEGIES.search(name):
        return "swap"
    if "pea_flag" in flags and (share.get("eng_asset_class") != "Equity" or share.get("region") not in _EUROPEAN_REGIONS):
        return "swap"
    return None


def needs_inventory_check(share: dict) -> bool:
    """PEA-eligible funds on pan-European indices: some are synthetic, some not.

    The French PEA requires 75% EU equities, so a fund tracking an index with
    UK or Swiss shares may hold a substitute basket. On 2026-11-03 Low Vol
    Europe and STOXX Europe 600 were ``Synthetic`` and Bloomberg Europe Defense
    ``Full``; nothing in the share search tells them apart.
    """
    return guess_replication(share) is None and "pea_flag" in (share.get("flags") or []) and share.get("region") == "Europe"


def parse_share_search(payload: dict, site: str) -> pd.DataFrame:
    """One site's share search to product rows (BNP Paribas Easy only).

    Adds a private ``_check`` column (True where :func:`needs_inventory_check`).
    """
    rows = []
    for s in payload.get("shares") or []:
        if "EASY" not in (s.get("compart_name") or "").upper():
            continue
        isin = (s.get("isin_code") or "").strip().upper()
        if not is_valid_isin(isin):
            continue
        name = s.get("legal_name") or s.get("compart_name")
        asset = s.get("eng_asset_class")
        rows.append(
            {
                "issuer": NAME,
                "site": site,
                "portfolio_id": str(s["compartment_id"]),
                "isin": isin,
                "ticker": None,
                "fund_name": name,
                "domicile": isin[:2],
                "currency": s.get("val_currency"),
                "asset_class": asset,
                "replication": guess_replication(s),
                "product_page_url": f"{WEBSITE}/{site}/fundsheet/{_ASSET_SLUG.get(asset, 'other')}/{_slug(name)}-{isin.lower()}",
                "holdings_url": fundsheet_url(site, isin),
                "_check": needs_inventory_check(s),
            }
        )
    df = pd.DataFrame(rows, columns=PRODUCT_COLUMNS + ["_check"]).astype(object)
    return df.where(df.notna(), None)


def _inventory_replication(site: str, isin: str) -> str | None:
    """The ``Replication`` label of one share's inventory, mapped (two requests)."""
    r = http.get(fundsheet_url(site, isin))
    if r.status_code != 200:
        return None
    doc = inventory_document(r.json())
    if doc is None:
        return None
    resp = http.get(doc["url"])
    if resp.status_code != 200:
        return None
    try:
        return parse_inventory(resp.content)[1].get("replication")
    except ValueError:
        return None


def products(sites: list[str] | tuple[str, ...] = tuple(SITES), check_replication: bool = True) -> pd.DataFrame:
    """Every BNP Paribas Easy share class on the given country sites.

    With ``check_replication``, the few portfolios :func:`needs_inventory_check`
    picks (11 on 2026-11-03) get their replication from one share's
    inventory, two requests each.
    """
    frames = []
    for site in sites:
        profile, lang, _ = SITES[site]
        url = f"{API}/sharesearchv2/{profile}/{lang}"
        r = http.get(url, timeout=120)
        r.raise_for_status()
        frames.append(parse_share_search(r.json(), site))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PRODUCT_COLUMNS + ["_check"])
    df = df.drop_duplicates("isin").reset_index(drop=True)
    if check_replication:
        for pid, grp in df[df["_check"].astype(bool)].groupby("portfolio_id"):
            first = grp.sort_values("fund_name", key=lambda s: ~s.str.contains("ETF")).iloc[0]
            label = _inventory_replication(first["site"], first["isin"])
            if label:
                df.loc[df["portfolio_id"] == pid, "replication"] = label
    df = df[PRODUCT_COLUMNS].astype(object)
    return df.where(df.notna(), None)


def fundsheet_url(site: str, isin: str) -> str:
    profile, lang, country = SITES[site]
    return f"{API}/fundsheet/{profile}/{lang}/{country}/{isin}"


def inventory_document(fundsheet: dict) -> dict | None:
    """The share's holdings workbook from a fundsheet, English first."""
    docs = [
        d
        for lang, pub in (fundsheet.get("publications") or {}).items()
        if isinstance(pub, dict)
        for d in pub.get("documents") or []
        if d.get("category") == "DOC_ACCOUNTING_INVENTORY" and d.get("url")
    ]
    docs.sort(key=lambda d: (d.get("language") != "ENG", d.get("language") or ""))
    return docs[0] if docs else None


def _sibling_isins(fundsheet: dict, isin: str) -> list[str]:
    """Other share classes of the same portfolio, ETF classes first."""
    shares = ((fundsheet.get("fundshare_selection") or {}).get("shares")) or []
    others = [s for s in shares if s.get("isin_code") and s["isin_code"] != isin]
    others.sort(key=lambda s: "ETF" not in (s.get("share_type") or "").upper())
    return [s["isin_code"] for s in others]


def _clean(v: object) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def _norm(v: object) -> str:
    return (_clean(v) or "").lower()


def _to_date(v: object) -> str | None:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = _clean(v)
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _asset_class(label: str | None) -> str:
    s = (label or "").lower()
    if s in ("equity", "action", "actions", "equities"):
        return "equity"
    if s in ("bond", "bonds", "obligation", "obligations", "fixed income"):
        return "debt"
    if "cash" in s or "liquidit" in s or "money market" in s:
        return "cash"
    if any(w in s for w in ("future", "swap", "forward", "option", "derivative", "dérivé")):
        return "derivative"
    if any(w in s for w in ("fund", "ucits", "ucit", "opc", "etf")):
        return "fund"
    return "other"


_HEADER = {
    "asset": ("asset class", "classe d'actifs"),
    "name": ("name of entity", "nom de l'entité"),
    "isin": ("isin", "code isin"),
    "weight": ("weight", "poids"),
}


def parse_inventory(content: bytes) -> tuple[str | None, dict, pd.DataFrame]:
    """Read an inventory workbook into ``(as_of, info, holdings)``.

    ``info`` has the header block (``fund_name``, ``share_isin``,
    ``share_category``, ``replication_label``, ``replication``). Holdings carry
    :data:`HOLDING_COLUMNS`, with ``weight_pct`` in percent.
    """
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    ws.reset_dimensions()  # the files declare a too-small sheet dimension
    rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
    wb.close()

    as_of = None
    info: dict = {}
    hdr = None
    for i, row in enumerate(rows):
        cells = [_norm(c) for c in row]
        if as_of is None and ("data date" in cells or "date des données" in cells):
            j = cells.index("data date") if "data date" in cells else cells.index("date des données")
            as_of = next((_to_date(c) for c in row[j + 1 :] if _clean(c)), None)
        if not info and ("replication" in cells or "réplication" in cells) and i + 1 < len(rows):
            nxt = rows[i + 1]
            col = {c: k for k, c in enumerate(cells) if c}

            def at(*labels):
                k = next((col[x] for x in labels if x in col), None)
                return _clean(nxt[k]) if k is not None and k < len(nxt) else None

            label = at("replication", "réplication")
            info = {
                "fund_name": at("fund name", "nom du fonds"),
                "share_isin": at("isin", "code isin"),
                "share_category": at("share category", "catégorie de la part"),
                "replication_label": label,
                "replication": _REPLICATION.get((label or "").lower()),
            }
        if any(c in _HEADER["weight"] for c in cells) and any(c in _HEADER["isin"] for c in cells) and any(
            c in _HEADER["asset"] for c in cells
        ):
            hdr = i
            break
    if hdr is None:
        raise ValueError("BNP Paribas inventory has no 'Asset Class | Name of entity | ISIN | Weight' header")

    cells = [_norm(c) for c in rows[hdr]]
    idx = {k: next(j for j, c in enumerate(cells) if c in labels) for k, labels in _HEADER.items()}

    body, total = [], None
    for row in rows[hdr + 1 :]:
        get = lambda k: row[idx[k]] if idx[k] < len(row) else None  # noqa: E731
        if _norm(get("isin")) == "total" or _norm(get("name")) == "total":
            total = pd.to_numeric(get("weight"), errors="coerce")
            break  # French files repeat the whole table after the total
        if _clean(get("name")) is None and _clean(get("weight")) is None:
            continue
        body.append(row)

    out = pd.DataFrame(index=range(len(body)), columns=HOLDING_COLUMNS, dtype=object)
    if body:
        g = lambda k: [r[idx[k]] if idx[k] < len(r) else None for r in body]  # noqa: E731
        weights = pd.to_numeric(pd.Series(g("weight")), errors="coerce")
        # TOTAL is 1 in every file seen: the weights are fractions of net assets.
        fraction = (total is not None and not pd.isna(total) and total <= 1.5) or (total is None and weights.sum() <= 1.5)
        out["name"] = [_clean(v) for v in g("name")]
        isins = [(_clean(v) or "").upper() for v in g("isin")]
        out["isin"] = [v if is_valid_isin(v) else None for v in isins]
        out["weight_pct"] = (weights * 100).round(8) if fraction else weights
        out["asset_class"] = [_asset_class(_clean(v)) for v in g("asset")]
    out["weight_pct"] = pd.to_numeric(out["weight_pct"], errors="coerce")
    info["total_reported"] = None if total is None or pd.isna(total) else float(total)
    return as_of, info, out


def _notes(info: dict, source_isin: str, isin: str, reference_date: str | None) -> list[str]:
    notes = []
    if source_isin != isin:
        notes.append(
            f"No inventory is published for {isin}; the holdings are those of share class {source_isin} of the same portfolio."
        )
    label = info.get("replication_label")
    if label:
        notes.append(f"Replication (issuer label): {label}" + (f" -> {info['replication']}" if info.get("replication") else ""))
    if info.get("replication") == "swap":
        notes.append(
            "Synthetic replication: these lines are the substitute basket the fund holds; its index exposure comes "
            "from a total return swap that the file does not list."
        )
    notes.append(
        "Weights are fractions of net assets rounded to 0.0001 (0.01%) by the issuer; securities on the balance "
        "sheet only (no cash or derivatives lines)."
    )
    if reference_date:
        notes.append(f"Published {reference_date}.")
    return notes


def _empty() -> pd.DataFrame:
    df = pd.DataFrame(columns=HOLDING_COLUMNS)
    df["weight_pct"] = pd.Series(dtype=float)
    return df


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    """Full holdings of one share class from its inventory workbook."""
    isin = product["isin"]
    site = product.get("site") if product.get("site") in SITES else "en-gb"
    cache = meta_path = None
    if ctx is not None and ctx.cache_dir:
        base = Path(ctx.cache_dir) / f"{NAME}_{isin}_{date.today().isoformat()}"
        cache, meta_path = base.with_suffix(".xlsx"), base.with_suffix(".json")
        if cache.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            as_of, info, df = parse_inventory(cache.read_bytes())
            return IssuerHoldings(as_of, df, meta["url"], meta["notes"])

    fs_url = fundsheet_url(site, isin)
    r = http.get(fs_url)
    if r.status_code == 404:
        return IssuerHoldings(None, _empty(), fs_url, [f"{isin} is not on the BNP Paribas AM {site} site."])
    r.raise_for_status()
    fundsheet = r.json()
    doc, source_isin = inventory_document(fundsheet), isin
    if doc is None:
        for other in _sibling_isins(fundsheet, isin)[:4]:
            r2 = http.get(fundsheet_url(site, other))
            if r2.status_code == 200:
                doc = inventory_document(r2.json())
                if doc is not None:
                    source_isin = other
                    break
    if doc is None:
        return IssuerHoldings(
            None, _empty(), fs_url, ["BNP Paribas AM publishes no holdings inventory for this fund on its fund page."]
        )

    logger.info("Fetching BNP Paribas Easy inventory for %s: %s", isin, doc["url"])
    resp = http.get(doc["url"])
    resp.raise_for_status()
    content = resp.content
    as_of, info, df = parse_inventory(content)
    notes = _notes(info, source_isin, isin, doc.get("reference_date"))
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(content)
        meta_path.write_text(json.dumps({"url": doc["url"], "notes": notes}))
    return IssuerHoldings(as_of, df, doc["url"], notes)


register(
    IssuerAdapter(
        name=NAME,
        title="BNP Paribas Easy (BNP Paribas Asset Management)",
        homepage="https://www.bnpparibas-am.com/en-gb/",
        credit="Holdings: BNP Paribas Asset Management, fund inventory files from bnpparibas-am.com",
        licence=(
            "No open licence. Public fund pages and API, no login. Each inventory workbook states that it is "
            "BNP Paribas Asset Management's exclusive property and that reproduction or distribution is forbidden "
            "without its prior written authorisation; republishing these holdings needs BNPP AM's permission."
        ),
        regions=("LU", "IE", "FR", "CH", "GB", "EU"),
        products=products,
        holdings=holdings,
        frequency="weekly",
    )
)
