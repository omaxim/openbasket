"""Recursive look-through of a fund to the securities behind it.

The engine knows nothing about where data comes from. A :class:`Providers`
object matches an ISIN to a fund, says whether a holding is itself a fund, and
returns candidate holdings lists from each source in priority order.

Weights are percent of the input fund's net assets. A holding in a sub-fund
is multiplied through: 10% in a fund that holds 5% of X gives 0.5% of X.
Nothing is rescaled. The weight splits three ways:

``resolved_pct``
    In securities with an ISIN (stocks, bonds and so on).
``unexpanded_fund_pct``
    In funds that could not be looked through: no source had their holdings,
    they sit in a cycle, or they were reached at ``max_depth``. Their rows are
    kept, with ``asset_class`` ``fund`` and a note saying why.
``unresolved_pct``
    The rest, ``100 - resolved - unexpanded``: lines without an ISIN (cash,
    derivatives) and whatever the reported lines do not add up to. It is
    negative when a fund's lines total more than 100%.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class FundRef:
    """A matched fund. ``key`` identifies it for cycle detection."""

    series_id: str | None
    ticker: str | None
    name: str | None
    via: str
    note: str | None = None
    #: Issuer fund ID such as ``ishares-uk:253743``, when an issuer file covers it.
    issuer_id: str | None = None

    @property
    def key(self) -> str:
        return self.series_id or self.issuer_id or f"ticker:{self.ticker}"

    @property
    def keys(self) -> tuple[str, ...]:
        """Every id this fund is known by, so a cycle is caught whichever route reaches it."""
        ks = tuple(k for k in (self.series_id, self.issuer_id) if k)
        return ks or (f"ticker:{self.ticker}",)


@dataclass
class Line:
    """One position as a source reports it."""

    weight_pct: float
    isin: str | None = None
    name: str | None = None
    issuer_name: str | None = None
    issuer_lei: str | None = None
    title: str | None = None
    cusip: str | None = None
    ticker: str | None = None
    asset_class: str | None = None
    isin_source: str | None = None
    #: The source suggests the line is a fund (N-PORT issuer type, money market).
    fund_hint: bool = False
    #: True when ``ticker`` is a US listing, so it may be looked up in SEC's
    #: fund ticker list. Issuer files use local tickers and cash codes such as
    #: ``USD``, which collide with US fund tickers.
    us_ticker: bool = False
    #: ISO 3166 alpha-2 code where the source's label is a known country, else the label.
    country: str | None = None
    #: The source's sector label.
    sector: str | None = None


@dataclass
class Holdings:
    """A fund's holdings from one source."""

    source: str
    as_of: str | None
    lines: list[Line]
    source_ref: str | None = None
    #: A page a reader can open to check the figures (product page, filing).
    source_url: str | None = None
    #: Set by :meth:`Providers.security` when the ISIN is a fund, not a security:
    #: ``lines`` then holds one zero-weight line naming it, which is not
    #: expanded, and ``note`` says why there is nothing to show.
    is_fund: bool = False
    note: str | None = None

    @property
    def resolved_pct(self) -> float:
        return sum(l.weight_pct for l in self.lines if l.isin)

    @property
    def reported_pct(self) -> float:
        return sum(l.weight_pct for l in self.lines)


class Providers(Protocol):
    def resolve(self, isin: str) -> FundRef | None:
        """Match an input ISIN to a fund."""

    def resolve_line(self, line: Line) -> FundRef | None:
        """Return the fund behind a holding, or ``None`` if it is not a fund."""

    def holdings(self, ref: FundRef) -> list[Holdings]:
        """Candidate holdings lists, highest-priority source first."""

    def security(self, isin: str) -> Holdings | None:
        """A single security (a stock or bond) as a one-line holdings list."""


@dataclass
class Result:
    leaves: list[dict] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


#: Plausibility of a result, worst first. ``error`` and ``no_match`` are set by the callers.
STATUSES = ("error", "no_match", "no_holdings", "leveraged", "over_100", "partial", "ok")

#: Names of leveraged and inverse products.
#: "Ultra-Short Duration" and the like are bond funds, not leveraged ones.
LEVERAGED_RE = re.compile(
    r"(?<![\w.])-?\d+(\.\d+)?x\b"
    r"|\bultra\b(?![\s-]*short[\s-]*(term|duration|income|bond|maturity|dated|muni|treasury|government|tax|fixed))"
    r"|\b(ultrapro|ultrashort|leveraged|inverse|bear)\b"
    r"|\bdaily\b.*\b(bull|bear|long|short)\b",
    re.I,
)

#: Points away from 100 that reported lines may total before a fund is flagged.
OFF_BY = 5.0
FAR_OFF_BY = 50.0


def choose(candidates: list[Holdings], min_resolved_pct: float) -> Holdings:
    """First source that resolves at least ``min_resolved_pct``, else the best one."""
    for h in candidates:
        if h.resolved_pct >= min_resolved_pct:
            return h
    return max(candidates, key=lambda h: h.resolved_pct)


def plausibility(summary: dict, min_resolved_pct: float = 90.0) -> tuple[str, list[str]]:
    """``(status, notes)`` for a look-through summary.

    ``no_holdings`` when no source had any; ``leveraged`` when the name says
    so (2x, Ultra, Inverse, Bear ...) or the reported lines are more than 50
    points from 100; ``over_100`` when they are more than 5 points above, or
    when the looked-through constituents are (sub-funds over 100%);
    ``partial`` when they are more than 5 points below or less than
    ``min_resolved_pct`` resolves to securities; else ``ok``.
    """
    notes = []
    if not summary.get("matched"):
        return "no_match", notes
    if summary.get("source") is None:
        return "no_holdings", notes
    reported = summary.get("reported_total_pct")
    resolved = summary.get("resolved_pct") or 0.0
    off = None if reported is None else reported - 100.0
    if off is not None and abs(off) > OFF_BY:
        notes.append(f"the source's lines add up to {reported:.2f}% of net assets")
    if off is not None and abs(off) > FAR_OFF_BY:
        notes.append("weights are market values, not exposure, so a leveraged, inverse or derivative-based fund is not shown as it trades")
    if summary.get("unexpanded_fund_pct"):
        notes.append(f"{summary['unexpanded_fund_pct']:.2f}% is in funds that could not be looked through")
    # Sub-funds whose own lines add up to more than 100 push the look-through over too.
    looked = resolved + (summary.get("unexpanded_fund_pct") or 0.0)
    deep_over = looked - 100.0 > OFF_BY and (off is None or off <= OFF_BY)
    if deep_over:
        notes.append(f"looked through, the constituents add up to {looked:.2f}% of net assets")
    if LEVERAGED_RE.search(summary.get("fund_name") or "") or (off is not None and abs(off) > FAR_OFF_BY):
        return "leveraged", notes
    if (off is not None and off > OFF_BY) or deep_over:
        return "over_100", notes
    if (off is not None and off < -OFF_BY) or resolved < min_resolved_pct:
        return "partial", notes
    return "ok", notes


def look_through(
    isin: str,
    providers: Providers,
    max_depth: int = 4,
    min_resolved_pct: float = 90.0,
    ref: FundRef | None = None,
) -> Result:
    """Expand ``isin`` into its constituents.

    With ``ref`` the fund is already known (a universe run builds its refs
    up front), so ``isin`` is only the label rows carry and no matching or
    single-security fallback happens.
    """
    isin = isin.strip().upper()
    res = Result()
    if ref is None:
        ref = providers.resolve(isin)
    summary = {
        "input_isin": isin,
        "matched": ref is not None,
        "fund_name": ref.name if ref else None,
        "series_id": ref.series_id if ref else None,
        "ticker": ref.ticker if ref else None,
        "matched_via": ref.via if ref else None,
        "note": ref.note if ref else None,
        "source": None,
        "source_ref": None,
        "source_url": None,
        "as_of": None,
        "sources_tried": None,
        "sources_status": None,
        "reported_total_pct": None,
        "fund_as_security": False,
        "max_depth_reached": False,
    }

    def add_note(text: str | None) -> None:
        if text:
            summary["note"] = "; ".join(x for x in (summary["note"], text) if x)

    def expand(
        fund: FundRef, factor: float, depth: int, path: frozenset[str], given: Holdings | None = None
    ) -> Holdings | None:
        candidates = [given] if given is not None else providers.holdings(fund)
        if not candidates:
            return None
        chosen = choose(candidates, min_resolved_pct)
        if depth == 0:
            summary["source"] = chosen.source
            summary["source_ref"] = chosen.source_ref
            summary["source_url"] = chosen.source_url
            summary["as_of"] = chosen.as_of
            summary["reported_total_pct"] = round(chosen.reported_pct, 6)
            summary["sources_tried"] = "; ".join(f"{h.source}={h.resolved_pct:.2f}%" for h in candidates)
        for line in chosen.lines:
            weight = factor * line.weight_pct / 100.0
            sub = providers.resolve_line(line)
            note = None
            unexpanded = False
            if sub is not None:
                unexpanded = True
                if any(k in path for k in sub.keys):
                    note = "fund cycle, not expanded"
                elif depth >= max_depth:
                    note = f"fund, not looked through: depth limit of {max_depth} reached"
                    summary["max_depth_reached"] = True
                elif expand(sub, weight, depth + 1, path | set(sub.keys)) is not None:
                    continue
                else:
                    note = getattr(providers, "explain", lambda r: None)(sub) or "fund, holdings not found"
            row = {
                "input_isin": isin,
                "constituent_isin": line.isin,
                "name": line.name or line.issuer_name,
                "issuer_name": line.issuer_name,
                "issuer_lei": line.issuer_lei,
                "title": line.title,
                "cusip": line.cusip,
                "ticker": line.ticker,
                "weight_pct": weight,
                "asset_class": ("fund" if sub is not None else line.asset_class),
                "country": line.country,
                "sector": line.sector,
                "isin_source": line.isin_source,
                "source": chosen.source,
                "source_ref": chosen.source_ref,
                "source_url": chosen.source_url,
                "as_of": chosen.as_of,
                "depth": depth,
                "via_fund": fund.name or fund.key,
                "note": note,
                "unexpanded": unexpanded,
            }
            if line.isin:
                res.leaves.append(row)
            else:
                row["reason"] = note or "no ISIN reported or derivable"
                res.unresolved.append(row)
        return chosen

    direct = providers.security(isin) if ref is None else None
    if direct is not None and direct.is_fund:
        # A fund some other fund holds, but no source has its holdings.
        summary.update(matched=True, fund_name=direct.lines[0].name if direct.lines else None,
                       matched_via="security_master", fund_as_security=True, source_url=direct.source_url)
        add_note(direct.note)
    elif direct is not None:
        # Not a fund: the security is its own constituent at 100%.
        summary.update(matched=True, fund_name=direct.lines[0].name, matched_via="security_master")
        expand(FundRef(None, None, direct.lines[0].name, "security_master"), 100.0, 0, frozenset({isin}), direct)
    elif ref is None:
        summary["note"] = "no fund or security matched this ISIN"
    elif expand(ref, 100.0, 0, frozenset(ref.keys)) is None:
        add_note(getattr(providers, "explain", lambda r: None)(ref) or "no holdings found from any source")
    if ref is not None and hasattr(providers, "source_status"):
        summary["sources_status"] = providers.source_status(ref)

    top_unresolved = [r for r in res.unresolved if r["depth"] == 0]
    biggest = max(top_unresolved, key=lambda r: r["weight_pct"], default=None)
    if biggest is not None and biggest["weight_pct"] >= 50:
        add_note(f"{biggest['weight_pct']:.2f}% sits in one line with no ISIN ({biggest['name']}); likely swap-based or a feeder")

    resolved = sum(r["weight_pct"] for r in res.leaves if not r["unexpanded"])
    unexpanded = sum(r["weight_pct"] for r in res.leaves if r["unexpanded"])
    summary["resolved_pct"] = round(resolved, 6)
    summary["unexpanded_fund_pct"] = round(unexpanded, 6)
    summary["unresolved_pct"] = round(100.0 - resolved - unexpanded, 6)
    summary["unidentified_lines_pct"] = round(sum(r["weight_pct"] for r in res.unresolved), 6)
    summary["n_constituents"] = len({r["constituent_isin"] for r in res.leaves})
    by_source: dict[str, float] = {}
    for r in res.leaves:
        by_source[r["source"]] = by_source.get(r["source"], 0.0) + r["weight_pct"]
    summary["sources_used"] = "; ".join(f"{k}={v:.2f}%" for k, v in sorted(by_source.items(), key=lambda kv: -kv[1]))
    status, notes = plausibility(summary, min_resolved_pct)
    summary["status"] = status
    for n in notes:
        add_note(n)
    res.summary = summary
    return res
