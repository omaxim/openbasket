"""Stand-in holdings for the Swiss banks' physically replicating index ETFs.

UBS and the other Swiss banks do not publish their ETFs' holdings anywhere
openbasket may read them (see ``docs/source/sources/swiss-bank-funds.md``).
For an ETF that holds the securities of a plain market index, the best free
picture of its portfolio is the published holdings of **another physically
replicating ETF on the same index** (the peer). This adapter lists such
Swiss-bank ETFs from SIX Swiss Exchange reference data and returns the peer's
holdings, unchanged and clearly labelled:

- weights are percent of the **peer's** net assets, never rescaled;
- the as-of date is the peer file's position date;
- the first note says ``stand-in: ... not the fund's own holdings``.

Rules:

- The target must be an ETF of a Swiss bank (:func:`swiss_banks.attribute`)
  whose replication SIX gives as ``Physical``. Swap (synthetic) ETFs are
  never stood in for, and neither are commodity ETFs.
- The target's SIX underlying index must normalise to one of :data:`INDEXES`
  and its name must name that index (so "MSCI World ESG Leaders" or the
  actively managed "Pictet AI Enhanced World Equity" never match "MSCI World").
- SIX does not say whether a physical fund replicates fully or samples; the
  note says so. Sampled funds hold a subset, so a stand-in can show names the
  fund does not hold.
- Peers are iShares ETFs found by ISIN in the iShares UK or Swiss product
  screener and read with :func:`openbasket.sources.ishares.holdings`.

``REDISTRIBUTION = "local_only"``: the rows are iShares (BlackRock) data whose
site terms restrict republication, and SIX reference data is "all rights
reserved". robots.txt of ishares.com, blackrock.com and six-group.com allows
the paths used and no gate is involved, so the adapter is on by default for
local runs.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd
import requests

from ...identifiers import is_valid_cusip, is_valid_isin
from .. import ishares, swiss_fetch, swiss_registry
from ..swiss_banks import attribute
from . import HOLDING_COLUMNS, PRODUCT_COLUMNS, FetchContext, IssuerAdapter, IssuerHoldings, register

logger = logging.getLogger(__name__)

NAME = "swiss_standin"
REDISTRIBUTION = "local_only"
ENABLED_BY_DEFAULT = True
CACHE_DIR = os.environ.get("OPENBASKET_SWISS_CACHE", "data/01_raw/swiss")

#: The Swiss iShares site, as on the src/ishares-regions branch; added here only if missing.
ISHARES_CH_SITE = {
    "country": "ch",
    "language": "en",
    "siteName": "ishares-ch",
    "targetSite": "ishares-ch",
    "locale": "en_CH",
    "data_site": "uk-retail01-product-data",
}


@dataclass(frozen=True)
class IndexSpec:
    key: str
    name: str                       # human name, used in notes
    aliases: tuple[str, ...]        # canonical SIX underlying descriptions
    target_name: str                # regex the target fund's name must match
    peers: tuple[tuple[str, str], ...]  # (peer ISIN, iShares site), tried in order
    provider: str


_NOT_PLAIN = r"(?!.*\b(ESG|SRI|Socially|Climate|Paris|Sustainable|Low Carbon|Equal|Enhanced|Select|Factor|Quality|Value|Momentum|Prime|Universal|Min(imum)? Vol))"

INDEXES: tuple[IndexSpec, ...] = (
    IndexSpec("smi", "Swiss Market Index (SMI)", ("smi", "smi swiss market"), r"\bSMI\b" + _NOT_PLAIN, (("CH0008899764", "ch"),), "SIX"),
    IndexSpec("sli", "Swiss Leader Index (SLI)", ("sli", "sli swiss leader"), r"\bSLI\b" + _NOT_PLAIN, (("CH0031768937", "ch"),), "SIX"),
    IndexSpec("smim", "SMI Mid (SMIM)", ("smim", "smim swiss market mid"), r"\bSMIM\b" + _NOT_PLAIN, (("CH0019852802", "ch"),), "SIX"),
    IndexSpec("spi", "Swiss Performance Index (SPI)", ("spi", "spi swiss performance"), r"\bSPI\b(?! (Extra|Mid))" + _NOT_PLAIN,
              (("CH0237935652", "ch"),), "SIX"),
    IndexSpec("sp500", "S&P 500", ("s&p 500",), r"S&P 500" + _NOT_PLAIN, (("IE00B5BMR087", "uk"),), "S&P Dow Jones Indices"),
    IndexSpec("msci_world", "MSCI World", ("msci world",), r"MSCI World(?! ex)" + _NOT_PLAIN, (("IE00B4L5Y983", "uk"),), "MSCI"),
    IndexSpec("msci_world_ex_usa", "MSCI World ex USA", ("msci world ex usa", "msci world ex us"), r"MSCI World ex[- ]US" + _NOT_PLAIN,
              (("IE000R4ZNTN3", "uk"),), "MSCI"),
    IndexSpec("msci_usa", "MSCI USA", ("msci usa",), r"MSCI USA" + _NOT_PLAIN, (("IE00B52SFT06", "uk"),), "MSCI"),
    IndexSpec("msci_emu", "MSCI EMU", ("msci emu",), r"MSCI EMU(?! Small)" + _NOT_PLAIN, (("IE00B53QG562", "uk"),), "MSCI"),
    IndexSpec("msci_emu_small", "MSCI EMU Small Cap", ("msci emu small cap",), r"MSCI EMU Small Cap" + _NOT_PLAIN, (("IE00B3VWMM18", "uk"),), "MSCI"),
    IndexSpec("msci_europe", "MSCI Europe", ("msci europe",), r"MSCI Europe(?! (ex|Small))" + _NOT_PLAIN, (("IE00B4K48X80", "uk"),), "MSCI"),
    IndexSpec("msci_em", "MSCI Emerging Markets", ("msci emerging markets",), r"MSCI (Emerging Markets|EM)\b(?! (ex|Asia|IMI))" + _NOT_PLAIN,
              (("IE00B0M63177", "uk"),), "MSCI"),
    IndexSpec("msci_em_ex_china", "MSCI Emerging Markets ex China", ("msci emerging markets ex china",), r"ex[- ]China" + _NOT_PLAIN,
              (("IE00BMG6Z448", "uk"),), "MSCI"),
    IndexSpec("msci_japan", "MSCI Japan", ("msci japan",), r"MSCI Japan(?! (Small|IMI))" + _NOT_PLAIN, (("IE00B02KXH56", "uk"),), "MSCI"),
    IndexSpec("msci_canada", "MSCI Canada", ("msci canada",), r"MSCI Canada" + _NOT_PLAIN, (("IE00B52SF786", "uk"),), "MSCI"),
    IndexSpec("msci_australia", "MSCI Australia", ("msci australia",), r"MSCI Australia" + _NOT_PLAIN, (("IE00B5377D42", "uk"),), "MSCI"),
    IndexSpec("msci_uk", "MSCI United Kingdom", ("msci uk", "msci united kingdom"), r"MSCI (UK|United Kingdom)(?! (IMI|Small))" + _NOT_PLAIN,
              (("IE00B539F030", "uk"),), "MSCI"),
    IndexSpec("msci_pacific_ex_japan", "MSCI Pacific ex Japan", ("msci pacific ex japan",), r"MSCI Pacific" + _NOT_PLAIN,
              (("IE00B52MJY50", "uk"),), "MSCI"),
    IndexSpec("euro_stoxx_50", "EURO STOXX 50", ("euro stoxx 50",), r"EURO STOXX 50" + _NOT_PLAIN, (("IE0008471009", "uk"),), "STOXX"),
    IndexSpec("ftse_100", "FTSE 100", ("ftse 100",), r"FTSE 100" + _NOT_PLAIN, (("IE0005042456", "uk"),), "FTSE Russell"),
)
INDEX_BY_ALIAS = {a: spec for spec in INDEXES for a in spec.aliases}

EXTRA_COLUMNS = ["bank", "index_key", "index_name", "peer_isin", "peer_name", "peer_site", "peer_portfolio_id",
                 "peer_page_url", "target_replication", "redistribution"]


def canon_index(value: object) -> str:
    """SIX underlying description to a comparable key ("SMI Total Return" -> "smi")."""
    if not isinstance(value, str):
        return ""
    s = value.lower().replace("®", " ").replace("™", " ")
    s = re.sub(r"100\s*%\s*hedged to \w+", " ", s)
    s = re.sub(r"[(),/]", " ", s)
    s = re.sub(r"\b(index|indices|total return|net return|gross return|price return|tr|nr|ntr|tri|net|gross|hedged|"
               r"eur|usd|chf|gbp|jpy|ucits)\b", " ", s)
    s = s.replace("-", " ")
    return " ".join(s.split())


def match_index(underlying: object, fund_name: object) -> IndexSpec | None:
    spec = INDEX_BY_ALIAS.get(canon_index(underlying))
    if spec is None or not isinstance(fund_name, str) or not re.search(spec.target_name, fund_name, re.I):
        return None
    return spec


def _ensure_ch_site() -> None:
    ishares.SITES.setdefault("ch", dict(ISHARES_CH_SITE))


@lru_cache(maxsize=4)
def _screener(site: str) -> pd.DataFrame:
    _ensure_ch_site()
    return ishares.products(site)


def _peer(spec: IndexSpec) -> dict | None:
    for isin, site in spec.peers:
        try:
            df = _screener(site)
        except Exception as exc:
            logger.warning("iShares %s screener: %s", site, exc)
            continue
        hit = df[df["isin"] == isin]
        if not hit.empty:
            r = hit.iloc[0]
            return {"peer_isin": isin, "peer_name": r["fund_name"], "peer_site": site,
                    "peer_portfolio_id": str(r["portfolio_id"]), "peer_page_url": r.get("product_page_url")}
    return None


def select_targets(six: pd.DataFrame) -> pd.DataFrame:
    """Swiss-bank physical index ETFs on SIX whose index is in :data:`INDEXES` (no peer lookup)."""
    rows = []
    for r in six.drop_duplicates("ISIN").to_dict("records"):
        if r.get("ProductLine") != "ET" or str(r.get("ReplicationMethodDesc") or "").lower() != "physical":
            continue
        if r.get("AssetClassDesc") == "Commodities":
            continue
        bank = attribute(r.get("FundLongName"), None, r.get("IssuerNameFull"))
        if bank is None:
            continue
        spec = match_index(r.get("FundUnderlyingDescription"), r.get("FundLongName"))
        if spec is None:
            continue
        rows.append(
            {
                "issuer": NAME, "site": "six", "portfolio_id": r["ISIN"], "isin": r["ISIN"], "ticker": None,
                "fund_name": r.get("FundLongName"), "domicile": r.get("LegalStructureCountryCode"),
                "currency": r.get("TradingBaseCurrency"), "asset_class": r.get("AssetClassDesc"), "replication": "physical",
                "product_page_url": None, "holdings_url": None,
                "bank": bank, "index_key": spec.key, "index_name": spec.name, "target_replication": "physical (SIX; full or sampled not stated)",
                "redistribution": REDISTRIBUTION,
            }
        )
    return pd.DataFrame(rows, columns=PRODUCT_COLUMNS + EXTRA_COLUMNS)


def products(cache_dir: str | Path | None = CACHE_DIR) -> pd.DataFrame:
    from ..swiss_reports import enabled

    if not enabled(NAME, ENABLED_BY_DEFAULT):
        return pd.DataFrame(columns=PRODUCT_COLUMNS + EXTRA_COLUMNS)
    six, _ = swiss_registry.six_funds(cache_dir)
    targets = select_targets(six)
    peers = {spec.key: _peer(spec) for spec in INDEXES if spec.key in set(targets["index_key"])}
    for col in ("peer_isin", "peer_name", "peer_site", "peer_portfolio_id", "peer_page_url"):
        targets[col] = targets["index_key"].map(lambda k: (peers.get(k) or {}).get(col))
    targets["product_page_url"] = targets["peer_page_url"]
    targets["holdings_url"] = None
    return targets[targets["peer_isin"].notna()].reset_index(drop=True)


def _first(r: dict, *keys: str) -> object:
    for k in keys:
        v = r.get(k)
        if isinstance(v, str) and v.strip() and v.strip() != "-":
            return v.strip()
    return None


def to_holding_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """An iShares holdings frame in :data:`HOLDING_COLUMNS` (weights unchanged)."""
    rows = []
    for r in frame.astype(object).where(frame.notna(), None).to_dict("records"):
        isin = _first(r, "isin")
        cusip = _first(r, "cusip")
        asset = r.get("assetClass")
        rows.append(
            {
                "isin": isin if isin and is_valid_isin(isin) else None,
                "cusip": cusip if cusip and is_valid_cusip(cusip) else None,
                "sedol": _first(r, "sedol"),
                "ticker": _first(r, "ticker", "issuerTicker"),
                "name": _first(r, "issueName", "name"),
                "weight_pct": pd.to_numeric(r.get("holdingPercent"), errors="coerce"),
                "asset_class": ishares.ASSET_CLASS.get(asset, (asset or "").lower() or None),
                "currency": _first(r, "marketCurrency", "currency", "localCurrency"),
                "country": _first(r, "countryOfRisk", "location", "country"),
            }
        )
    df = pd.DataFrame(rows, columns=HOLDING_COLUMNS)
    df["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce")
    return df


def holdings(product: dict, ctx: FetchContext) -> IssuerHoldings:
    spec = next(s for s in INDEXES if s.key == product["index_key"])
    _ensure_ch_site()
    got = ishares.holdings(product["peer_portfolio_id"], product["peer_site"], cache_dir=ctx.cache_dir or f"{CACHE_DIR}/ishares")
    df = to_holding_columns(got.holdings)
    notes = [
        f"stand-in: index composition of the {spec.name} ({spec.provider}) as held by {product['peer_name']} "
        f"({product['peer_isin']}), a physically replicating iShares ETF on the same index, positions as of {got.as_of}; "
        f"used as a stand-in for {product['fund_name']} ({product['isin']}); not the fund's own holdings",
        "kind: index stand-in (not the fund's own holdings)",
        f"target replication: {product.get('target_replication')}; weights are percent of the peer's net assets",
        f"source: iShares by BlackRock, {product.get('peer_page_url') or got.url}",
        "hedged share classes also hold currency forwards that the peer does not show",
    ]
    return IssuerHoldings(got.as_of, df, got.url, notes)


register(
    IssuerAdapter(
        name=NAME,
        title="Swiss bank index ETFs (peer stand-in)",
        homepage="https://www.six-group.com/en/market-data/etf/etf-explorer.html",
        credit="Stand-in holdings from iShares by BlackRock (peer ETF on the same index); fund list: SIX Swiss Exchange",
        licence="local only: iShares site data and SIX reference data, not licensed for republication",
        regions=("CH", "LU", "IE"),
        products=lambda: products(),
        holdings=holdings,
        frequency="daily",
    )
)
