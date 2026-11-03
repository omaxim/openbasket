"""iShares (BlackRock) as a registered issuer adapter, over every regional site.

The fetching and parsing live in :mod:`openbasket.sources.ishares`, which
keeps its per-site interface (``products(site)``, ``holdings(portfolio_id,
site, cache_dir)``). This module reads all :data:`SITES` and merges them:

- a share class listed on several sites is kept once, from the first site in
  :data:`SITES` order that has its full holdings (English-language EMEA
  sites first, then the US, then local-language and other regions);
- only funds with a full holdings table are offered (ETFs). Mutual and index
  funds, which publish ten names without identifiers, and exchange-traded
  products are left out; :func:`all_products` returns them too.

``portfolio_id`` is the fund-level ticker iShares uses for all share classes
of one portfolio (``aladdinFundTicker``); the share class's own portfolio ID,
which the holdings request needs, is in ``share_class_id``.
"""

from __future__ import annotations

import logging

import pandas as pd

from .. import ishares as site_api
from ...identifiers import is_valid_cusip, is_valid_isin
from . import HOLDING_COLUMNS, PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

logger = logging.getLogger(__name__)

#: Sites read by default, in order of preference for duplicates.
SITES: tuple[str, ...] = tuple(site_api.SITES)

EXTRA_COLUMNS = ["share_class_id", "fund_id", "holdings_scope", "total_net_assets", "sites"]


def all_products(sites: tuple[str, ...] | list[str] | None = None, max_lookups: int | None = None) -> pd.DataFrame:
    """Every share class on the given sites, one row per ISIN, before the
    full-holdings filter. A site that fails is logged and skipped."""
    sites = list(sites or SITES)
    frames = []
    for site in sites:
        try:
            df = site_api.products(site, max_lookups=max_lookups)
        except Exception as exc:  # one regional site down must not lose the rest
            logger.warning("iShares %s fund list: %s", site, exc)
            continue
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=PRODUCT_COLUMNS + EXTRA_COLUMNS)
    return merge_sites(pd.concat(frames, ignore_index=True), sites)


def merge_sites(df: pd.DataFrame, sites: list[str]) -> pd.DataFrame:
    """One row per ISIN across sites: full holdings beat top 10, then site order."""
    df = df.copy()
    rank = {s: i for i, s in enumerate(sites)}
    scope_rank = {"full": 0, "top10": 1}
    listed = df.groupby("isin")["site"].agg(lambda s: ",".join(dict.fromkeys(s)))
    # Sites without the fund-level ticker borrow it from another site's row
    # for the same share class (portfolio IDs are global).
    fund_ids = df.dropna(subset=["fund_id"]).drop_duplicates("portfolio_id").set_index("portfolio_id")["fund_id"]
    df["fund_id"] = df["fund_id"].fillna(df["portfolio_id"].map(fund_ids))
    # A product any site calls an exchange-traded product stays one.
    etp = set(df.loc[df["holdings_scope"] == "etp", "isin"])
    df.loc[df["isin"].isin(etp), "holdings_scope"] = "etp"
    df["_scope"] = df["holdings_scope"].map(scope_rank).fillna(2)
    df["_site"] = df["site"].map(rank).fillna(len(rank))
    df = df.sort_values(["_scope", "_site"], kind="stable").drop_duplicates("isin")
    df["sites"] = df["isin"].map(listed)
    df["share_class_id"] = df["portfolio_id"]
    df["portfolio_id"] = df["fund_id"].fillna(df["share_class_id"])
    df["issuer"] = "ishares"
    for c in PRODUCT_COLUMNS + EXTRA_COLUMNS:
        if c not in df:
            df[c] = None
    return df[PRODUCT_COLUMNS + EXTRA_COLUMNS].reset_index(drop=True)


def products(sites: tuple[str, ...] | list[str] | None = None, max_lookups: int | None = None) -> pd.DataFrame:
    """iShares share classes with full holdings, one row per ISIN, across sites."""
    df = all_products(sites, max_lookups=max_lookups)
    return df[df["holdings_scope"] == "full"].reset_index(drop=True)


_ASSET_CLASS_CI = {k.casefold(): v for k, v in site_api.ASSET_CLASS.items()}


def _asset_class(label: object) -> str | None:
    if not isinstance(label, str) or not label.strip() or label.strip() == "-":
        return None
    return _ASSET_CLASS_CI.get(label.strip().casefold(), label.strip().lower())


def _text(value: object) -> str | None:
    if not isinstance(value, str) or value.strip() in ("", "-"):
        return None
    return value.strip()


def to_holding_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map an iShares holdings table to :data:`HOLDING_COLUMNS`."""
    n = len(df)

    def col(*names: str) -> pd.Series:
        for name in names:
            if name in df:
                return df[name]
        return pd.Series([None] * n, index=df.index, dtype=object)

    isin = col("isin").map(lambda v: v.strip().upper() if is_valid_isin(v) else None)
    cusip = col("cusip").map(lambda v: v.strip().upper() if is_valid_cusip(v) else None)
    sedol = col("sedol").map(lambda v: v.strip().upper() if isinstance(v, str) and len(v.strip()) == 7 else None)
    out = pd.DataFrame(
        {
            "isin": isin.astype(object),
            "cusip": cusip.astype(object),
            "sedol": sedol.astype(object),
            "ticker": col("ticker").map(_text).astype(object),
            "name": col("issueName", "name").map(_text).astype(object),
            "weight_pct": pd.to_numeric(col("holdingPercent"), errors="coerce").astype(float),
            "asset_class": col("assetClass").map(_asset_class).astype(object),
            "currency": col("marketCurrencyCode", "currencyCode", "currency").map(_text).astype(object),
            "country": col("countryOfRisk", "location").map(_text).astype(object),
        },
        index=df.index,
    )
    text = [c for c in HOLDING_COLUMNS if c != "weight_pct"]
    out[text] = out[text].astype(object).where(out[text].notna(), None)
    return out[HOLDING_COLUMNS].reset_index(drop=True)


def holdings(product: dict, ctx: FetchContext) -> IssuerHoldings:
    pid = product.get("share_class_id") or product["portfolio_id"]
    got = site_api.holdings(str(pid), product["site"], cache_dir=ctx.cache_dir)
    notes = list(got.notes)
    if got.replication and product.get("replication") != got.replication:
        notes.append(f"Replication per the issuer's key facts: {got.replication}.")
    return IssuerHoldings(as_of=got.as_of, holdings=to_holding_columns(got.holdings), url=got.url, notes=notes)


ADAPTER = register(
    IssuerAdapter(
        name="ishares",
        title="iShares (BlackRock)",
        homepage="https://www.ishares.com",
        credit="Holdings: iShares by BlackRock, from BlackRock's regional websites (ishares.com, blackrock.com).",
        licence=(
            "No open licence. Read from the public JSON services behind BlackRock's product pages (no login). "
            "The site terms (e.g. ishares.com/ch .../compliance/terms-and-conditions, read 2026-11-03) say the "
            "content is copyright with all rights reserved and is for personal, non-commercial use, and must not "
            "be reproduced or redistributed; republishing holdings needs BlackRock's permission or a legal view. "
            "Each fund is fetched at most once a day and every row cites BlackRock."
        ),
        regions=("Switzerland", "United Kingdom", "Ireland", "Luxembourg", "Germany", "Netherlands", "Italy",
                 "France", "Spain", "Nordics", "United States", "Canada", "Mexico", "Brazil",
                 "Colombia", "Latin America", "Hong Kong", "Singapore", "Japan", "Australia"),
        products=products,
        holdings=holdings,
        frequency="daily",
    )
)
