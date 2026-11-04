"""Stand-ins: a US fund on the same index for a fund with no public holdings.

Most European index ETFs publish their holdings only on their issuer's site,
under terms that do not allow republishing them, so the public build lists
them with no holdings (status ``local_only``). Many of them track an index
that a US-registered fund also tracks, and a US fund's holdings are public:
it files them with the SEC on Form N-PORT, a US government record.

This module pairs such a fund with a US fund **by the index named in both
funds' names**: "iShares Core MSCI World UCITS ETF" and "iShares MSCI World
ETF" both name the MSCI World. :func:`index_key` folds a name to the index
it names, dropping brands, wrappers, share-class and currency words; two
funds pair only when their keys are equal, so "MSCI World SRI" never pairs
with "MSCI World", nor "S&P 500 Equal Weight" with "S&P 500". The US fund
with the largest net assets among those with the same key is the peer.

A stand-in is used only when the fund's own holdings are not available: its
own look-through came back ``no_holdings`` or ``local_only``. Its rows are
the US fund's N-PORT holdings, source ``nport``, as of that filing, in
percent of the US fund's net assets, never rescaled. The fund gets status
``stand_in``, ``matched_via`` ``stand_in`` and a note naming the US fund,
the index and the date, saying they are not the fund's own holdings.

How close a stand-in is was measured against funds whose own holdings a
local run reads (``docs/source/sources/stand-ins.md``): physically
replicating funds on broad indexes overlap their stand-in by about 88 to
93% of net assets, most of the gap being the age of the N-PORT filing.
Funds that replicate through a swap hold a basket unrelated to the index;
for them the stand-in shows the index exposure, not what the fund holds.
"""

from __future__ import annotations

import re
import unicodedata

import pandas as pd

from .lookthrough import LEVERAGED_RE, FundRef

#: Words that start the index part of a fund name.
_PROVIDER = re.compile(
    r"\b(msci|s&p|ftse|nasdaq|russell|stoxx|euro stoxx|dax|crsp|dow jones|smi|topix|nikkei|solactive|"
    r"bloomberg|iboxx|markit iboxx|jp morgan embi|j p morgan embi)\b"
)

#: Brands, wrappers and fund words that say nothing about the index.
_BRANDS = re.compile(
    r"\b(ishares|core|amundi|lyxor|spdr|state street|xtrackers|vanguard|invesco|bnp paribas easy|bnp paribas|"
    r"easy|global x|vaneck|franklin|jpmorgan|j p morgan|schwab|fidelity|ssga|hsbc|ubs|deka|l g|legal general|"
    r"wisdomtree|first trust|dimensional|portfolio|ucits|etf|etc|trust|index|fund|funds|tracker|shares|class|"
    r"units?|series|plc|sicav|icav|s a|sa|ag|dr|swap|physical|synthetic|the)\b"
)

#: Share-class, currency, distribution and hedging words.
_NOISE = re.compile(
    r"\b(usd|eur|gbp|chf|jpy|cad|aud|sek|nok|dkk|mxn|hkd|sgd|acc|accumulating|dist|distributing|dis|inc|income|"
    r"capitalisation|capitalization|c|d|a|b|i|1c|2c|1d|monthly|quarterly|annual|hedged|hdg|h|of|and|on)\b"
)

#: Different spellings of one index word.
_SYNONYMS = [
    (re.compile(r"\bs&p\s*(\d)"), r"s&p \1"),
    (re.compile(r"\bnasdaq\s*-?\s*100\b"), "nasdaq 100"),
    (re.compile(r"\beurozone\b"), "emu"),
    (re.compile(r"\bem\b"), "emerging markets"),
    (re.compile(r"\bsmall\s*-\s*cap\b|\bsmallcap\b"), "small cap"),
    (re.compile(r"\bmid\s*-\s*cap\b|\bmidcap\b"), "mid cap"),
    (re.compile(r"\bex\s*-\s*"), "ex "),
]

#: Funds whose holdings do not follow their index: leveraged and inverse
#: (``LEVERAGED_RE``), option overlays, buffers, futures and active funds
#: that only borrow an index family's name.
_NOT_A_TRACKER = re.compile(
    r"\b(covered call|buy ?write|buffer|defined outcome|premium|option|options|futures|short|"
    r"managed volatility|hedged equity|tactical|dynamic|russell investments)\b"
)


def _fold(name: object) -> str:
    if not isinstance(name, str):
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return s.replace("®", " ").replace("™", " ")


def index_key(name: object) -> str | None:
    """The index a fund's name names, folded for comparison, or ``None``.

    ``"iShares Core S&P 500 UCITS ETF USD (Acc)"`` and ``"iShares Core
    S&P 500 ETF"`` are both ``"s&p 500"``; ``"Xtrackers MSCI World Swap
    UCITS ETF 1C"`` is ``"msci world"``. A name with no index provider
    in it, or one of a leveraged, inverse or option-overlay fund, has none.
    Every word but brands and share-class words counts, before the index
    name as well as after it.
    """
    s = _fold(name)
    if not s or LEVERAGED_RE.search(s) or _NOT_A_TRACKER.search(s):
        return None
    for pattern, repl in _SYNONYMS:
        s = pattern.sub(repl, s)
    if not _PROVIDER.search(s):
        return None
    # Words before the provider count too: "ESG Aware MSCI USA" is not "MSCI USA".
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9&+ ]", " ", s)
    s = _NOISE.sub(" ", _BRANDS.sub(" ", s))
    s = " ".join(s.split())
    # The provider alone ("msci", "ftse") does not name an index.
    return s if _PROVIDER.search(s) and " " in s else None


def peers(fund_master: pd.DataFrame) -> pd.DataFrame:
    """The US fund each index key pairs with: the largest N-PORT series by net assets.

    Columns ``index_key``, ``series_id``, ``series_name``, ``net_assets``,
    ``report_date``.
    """
    cols = ["index_key", "series_id", "series_name", "net_assets", "report_date"]
    fm = fund_master
    if "has_nport" in fm:
        fm = fm[fm["has_nport"].fillna(False).astype(bool)]
    if "series_name" not in fm or "net_assets" not in fm:
        return pd.DataFrame(columns=cols)
    fm = fm.dropna(subset=["series_id"]).drop_duplicates("series_id")
    if fm.empty:
        return pd.DataFrame(columns=cols)
    fm = fm.assign(index_key=fm["series_name"].map(index_key),
                   net_assets=pd.to_numeric(fm["net_assets"], errors="coerce"),
                   report_date=fm["report_date"] if "report_date" in fm else None)
    fm = fm.dropna(subset=["index_key"])
    fm = fm[fm["net_assets"].fillna(0) > 0]
    fm = fm.sort_values(["net_assets", "series_id"], ascending=[False, True]).drop_duplicates("index_key")
    return fm[cols].reset_index(drop=True)


def stand_ins(universe: pd.DataFrame, fund_master: pd.DataFrame) -> pd.DataFrame:
    """One row per universe fund that has a stand-in.

    Only funds listed by an issuer or a registry (not N-PORT series), not
    already matched to a series, and without a US ISIN qualify: a US fund
    files N-PORT itself. Columns ``fund_id``, ``index_key``, ``series_id``,
    ``series_name``, ``net_assets``, ``report_date``.
    """
    cols = ["fund_id", "index_key", "series_id", "series_name", "net_assets", "report_date"]
    p = peers(fund_master)
    if universe.empty or p.empty:
        return pd.DataFrame(columns=cols)
    u = universe[(universe["universe_source"] != "nport") & universe["series_id"].isna()]
    u = u[~u["id"].astype(str).str.upper().str.startswith("US")]
    u = u.assign(index_key=u["name"].map(index_key)).dropna(subset=["index_key"])
    out = u[["id", "index_key"]].rename(columns={"id": "fund_id"}).merge(p, on="index_key")
    return out[cols].reset_index(drop=True)


def _index_label(key: str) -> str:
    """``"msci world"`` -> ``"MSCI World"``, for notes."""
    upper = {"msci", "s&p", "ftse", "smi", "dax", "crsp", "emu", "esg", "sri", "usa", "us", "uk", "acwi", "imi",
             "eafe", "ex", "topix", "embi", "ice", "reit", "reits"}
    return " ".join(w.upper() if w in upper else w.capitalize() for w in key.split()).replace(" EX ", " ex ")


def ref(row: dict) -> FundRef:
    """The engine's ref for one :func:`stand_ins` row: the US series, matched via ``stand_in``."""
    when = row.get("report_date")
    when = pd.Timestamp(when).date().isoformat() if when is not None and pd.notna(when) else "its latest filing"
    note = (f"stand-in: holdings of {row['series_name']} (SEC series {row['series_id']}), a US fund whose name "
            f"names the same index ({_index_label(row['index_key'])}), from its SEC Form N-PORT filing for "
            f"{when}; not this fund's own holdings, and weights are percent of the US fund's net assets")
    return FundRef(series_id=row["series_id"], ticker=None, name=row["series_name"], via="stand_in", note=note)


def refs(universe: pd.DataFrame, fund_master: pd.DataFrame) -> dict[str, FundRef]:
    """Stand-in refs by universe fund id."""
    return {r["fund_id"]: ref(r) for r in stand_ins(universe, fund_master).to_dict("records")}
