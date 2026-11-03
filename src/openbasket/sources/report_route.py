"""Shared steps for adapters that read a fund's holdings from its statutory report.

Used where an issuer's own holdings files sit behind robots.txt
(Xtrackers, L&G): find the share class's latest annual or semi-annual report
in the AVL library (:mod:`openbasket.sources.avl_documents`), reuse an
umbrella report already read where possible, cut out the sub-fund's schedule
(:mod:`openbasket.sources.schedule_tables`), add ISINs by name where the
report prints none (:mod:`openbasket.sources.name_isin`), and label the
result.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import pandas as pd

from . import avl_documents as avl
from . import name_isin
from . import schedule_tables as st
from .issuers import IssuerHoldings, validate_holdings

#: Builds results from report data, so anything it returns is local only.
REDISTRIBUTION = "local_only"

MAX_DOWNLOADS = 3
_MASTER: dict = {}


@dataclass(frozen=True)
class Family:
    brand: str            # how sub-fund titles start, e.g. "Xtrackers", "L&G"
    credit: str
    blocked: str          # why the issuer's own holdings are not used (robots.txt)
    loose: bool = False   # allow a unique substring match of the fund key in a sub-fund title


def name_index():
    """Name -> ISIN index from the security master, or None if it has not been built."""
    path = str(os.environ.get("OPENBASKET_SECURITY_MASTER") or name_isin.DEFAULT_MASTER)
    if path not in _MASTER:
        master = name_isin.load_master(path)
        _MASTER[path] = name_isin.build_index(master) if master is not None else None
    return _MASTER[path]


def find_schedule(isin: str, key: str, brand: str, idx: pd.DataFrame, siblings: list[str], cache, fetch=None,
                  loose: bool = False):
    """``(CachedReport, title, tried, borrowed)`` for a sub-fund; the report is None if none is found.

    ``borrowed`` is True when the report read is not filed under this share class's ISIN.
    """
    dom = isin[:2]
    refs = avl.locate(isin, idx)
    borrowed = False
    if not refs:
        for sib in siblings:
            refs = avl.locate(sib, idx)
            if refs:
                borrowed = True
                break
    tried: list[str] = []
    downloads = 0
    for ref in refs:
        hit = avl.reusable(key, brand, ref.kind, ref.period, dom, cache, loose=loose)
        if hit:
            return hit[0], hit[1], tried, borrowed
        if downloads >= MAX_DOWNLOADS:
            break
        if avl.known_duplicate(ref, brand, cache) and not avl.is_cached(ref, cache):
            tried.append(f"{ref.url}: not fetched; this edition duplicates umbrella reports already read")
            continue
        downloads += 1
        try:
            rep = avl.fetch_report(ref, brand, cache, fetch=fetch)
        except Exception as exc:  # one bad document should not stop the fund
            tried.append(f"{ref.url}: {type(exc).__name__}")
            continue
        title = st.match_title(key, rep.subfunds, brand, loose)
        if title:
            return rep, title, tried, borrowed
        tried.append(f"{ref.url}: sub-fund not found in this report")
    hit = avl.latest_cached(key, brand, dom, cache, loose=loose)
    if hit:
        return hit[0], hit[1], tried, True
    return None, None, tried, borrowed


def holdings(product: dict, family: Family, cat: pd.DataFrame, cache=None, fetch=None,
             index: dict | None = None) -> IssuerHoldings:
    """Holdings of one share class from its sub-fund's latest report."""
    idx = avl.index(cache, fetch=fetch)
    key = product["portfolio_id"]
    siblings = [i for i in cat.loc[cat["portfolio_id"] == key, "isin"] if i != product["isin"]]
    rep, title, tried, borrowed = find_schedule(product["isin"], key, family.brand, idx, siblings, cache, fetch,
                                                loose=family.loose)
    if rep is None:
        raise LookupError(f"no annual or semi-annual report found for {product['isin']}; tried: {tried or 'none listed'}")
    pages = rep.subfunds[title]
    sched = st.parse_schedule(st.pdf_lines(rep.pdf, pages), title)
    if not st.acceptable(sched):
        raise ValueError(f"{title}: schedule did not parse ({st.stats(sched)})")
    index = index if index is not None else name_index()
    frame, match = name_isin.resolve(sched.holdings, index)
    problems = validate_holdings(frame)
    if problems:
        raise ValueError(f"{title}: {problems}")
    s = st.stats(sched)
    notes = [
        f"{rep.ref.kind}, positions as of {rep.ref.period}",
        f"route: statutory report ({family.blocked})",
        f"sub-fund '{title}' in the {rep.umbrella.strip('* ')} {rep.ref.kind} ({rep.ref.lang} edition), "
        f"PDF pages {pages[0] + 1}-{pages[-1] + 1}",
        f"source: {rep.ref.url} (AVL Investmentfonds document library); credit: {family.credit}",
    ]
    own = [r for r in avl.locate(product["isin"], idx) if (r.kind, r.period) == (rep.ref.kind, rep.ref.period)]
    if own and own[0].url != rep.ref.url:
        notes.append(f"the same umbrella report is also filed under this share class at {own[0].url}; "
                     f"the copy filed under {rep.ref.isin} was read")
    elif borrowed:
        notes.append("no report is filed under this share class; the umbrella report filed under another "
                     f"share class ({rep.ref.isin}) was read, and all share classes of a sub-fund share one schedule")
    if sched.total_pct is None:
        inv = s["investments_total"] if s["investments_total"] is not None else s["weight_sum"]
        notes.append(f"the schedule ends at total investments ({inv}% of net assets); cash, derivatives valued "
                     f"elsewhere and other net assets ({round(100 - s['weight_sum'], 2)}%) are unresolved")
    if product.get("replication") == "swap" or re.search(r"\bsw ?ap\b", title, re.I):
        notes.append("swap-based ETF: the schedule is what the fund holds (a substitute basket or collateral); "
                     "the index return comes from a swap, which is a derivative line or not listed")
    if match["tried"]:
        notes.append(
            f"the report prints no ISINs: {match['matched']} of {match['tried']} equity lines "
            f"({match['matched_weight']}% of net assets) were matched by name to the SEC N-PORT security master "
            "(isin_source = name_match); other lines stay unresolved"
        )
    elif index is None:
        notes.append("the report prints no ISINs and no security master was found, so lines carry names only")
    notes.extend(sched.notes)
    notes.extend(tried)
    return IssuerHoldings(as_of=rep.ref.period, holdings=frame, url=rep.ref.url, notes=notes)
