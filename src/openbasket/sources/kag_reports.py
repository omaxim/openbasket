"""Read the Vermögensaufstellung of a German or Austrian fund report.

German (KAGB) and Austrian (InvFG) fund reports list every position in a
*Vermögensaufstellung*, one line per security with its ISIN, quantity,
price, market value and share of the fund's net assets (``% des
Fondsvermögens``, ``%-Anteil``)::

    US0378331005 Apple Inc. STK 3.744.928 1.086.272 750.963 USD 253,7900 824.950.331,67 3,95
    0,7500 % A.P.Moeller-Maersk A/S EO-Medium-Term Nts 2021(21/31) XS2410368042 EUR 300 0 0 % 87,2036 261.610,65 1,47

A holding is a line with an ISIN followed by at least three German-style
numbers, the last of which has two or three decimals and lies between -100
and 100: that is its weight in percent of net assets, as printed, never
rescaled. Reading starts at the schedule's heading (not its table-of-contents
entry) and stops at the list of transactions made during the period
(``Während des Berichtszeitraumes abgeschlossene Geschäfte``), so purchases
and sales are not read as holdings. The fund's own ISINs are skipped.

The same rule reads the report as HTML (Bundesanzeiger, one table row per
line) or as PDF text. A PDF is read with ``pypdf`` first; if that gives no
schedule that adds up, with ``pdfplumber``, whose text keeps a row's cells
on one line where ``pypdf`` puts each cell on its own line.

A report is accepted only if its holdings add up to between
:data:`MIN_TOTAL` and :data:`MAX_TOTAL` percent; the rest of the fund
(cash, receivables, derivatives' margin) is the unresolved weight.
"""

from __future__ import annotations

import io
import logging
import re

import pandas as pd

from ..identifiers import is_valid_isin
from .issuers import HOLDING_COLUMNS

logger = logging.getLogger(__name__)

#: An ISIN may run straight into the name ("US0378331005Apple Inc."); the checksum keeps false hits out.
ISIN = re.compile(r"(?<![A-Z0-9])([A-Z]{2}[A-Z0-9]{9}\d)(?![0-9])")
_NUM = re.compile(r"^-?\d{1,3}(?:\.\d{3})*(?:,\d+)?$|^-?\d+(?:,\d+)?$")
#: Swiss and Liechtenstein reports: "184’073", "227.25", "2.08%".
_NUM_CH = re.compile(r"^-?\d{1,3}(?:[’'`]\d{3})*(?:\.\d+)?%?$")
#: The schedule's heading, on a line of its own (a contents entry ends with a page number).
START = re.compile(r"^\s*(Vermögensaufstellung|Vermögensaufstellung zum [\d.]+(\s*\(.*\))?|Vermögensaufstellung per [\d.]+|"
                   r"Vermögensinventar( per [\d.]+)?)\s*$", re.I)
#: Where the schedule ends and the period's transactions begin.
STOP = re.compile(
    r"abgeschlossene Geschäfte|nicht mehr in der Vermögensaufstellung|Käufe und Verkäufe|"
    r"Gliederung des Fondsvermögens|Wertpapier-?Transaktionen",
    re.I,
)
MIN_TOTAL, MAX_TOTAL = 60.0, 105.0

#: Section headings in the schedule, mapped to an asset class (checked in order).
_SECTIONS = (
    (re.compile(r"Investmentanteile|Investmentzertifikate|Investmentfonds|Fondsanteile|Wertpapier-Investmentanteile", re.I), "fund"),
    (re.compile(r"Derivate|Futures|Termingeschäfte|Optionen|Optionsrechte|Swaps", re.I), "derivative"),
    (re.compile(r"Verzinsliche|Anleihen|Renten|Schuldverschreibungen|Pfandbriefe|Geldmarktpapiere|Commercial Paper", re.I), "debt"),
    (re.compile(r"\bAktien\b|Genussscheine|Bezugsrechte|REIT", re.I), "equity"),
)


def _num(token: str) -> float:
    if token.endswith("%") or "’" in token or "'" in token or ("." in token and "," not in token
                                                                and not re.fullmatch(r"-?\d{1,3}(\.\d{3})+", token)):
        return float(re.sub(r"[’'`%]", "", token))
    return float(token.replace(".", "").replace(",", "."))


def _is_num(token: str) -> bool:
    return bool(_NUM.match(token) or _NUM_CH.match(token))


def _is_pct(token: str) -> bool:
    """A weight: two or three decimals, German ("1,47") or Swiss ("1.47%")."""
    m = re.fullmatch(r"-?\d+(?:[.’']\d{3})*([,.])(\d{2,3})%?", token)
    return bool(m) and (m.group(1) == "," or token.endswith("%"))


def section_class(line: str) -> str | None:
    """The asset class a schedule heading names, or ``None`` for a line that is not one."""
    if ISIN.search(line) or len(line) > 90:
        return None
    for pattern, cls in _SECTIONS:
        if pattern.search(line):
            return cls
    return None


def holding(line: str, own: set[str] | frozenset[str] = frozenset()) -> dict | None:
    """One holding from a schedule line, or ``None``."""
    m = ISIN.search(line)
    if not m or m.group(1) in own or not is_valid_isin(m.group(1)):
        return None
    after = line[m.end():].split()
    nums = [t for t in after if _is_num(t)]
    if len(nums) < 3 or not _is_pct(nums[-1]):
        return None
    pct = _num(nums[-1])
    if not -100.0 <= pct <= 100.0:
        return None
    rest = (line[:m.start()] + " " + line[m.end():]).strip()
    name = re.split(r"\s(?:STK|ANT|Stück|Anteile|EUR|USD|CHF|GBP|JPY|%)(?:\s|$)", " " + rest)[0].strip()
    value = _num(nums[-2]) if len(nums) >= 2 else None
    return {"isin": m.group(1), "name": name[:120] or None, "weight_pct": pct, "value": value}


def _join_split_rows(lines: list[str]) -> list[str]:
    """Rows whose ISIN sits on a line of its own next to the line of figures, as one line.

    Liechtenstein and Swiss reports often print a row as "USD Johnson & Johnson" /
    "US4781601046" / "965.00 435.00 810.00 227.25 184’073 2.08%" (or the ISIN after the figures).
    """
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        m = ISIN.fullmatch(line.strip())
        if m:
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            prev = out[-1] if out else ""
            figs = lambda s: sum(_is_num(t) for t in s.split())
            if figs(nxt) >= 3 and not ISIN.search(nxt):
                out.append(f"{m.group(1)} {nxt}")
                i += 2
                continue
            if figs(prev) >= 3 and not ISIN.search(prev):
                out[-1] = f"{m.group(1)} {prev}"
                i += 1
                continue
        out.append(line)
        i += 1
    return out


def _schedule(lines: list[str], own: set[str] | frozenset[str]) -> pd.DataFrame:
    rows, section = [], None
    for line in _join_split_rows(lines):
        # "Vermögensinventar / Käufe und Verkäufe" heads the holdings themselves (with movement columns).
        if STOP.search(line) and rows and not re.search(r"Vermögens(inventar|aufstellung)", line, re.I):
            break
        cls = section_class(line)
        if cls is not None:
            section = cls
            continue
        h = holding(line, own)
        if h:
            h["asset_class"] = section or "other"
            rows.append(h)
    df = pd.DataFrame(rows, columns=["isin", "name", "weight_pct", "value", "asset_class"])
    # A row printed twice (page break repeats) counts once.
    return df.drop_duplicates(["isin", "weight_pct", "value"]).reset_index(drop=True)


#: The line that closes one fund's schedule in a report for several funds
#: ("III. Fondsvermögen EUR 48.074.544,78 100,00"); the fund's name follows in its unit values.
FUND_TOTAL = re.compile(r"^\s*(?:[IVX]+\.\s*)?Fondsvermögen\s+[A-Z]{3}\s+-?[\d.]+,\d+\s+100,00\s*$")


def _by_fund_totals(lines: list[str], totals: list[int], own, fund_name: str | None) -> pd.DataFrame | None:
    """A report for several funds without a heading per fund: blocks between fund totals, named by what follows."""
    if not fund_name:
        return None
    want = _words(fund_name)
    best, best_score = None, 0.0
    bounds = [-1] + totals
    for a, b in zip(bounds, bounds[1:]):
        df = _schedule(lines[a + 1:b], own)
        if not len(df):
            continue
        after = []
        for line in lines[b + 1:b + 8]:
            if FUND_TOTAL.match(line) or holding(line, own):
                break  # the next fund's block
            after.append(line)
        score = len(want & _words(" ".join(after))) / max(len(want), 1)
        if score > best_score and plausible(df):
            best, best_score = df, score
    return best


_GENERIC = {"fonds", "fund", "inhaber", "anteile", "anteil", "class", "klasse", "eur", "usd", "chf", "o.n.", "und",
            "der", "die", "das", "for", "the", "and", "invest", "investment", "sondervermögen", "ogaw"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zäöüß0-9]+", text.lower()) if len(w) > 2 and w not in _GENERIC}


def parse_lines(lines: list[str], own: set[str] | frozenset[str] = frozenset(),
                fund_name: str | None = None) -> pd.DataFrame:
    """Holdings from the lines of a report (all pages, or all table rows, in order).

    A report for several funds has one schedule per fund, each after its own
    heading; the one whose heading's surroundings name ``fund_name`` best is
    read. Without a name, or when no heading names the fund, the first
    schedule that adds up is.
    """
    starts = [i for i, l in enumerate(lines) if START.match(l)]
    whole = _schedule(lines[starts[0] if starts else 0:], own)
    # One fund, even when its heading repeats on every page: read straight through.
    if plausible(whole) or total(whole) < MIN_TOTAL:
        return whole
    totals = [i for i, l in enumerate(lines) if FUND_TOTAL.match(l)]
    if len(starts) <= 1:
        if len(totals) < 2:
            return whole
        split = _by_fund_totals(lines, totals, own, fund_name)
        return split if split is not None else whole
    segments = []
    for k, s in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(lines)
        context = " ".join(lines[max(0, s - 15):s + 3])
        segments.append((context, _schedule(lines[s:end], own)))
    segments = [(c, df) for c, df in segments if len(df)]
    if not segments:
        return _schedule([], own)
    if fund_name:
        want = _words(fund_name)
        scored = [(len(want & _words(c)) / max(len(want), 1), plausible(df), -i, df) for i, (c, df) in enumerate(segments)]
        best = max(scored, key=lambda x: x[:3])
        if best[0] > 0 and best[1]:
            return best[3]
    if len(totals) >= 2:
        split = _by_fund_totals(lines, totals, own, fund_name)
        if split is not None:
            return split
    return next((df for _, df in segments if plausible(df)), whole)


def total(df: pd.DataFrame) -> float:
    return float(df["weight_pct"].sum()) if len(df) else 0.0


def plausible(df: pd.DataFrame) -> bool:
    return MIN_TOTAL <= total(df) <= MAX_TOTAL


def own_isins(text: str) -> set[str]:
    """ISINs on a report's first pages: the fund's own share classes."""
    return set(ISIN.findall(text))


def _pages_pypdf(content: bytes) -> list[str]:
    from .report_parser import extract_pages

    return extract_pages(content)


def _pages_pdfplumber(content: bytes) -> list[str]:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return [p.extract_text() or "" for p in pdf.pages]


def parse_pdf(content: bytes, own: set[str] | None = None, fund_name: str | None = None) -> tuple[pd.DataFrame, str]:
    """Holdings and the text extractor that gave them (``pypdf`` or ``pdfplumber``).

    ``own`` defaults to the ISINs on the first two pages.
    """
    best, how = pd.DataFrame(columns=["isin", "name", "weight_pct", "value", "asset_class"]), "none"
    for name, extract in (("pypdf", _pages_pypdf), ("pdfplumber", _pages_pdfplumber)):
        try:
            pages = extract(content)
        except Exception as exc:  # a PDF one extractor cannot read may suit the other
            logger.info("%s could not read the report: %s", name, exc)
            continue
        mine = own if own is not None else own_isins(" ".join(pages[:2]))
        df = parse_lines([l for p in pages for l in p.splitlines()], mine, fund_name)
        if plausible(df):
            return df, name
        if len(df) > len(best):
            best, how = df, name
    return best, how


def parse_html(html: str, own: set[str] | frozenset[str] = frozenset(), fund_name: str | None = None) -> pd.DataFrame:
    """Holdings from a report published as HTML tables (one row per line).

    The text between tables is kept as context lines; where it carries a
    schedule heading, a heading line is added, so a report for several funds
    splits as a PDF does.
    """
    from .edinet_reports import tables

    lines = []
    for before, rows in tables(html):
        if before.strip():
            lines.append(before)
            if re.search(r"Vermögensaufstellung", before, re.I):
                lines.append("Vermögensaufstellung")
        lines.extend(" ".join(c for c in r if c) for r in rows)
    return parse_lines(lines, own, fund_name)


def as_holdings(df: pd.DataFrame) -> pd.DataFrame:
    """In :data:`~openbasket.sources.issuers.HOLDING_COLUMNS`, ISINs as reported."""
    out = pd.DataFrame({c: None for c in HOLDING_COLUMNS}, index=df.index)
    out["isin"] = df["isin"]
    out["name"] = df["name"]
    out["weight_pct"] = df["weight_pct"].astype(float)
    out["asset_class"] = df["asset_class"]
    return out[HOLDING_COLUMNS].reset_index(drop=True)
