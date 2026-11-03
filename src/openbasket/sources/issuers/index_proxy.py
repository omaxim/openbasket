"""Index composition proxies for physically replicating funds whose issuer
does not publish holdings openbasket can reach.

UBS, Invesco EMEA, WisdomTree, Fidelity and Franklin block automated access
to their sites, and index providers (SIX, Euronext, STOXX, Solactive, MSCI,
S&P, FTSE, Nasdaq) license their constituent weights rather than publishing
them for reuse (see ``docs/source/sources/exchange-index.md``). European
exchanges do not publish ETF portfolio composition files either.

What *is* public is the full holdings file of another physically, fully
replicating ETF on the same index, published daily by its own issuer
(iShares, SPDR, ...). For a full-replication fund, that file is the best free
picture of the index composition, and therefore of the target fund.

This adapter maps a target fund to its index (a small curated list below, or
a fund directory's index and replication fields via
:func:`match_directory`), fetches a peer fund's holdings and returns them
labelled for what they are::

    index composition proxy: holdings of <peer> (<ISIN>) tracking <index>,
    positions as of <date>, used as a proxy for <target>; not the fund's
    own holdings

It is used **only** for physically, fully replicating target funds. Swap
funds and sampled or optimised funds are refused, because their holdings
need not resemble the index.

Weights are percent of the *peer's* net assets as its issuer reports them;
they are never rescaled.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from ... import http
from ...identifiers import is_valid_cusip, is_valid_isin
from .. import ishares, spdr
from . import (
    HOLDING_COLUMNS,
    PRODUCT_COLUMNS,
    FetchContext,
    IssuerAdapter,
    IssuerHoldings,
    register,
)

logger = logging.getLogger(__name__)

NAME = "index_proxy"
KIND = "index composition proxy"

#: The only replication method this adapter accepts for a target fund.
FULL = "physical-full"


@dataclass(frozen=True)
class Peer:
    """A physically, fully replicating fund whose issuer publishes full holdings."""

    issuer: str           # "ishares", "spdr" or any registered adapter name
    isin: str             # the peer's share-class ISIN
    name: str
    site: str | None = None  # iShares site ("uk", "us", "ch") or SPDR region ("us", "emea")
    page: str | None = None  # the peer's product page, shown as the source link


_ISH = "https://www.ishares.com"


@dataclass(frozen=True)
class IndexSpec:
    key: str
    name: str
    provider: str                 # index provider, cited in the notes
    aliases: tuple[str, ...]      # how fund directories write the index name
    peers: tuple[Peer, ...]       # tried in order


@dataclass(frozen=True)
class Target:
    """A fund whose own holdings are not reachable, mapped to its index."""

    isin: str
    fund_name: str
    issuer: str
    index: str                    # key into INDICES
    replication: str              # must be FULL to be used
    domicile: str | None = None
    currency: str | None = None
    evidence: str = ""            # where the replication method was confirmed


INDICES: dict[str, IndexSpec] = {
    s.key: s
    for s in [
        IndexSpec(
            "SMI", "Swiss Market Index (SMI)", "SIX",
            ("smi", "swiss market index", "smi index", "smi (tr)", "smi total return", "swiss market index (smi)"),
            (Peer("ishares", "CH0008899764", "iShares SMI ETF (CH)", "ch",
                  _ISH + "/ch/individuals/en/products/261154/ishares-smi-ch-fund"),),
        ),
        IndexSpec(
            "SLI", "Swiss Leader Index (SLI)", "SIX",
            ("sli", "swiss leader index", "sli swiss leader index", "sli (tr)"),
            (Peer("ishares", "CH0031768937", "iShares SLI ETF (CH)", "ch",
                  _ISH + "/ch/individuals/en/products/261153/ishares-sli-ch-fund"),),
        ),
        IndexSpec(
            "SP500", "S&P 500", "S&P Dow Jones Indices",
            ("s&p 500", "s&p 500 index", "s&p 500 net total return", "s&p 500 (nr)", "s&p 500 net return"),
            (
                Peer("ishares", "IE00B5BMR087", "iShares Core S&P 500 UCITS ETF (Acc)", "uk",
                     _ISH + "/uk/individual/en/products/253743/ishares-sp-500-b-ucits-etf-acc-fund"),
                Peer("spdr", "IE00B6YX5C33", "SPDR S&P 500 UCITS ETF", "emea",
                     "https://www.ssga.com/uk/en_gb/intermediary/etfs/state-street-spdr-sp-500-ucits-etf-dist-spy5-gy"),
            ),
        ),
        IndexSpec(
            "NDX", "Nasdaq-100", "Nasdaq",
            ("nasdaq-100", "nasdaq 100", "nasdaq-100 index", "nasdaq-100 notional net total return"),
            (Peer("ishares", "IE00B53SZB19", "iShares Nasdaq 100 UCITS ETF (Acc)", "uk",
                  _ISH + "/uk/individual/en/products/253741/ishares-nasdaq-100-ucits-etf"),),
        ),
        IndexSpec(
            "MSCI_EMU", "MSCI EMU", "MSCI",
            ("msci emu", "msci emu index", "msci emu net return", "msci emu (nr)", "msci emu nr eur"),
            (Peer("ishares", "IE00B53QG562", "iShares Core MSCI EMU UCITS ETF EUR (Acc)", "uk",
                  _ISH + "/uk/individual/en/products/253729/ishares-msci-emu-ucits-etf"),),
        ),
    ]
}

#: Blocked-issuer funds mapped by hand. Replication was checked against the
#: issuer's factsheet or several independent fund databases (see the docs).
TARGETS: list[Target] = [
    Target("CH0017142719", "UBS ETF (CH) - SMI (CHF) A-dis", "UBS", "SMI", FULL, "CH", "CHF",
           "UBS factsheet: physical, full replication"),
    Target("CH0032912732", "UBS ETF (CH) - SLI (CHF) A-dis", "UBS", "SLI", FULL, "CH", "CHF",
           "UBS factsheet: physical (full replicated)"),
    Target("IE00B7K93397", "UBS Core S&P 500 UCITS ETF USD dis", "UBS", "SP500", FULL, "IE", "USD",
           "fund databases: physical, full replication"),
    Target("LU0147308422", "UBS Core MSCI EMU UCITS ETF EUR dis", "UBS", "MSCI_EMU", FULL, "LU", "EUR",
           "fund databases: physical, full replication"),
    Target("IE0032077012", "Invesco EQQQ Nasdaq-100 UCITS ETF Dist", "Invesco", "NDX", FULL, "IE", "USD",
           "Invesco product sheet: full physical replication"),
    Target("IE00BFZXGZ54", "Invesco EQQQ Nasdaq-100 UCITS ETF Acc", "Invesco", "NDX", FULL, "IE", "USD",
           "Invesco factsheet: physical replication"),
]

#: Credit lines for peer issuers, shown with every row.
PEER_CREDIT = {
    "ishares": ("BlackRock (iShares)", "https://www.ishares.com"),
    "spdr": ("State Street Global Advisors (SPDR)", "https://www.ssga.com"),
}

#: iShares sites a peer can sit on. ``uk`` and ``us`` follow ``ishares.SITES``;
#: ``ch`` is the Swiss site, served by the same product-data service as the UK.
ISHARES_SITES = {
    "uk": {"country": "gb", "language": "en", "siteName": "ishares-uk",
           "targetSite": "ishares-uk", "locale": "en_GB", "data_site": "uk-retail01-product-data"},
    "ch": {"country": "ch", "language": "en", "siteName": "ishares-ch",
           "targetSite": "ishares-ch", "locale": "en_CH", "data_site": "uk-retail01-product-data"},
    "us": {"targetSite": "us-ishares", "locale": "en_US", "data_site": "blk-one01-product-data"},
}


# -- replication ------------------------------------------------------------

def normalise_replication(text: object) -> str | None:
    """Map a replication label from any source to ``physical-full``,
    ``physical-sampled``, ``swap`` or None when it cannot be told."""
    if not isinstance(text, str) or not text.strip():
        return None
    t = text.strip().lower()
    if any(k in t for k in ("swap", "synthetic", "synthetisch", "derivative", "indirect")):
        return "swap"
    if any(k in t for k in ("sampl", "optimi", "stratif", "partial", "optimiert", "teilweise")):
        return "physical-sampled"
    if any(k in t for k in ("full", "vollständig", "vollstaendig", "complete", "replicated")):
        return FULL
    if t in ("physical", "physisch", "direct", "physical-full"):
        # "Physical" alone does not say full; it is not enough.
        return None
    return None


# -- products ---------------------------------------------------------------

def _product_row(t: Target, idx: IndexSpec) -> dict:
    peer = idx.peers[0]
    return {
        "issuer": NAME,
        "site": None,
        "portfolio_id": f"{idx.key}:{t.isin}",
        "isin": t.isin,
        "ticker": None,
        "fund_name": t.fund_name,
        "domicile": t.domicile,
        "currency": t.currency,
        "asset_class": "Equity",
        "replication": "physical",
        "product_page_url": peer.page,  # the source shown is the peer fund's page
        "holdings_url": None,           # resolved from the peer's issuer at fetch time
        "index_key": idx.key,
        "index_name": idx.name,
        "index_provider": idx.provider,
        "fund_issuer": t.issuer,
        "peer_isin": peer.isin,
        "peer_name": peer.name,
        "replication_method": t.replication,
        "replication_evidence": t.evidence,
        "kind": KIND,
    }


def products(targets: list[Target] | None = None) -> pd.DataFrame:
    """One row per mapped target fund. Targets that are not full physical
    replication are left out with a warning."""
    rows = []
    for t in TARGETS if targets is None else targets:
        if t.replication != FULL:
            logger.warning("%s is %s, not %s; no index proxy", t.isin, t.replication, FULL)
            continue
        rows.append(_product_row(t, INDICES[t.index]))
    cols = PRODUCT_COLUMNS + [c for c in (rows[0] if rows else {}) if c not in PRODUCT_COLUMNS]
    return pd.DataFrame(rows, columns=cols or PRODUCT_COLUMNS)


def index_for(name: object) -> IndexSpec | None:
    """The index spec whose alias matches a directory's index name exactly
    (case and spacing ignored), else None. No fuzzy matching: "MSCI EMU
    Small Cap" must not become "MSCI EMU"."""
    if not isinstance(name, str):
        return None
    key = re.sub(r"\s+", " ", name.replace("®", "").strip().lower())
    for spec in INDICES.values():
        if key == spec.key.lower() or key in spec.aliases:
            return spec
    return None


def match_directory(directory: pd.DataFrame, index_col: str = "index", replication_col: str = "replication") -> pd.DataFrame:
    """Products rows for directory funds on a known index with full physical
    replication. ``directory`` needs ``isin`` and ``fund_name`` plus the two
    named columns (for example SIX's ``FundUnderlyingDescription`` and
    ``ReplicationMethodDesc``). Funds already in :data:`TARGETS` keep their
    curated row."""
    known = {t.isin for t in TARGETS}
    rows = []
    for r in directory.to_dict("records"):
        isin = r.get("isin")
        if not isinstance(isin, str) or isin in known:
            continue
        spec = index_for(r.get(index_col))
        if spec is None or normalise_replication(r.get(replication_col)) != FULL:
            continue
        if any(p.isin == isin for p in spec.peers):
            continue
        t = Target(isin, r.get("fund_name") or isin, r.get("issuer") or "unknown", spec.key, FULL,
                   r.get("domicile"), r.get("currency"),
                   f"fund directory: {r.get(replication_col)}")
        rows.append(_product_row(t, spec))
    return pd.DataFrame(rows, columns=products().columns)


# -- peer holdings ----------------------------------------------------------

def _cache_path(ctx: FetchContext | None, name: str) -> Path | None:
    if ctx is None or not ctx.cache_dir:
        return None
    return Path(ctx.cache_dir) / f"index_proxy_{name}_{date.today().isoformat()}.json"


def _cached_json(path: Path | None, fetch) -> object:
    if path is not None and path.exists():
        return json.loads(path.read_text())
    data = fetch()
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    return data


def _get_json(url: str, params: dict | None = None) -> object:
    r = http.get(url, params=params)
    r.raise_for_status()
    return r.json()


def ishares_portfolio(site: str, isin: str, ctx: FetchContext | None = None) -> dict:
    """``{portfolio_id, fund_name, product_page_url}`` for an iShares ISIN,
    from the site's product screener (only these fields are cached)."""

    def fetch() -> dict:
        if site == "us":
            cfg = ishares.SITES["us"]
            table = _get_json(cfg["screener_url"], cfg["screener_params"])["data"]["tableData"]
            names = [c["name"] for c in table["columns"]]
            rows = [dict(zip(names, r)) for r in table["data"]]
        else:
            cfg = ISHARES_SITES[site]
            params = {k: cfg[k] for k in ("country", "language", "siteName")} | {"userType": "individual"}
            rows = [p | {"portfolioId": p.get("portfolioId") or pid} for pid, p in _get_json(ishares.SCREENER_URL, params).items()]
        out = {}
        for p in rows:
            i = p.get("isin")
            if isinstance(i, str) and i.strip() not in ("", "-"):
                out[i.strip().upper()] = {
                    "portfolio_id": str(p.get("portfolioId")),
                    "fund_name": p.get("fundName"),
                    "product_page_url": ishares.HOST + p["productPageUrl"] if p.get("productPageUrl") else None,
                }
        return out

    found = _cached_json(_cache_path(ctx, f"ishares_{site}_screener"), fetch)
    if isin not in found:
        raise LookupError(f"{isin} is not on the iShares {site} site")
    return found[isin]


def _ishares_holdings(peer: Peer, ctx: FetchContext | None) -> tuple[str | None, pd.DataFrame, str, str | None]:
    info = ishares_portfolio(peer.site or "uk", peer.isin, ctx)
    cfg = ISHARES_SITES[peer.site or "uk"]
    url = ishares.PRODUCT_DATA_URL.format(data_site=cfg["data_site"])
    params = {
        "appType": "PRODUCT_PAGE", "appSubType": "ISHARES", "targetSite": cfg["targetSite"],
        "locale": cfg["locale"], "userType": "individual", "portfolioId": info["portfolio_id"],
        "component": "holdings",
    }
    payload = _cached_json(_cache_path(ctx, f"ishares_{peer.site}_{info['portfolio_id']}"), lambda: _get_json(url, params))
    as_of, raw = ishares.parse_holdings(payload)
    return as_of, normalise_ishares(raw), requests.Request("GET", url, params=params).prepare().url, info["product_page_url"]


def normalise_ishares(raw: pd.DataFrame) -> pd.DataFrame:
    """iShares holdings columns to :data:`HOLDING_COLUMNS`."""

    def col(*names):
        for n in names:
            if n in raw:
                return raw[n].map(lambda v: None if v in (None, "", "-") else v)
        return pd.Series([None] * len(raw), index=raw.index, dtype=object)

    out = pd.DataFrame(index=raw.index)
    out["isin"] = col("isin")
    out["cusip"] = col("cusip")
    out["sedol"] = col("sedol")
    out["ticker"] = col("ticker")
    out["name"] = col("issueName")
    out["weight_pct"] = pd.to_numeric(col("holdingPercent"), errors="coerce")
    out["asset_class"] = col("assetClass").map(lambda v: ishares.ASSET_CLASS.get(v, v.lower()) if isinstance(v, str) else None)
    out["currency"] = col("marketCurrencyCode", "marketCurrency", "currency", "localCurrency")
    out["country"] = col("countryOfRisk", "location", "country")
    return _validated(out)


def _spdr_holdings(peer: Peer, ctx: FetchContext | None) -> tuple[str | None, pd.DataFrame, str, str | None]:
    region = peer.site or "emea"

    def fetch() -> dict:
        params = spdr.REGIONS[region]["params"] | {"product": "etfs", "ui": "fund-finder"}
        df = spdr.parse_fund_finder(_get_json(spdr.FUND_FINDER_URL, params), region)
        return {r["isin"]: {"holdings_url": r["holdings_url"], "product_page_url": r["product_page_url"]}
                for r in df.to_dict("records")}

    found = _cached_json(_cache_path(ctx, f"spdr_{region}_finder"), fetch)
    if peer.isin not in found:
        raise LookupError(f"{peer.isin} is not in the SPDR {region} fund finder")
    url = found[peer.isin]["holdings_url"]
    r = http.get(url)
    r.raise_for_status()
    as_of, raw = spdr.parse_holdings(r.content)
    return as_of, normalise_spdr(raw), url, found[peer.isin]["product_page_url"]


def normalise_spdr(raw: pd.DataFrame) -> pd.DataFrame:
    """SPDR holdings columns to :data:`HOLDING_COLUMNS`. SPDR files carry no
    asset class, so equity-index lines are ``equity`` and lines without an
    identifier that name cash are ``cash``."""
    out = pd.DataFrame(index=raw.index)
    for c in ("isin", "cusip", "sedol", "ticker", "name", "weight_pct", "currency"):
        out[c] = raw[c] if c in raw else None
    debt = bool(raw["is_debt"].iloc[0]) if "is_debt" in raw and len(raw) else False

    def asset(r) -> str:
        name = r["name"].upper() if isinstance(r["name"], str) else ""
        no_id = not isinstance(r["isin"], str) and not isinstance(r["cusip"], str)
        if no_id and ("CASH" in name or name in ("USD", "EUR", "GBP", "CHF")):
            return "cash"
        return "debt" if debt else "equity"

    out["asset_class"] = out.apply(asset, axis=1) if len(out) else None
    out["country"] = None
    return _validated(out)


def _validated(df: pd.DataFrame) -> pd.DataFrame:
    df =df[HOLDING_COLUMNS].reset_index(drop=True)

    def checked(values, valid) -> pd.Series:
        out = [v.strip().upper() if isinstance(v, str) and valid(v.strip().upper()) else None for v in values]
        return pd.Series(out, index=df.index, dtype=object)

    def text(values) -> pd.Series:
        return pd.Series([v if isinstance(v, str) and v.strip() else None for v in values], index=df.index, dtype=object)

    out = pd.DataFrame(index=df.index)
    out["isin"] = checked(df["isin"], is_valid_isin)
    out["cusip"] = checked(df["cusip"], is_valid_cusip)
    for c in ("sedol", "ticker", "name"):
        out[c] = text(df[c])
    out["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce").astype(float)
    for c in ("asset_class", "currency", "country"):
        out[c] = text(df[c])
    return out[HOLDING_COLUMNS]


def _registry_holdings(peer: Peer, ctx: FetchContext | None) -> tuple[str | None, pd.DataFrame, str, str | None]:
    from . import discover

    adapter = discover().get(peer.issuer)
    if adapter is None:
        raise LookupError(f"no issuer adapter named {peer.issuer!r}")
    prods = adapter.products()
    row = prods[prods["isin"] == peer.isin]
    if row.empty:
        raise LookupError(f"{peer.isin} is not in {peer.issuer} products")
    product = row.iloc[0].to_dict()
    got = adapter.holdings(product, ctx or FetchContext())
    return got.as_of, got.holdings[HOLDING_COLUMNS].copy(), got.url, product.get("product_page_url")


def peer_holdings(peer: Peer, ctx: FetchContext | None = None) -> tuple[str | None, pd.DataFrame, str, str | None]:
    """``(as_of, holdings, url, product_page_url)`` of one peer fund."""
    if peer.issuer == "ishares":
        return _ishares_holdings(peer, ctx)
    if peer.issuer == "spdr":
        return _spdr_holdings(peer, ctx)
    return _registry_holdings(peer, ctx)


# -- holdings ---------------------------------------------------------------

def _target(product: dict) -> Target:
    for t in TARGETS:
        if t.isin == product.get("isin"):
            return t
    key = product.get("index_key")
    if key not in INDICES:
        raise LookupError(f"{product.get('isin')} has no index mapping")
    return Target(product["isin"], product.get("fund_name") or product["isin"], product.get("fund_issuer") or "unknown",
                  key, product.get("replication_method") or "", product.get("domicile"), product.get("currency"), product.get("replication_evidence") or "")


def proxy_note(target: Target, idx: IndexSpec, peer: Peer, as_of: str | None) -> str:
    return (
        f"{KIND}: index composition of the {idx.name} ({idx.provider}) as held by {peer.name} "
        f"({peer.isin}), a physically replicating ETF on the same index, positions as of {as_of or 'unknown date'}; "
        f"used as a proxy for {target.fund_name} ({target.isin}); not the fund's own holdings"
    )


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    """The index composition, as held by the first peer that answers, for one
    mapped target fund. Raises ``ValueError`` for a fund that is not full
    physical replication."""
    target = _target(product)
    if target.replication != FULL:
        raise ValueError(f"{target.isin} is {target.replication or 'of unknown replication'}; index proxies are only used for {FULL} funds")
    idx = INDICES[target.index]
    errors = []
    for peer in idx.peers:
        if peer.isin == target.isin:
            continue
        try:
            as_of, df, url, page = peer_holdings(peer, ctx)
        except (LookupError, requests.RequestException, ValueError, KeyError) as e:
            logger.warning("Index proxy peer %s for %s failed: %s", peer.isin, target.isin, e)
            errors.append(f"{peer.isin}: {e}")
            continue
        credit, home = PEER_CREDIT.get(peer.issuer, (peer.issuer, None))
        notes = [
            proxy_note(target, idx, peer, as_of),
            f"kind: {KIND} (not the fund's own holdings)",
            f"source: {credit}, holdings file of {peer.name}; {page or peer.page or home or url}",
            f"credit: {credit}; index {idx.name} by {idx.provider}",
            f"weights: percent of {peer.name}'s net assets as reported by its issuer, not rescaled",
            f"target replication: {target.evidence or target.replication}",
        ]
        return IssuerHoldings(as_of=as_of, holdings=df, url=url, notes=notes)
    raise LookupError(f"no peer answered for {target.isin}: {'; '.join(errors)}")


# -- comparison -------------------------------------------------------------

def compare(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    """How close two holdings frames are, by ISIN.

    ``overlap_pct`` is the sum over ISINs of the smaller of the two weights:
    100 means identical portfolios. ``max_abs_diff_pp`` is the largest
    difference in one line's weight, in percentage points.
    """
    wa = a.dropna(subset=["isin"]).groupby("isin")["weight_pct"].sum()
    wb = b.dropna(subset=["isin"]).groupby("isin")["weight_pct"].sum()
    both = pd.concat([wa.rename("a"), wb.rename("b")], axis=1).fillna(0.0)
    diff = (both["a"] - both["b"]).abs()
    return {
        "lines_a": int(len(a)), "lines_b": int(len(b)),
        "weight_sum_a": round(float(a["weight_pct"].sum()), 4),
        "weight_sum_b": round(float(b["weight_pct"].sum()), 4),
        "isin_weight_a": round(float(wa.sum()), 4), "isin_weight_b": round(float(wb.sum()), 4),
        "common_isins": int(((both["a"] > 0) & (both["b"] > 0)).sum()),
        "only_a": int(((both["a"] > 0) & (both["b"] == 0)).sum()),
        "only_b": int(((both["b"] > 0) & (both["a"] == 0)).sum()),
        "overlap_pct": round(float(both.min(axis=1).sum()), 4),
        "max_abs_diff_pp": round(float(diff.max()) if len(diff) else 0.0, 4),
        "max_diff_isin": diff.idxmax() if len(diff) else None,
    }


ADAPTER = register(
    IssuerAdapter(
        name=NAME,
        title="Index composition proxy (peer ETF holdings)",
        homepage="https://openbasket.leithresearch.org",
        credit=(
            "Index composition taken from the published holdings of a physically replicating peer ETF "
            "(credited per row to its issuer); not the target fund's own holdings"
        ),
        licence=(
            "Peer holdings are public issuer data under the peer issuer's site terms (not reviewed for bulk "
            "use). Index constituent weights from index providers are licensed and are not used."
        ),
        regions=("CH", "IE", "LU"),
        products=products,
        holdings=holdings,
        frequency="daily (the peer's holdings file)",
    )
)
