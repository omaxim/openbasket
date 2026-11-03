"""Schedules of investments from Swiss and Luxembourg fund reports.

Swiss funds (KAG/CISA) and Luxembourg/Irish UCITS must publish annual and
semi-annual reports with a full inventory of the portfolio. This module turns
the text of such a report into positions: one line per holding with its
name, currency, quantity, market value and **percent of net assets as
printed** (never rescaled), grouped by sub-fund, with the positions date.

Two layouts are handled, both read from text extracted with pypdf:

``lux_portfolio``
    "Portfolio as at <date>" pages of Luxembourg reports (Vontobel Fund and
    similar): ``<quantity> <ccy> <security> [<coupon> <maturity>] <market
    value> <% of net assets>``, wrapped over several lines when the name is
    long, grouped under asset-type and country headings with "Total - ..."
    lines.
``ch_inventar``
    "Inventar des Fondsvermögens" pages of Swiss reports (UBS Fund
    Management layout, used for Migros Bank, Bank Cler, BKB and other
    white-label funds): ``<security>* <ccy> <holding at start> [purchases]
    [sales] <holding at end> <market value> <in %> [lent]``.

Reports print names, not ISINs. :class:`NameResolver` looks equity names up
in SIX Swiss Exchange's share list and in the local security master
(``data/03_primary/security_master.parquet``); a match is kept only when it is
unambiguous, and the row says ``isin_source = "name_match"``. Bonds, fund
units and derivatives keep ``isin = None``.

``REDISTRIBUTION = "local_only"``: report text is the fund's own publication
and the hosts' terms allow no republication.
"""

from __future__ import annotations

import io
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from ..identifiers import is_valid_isin

logger = logging.getLogger(__name__)

REDISTRIBUTION = "local_only"

# --------------------------------------------------------------------------- switches


def enabled(name: str, default: bool) -> bool:
    """Whether source ``name`` may run: ``OPENBASKET_ENABLE`` / ``OPENBASKET_DISABLE`` (comma lists) override ``default``."""
    on = {s.strip() for s in os.environ.get("OPENBASKET_ENABLE", "").split(",") if s.strip()}
    off = {s.strip() for s in os.environ.get("OPENBASKET_DISABLE", "").split(",") if s.strip()}
    if name in off:
        return False
    return name in on or default


# --------------------------------------------------------------------------- dates and countries

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "januar": 1, "februar": 2, "märz": 3, "maerz": 3, "mai": 5, "juni": 6, "juli": 7, "oktober": 10, "dezember": 12,
    "janvier": 1, "février": 2, "mars": 3, "avril": 4, "juin": 6, "juillet": 7, "août": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "décembre": 12,
}


def parse_date(text: str) -> str | None:
    """First date in ``text``: "February 28, 2026", "31. Dezember 2025", "28.02.2026" or "2026-02-28"."""
    m = re.search(r"([A-Za-zäéûô]+)\s+(\d{1,2}),\s*(\d{4})", text)
    if m and m.group(1).lower() in MONTHS:
        return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2))).isoformat()
    m = re.search(r"(\d{1,2})\.?\s+([A-Za-zäéûô]+)\s+(\d{4})", text)
    if m and m.group(2).lower() in MONTHS:
        return date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))).isoformat()
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", text)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    return m.group(0) if m else None


#: Country headings in English and German reports, to ISO 3166 alpha-2.
COUNTRIES = {
    "argentina": "AR", "australia": "AU", "australien": "AU", "austria": "AT", "österreich": "AT", "belgium": "BE", "belgien": "BE",
    "bermuda": "BM", "brazil": "BR", "brasilien": "BR", "canada": "CA", "kanada": "CA", "cayman islands": "KY", "chile": "CL",
    "china": "CN", "colombia": "CO", "czech republic": "CZ", "denmark": "DK", "dänemark": "DK", "egypt": "EG", "finland": "FI",
    "finnland": "FI", "france": "FR", "frankreich": "FR", "germany": "DE", "deutschland": "DE", "greece": "GR", "griechenland": "GR",
    "hong-kong": "HK", "hong kong": "HK", "hongkong": "HK", "hungary": "HU", "india": "IN", "indien": "IN", "indonesia": "ID",
    "indonesien": "ID", "ireland": "IE", "irland": "IE", "israel": "IL", "italy": "IT", "italien": "IT", "japan": "JP",
    "jersey": "JE", "guernsey": "GG", "kazakhstan": "KZ", "korea, republic of": "KR", "south korea": "KR", "korea": "KR",
    "südkorea": "KR", "luxembourg": "LU", "luxemburg": "LU", "liechtenstein": "LI", "malaysia": "MY", "mexico": "MX",
    "mexiko": "MX", "netherlands": "NL", "niederlande": "NL", "new zealand": "NZ", "neuseeland": "NZ", "norway": "NO",
    "norwegen": "NO", "panama": "PA", "peru": "PE", "philippines": "PH", "poland": "PL", "polen": "PL", "portugal": "PT",
    "qatar": "QA", "russia": "RU", "saudi arabia": "SA", "singapore": "SG", "singapur": "SG", "south africa": "ZA",
    "südafrika": "ZA", "spain": "ES", "spanien": "ES", "sweden": "SE", "schweden": "SE", "switzerland": "CH", "schweiz": "CH",
    "taiwan": "TW", "thailand": "TH", "turkey": "TR", "türkei": "TR", "united arab emirates": "AE", "united kingdom": "GB",
    "grossbritannien": "GB", "vereinigtes königreich": "GB", "united states": "US", "usa": "US", "vereinigte staaten": "US",
    "uzbekistan": "UZ", "vietnam": "VN", "curacao": "CW", "jordan": "JO", "kuwait": "KW", "macau": "MO", "greenland": "GL",
}
CURRENCY_COUNTRY = {
    "CHF": "CH", "JPY": "JP", "GBP": "GB", "GBX": "GB", "SEK": "SE", "NOK": "NO", "DKK": "DK", "AUD": "AU", "CAD": "CA",
    "TWD": "TW", "KRW": "KR", "INR": "IN", "BRL": "BR", "ZAR": "ZA", "MXN": "MX", "IDR": "ID", "THB": "TH", "SGD": "SG",
    "PLN": "PL", "TRY": "TR", "ILS": "IL", "NZD": "NZ", "CNY": "CN", "CNH": "CN", "AED": "AE", "SAR": "SA", "VND": "VN",
}

# --------------------------------------------------------------------------- positions


@dataclass
class Position:
    subfund: str
    name: str
    currency: str | None
    weight_pct: float
    quantity: float | None = None
    market_value: float | None = None
    asset_class: str | None = None   # equity, debt, fund, other
    country: str | None = None       # ISO code of the country heading, if any
    kind: str | None = None          # share type hint: bearer, registered, participation, pref
    section: str | None = None


@dataclass
class ReportPortfolio:
    subfund: str
    as_of: str | None
    positions: list[Position] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)

    @property
    def weight_sum(self) -> float:
        return round(sum(p.weight_pct for p in self.positions), 4)


def pdf_pages(content: bytes) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    return [(p.extract_text() or "") for p in reader.pages]


def _num(s: str) -> float | None:
    s = s.replace(",", "").replace("'", "").replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return None


ASSET_HEADINGS = [
    (re.compile(r"^(shares|equities|aktien|inhaberaktien|namensaktien|weitere aktien|partizipationsscheine|genussscheine|"
                r"actions|preferred shares|vorzugsaktien)\b", re.I), "equity"),
    (re.compile(r"^(bonds|obligationen|anleihen|notes|money market instruments|geldmarktinstrumente|convertible bonds|"
                r"wandelanleihen|wandelobligationen|treasury bills|commercial papers?|floating rate notes|asset backed)", re.I), "debt"),
    (re.compile(r"^(investment funds|investmentfonds|funds|investmentzertifikate|anteile anderer|undertakings for collective|ucits|"
                r"exchange traded funds|fonds)", re.I), "fund"),
    (re.compile(r"^(certificates|zertifikate|warrants|rights|bezugsrechte|optionsscheine|structured products|strukturierte)", re.I), "other"),
]
KIND_HEADINGS = {"inhaberaktien": "bearer", "namensaktien": "registered", "partizipationsscheine": "participation",
                 "genussscheine": "participation", "preferred shares": "pref", "vorzugsaktien": "pref"}
_SKIP_HEADINGS = re.compile(
    r"^(transferable securities|other transferable|unlisted securities|effekten, die|securities admitted|"
    r"investments? in securities|übrige|other securities|derivative|quantity ccy|titel |anzahl|nominal|market$|value in|"
    r"% of net|assets \(|in chf|in eur|in usd)", re.I)


def _heading(line: str, state: dict) -> None:
    low = line.strip().lower()
    for rx, asset in ASSET_HEADINGS:
        if rx.match(low):
            state["asset"] = asset
            state["kind"] = next((v for k, v in KIND_HEADINGS.items() if low.startswith(k)), None)
            state["country"] = None
            return
    if _SKIP_HEADINGS.match(low):
        return
    iso = COUNTRIES.get(low.rstrip(" :"))
    if iso:
        state["country"] = iso
    elif len(low) < 60:
        state["country"] = None  # a region ("Global", "Europa") or another grouping


# ---- lux_portfolio

_LUX_START = re.compile(r"^(-?[\d,]+(?:\.\d+)?)\s+([A-Z]{3})\s+(.*)$")
_LUX_END = re.compile(r"^(.*?)\s*(-?[\d,]{1,}(?:\.\d+)?)\s+(-?\d{1,3}\.\d{2})$")
_LUX_PORTFOLIO = re.compile(r"^Portfolio as at (.+)$", re.I)
_LUX_NOISE = re.compile(r"^(\d+/\d+ |the accompanying notes|\(\d\) |quantity ccy securities|market$|value in|[A-Z]{3}$|"
                        r"% of net|assets \(\d\))", re.I)


def parse_lux_portfolio(pages: list[str]) -> list[ReportPortfolio]:
    out: dict[str, ReportPortfolio] = {}
    for no, text in enumerate(pages):
        lines = [l.strip() for l in text.splitlines()]
        idx = next((i for i, l in enumerate(lines[:6]) if _LUX_PORTFOLIO.match(l)), None)
        if idx is None or idx == 0:
            continue
        # The sub-fund heading sits between the page header ("84/352 Vontobel") and "Portfolio as at";
        # long names wrap onto two lines.
        head = [l for l in lines[:idx] if l and not re.match(r"^\d+/\d+\b|^\d+$", l)]
        subfund = " ".join(head[-2:]) if len(head) >= 2 and not re.search(r"[–-]", head[-1]) else (head[-1] if head else lines[idx - 1])
        as_of = parse_date(_LUX_PORTFOLIO.match(lines[idx]).group(1))
        pf = out.setdefault(subfund, ReportPortfolio(subfund, as_of))
        pf.pages.append(no)
        state = pf.__dict__.setdefault("_state", {"asset": None, "country": None, "kind": None})
        pending: list[str] | None = None
        for line in lines[idx + 1:]:
            if not line or _LUX_NOISE.match(line):
                continue
            if pending is None and line.lower().startswith("total"):
                continue
            if pending is None:
                m = _LUX_START.match(line)
                if not m:
                    if not _LUX_END.match(line):
                        _heading(line, state)
                    continue
                pending = [line]
            else:
                pending.append(line)
            joined = " ".join(pending)
            end = _LUX_END.match(joined)
            if not end:
                if len(pending) > 6:  # not a position after all
                    pending = None
                continue
            start = _LUX_START.match(joined)
            body = _LUX_END.match(start.group(3))
            pending = None
            if not body or not re.search(r"[A-Za-z]", body.group(1)):
                continue
            name = body.group(1).strip()
            asset = state["asset"]
            pf.positions.append(
                Position(
                    subfund=subfund, name=name, currency=start.group(2), weight_pct=float(body.group(3)),
                    quantity=_num(start.group(1)), market_value=_num(body.group(2)), asset_class=asset,
                    country=state["country"], kind="pref" if re.search(r"\bpref\b", name, re.I) else state["kind"],
                )
            )
    for pf in out.values():
        pf.__dict__.pop("_state", None)
    return list(out.values())


# ---- ch_inventar

_CH_LINE = re.compile(r"^(?P<name>.*?[A-Za-z].*?)\*{0,3}\s+(?P<ccy>[A-Z]{3})\s+(?P<nums>[\d '.\-]+?)\s*$")
_CH_PCT = re.compile(r"^-?\d{1,3}\.\d{2}$")
_CH_STOP = re.compile(r"^(Gesamtfondsvermögen|Bankguthaben|Sonstige Vermögenswerte|Derivative Finanzinstrumente|"
                      r"Devisentermingeschäfte|Aufgenommene Kredite|Nettofondsvermögen|Ergänzende Angaben|"
                      r"Bewertungskategorie|Vermögensrechnung|Struktur des)")
_CH_REPORT_DATE = re.compile(r"(?:bericht|report)\s+per\s+(.+?\d{4})", re.I)


def _ch_numbers(nums: str) -> tuple[float | None, float | None]:
    """(market value, percent) from the right-hand numbers of an inventory line."""
    tokens = nums.split()
    pct_at = max((i for i, t in enumerate(tokens) if _CH_PCT.match(t)), default=None)
    if pct_at is None:
        return None, None
    pct = float(tokens[pct_at])
    # The market value is the run of 1-3 digit groups just before the percentage.
    groups = []
    for t in reversed(tokens[:pct_at]):
        if re.fullmatch(r"\d{1,3}", t) and (not groups or len(groups[0]) == 3):
            groups.insert(0, t)
            if len(t) < 3:
                break
        else:
            break
    return (_num("".join(groups)) if groups else None), pct


def parse_ch_inventar(pages: list[str]) -> list[ReportPortfolio]:
    out: dict[str, ReportPortfolio] = {}
    report_date = None
    for text in pages[:5]:
        m = _CH_REPORT_DATE.search(text)
        if m:
            report_date = parse_date(m.group(1))
            break
    active: set[str] = set()   # sub-funds whose inventory runs on to the next page
    for no, text in enumerate(pages):
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            continue
        head = [l for l in lines[:3] if not re.search(r"bericht per|^\d+$", l, re.I)]
        subfund = re.sub(r"^\d+\s+", "", head[0] if head else lines[0]).strip()
        if "Inventar des Fondsverm" not in text and subfund not in active:
            continue
        pf = out.setdefault(subfund, ReportPortfolio(subfund, report_date))
        pf.pages.append(no)
        state = pf.__dict__.setdefault("_state", {"asset": None, "country": None, "kind": None})
        started = subfund in active
        for line in lines[2:] if started else lines:
            if line.startswith("Inventar des Fonds") or (line.startswith("Titel ") and "Verkehrswert" in line):
                started = True
                active.add(subfund)
                continue
            if not started or line.lower().startswith(("total", "titel", "anzahl", "nominal", "(davon")):
                continue
            if _CH_STOP.match(line):
                started = False
                active.discard(subfund)
                continue
            m = _CH_LINE.match(line)
            if not m:
                _heading(line, state)
                continue
            value, pct = _ch_numbers(m.group("nums"))
            if pct is None:
                continue
            name = m.group("name").strip().rstrip("*").strip()
            kind = "participation" if re.search(r"genus+schein|\bptg\b|partizipation", name, re.I) else state["kind"]
            pf.positions.append(
                Position(subfund=subfund, name=name, currency=m.group("ccy"), weight_pct=pct, market_value=value,
                         asset_class=state["asset"], country=state["country"], kind=kind)
            )
    for pf in out.values():
        pf.__dict__.pop("_state", None)
    return list(out.values())


# ---- lux_aufstellung (UBS Asset Management (Europe) S.A. reports in German)

_AUF_HEAD = "Aufstellung der Wertpapierbest"
_AUF_STOP = re.compile(r"^(Total des Wertpapierbestandes|Bankguthaben|Andere Aktiva|Andere Passiva|"
                       r"Total Nettovermögen|Derivative Instrumente|Devisenterminkontrakte|Kontokorrentkredit|"
                       r"Verbindlichkeiten|Nettovermögen)", re.I)
_AUF_LINE = re.compile(r"^(?P<ccy>[A-Z]{3})\s+(?P<rest>.*[A-Za-z].*?\s-?\d{1,3}\.\d{2})$")


def _take_number(tokens: list[str]) -> tuple[float | None, list[str]]:
    """Pop one space-grouped number ("1 234 567.89") off the right of ``tokens``."""
    if not tokens or not re.fullmatch(r"-?\d{1,3}(\.\d+)?", tokens[-1]):
        return None, tokens
    groups = [tokens[-1]]
    i = len(tokens) - 2
    while i >= 0 and re.fullmatch(r"-?\d{1,3}", tokens[i]) and len(groups[0].split(".")[0].lstrip("-")) == 3:
        groups.insert(0, tokens[i])
        i -= 1
    return _num("".join(groups)), tokens[: i + 1]


def parse_lux_aufstellung(pages: list[str]) -> list[ReportPortfolio]:
    out: dict[str, ReportPortfolio] = {}
    active: set[str] = set()
    for no, text in enumerate(pages):
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            continue
        head = [l for l in lines[:3] if not re.search(r"bericht per|^\d+$", l, re.I)]
        subfund = re.sub(r"^\d+\s+", "", head[0] if head else lines[0]).strip()
        starts_here = any(l.startswith(_AUF_HEAD) for l in lines)
        if not starts_here and subfund not in active:
            continue
        as_of = None
        for l in lines:
            if l.startswith(_AUF_HEAD):
                as_of = parse_date(l)
        pf = out.setdefault(subfund, ReportPortfolio(subfund, as_of))
        pf.as_of = pf.as_of or as_of
        pf.pages.append(no)
        state = pf.__dict__.setdefault("_state", {"asset": None, "country": None, "kind": None})
        started = subfund in active
        for line in lines:
            if line.startswith(_AUF_HEAD):
                started = True
                active.add(subfund)
                continue
            if not started or line.startswith(("Der Anhang", "Total ", "Bezeichnung", "Nominal", "(-verlust)", "Devisentermin", "kontrakten", "Swaps")):
                if started and _AUF_STOP.match(line):
                    started = False
                    active.discard(subfund)
                continue
            if _AUF_STOP.match(line):
                started = False
                active.discard(subfund)
                continue
            m = _AUF_LINE.match(line)
            if not m:
                _heading(line, state)
                continue
            tokens = m.group("rest").split()
            pct = float(tokens.pop())
            value, tokens = _take_number(tokens)
            qty, tokens = _take_number(tokens)
            if value is None or not tokens:
                continue
            name = " ".join(tokens)
            pf.positions.append(
                Position(subfund=subfund, name=name, currency=m.group("ccy"), weight_pct=pct, quantity=qty, market_value=value,
                         asset_class=state["asset"], country=state["country"],
                         kind="pref" if re.search(r"\b(prf|pref|vorzug)\b", name, re.I) else state["kind"])
            )
    for pf in out.values():
        pf.__dict__.pop("_state", None)
    return list(out.values())


def parse_ch_toc_isins(pages: list[str]) -> dict[str, list[str]]:
    """Share-class ISINs per sub-fund from a Swiss report's table of contents ("Name  page  A/ CH...")."""
    out: dict[str, list[str]] = {}
    for text in pages[:6]:
        if "ISIN" not in text:
            continue
        current = None
        carry = ""
        for line in text.splitlines():
            line = line.strip()
            m = re.match(r"^(?P<name>.+?)\s+\d{1,3}\s+[A-Z]{1,3}/?\s*(?P<isin>[A-Z]{2}[A-Z0-9]{9}\d)$", line)
            if m:
                current = " ".join((carry + " " + m.group("name")).split())
                carry = ""
                out.setdefault(current, []).append(m.group("isin"))
                continue
            m = re.match(r"^[A-Z]{1,3}/?\s*([A-Z]{2}[A-Z0-9]{9}\d)$", line)
            if m and current:
                out[current].append(m.group(1))
                continue
            if line.endswith("Fonds") or line.endswith("Fund"):
                carry = line  # a name wrapped onto the next line
    return {k: [i for i in v if is_valid_isin(i)] for k, v in out.items()}


def parse_report(pages: list[str]) -> list[ReportPortfolio]:
    """Positions of every sub-fund, with the parser chosen by the report's layout."""
    if any("Inventar des Fondsverm" in t for t in pages):
        return parse_ch_inventar(pages)
    if any(_AUF_HEAD in t for t in pages):
        return parse_lux_aufstellung(pages)
    return parse_lux_portfolio(pages)


# --------------------------------------------------------------------------- names to ISINs

_LEGAL = {
    "ag", "sa", "ltd", "limited", "plc", "inc", "corp", "corporation", "co", "company", "nv", "se", "spa", "ab", "asa",
    "oyj", "as", "tbk", "pt", "the", "llc", "lp", "kgaa", "kg", "bhd", "berhad", "pcl", "sab", "de", "cv", "npv", "regd",
    "reg", "br", "namen", "inh", "registered", "bearer", "shs", "sh", "ord", "ordinary", "cl", "class", "a", "b", "h",
    "n", "genusscheine", "genussscheine", "ptg", "cert", "ps", "post", "rev", "split", "subd", "subdivision", "adr", "gdr",
    "pref", "preferred", "pfd", "vorzug", "units", "unit", "dr", "ser", "series", "vinkuliert", "abp",
}
_ABBREV = {"hldgs": "holding", "hldg": "holding", "hlg": "holding", "holdings": "holding", "hlds": "holding", "grp": "group", "intl": "international",
           "ind": "industries", "inds": "industries", "tech": "technology", "technologies": "technology", "mfg": "manufacturing",
           "fin": "financial", "svcs": "services", "bk": "bank", "ktbk": "kantonalbank"}


def norm_security(name: object) -> str:
    """Comparable issuer key from a security name ("ROCHE HLDGS AG GENUSSCHEINE NPV" -> "roche holding")."""
    if not isinstance(name, str):
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"['‘’][a-z]['‘’]", " ", s)
    s = re.sub(r"\b[a-z]{3}\s?\d+(\.\d+)?\b", " ", s)   # nominal value: chf0.10, eur 0.26
    s = re.sub(r"[^a-z0-9&]+", " ", s)
    # German transliterations and accents compare equal ("Baer" / "Bär", "Zuerich" / "Zürich").
    tokens = [_ABBREV.get(t, t).replace("ae", "a").replace("oe", "o").replace("ue", "u") for t in s.split()]
    tokens = [t for t in tokens if t not in _LEGAL and not t.isdigit()]
    return " ".join(tokens)


@dataclass(frozen=True)
class _Entry:
    isin: str
    key: str
    kind: str | None
    rank: float
    source: str


class NameResolver:
    """Unambiguous equity name -> ISIN lookups against reference lists that carry ISINs."""

    SIX_KIND = {"Bearer Share": "bearer", "Registered Share": "registered", "Participation Certificate": "participation",
                "Dividend-right Certificate": "participation"}

    def __init__(self, entries: list[_Entry]):
        self.by_key: dict[str, list[_Entry]] = {}
        self.by_first: dict[str, list[_Entry]] = {}
        for e in entries:
            if e.key:
                self.by_key.setdefault(e.key, []).append(e)
                self.by_first.setdefault(e.key.split()[0], []).append(e)

    @classmethod
    def from_frames(cls, six_equities: pd.DataFrame | None = None, security_master: pd.DataFrame | None = None) -> "NameResolver":
        entries: list[_Entry] = []
        if six_equities is not None and not six_equities.empty:
            for r in six_equities.to_dict("records"):
                isin, name = r.get("ISIN"), r.get("IssuerNameFull")
                if is_valid_isin(isin) and isinstance(name, str) and "Sponsor" not in name:
                    entries.append(_Entry(isin, norm_security(name), cls.SIX_KIND.get(r.get("SecTypeDesc")), 1e9, "six"))
        if security_master is not None and not security_master.empty:
            sm = security_master[security_master["asset_cat"].isin(["EC", "EP"])]
            for isin, issuer, title, cat, n in sm[["isin", "issuer_name", "title", "asset_cat", "n_positions"]].itertuples(index=False):
                if not is_valid_isin(isin):
                    continue
                kind = "pref" if cat == "EP" else None
                rank = float(n or 0)
                for nm in {issuer, title}:
                    if isinstance(nm, str):
                        entries.append(_Entry(isin, norm_security(nm), kind, rank, "security_master"))
        return cls(entries)

    @classmethod
    def from_paths(cls, six_equities: pd.DataFrame | None, security_master_path: str | Path | None) -> "NameResolver":
        sm = None
        if security_master_path and Path(security_master_path).exists():
            sm = pd.read_parquet(security_master_path, columns=["isin", "issuer_name", "title", "asset_cat", "n_positions"])
        return cls.from_frames(six_equities, sm)

    def _candidates(self, key: str) -> list[_Entry]:
        exact = self.by_key.get(key)
        if exact:
            return exact
        tokens = key.split()
        if len(key) < 6 or not tokens:
            return []
        out = []
        for e in self.by_first.get(tokens[0], []):
            et = e.key.split()
            if len(et) >= len(tokens) and all(a == b for a, b in zip(tokens[:-1], et)) and et[len(tokens) - 1].startswith(tokens[-1]):
                out.append(e)
        return out

    def resolve(self, name: str, currency: str | None = None, country: str | None = None, kind: str | None = None) -> str | None:
        key = norm_security(name)
        if not key:
            return None
        cands = self._candidates(key)
        if not cands or len({e.key for e in cands}) > 1:
            return None
        if kind in ("pref", "participation"):
            cands = [e for e in cands if e.kind == kind]
        elif kind in ("bearer", "registered"):
            cands = [e for e in cands if e.kind == kind] or [e for e in cands if e.kind is None]
        else:
            cands = [e for e in cands if e.kind not in ("pref", "participation")]
        want = country or CURRENCY_COUNTRY.get(currency or "")
        if want:
            k = [e for e in cands if e.isin[:2] == want]
            if k:
                cands = k
            elif currency and currency != "USD":
                cands = [e for e in cands if e.isin[:2] != "US"] or cands
        if not cands:
            return None
        best = max(cands, key=lambda e: e.rank)
        return best.isin


# --------------------------------------------------------------------------- to the adapter contract


_FUND_NAME = re.compile(r"\b(UCITS|ETF|ETFDR|FONDS|FUND|FUNDS|SICAV|ICAV|FCP)\b", re.I)


def to_holdings_frame(pf: ReportPortfolio, resolver: NameResolver | None = None) -> pd.DataFrame:
    rows = []
    for p in pf.positions:
        if p.asset_class in (None, "equity") and _FUND_NAME.search(p.name):
            p.asset_class = "fund"   # fund units listed under a share heading
        isin = None
        how = None
        printed = re.search(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b", p.name)
        if printed and is_valid_isin(printed.group(1)):
            isin, how = printed.group(1), "reported"
        elif resolver is not None and p.asset_class == "equity":
            isin = resolver.resolve(p.name, p.currency, p.country, p.kind)
            how = "name_match" if isin else None
        rows.append(
            {
                "isin": isin, "cusip": None, "sedol": None, "ticker": None, "name": p.name,
                "weight_pct": p.weight_pct, "asset_class": p.asset_class or "other", "currency": p.currency,
                "country": p.country, "isin_source": how, "market_value": p.market_value, "quantity": p.quantity,
            }
        )
    return pd.DataFrame(rows, columns=["isin", "cusip", "sedol", "ticker", "name", "weight_pct", "asset_class", "currency",
                                       "country", "isin_source", "market_value", "quantity"])


def find_portfolio(portfolios: list[ReportPortfolio], fund_name: str, umbrella_prefix: str = "") -> ReportPortfolio | None:
    """The sub-fund section whose heading matches ``fund_name`` (umbrella prefix and punctuation ignored)."""
    def key(s: str) -> str:
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
        if umbrella_prefix:
            s = s.replace(umbrella_prefix.lower(), " ")
        return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())

    want = key(fund_name)
    exact = [p for p in portfolios if key(p.subfund) == want]
    if exact:
        return exact[0]
    # GLEIF names sometimes drop the umbrella or add share-class words: longest heading contained in the name wins.
    inside = [p for p in portfolios if key(p.subfund) and re.search(rf"\b{re.escape(key(p.subfund))}\b", want)]
    return max(inside, key=lambda p: len(key(p.subfund))) if inside else None


# --------------------------------------------------------------------------- fetching reports

SIX_EQUITIES_URL = ("https://www.six-group.com/fqs/ref.json?select=ISIN,ShortName,IssuerNameFull,SecTypeDesc,TradingBaseCurrency"
                    "&where=PortalSegment=EQ&orderby=ISIN&page=1&pagesize=99999")
#: The project's security master (N-PORT and issuer files): ISINs with issuer names, used to match equity names.
SECURITY_MASTER = os.environ.get("OPENBASKET_SECURITY_MASTER", "data/03_primary/security_master.parquet")
_resolvers: dict[str, NameResolver] = {}


def default_resolver(cache_dir: str | Path | None = None) -> NameResolver:
    """Resolver over SIX's share list (cached per day) and the local security master, built once per process."""
    key = str(cache_dir)
    if key not in _resolvers:
        from . import swiss_fetch, swiss_registry

        six = None
        try:
            body = swiss_fetch.cached(cache_dir, f"six_equities/{swiss_fetch.today()}.json", lambda: swiss_fetch.get_bytes(SIX_EQUITIES_URL))
            six, _ = swiss_registry.parse_six(body)
        except Exception as exc:
            logger.warning("SIX share list unavailable: %s", exc)
        _resolvers[key] = NameResolver.from_paths(six, SECURITY_MASTER)
    return _resolvers[key]


def report_link_date(url: str) -> str:
    """Sortable date (``YYYYMMDD``) in a report's file name, or ``""``."""
    m = re.search(r"(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)", url)
    return "".join(m.groups()) if m else ""


def fetch_report(url: str, cache_dir: str | Path | None) -> list[str]:
    """Text pages of the report at ``url`` (PDF cached under ``reports/``)."""
    from . import swiss_fetch

    name = re.sub(r"[^A-Za-z0-9._-]+", "_", url.rsplit("/", 1)[-1])[-120:]
    content = swiss_fetch.cached(cache_dir, f"reports/{name}", lambda: swiss_fetch.get_bytes(url, timeout=300))
    if not content.startswith(b"%PDF"):
        raise ValueError(f"{url} is not a PDF")
    return pdf_pages(content)


_parsed: dict[str, list[ReportPortfolio]] = {}


def report_portfolios(url: str, cache_dir: str | Path | None) -> list[ReportPortfolio]:
    if url not in _parsed:
        _parsed[url] = parse_report(fetch_report(url, cache_dir))
    return _parsed[url]


def holdings_from_report(url: str, fund_name: str, umbrella_prefix: str, cache_dir: str | Path | None,
                         kind: str = "report") -> tuple[str | None, pd.DataFrame, list[str]]:
    """(as_of, HOLDING_COLUMNS frame plus ``isin_source``, notes) for one sub-fund of the report at ``url``."""
    pf = find_portfolio(report_portfolios(url, cache_dir), fund_name, umbrella_prefix)
    if pf is None:
        raise LookupError(f"{fund_name!r} has no schedule of investments in {url}")
    df = to_holdings_frame(pf, default_resolver(cache_dir))
    eq = df[df["asset_class"] == "equity"]
    notes = [
        f"{kind}, positions as of {pf.as_of} (sub-fund {pf.subfund!r}, report pages {pf.pages[0] + 1}-{pf.pages[-1] + 1})",
        f"weights are percent of net assets as printed (two decimals); lines sum to {pf.weight_sum:.2f}%, the rest is cash and other assets",
        f"the report prints no ISINs: {int(eq['isin'].notna().sum())} of {len(eq)} equity lines matched by name (isin_source = name_match); "
        "bonds, fund units and derivatives keep isin = None",
    ]
    return pf.as_of, df, notes
