"""Recursive look-through of a fund to the securities behind it.

The engine knows nothing about where data comes from. A :class:`Providers`
object matches an ISIN to a fund, says whether a holding is itself a fund, and
returns candidate holdings lists from each source in priority order.

Weights are percent of the input fund's net assets. A holding in a sub-fund
is multiplied through: 10% in a fund that holds 5% of X gives 0.5% of X.
Nothing is rescaled. Whatever the identified constituents do not account for
is reported as unresolved weight, ``100 - resolved``, which can be negative
when a fund's reported lines total more than 100%.
"""

from __future__ import annotations

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


@dataclass
class Holdings:
    """A fund's holdings from one source."""

    source: str
    as_of: str | None
    lines: list[Line]
    source_ref: str | None = None
    #: A page a reader can open to check the figures (product page, filing).
    source_url: str | None = None

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


def choose(candidates: list[Holdings], min_resolved_pct: float) -> Holdings:
    """First source that resolves at least ``min_resolved_pct``, else the best one."""
    for h in candidates:
        if h.resolved_pct >= min_resolved_pct:
            return h
    return max(candidates, key=lambda h: h.resolved_pct)


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
        "reported_total_pct": None,
    }

    def expand(
        fund: FundRef, factor: float, depth: int, path: tuple[str, ...], given: Holdings | None = None
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
            sub = providers.resolve_line(line) if depth < max_depth else None
            note = None
            if sub is not None:
                if sub.key in path:
                    note = "fund cycle, not expanded"
                elif expand(sub, weight, depth + 1, path + (sub.key,)) is not None:
                    continue
                else:
                    note = "fund, holdings not found"
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
                "isin_source": line.isin_source,
                "source": chosen.source,
                "source_ref": chosen.source_ref,
                "source_url": chosen.source_url,
                "as_of": chosen.as_of,
                "depth": depth,
                "via_fund": fund.name or fund.key,
                "note": note,
            }
            if line.isin:
                res.leaves.append(row)
            else:
                row["reason"] = note or "no ISIN reported or derivable"
                res.unresolved.append(row)
        return chosen

    direct = providers.security(isin) if ref is None else None
    if direct is not None:
        # Not a fund: the security is its own constituent at 100%.
        summary.update(matched=True, fund_name=direct.lines[0].name, matched_via="security_master")
        expand(FundRef(None, None, direct.lines[0].name, "security_master"), 100.0, 0, (isin,), direct)
    elif ref is None:
        summary["note"] = "no fund or security matched this ISIN"
    elif expand(ref, 100.0, 0, (ref.key,)) is None:
        summary["note"] = "; ".join(x for x in (ref.note, "no holdings found from any source") if x)

    top_unresolved = [r for r in res.unresolved if r["depth"] == 0]
    biggest = max(top_unresolved, key=lambda r: r["weight_pct"], default=None)
    if biggest is not None and biggest["weight_pct"] >= 50:
        flag = f"{biggest['weight_pct']:.2f}% sits in one line with no ISIN ({biggest['name']}); likely swap-based or a feeder"
        summary["note"] = "; ".join(x for x in (summary["note"], flag) if x)

    resolved = sum(r["weight_pct"] for r in res.leaves)
    summary["resolved_pct"] = round(resolved, 6)
    summary["unresolved_pct"] = round(100.0 - resolved, 6)
    summary["unidentified_lines_pct"] = round(sum(r["weight_pct"] for r in res.unresolved), 6)
    summary["n_constituents"] = len({r["constituent_isin"] for r in res.leaves})
    by_source: dict[str, float] = {}
    for r in res.leaves:
        by_source[r["source"]] = by_source.get(r["source"], 0.0) + r["weight_pct"]
    summary["sources_used"] = "; ".join(f"{k}={v:.2f}%" for k, v in sorted(by_source.items(), key=lambda kv: -kv[1]))
    res.summary = summary
    return res
