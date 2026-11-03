"""Match holdings that a source names without an identifier to ISINs, conservatively.

Statutory reports (:mod:`openbasket.sources.fund_reports`) list each holding
by name only: 'ROCHE HLDGS AG CHF- PRP', 'Toyota Tsusho Corp.',
'CARNIVAL CORP-REG-S 5.75000% 24-15.01.30'. Without an ISIN their weight is
unresolved. This module gives such a line an ISIN only when one security,
and only one, fits it.

Candidates
----------

* the security master: every ISIN SEC Form N-PORT filings hold, with the
  issuer names and titles they give it, the N-PORT country, and from
  :func:`~openbasket.pipelines.security_master.nodes.security_terms` its
  currency and, for a bond, its coupon and maturity;
* names from the OpenFIGI cache (equities only; names cut at the field's
  length are skipped);
* FIRDS records, when a FIRDS extract is configured (``isin``, ``name`` and
  optionally ``cfi``, ``currency``, ``coupon``, ``maturity``);
* the holdings of peer funds: physically replicating funds on the same
  index, whose files carry ISINs. A peer narrows the field to the index's
  own constituents and its weights confirm the match.

How a line is matched
---------------------

Names are normalised: accents and punctuation dropped, abbreviations
expanded (``HLDGS`` -> ``HOLDING``, ``INTL`` -> ``INTERNATIONAL``), legal
forms (AG, SA, PLC, Inc, Corp, NV, SE, Ltd ...) and share descriptors
(``Reg``, ``Namen``, ``CHF0.10``, ``NPV``, ``144A``) removed. Share classes
('A', 'B', 'H', ``Class A``, ``Pref``, ``PRP``) and depositary receipts
(``ADR``, ``GDR``) are kept apart: they are different securities.

An equity matches on its normalised name (exactly; or the same words in
another order; or the same words with abbreviations), its country (the
report's country heading against the N-PORT country or the ISIN's prefix)
and its currency. A bond matches on its issuer name, its coupon and its
maturity date, and its currency where printed. A line is matched only if
the candidates that fit leave exactly one ISIN. Ambiguity is never broken by
a guess: two ISINs for the same name (Alphabet A and C, Roche's bearer
share and its participation certificate) leave the line unresolved.

After a list is matched, an ISIN that two of its lines would share, or that
another line of the list reports itself, is taken away again: one of those
matches is wrong.

Every matched line gets ``isin_source`` ``name_match`` and a ``match_score``
between :data:`MIN_SCORE` and 1: 1.0 for an exact name confirmed by country
or currency, less for a reordered or abbreviated name, or an exact name
with nothing to confirm it. Cash, derivatives, funds and lines classed as
residual are never matched.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

#: ``isin_source`` of a line whose ISIN was found by its name.
ISIN_SOURCE = "name_match"
#: Matches scoring below this are not used.
MIN_SCORE = 0.9

# --------------------------------------------------------------------------
# Name normalisation
# --------------------------------------------------------------------------

#: Legal forms, dropped from the end of a name (and ``THE`` from its start).
LEGAL = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "COS", "LTD", "LIMITED", "PLC", "AG", "SA", "SE",
    "NV", "BV", "SPA", "AB", "ASA", "AS", "OYJ", "OY", "KGAA", "GMBH", "SAS", "SCA", "SACA", "LLC", "LP", "LLP",
    "PJSC", "OJSC", "JSC", "PCL", "TBK", "BHD", "BERHAD", "KK", "NL", "SADP", "SAB", "CV", "DE", "SAA", "SARL",
    "SELARL", "SRL", "SL", "SICAV", "PTE", "PTY", "HF", "THE", "AKTIENGESELLSCHAFT", "SOCIETE",
    "ANONYME", "PUBLIC", "HLDG", "LIMITADA", "LTDA", "NA", "PT", "SOCIETA", "PER", "AZIONI", "AKTIEBOLAG", "AKTIEBOLAGET",
}
#: Legal forms that are one form under different spellings, for telling apart companies that
#: share a name ("Rio Tinto Ltd" and "Rio Tinto PLC", "EQT Corp" and "EQT AB").
LEGAL_GROUP = {
    "INC": "US", "INCORPORATED": "US", "CORP": "US", "CORPORATION": "US", "CO": "US", "COMPANY": "US", "COS": "US",
    "LTD": "LTD", "LIMITED": "LTD", "PTE": "LTD", "PTY": "LTD", "PLC": "PLC", "AG": "AG", "AKTIENGESELLSCHAFT": "AG",
    "SE": "SE", "SA": "SA", "SAS": "SA", "SAB": "SA", "CV": "SA", "SCA": "SA", "SACA": "SA", "NV": "NV", "BV": "NV",
    "SPA": "SPA", "AB": "AB", "AKTIEBOLAG": "AB", "AKTIEBOLAGET": "AB", "ASA": "AS", "AS": "AS", "OYJ": "OY",
    "OY": "OY", "KGAA": "KGAA", "LLC": "US", "LP": "LP", "LLP": "LP", "PJSC": "JSC", "JSC": "JSC", "OJSC": "JSC",
    "PCL": "PCL", "TBK": "TBK", "BHD": "BHD", "BERHAD": "BHD", "KK": "KK", "NL": "NL", "SADP": "SA", "SAA": "SA",
}
#: Legal forms that may also open a name ("AB Volvo", "PT Bank Central Asia").
LEADING = {"THE", "AB", "PT", "PJSC", "OAO", "PAO", "JSC"}

#: Words that describe the share rather than name it.
DESCRIPTORS = {
    "REG", "REGD", "REGISTERED", "NAMEN", "NAMENS", "NAMENSAKTIE", "NAMENSAKTIEN", "NA", "ORD", "ORDINARY", "SHS", "SHARE", "SHARES", "STK", "AKT",
    "AKTIE", "AKTIEN", "COM", "COMMON", "STOCK", "NPV", "POST", "SUBD", "REGS", "144A", "UNSPONSORED",
    "NOM", "NOMINATIVE", "ACTION", "ACTIONS",
}
#: Words that mark a depositary receipt.
DEPOSITARY = {"ADR", "ADRS", "ADS", "GDR", "GDRS", "EDR", "NVDR", "DEPOSITARY", "DEPOSITORY", "RECEIPT", "RECEIPTS",
              "SPONS", "SPON", "SPONSORED", "SP", "DR"}
#: Words that mark a share class other than the ordinary share, by canonical form.
CLASS_WORDS = {
    "PREF": "PREF", "PFD": "PREF", "PREFERENCE": "PREF", "PREFERRED": "PREF", "VORZUG": "PREF", "VZ": "PREF",
    "VORZUGSAKTIEN": "PREF", "PRIV": "PREF", "PRF": "PREF", "PREFS": "PREF",
    "PRP": "PART", "GENUSSSCHEIN": "PART", "GENUSSCHEIN": "PART", "GENUSS": "PART", "PARTICIPATION": "PART",
    "NONVOTING": "NONVOTING", "RESTRICTED": "RESTRICTED", "VOTING": "VOTING", "SUBORDINATE": "SUBORDINATE",
    "MULTIPLE": "MULTIPLE", "UNITS": "UNITS", "UNIT": "UNITS", "SAVINGS": "SAVINGS", "RSP": "SAVINGS",
    "RIGHTS": "RIGHTS", "RTS": "RIGHTS", "WARRANTS": "WARRANTS", "WTS": "WARRANTS",
    # a bearer share is a different line from the registered one (Swatch, Roche, Schindler)
    "BEARER": "BEARER", "BR": "BEARER", "INH": "BEARER", "INHABER": "BEARER", "INHABERAKTIE": "BEARER",
    "INHABERAKTIEN": "BEARER", "PORTEUR": "BEARER",
}
#: Abbreviations, expanded before names are compared.
ABBREV = {
    "HLDGS": "HOLDING", "HLDG": "HOLDING", "HOLDINGS": "HOLDING", "HLD": "HOLDING", "HOLD": "HOLDING", "HOLDGS": "HOLDING",
    "INTL": "INTERNATIONAL", "INTERNAT": "INTERNATIONAL", "GRP": "GROUP", "MFG": "MANUFACTURING", "BK": "BANK",
    "NATL": "NATIONAL", "SVCS": "SERVICES", "SVC": "SERVICE", "INDS": "INDUSTRIES",
    "INDUSTRY": "INDUSTRIES", "MGMT": "MANAGEMENT", "INVT": "INVESTMENT", "INVTS": "INVESTMENTS", "DEV": "DEVELOPMENT",
    "ELEC": "ELECTRIC", "COMMS": "COMMUNICATIONS", "COMMUN": "COMMUNICATIONS", "COMM": "COMMUNICATIONS",
    "RES": "RESOURCES", "PPTY": "PROPERTY", "PPTYS": "PROPERTIES", "SYS": "SYSTEMS", "ENTMT": "ENTERTAINMENT",
    "TECHNOLOGIES": "TECHNOLOGY", "TECHS": "TECHNOLOGY", "PHARMACEUTICALS": "PHARMACEUTICAL", "PHARMA": "PHARMACEUTICAL",
    "FINL": "FINANCIAL", "AMER": "AMERICA", "AMERN": "AMERICAN", "MTRS": "MOTORS", "MOTOR": "MOTORS",
    "LABS": "LABORATORIES", "LAB": "LABORATORIES", "INS": "INSURANCE", "ASSUR": "ASSURANCE", "UTD": "UNITED",
    "CHEM": "CHEMICAL", "CHEMICALS": "CHEMICAL", "SOLNS": "SOLUTIONS", "PRODS": "PRODUCTS", "MATLS": "MATERIALS",
    "ENGR": "ENGINEERING", "CTR": "CENTER", "CENTRE": "CENTER", "ST": "SAINT", "STE": "SAINTE", "MT": "MOUNT",
    "CIE": "COMPAGNIE", "COMPANIES": "COMPANY", "BANCORPORATION": "BANCORP", "TELECOMM": "TELECOMMUNICATIONS",
    "TEL": "TELEPHONE", "TELE": "TELEVISION", "GEN": "GENERAL", "MED": "MEDICAL", "HLTH": "HEALTH", "HEALTHCARE": "HEALTH CARE",
}
#: Words dropped for the looser key (the same company under a slightly different style).
LOOSE = {"HOLDING", "GROUP", "AND", "OF", "THE", "COMPANY", "COMPAGNIE", "CORPORATION", "INTERNATIONAL", "SAINT"}

CURRENCIES = set(
    "AED ARS AUD BRL CAD CHF CLP CNH CNY COP CZK DKK EGP EUR GBP GBX HKD HUF IDR ILS INR ISK JPY KRW KWD "
    "MXN MYR NOK NZD PEN PHP PKR PLN QAR RON RUB SAR SEK SGD THB TRY TWD USD VND ZAR".split()
)
_PAR_VALUE = re.compile(r"\b(?:%s)\s?-?\d*(?:[.,]\d+)?(?=\b|\s|$|\()" % "|".join(sorted(CURRENCIES)))
_QUOTED = re.compile(r"['‘’\"]([A-Z0-9]{1,2})['‘’\"]")
_CLASS_PHRASE = re.compile(r"\b(?:CLASS|CL|SER|SERIES|KLASSE)\s*[-.']?\s*([A-Z0-9]{1,2})\b")
_DASHED_CLASS = re.compile(r"\s-([A-Z])-(?=\s|$)|-([A-Z])$")
_PAREN = re.compile(r"\(([^()]*)\)")


def fold(s: str) -> str:
    """Upper case, accents removed: 'Nestlé' -> 'NESTLE'."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()


@dataclass(frozen=True)
class NameKey:
    """A name reduced for comparison."""

    tokens: tuple[str, ...]
    share_class: str | None = None
    depositary: bool = False
    #: The legal form the name ends with, by :data:`LEGAL_GROUP`; not part of the keys.
    legal: str | None = field(default=None, compare=False)

    @property
    def exact(self) -> str:
        return "".join(self.tokens)

    @property
    def sorted(self) -> str:
        return " ".join(sorted(self.tokens))

    @property
    def loose(self) -> str:
        return "".join(t for t in self.tokens if t not in LOOSE)


def name_key(name: str | None) -> NameKey | None:
    """Normalise an equity or issuer name; ``None`` when nothing distinctive is left.

    >>> name_key("ROCHE HLDGS AG CHF- PRP")
    NameKey(tokens=('ROCHE', 'HOLDING'), share_class='PART', depositary=False)
    """
    if not name or not isinstance(name, str):
        return None
    s = fold(name)
    share_class = None
    depositary = False
    # bracketed parts: descriptors are dropped, share classes kept apart, anything else read as name
    kept = []
    for part in _PAREN.findall(s):
        words = set(re.findall(r"[A-Z0-9]+", part))
        if words & DEPOSITARY:
            depositary = True
        elif words & set(CLASS_WORDS):
            first = next(w for w in re.findall(r"[A-Z0-9]+", part) if w in CLASS_WORDS)
            share_class = share_class or CLASS_WORDS[first]
        elif words and not words <= DESCRIPTORS | CURRENCIES | {"REIT"} and not re.search(r"\d", part):
            kept.append(part)
    s = _PAREN.sub(" ", s) + " " + " ".join(kept)
    m = _CLASS_PHRASE.search(s) or _QUOTED.search(s)
    if m:
        share_class = share_class or m.group(1)
        s = s[: m.start()] + " " + s[m.end():]
    s = _QUOTED.sub(" ", s)
    m = _DASHED_CLASS.search(s)
    if m:
        share_class = share_class or (m.group(1) or m.group(2))
        s = s[: m.start()] + " " + s[m.end():]
    s = _PAR_VALUE.sub(" ", s)
    s = re.sub(r"(?<=\b[A-Z])-(?=[A-Z])|(?<=[A-Z])-(?=[A-Z]\b)", "", s)     # "ARGEN-X" -> "ARGENX", "E-ON"
    s = s.replace("&", " AND ").replace("+", " AND ")
    s = re.sub(r"\bREG\s*-?\s*S\b|\b144\s*A\b|\bS\.?\s?A\.?\s?B\.?\s+DE\s+C\.?\s?V\.?", " ", s)
    raw = re.findall(r"[A-Z0-9]+", s.replace("'", "").replace("’", ""))
    # "S A", "N V", "A S", "P L C": letters the punctuation split apart
    toks: list[str] = []
    run: list[str] = []
    for t in raw + [""]:
        if len(t) == 1 and t.isalpha():
            run.append(t)
            continue
        if run:
            toks.append("".join(run))
            run = []
        if t:
            toks.append(t)
    out = []
    for t in toks:
        if t in DEPOSITARY:
            depositary = depositary or t not in ("SP", "DR") or "ADR" in toks
            continue
        if t in CLASS_WORDS:
            share_class = share_class or CLASS_WORDS[t]
            continue
        if t in DESCRIPTORS:
            continue
        out.extend(ABBREV.get(t, t).split())
    legal = None
    while out and out[0] in LEADING and len(out) > 1:
        legal = legal or LEGAL_GROUP.get(out[0])
        out = out[1:]
    # a lone trailing letter after the name is a share class ("Atlas Copco AB A")
    for _ in range(2):
        while len(out) > 1 and out[-1] in LEGAL:
            legal = LEGAL_GROUP.get(out[-1]) or legal
            out = out[:-1]
        if len(out) > 1 and out[-1] == "N":
            out = out[:-1]          # "BASF N": Namensaktie, the registered share
        elif len(out) > 1 and len(out[-1]) == 1 and out[-1].isalpha():
            share_class = share_class or out[-1]
            out = out[:-1]
    if not out or len("".join(out)) < 3:
        return None
    return NameKey(tuple(out), share_class, depositary, legal)


# --------------------------------------------------------------------------
# Bonds
# --------------------------------------------------------------------------

_COUPON = re.compile(r"(?<![\d.,])(\d{1,2}(?:[.,]\d{1,6})?)\s?(?:%|PCT\b)")
_FRACTION_COUPON = re.compile(r"(?<![\d/])(\d{1,2})\s+(\d)/(\d{1,2})(?=\s)")
_D_DMY = re.compile(r"(?<!\d)(\d{1,2})([./-])(\d{1,2})\2(\d{2}|\d{4})(?![\d.,])")
_D_ISO = re.compile(r"(?<!\d)((?:19|20)\d{2})-(\d{2})-(\d{2})(?!\d)")
_D_TEXT = re.compile(r"(?<!\d)(\d{1,2})\s?(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\.?\s?((?:19|20)?\d{2})(?!\d)")
_D_MONTH_YEAR = re.compile(r"\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\.?\s?((?:19|20)\d{2})\b")
_MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
_FLOATING = re.compile(r"\b(?:FRN|VAR|FLOATING|FLOAT|FRB|ZERO|ZCP)\b|%/VAR|EURIBOR|SOFR|SONIA|ESTR|LIBOR")
_BOND_NOISE = re.compile(
    r"\b(?:REG\s*-?\s*S|144\s*A|SUB|SR|SNR|SENIOR|UNSECURED|UNSEC|SECURED|SEC|MTN|EMTN|GMTN|NOTES?|BONDS?|DEB|"
    r"DEBENTURES?|PERP|PERPETUAL|CALLABLE|CALL|GTD|GUARANTEED|1ST|2ND|LIEN|FIXED|FIX|TO|SER|SERIES|CV|CONV|"
    r"CONVERTIBLE|NC\d*)\b"
)


@dataclass(frozen=True)
class BondTerms:
    issuer: NameKey | None
    coupon: float | None
    maturity: str | None          # ISO date
    maturity_year: int | None     # when only the year is known
    tranche: str | None = None    # "144A" or "REGS" when the name says which


def tranche(name: str | None) -> str | None:
    """'144A' or 'REGS' when a bond's name says which tranche it is."""
    s = fold(name or "")
    if re.search(r"\b144\s?A\b", s):
        return "144A"
    if re.search(r"\bREG\s*-?\s*S\b", s):
        return "REGS"
    return None


def _year(y: str) -> int:
    v = int(y)
    return v + 2000 if v < 100 else v


def _valid(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def bond_terms(name: str | None, day_first: bool = True) -> BondTerms | None:
    """Issuer, coupon and maturity of a bond named in a report; ``None`` if it has no fixed coupon.

    'CARNIVAL CORP-REG-S 5.75000% 24-15.01.30' -> Carnival, 5.75, 2030-01-15 (the UBS
    layout prints the issue year before the maturity date); '4.5% 15/05/2030',
    '4.500% 05/15/30', '4.25% 2031' (year only) and '5 3/4 01/15/30' are read too.
    Floating and zero-coupon notes are not matched.
    """
    if not name or not isinstance(name, str):
        return None
    s = fold(name)
    if _FLOATING.search(s):
        return None
    m = _COUPON.search(s)
    coupon = None
    if m:
        coupon = float(m.group(1).replace(",", "."))
        issuer_part = s[: m.start()]
        rest = s[m.end():]
    else:
        f = _FRACTION_COUPON.search(s)
        if not f:
            return None
        coupon = int(f.group(1)) + int(f.group(2)) / int(f.group(3))
        issuer_part, rest = s[: f.start()], s[f.end():]
    # the maturity: the last date on the line ("24-15.01.30": issue year 24, then the maturity)
    maturity = None
    year_only = None
    dates = list(_D_DMY.finditer(rest)) + list(_D_DMY.finditer(issuer_part))
    if dates:
        a, b, y = int(dates[0].group(1)), int(dates[0].group(3)), _year(dates[0].group(4))
        if a > 12 or (day_first and b <= 12):
            maturity = _valid(y, b, a)
        else:
            maturity = _valid(y, a, b) if b > 12 or not day_first else None
    elif (iso := _D_ISO.search(rest)) is not None:
        maturity = _valid(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    elif (t := _D_TEXT.search(rest)) is not None:
        maturity = _valid(_year(t.group(3)), _MONTHS[t.group(2)], int(t.group(1)))
    else:
        ym = re.search(r"(?<![\d.,])((?:19|20)\d{2})(?![\d.,])", rest) or re.search(r"(?<![\d.,/])(\d{2})-(?:19|20)?(\d{2})\b", rest)
        if ym is not None:
            year_only = _year(ym.group(ym.lastindex))
    if maturity is None and year_only is None:
        return None
    issuer_part = re.sub(r"\b\d{2}-$", "", issuer_part.strip())
    issuer = name_key(_BOND_NOISE.sub(" ", issuer_part.replace("-", " ")))
    return BondTerms(issuer, round(coupon, 4), maturity, year_only if maturity is None else int(maturity[:4]), tranche(name))


# --------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------


@dataclass
class Candidate:
    isin: str
    name: str
    key: NameKey
    kind: str                       # "equity" or "debt"
    source: str                     # security_master, openfigi, firds, peer
    country: str | None = None
    currency: str | None = None
    coupon: float | None = None
    maturity: str | None = None
    weight_pct: float | None = None  # for a peer's line
    tranche: str | None = None       # a bond's "144A" or "REGS" tranche, when its name says so

    @property
    def foreign_form(self) -> bool:
        """A US-numbered ISIN for a company of another country: an ADR or a US over-the-counter line."""
        return self.isin[:2] == "US" and bool(self.country) and self.country != "US"


@dataclass
class Named:
    """A line to match: what the source says about it."""

    name: str | None
    asset_class: str | None = None
    country: str | None = None
    currency: str | None = None
    weight_pct: float | None = None


@dataclass(frozen=True)
class NameMatch:
    isin: str
    score: float
    via: str                         # which candidates decided it
    candidate: str                   # the matched candidate's name


@dataclass
class _Pool:
    """Candidates indexed by exact, reordered and loose keys, and by their longer words."""

    cands: list[Candidate] = field(default_factory=list)
    exact: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    sorted: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    loose: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    words: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))

    def add(self, c: Candidate) -> None:
        i = len(self.cands)
        self.cands.append(c)
        self.exact[c.key.exact].append(i)
        self.sorted[c.key.sorted].append(i)
        if c.key.loose and len(c.key.loose) >= 4:
            self.loose[c.key.loose].append(i)
        for t in set(c.key.tokens):
            if len(t) >= 4 and t not in LOOSE:
                self.words[t].append(i)


def _isins(cands: list[Candidate]) -> set[str]:
    return {c.isin for c in cands}


_ASSET_KINDS = {"equity": "equity", "preferred equity": "equity", "debt": "debt", "bond": "debt"}


class NameMatcher:
    """Match named lines to ISINs against a fixed set of candidates (see the module docs)."""

    def __init__(self, equities: list[Candidate], bonds: list[Candidate]):
        self.equity = _Pool()
        for c in equities:
            self.equity.add(c)
        self.bonds: dict[tuple[float, str], list[Candidate]] = defaultdict(list)
        self.bonds_by_year: dict[tuple[float, int], list[Candidate]] = defaultdict(list)
        for c in bonds:
            if c.coupon is None or not c.maturity:
                continue
            self.bonds[(round(c.coupon, 4), c.maturity)].append(c)
            self.bonds_by_year[(round(c.coupon, 4), int(c.maturity[:4]))].append(c)
        self.stats: Counter = Counter()

    # -- building -------------------------------------------------------------

    @classmethod
    def from_frames(
        cls,
        security_master: pd.DataFrame,
        terms: pd.DataFrame | None = None,
        figi_cache: pd.DataFrame | None = None,
        firds: pd.DataFrame | None = None,
    ) -> "NameMatcher":
        """Candidates from the security master (with ``terms``), the OpenFIGI cache and FIRDS."""
        sm = security_master
        if terms is not None and len(terms):
            sm = sm.merge(terms[[c for c in ("isin", "currency", "coupon", "maturity") if c in terms]], on="isin", how="left")
        equities: list[Candidate] = []
        bonds: list[Candidate] = []
        fund_types = {"RF", "PF"}
        for r in sm.itertuples(index=False):
            cat = getattr(r, "asset_cat", None)
            isin = r.isin
            if not isinstance(isin, str):
                continue
            country = _str(getattr(r, "country", None))
            currency = _str(getattr(r, "currency", None))
            if cat in ("EC", "EP") and getattr(r, "issuer_type", None) not in fund_types:
                seen = set()
                for nm in (getattr(r, "issuer_name", None), getattr(r, "title", None)):
                    k = name_key(nm)
                    if k is None:
                        continue
                    if cat == "EP" and k.share_class is None:
                        k = NameKey(k.tokens, "PREF", k.depositary)
                    if (k.exact, k.share_class, k.depositary) in seen:
                        continue
                    seen.add((k.exact, k.share_class, k.depositary))
                    equities.append(Candidate(isin, nm, k, "equity", "security_master", country, currency))
            elif cat == "DBT":
                coupon = getattr(r, "coupon", None)
                maturity = getattr(r, "maturity", None)
                if coupon is None or pd.isna(coupon) or maturity is None or (isinstance(maturity, float) and pd.isna(maturity)):
                    continue
                mat = maturity.isoformat() if hasattr(maturity, "isoformat") else str(maturity)[:10]
                names = [n for n in (getattr(r, "issuer_name", None), getattr(r, "title", None)) if isinstance(n, str)]
                tr = next((t for t in map(tranche, names) if t), None)
                for nm in dict.fromkeys(names):
                    k = name_key(_BOND_NOISE.sub(" ", fold(nm).replace("-", " ")))
                    if k is not None:
                        bonds.append(Candidate(isin, nm, k, "debt", "security_master", country, currency,
                                               round(float(coupon), 4), mat[:10], tranche=tr))
        if figi_cache is not None and len(figi_cache):
            equities += figi_candidates(figi_cache)
        if firds is not None and len(firds):
            e, b = firds_candidates(firds)
            equities += e
            bonds += b
        logger.info("Name matcher: %d equity and %d bond candidates", len(equities), len(bonds))
        return cls(equities, bonds)

    @classmethod
    def from_paths(
        cls,
        security_master,
        terms_path: str | None = None,
        figi_cache_path: str | None = None,
        firds_path: str | None = None,
    ) -> "NameMatcher":
        """As :meth:`from_frames`; ``security_master`` may be a DuckDB relation, paths may be missing."""
        sm = security_master
        if not isinstance(sm, pd.DataFrame):
            sm = sm.filter("asset_cat in ('EC', 'EP', 'DBT')").select(
                "isin, issuer_name, title, asset_cat, issuer_type, country").df()
        return cls.from_frames(sm, _read(terms_path), _read(figi_cache_path), _read(firds_path))

    # -- matching -------------------------------------------------------------

    def match(self, line: Named, peers: list[Candidate] | None = None, peer_pool: _Pool | None = None) -> NameMatch | None:
        """The one ISIN that fits ``line``, or ``None``."""
        kind = _ASSET_KINDS.get((line.asset_class or "equity").lower())
        if kind is None:
            self.stats["skipped"] += 1
            return None
        if kind == "debt" or _COUPON.search(fold(line.name or "")):
            terms = bond_terms(line.name)
            if terms is not None:
                return self._match_bond(line, terms)
            if kind == "debt":
                self.stats["bond_terms_unread"] += 1
                return None
        key = name_key(line.name)
        if key is None:
            self.stats["no_name"] += 1
            return None
        if peer_pool is None and peers:
            peer_pool = make_pool(peers)
        peer = self._match_equity(line, key, peer_pool, "peer") if peer_pool is not None else None
        wide = self._match_equity(line, key, self.equity, None)
        if peer is not None and wide is not None and wide.isin != peer.isin:
            self.stats["peer_conflict"] += 1
            return None
        named_class = key.share_class is not None and (name_key(peer.candidate) or key).share_class == key.share_class if peer else False
        if peer is not None and wide is None and not named_class and self._other_plain_isins(key, peer.isin):
            # the peer's file is today's; another ISIN under the same plain name may be the line the
            # report held before a corporate action ("Exxon Mobil Corp" before its holding company)
            self.stats["peer_conflict"] += 1
            return None
        got = peer or wide
        self.stats["matched" if got else "unmatched"] += 1
        return got

    def _match_equity(self, line: Named, key: NameKey, pool: _Pool, via: str | None) -> NameMatch | None:
        tiers = (
            (1.0, pool.exact.get(key.exact, [])),
            (0.95, pool.sorted.get(key.sorted, [])),
            (0.92, pool.loose.get(key.loose, []) if len(key.loose) >= 4 else []),
        )
        for base, ids in tiers:
            cands = [pool.cands[i] for i in ids]
            if not cands:
                continue
            res = self._decide(line, key, cands, base, via)
            if res is not None:
                return res if res.score >= MIN_SCORE else None
            # candidates exist at this tier but none fits uniquely: never fall to a looser tier
            return None
        # abbreviated words ("RICHEMONT CIE FIN" for "Cie Financiere Richemont"): same number of
        # words, each the candidate's word or its start, found through the line's longest word
        longest = sorted((t for t in key.tokens if len(t) >= 4 and t not in LOOSE), key=len, reverse=True)
        for t in longest[:1]:
            cands = [pool.cands[i] for i in pool.words.get(t, []) if _abbreviates(key.tokens, pool.cands[i].key.tokens)]
            if cands:
                res = self._decide(line, key, cands, 0.9, via, need_confirmation=True)
                return res if res is not None and res.score >= MIN_SCORE else None
        return None

    def _other_plain_isins(self, key: NameKey, isin: str) -> set[str]:
        """ISINs other than ``isin`` filed under exactly this name and legal form, with no class or receipt marker."""
        return {c.isin for i in self.equity.exact.get(key.exact, []) if (c := self.equity.cands[i]).isin != isin
                and c.key.share_class is None and not c.key.depositary and not c.foreign_form
                and (key.legal is None or c.key.legal in (None, key.legal))}

    def _decide(
        self, line: Named, key: NameKey, cands: list[Candidate], base: float, via: str | None,
        need_confirmation: bool = False,
    ) -> NameMatch | None:
        score = base
        # depositary receipts and ordinary shares are different securities; a US-numbered ISIN of a
        # foreign company is read as a receipt unless the line itself is placed in the US
        cands = [c for c in cands if c.key.depositary == key.depositary
                 and (key.depositary or not c.foreign_form or line.country == "US" or via == "peer")]
        if not cands:
            self.stats["conflict_depositary"] += 1
            return None
        # share class: the same class, or (when no candidate states one) the only security of that
        # name; a line that names no class is the ordinary share, never a named class
        if key.share_class is not None:
            same = [c for c in cands if c.key.share_class == key.share_class]
            if same:
                cands = same
            elif all(c.key.share_class is None for c in cands) and len(_isins(cands)) == 1:
                score -= 0.03
            else:
                self.stats["conflict_class"] += 1
                return None
        else:
            plain = [c for c in cands if c.key.share_class is None]
            classed_only = _isins(cands) - _isins(plain)
            if plain and not classed_only:
                cands = plain
            elif via == "peer" and len(_isins(cands)) == 1 and _weight_agrees(line, cands):
                # a peer on the same index holds one line of this company ("SAAB CLASS B" for
                # "Saab AB"), at the same weight
                score -= 0.03
            else:
                self.stats["conflict_class"] += 1
                return None
        confirmed = False
        if line.country:
            ok = [c for c in cands if key.depositary or line.country in (c.country, c.isin[:2])]
            if not ok:
                self.stats["conflict_country"] += 1
                return None
            confirmed = True
            cands = ok
        if line.currency and any(c.currency for c in cands):
            ok = [c for c in cands if not c.currency or c.currency == line.currency or (key.depositary and line.currency == "USD")]
            if not ok:
                self.stats["conflict_currency"] += 1
                return None
            confirmed = confirmed or any(c.currency == line.currency for c in ok)
            cands = ok
        if via == "peer" and _weight_agrees(line, cands):
            confirmed = True
        isins = _isins(cands)
        if len(isins) > 1 and key.legal:
            # companies that share a name but not a legal form: "Rio Tinto Ltd" / "Rio Tinto PLC"
            same = [c for c in cands if c.key.legal in (key.legal, None)]
            if same and len(_isins(same)) == 1 and any(c.key.legal == key.legal for c in same):
                cands, isins = same, _isins(same)
        if len(isins) > 1 and not key.depositary:
            # an ordinary share and its unflagged US line (an ADR filed under the company's name)
            home = [c for c in cands if not c.foreign_form and c.isin not in {x.isin for x in cands if x.foreign_form}]
            if home and len(_isins(home)) == 1 and all(x.foreign_form for x in cands if x.isin not in _isins(home)):
                cands, isins = home, _isins(home)
                score -= 0.02
        if len(isins) != 1:
            self.stats["ambiguous"] += 1
            return None
        if need_confirmation and not confirmed:
            self.stats["unconfirmed"] += 1
            return None
        if not confirmed:
            score -= 0.05
        if len(key.exact) < 5 and not confirmed:
            self.stats["short_unconfirmed"] += 1
            return None
        c = cands[0]
        return NameMatch(c.isin, round(score, 3), via or c.source, c.name)

    def _match_bond(self, line: Named, terms: BondTerms) -> NameMatch | None:
        if terms.issuer is None or terms.coupon is None:
            self.stats["bond_terms_unread"] += 1
            return None
        if terms.maturity:
            cands = self.bonds.get((terms.coupon, terms.maturity), [])
            base = 1.0
        else:
            cands = self.bonds_by_year.get((terms.coupon, terms.maturity_year), [])
            base = 0.92
        fits = []
        for c in cands:
            sim = _issuer_similarity(terms.issuer, c.key)
            if sim:
                fits.append((sim, c))
        if line.currency:
            fits = [(s, c) for s, c in fits if not c.currency or c.currency == line.currency]
        if not fits:
            self.stats["unmatched_bond"] += 1
            return None
        isins = {c.isin for _, c in fits}
        tranche_pick = False
        if len(isins) > 1 and terms.tranche:
            # the Reg S and 144A tranches of one bond share issuer, coupon and maturity; a
            # candidate whose own name says it is the other tranche is not this line
            other = "144A" if terms.tranche == "REGS" else "REGS"
            keep = [(s_, c) for s_, c in fits if c.tranche != other]
            if keep and len({c.isin for _, c in keep}) == 1 and len(keep) < len(fits):
                fits, isins, tranche_pick = keep, {keep[0][1].isin}, True
        if len(isins) != 1:
            self.stats["ambiguous_bond"] += 1
            return None
        sim, c = max(fits, key=lambda x: x[0])
        score = base * sim * (0.97 if tranche_pick else 1.0)
        if score < MIN_SCORE:
            self.stats["unmatched_bond"] += 1
            return None
        self.stats["matched_bond"] += 1
        return NameMatch(c.isin, round(score, 3), c.source, c.name)

    def match_all(self, lines: list[Named], peers: list[Candidate] | None = None,
                  reported: set[str] | None = None) -> list[NameMatch | None]:
        """Match every line of one holdings list, then drop matches that collide.

        An ISIN that two lines would get, or that another line of the list
        already reports, is removed from the name-matched lines: at least one of
        them is wrong, and which one is not known.
        """
        pool = make_pool(peers) if peers else None
        out = [self.match(l, peer_pool=pool) if l.name else None for l in lines]
        counts = Counter(m.isin for m in out if m is not None)
        reported = reported or set()
        for i, m in enumerate(out):
            if m is not None and (counts[m.isin] > 1 or m.isin in reported):
                self.stats["dropped_duplicate"] += 1
                out[i] = None
        return out


def _weight_agrees(line: Named, cands: list[Candidate]) -> bool:
    """A peer line's weight within 0.02 points or 30% of the line's own."""
    if line.weight_pct is None:
        return False
    w = [c.weight_pct for c in cands if c.weight_pct is not None]
    return any(abs(x - line.weight_pct) <= max(0.02, 0.3 * max(abs(x), abs(line.weight_pct))) for x in w)


def make_pool(cands: list[Candidate]) -> _Pool:
    pool = _Pool()
    for c in cands:
        pool.add(c)
    return pool


def peer_candidates(rows: list[tuple[str | None, str | None, float | None]], source: str = "peer") -> list[Candidate]:
    """Candidates from a peer fund's holdings: ``(isin, name, weight_pct)`` per line with an ISIN."""
    out = []
    for isin, name, w in rows:
        if not isin or not name:
            continue
        k = name_key(name)
        if k is not None:
            out.append(Candidate(isin, name, k, "equity", source, None, None, None, None, w))
    return out


def _abbreviates(line: tuple[str, ...], cand: tuple[str, ...]) -> bool:
    """Same number of words, and each line word is a candidate word or its start (any order)."""
    if len(line) != len(cand) or len(line) < 2:
        return False
    left = list(cand)
    for t in sorted(line, key=len, reverse=True):
        hit = next((c for c in left if c == t or (len(t) >= 3 and c.startswith(t))), None)
        if hit is None:
            return False
        left.remove(hit)
    return True


def _issuer_similarity(a: NameKey, b: NameKey) -> float:
    """1.0 for the same issuer name, 0.95 when one is the other cut short or abbreviated, else 0."""
    if a.exact == b.exact or a.loose and a.loose == b.loose:
        return 1.0
    x, y = (a.exact, b.exact) if len(a.exact) <= len(b.exact) else (b.exact, a.exact)
    if len(x) >= 6 and y.startswith(x) and a.tokens[0] == b.tokens[0]:
        return 0.95
    if a.tokens[0] == b.tokens[0] and len(a.tokens) == len(b.tokens) and _abbreviates(a.tokens, b.tokens):
        return 0.95
    # a report cuts long issuer names: every word of the shorter one starts the same word of the longer
    s, l = (a.tokens, b.tokens) if len(a.tokens) <= len(b.tokens) else (b.tokens, a.tokens)
    if len(s) >= 2 and s[0] == l[0] and all(l[i].startswith(s[i]) for i in range(len(s))):
        return 0.95
    # or leaves a word out ("Teva Pharmaceutical Finance II" for "... Finance Netherlands II"):
    # the shorter name's words, in order, within the longer one's, same first word
    if len(s) >= 3 and s[0] == l[0] and _in_order(s, l):
        return 0.93
    return 0.0


def _in_order(short: tuple[str, ...], long: tuple[str, ...]) -> bool:
    it = iter(long)
    return all(any(w == x for x in it) for w in short)


# --------------------------------------------------------------------------
# Other candidate sources
# --------------------------------------------------------------------------

_FIGI_EQUITY_TYPES = {"Common Stock", "Preference", "ADR", "GDR", "Depositary Receipt", "REIT", "Ltd Part", "NY Reg Shrs",
                      "Receipt", "Dutch Cert", "Savings Share", "Tracking Stk", "Stapled Security", "Units"}
#: OpenFIGI cuts names at this length; a name this long may be cut and is not used.
FIGI_NAME_LIMIT = 26


def figi_candidates(cache: pd.DataFrame) -> list[Candidate]:
    """Equity names from the OpenFIGI cache (``id_type``, ``id_value``, ``result`` JSON)."""
    out = []
    for t, isin, res in zip(cache["id_type"], cache["id_value"], cache["result"]):
        if t != "ID_ISIN" or not isinstance(res, str) or not res:
            continue
        try:
            rows = json.loads(res)
        except ValueError:
            continue
        names = {}
        for r in rows or []:
            if r.get("marketSector") != "Equity" or r.get("securityType") not in _FIGI_EQUITY_TYPES:
                continue
            nm = r.get("name")
            if nm and len(nm) < FIGI_NAME_LIMIT:
                names.setdefault(nm, r.get("securityType"))
        for nm, st in names.items():
            k = name_key(nm)
            if k is None:
                continue
            if st in ("ADR", "GDR", "Depositary Receipt") and not k.depositary:
                k = NameKey(k.tokens, k.share_class, True)
            if st in ("Preference",) and k.share_class is None:
                k = NameKey(k.tokens, "PREF", k.depositary)
            out.append(Candidate(isin, nm, k, "equity", "openfigi"))
    return out


def firds_candidates(df: pd.DataFrame) -> tuple[list[Candidate], list[Candidate]]:
    """Equities (CFI starting with E) and fixed-coupon bonds (D) from a FIRDS extract."""
    eq, bd = [], []
    cols = set(df.columns)
    for r in df.itertuples(index=False):
        isin, nm = getattr(r, "isin", None), getattr(r, "name", None)
        if not isinstance(isin, str) or not isinstance(nm, str):
            continue
        cfi = str(getattr(r, "cfi", "") or "")
        cur = _str(getattr(r, "currency", None)) if "currency" in cols else None
        if cfi.startswith("E") or not cfi:
            k = name_key(nm)
            if k is not None:
                eq.append(Candidate(isin, nm, k, "equity", "firds", None, cur))
        elif cfi.startswith("D") and "coupon" in cols and "maturity" in cols:
            coupon, mat = getattr(r, "coupon", None), getattr(r, "maturity", None)
            if coupon is None or pd.isna(coupon) or not mat:
                continue
            k = name_key(_BOND_NOISE.sub(" ", fold(nm).replace("-", " ")))
            if k is not None:
                bd.append(Candidate(isin, nm, k, "debt", "firds", None, cur, round(float(coupon), 4), str(mat)[:10]))
    return eq, bd


def _read(path: str | None) -> pd.DataFrame | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        logger.info("Name matcher: %s not found; matching without it", path)
        return None
    return pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p, dtype=str)


def _str(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


# --------------------------------------------------------------------------
# Peers on the same index
# --------------------------------------------------------------------------

#: Indices a fund name can be matched on, as they read once brand and wrapper words are dropped.
INDICES = {
    "MSCI WORLD", "MSCI ACWI", "MSCI EMERGING MARKETS", "MSCI EM", "MSCI EMU", "MSCI EUROPE", "MSCI JAPAN", "MSCI USA",
    "MSCI PACIFIC", "MSCI UK", "MSCI CANADA", "MSCI SWITZERLAND", "MSCI CHINA", "S AND P 500", "NASDAQ 100",
    "EURO STOXX 50", "STOXX EUROPE 600", "FTSE 100", "FTSE 250", "FTSE ALL WORLD", "SMI", "SLI", "SPI", "DAX",
    "RUSSELL 2000", "TOPIX", "NIKKEI 225", "DOW JONES INDUSTRIAL AVERAGE",
}
_WRAPPER = {
    "UCITS", "ETF", "CORE", "FUND", "INDEX", "TRACKER", "THE", "ETC", "PLC", "ICAV", "SICAV", "CH", "IE", "LU",
    "STATE", "STREET", "SPDR", "ISHARES", "UBS", "INVESCO", "XTRACKERS", "AMUNDI", "VANGUARD", "HSBC", "LYXOR",
    "FRANKLIN", "WISDOMTREE", "FIDELITY", "JPMORGAN", "BNP", "PARIBAS", "EASY", "DEKA", "COMSTAGE", "SWISSCANTO",
    "ZKB", "PICTET", "LO", "R", "TM", "ACC", "DIST", "DIS", "INC", "A", "B", "C", "D",
} | CURRENCIES


def index_of(fund_name: str | None) -> str | None:
    """The index a fund tracks, when its name is the index and nothing more.

    'UBS Core MSCI World UCITS ETF USD acc' and 'iShares Core MSCI World UCITS ETF'
    -> 'MSCI WORLD'; 'UBS MSCI World Socially Responsible UCITS ETF' -> ``None``
    (a variant of the index, not the index).
    """
    if not fund_name:
        return None
    s = fold(fund_name).replace("&", " AND ").replace("-", " ")
    s = re.sub(r"\(.*?\)|®|™", " ", s)
    words = [w for w in re.findall(r"[A-Z0-9]+", s) if w not in _WRAPPER and not re.fullmatch(r"H[A-Z]{3}", w)]
    core = " ".join(words)
    core = core.replace("SANDP", "S AND P")
    return core if core in INDICES else None
