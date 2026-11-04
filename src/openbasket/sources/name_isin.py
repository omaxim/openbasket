"""Give report lines an ISIN by matching the security name to the security master.

Annual reports print security names, not ISINs. openbasket's own security
master (``data/03_primary/security_master.parquet``, built by the
``security_master`` pipeline from SEC N-PORT filings, which are public
domain) has an issuer name and ISIN for some 400,000 securities held by US
funds, including most listed shares outside the US. This module matches
**equity** lines by normalised issuer name:

- names are folded to lower-case ASCII, ``&`` becomes "and", punctuation and
  legal-form words (Inc, Corp, PLC, SA, AG, NV, ...) are dropped, and report
  share-class tags (``-ADR-``, ``B-Aktien``, ``-A-``) are noted and dropped;
- among the master's equity ISINs with that name, the one held in most N-PORT
  positions is used, but only if it accounts for at least
  :data:`DOMINANCE` of them; ADR lines prefer US ISINs and other lines
  prefer non-US ISINs when the name has both;
- bonds, funds and derivatives are not matched (a name does not identify a
  bond), so they stay unresolved.

Every matched line is flagged in ``isin_source`` = ``name_match``; the
caller says so in its notes. A wrong match is possible when two issuers
normalise to the same name; such names (more than one issuer country among
the candidates with no clear winner) are left unmatched.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pandas as pd

from ..identifiers import is_valid_isin

#: Code only. The ISINs it adds come from SEC N-PORT (public domain).
REDISTRIBUTION = "public"

DEFAULT_MASTER = Path("data/03_primary/security_master.parquet")
DOMINANCE = 0.8
EQUITY_CATS = {"EC", "EP"}

_LEGAL = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "cos", "ltd", "limited", "plc",
    "sa", "ag", "nv", "se", "spa", "ab", "asa", "oyj", "oy", "as", "a/s", "bhd", "tbk", "pjsc",
    "ojsc", "sab", "cv", "de", "kgaa", "kg", "llc", "lp", "the", "publ", "bv", "sarl", "sca",
    "scs", "pt", "nl", "hldgs", "reit", "class", "cl", "ord", "shs", "com", "npv", "adr", "sp",
    "spon", "ads", "reg", "registered", "shares", "akt", "aktien", "a", "b", "c",
}
_TAGS = re.compile(
    r"(-adr-|\badr\b|-gdr-|\bgdr\b|\bads\b|-[a-z]-|\b[a-c]-aktien\b|\bvorzugsaktien\b|\bvz\b|"
    r"- ?eingeschränkte stimmrechte|- ?stimmberechtigt|- ?nicht stimmberechtigt|\b144a\b|\breg s\b|\bnon-voting\b|"
    r"- ?notierung in [^,]*$|- ?listed in [^,]*$|\(ticker-code [^)]*\)|\(ticker [^)]*\))",
    re.I,
)
_CLASS = re.compile(r"\b([A-C])-aktien\b|-([A-C])-|\bclass ([A-C])\b|\b([A-C])-shares\b", re.I)


def normalise(name: object) -> str:
    t = unicodedata.normalize("NFKD", str(name or ""))
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower()
    t = _TAGS.sub(" ", t)
    t = re.sub(r"/\s*[a-z]{2,3}\s*/?\s*$", " ", t)   # EDGAR state suffixes: "Inc/MD", "/DE/"
    t = t.replace("&", " and ").replace("’", "'")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    words = [w for w in t.split() if w not in _LEGAL]
    return " ".join(words)


def share_class(name: object) -> str | None:
    m = _CLASS.search(str(name or ""))
    return next((g.upper() for g in m.groups() if g), None) if m else None


def is_adr(name: object) -> bool:
    return bool(re.search(r"-adr-|\badr\b|-gdr-|\bads\b", str(name or ""), re.I))


def load_master(path: str | Path | None = None) -> pd.DataFrame | None:
    p = Path(path) if path else DEFAULT_MASTER
    if not p.exists():
        return None
    df = pd.read_parquet(p, columns=["isin", "issuer_name", "title", "asset_cat", "country", "n_positions"])
    return df


def build_index(master: pd.DataFrame) -> dict[str, list[tuple[str, int, str]]]:
    eq = master[master["asset_cat"].isin(EQUITY_CATS)].dropna(subset=["isin", "issuer_name"])
    eq = eq[eq["isin"].map(is_valid_isin)]
    titles = eq["title"].fillna("") if "title" in eq else pd.Series("", index=eq.index)
    out: dict[str, list[tuple[str, int, str]]] = {}
    for isin, name, n, title in zip(eq["isin"], eq["issuer_name"], eq["n_positions"].fillna(0), titles):
        key = normalise(name)
        if key:
            out.setdefault(key, []).append((isin, int(n), str(title)))
    return out


def _title_class(title: str, letter: str) -> bool:
    return bool(re.search(rf"\b(?:cl|class|ser|series)\.?\s*-?\s*{letter}\b|-{letter}$|\s{letter}$", title, re.I))


def pick(candidates: list[tuple[str, int, str]], adr: bool, letter: str | None = None) -> str | None:
    if not candidates:
        return None
    uniq: dict[str, tuple[int, str]] = {}
    for isin, n, title in candidates:
        if isin not in uniq or uniq[isin][0] < n:
            uniq[isin] = (n, title)
    pool = list(uniq.items())
    if letter and len(pool) > 1:
        same = [c for c in pool if _title_class(c[1][1], letter)]
        if same:
            pool = same
    us = [c for c in pool if c[0].startswith("US")]
    other = [c for c in pool if not c[0].startswith("US")]
    if adr and us:
        pool = us
    elif not adr and other:
        pool = other
    pool.sort(key=lambda c: c[1][0], reverse=True)
    total = sum(c[1][0] for c in pool) or 1
    if len(pool) > 1 and pool[0][1][0] / total < DOMINANCE:
        return None
    return pool[0][0]


def resolve(frame: pd.DataFrame, index: dict | None) -> tuple[pd.DataFrame, dict]:
    """Fill missing ISINs on equity lines; return the frame and match counts."""
    out = frame.copy()
    if "isin_source" not in out:
        out["isin_source"] = out["isin"].map(lambda v: "reported" if isinstance(v, str) else None)
    tried = matched = 0
    weight = 0.0
    if not index:
        return out, {"tried": 0, "matched": 0, "matched_weight": 0.0}
    for i, row in out.iterrows():
        if isinstance(row["isin"], str) or row["asset_class"] != "equity":
            continue
        tried += 1
        isin = pick(index.get(normalise(row["name"]), []), is_adr(row["name"]), share_class(row["name"]))
        if isin:
            out.at[i, "isin"] = isin
            out.at[i, "isin_source"] = "name_match"
            matched += 1
            weight += float(row["weight_pct"] or 0)
    return out, {"tried": tried, "matched": matched, "matched_weight": round(weight, 4)}
