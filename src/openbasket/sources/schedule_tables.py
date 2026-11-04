"""Read the schedule of investments out of a fund's annual or semi-annual report.

Built for the report layouts of Xtrackers (DWS) and other umbrella funds whose
reports print headings, sub-totals and totals in a **bold** font and position
lines in a regular one. The bold flag is what makes the parse reliable: a
regular line without numbers is the first half of a wrapped security name; a
bold line is a heading (asset class, country, industry) or a total.

Two line shapes are read:

``qty name value pct``
    Irish plc reports (for example Xtrackers (IE) plc "Anlageportfolio" /
    "Schedule of Investments"): holding, description, fair value, percent of
    net assets. Bond lines also carry rating, coupon and maturity in the
    middle; they stay in the description.
``name qty CCY cost value pct``
    Luxembourg SICAV reports by DWS ("Anlagebestand" / "Statement of
    Investments"): description, nominal or number of shares, local currency,
    acquisition cost, market value, percent of net assets.

Weights are the report's own percent of net assets (or of the fund, as the
report says), never rescaled. Hedging forwards of share classes are kept as
one net line per fund, from the report's own "net unrealised" total.

The input is a list of pages, each a list of :class:`Line` (text plus a bold
flag), which :func:`pdf_lines` produces with pdfplumber. Pure functions only:
no network.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import pandas as pd

from ..identifiers import is_valid_isin
from .issuers import HOLDING_COLUMNS

#: Code only; the frames it returns carry report data, whose flag is set by the caller.
REDISTRIBUTION = "public"

EXTRA_COLUMNS = ["quantity", "market_value", "residual", "group", "page"]


@dataclass(frozen=True)
class Line:
    text: str
    bold: bool = False


@dataclass
class Schedule:
    title: str
    holdings: pd.DataFrame
    total_pct: float | None            # the report's own total line, when found
    pages: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    investments_pct: float | None = None   # "total investments" line, when the schedule has one


# -- PDF text -----------------------------------------------------------------

def pdf_page_texts(path) -> list[str]:
    """Plain text of every page (pypdf), used to find the contents and page numbers."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return [p.extract_text() or "" for p in reader.pages]


def pdf_lines(path, page_numbers: list[int]) -> dict[int, list[Line]]:
    """Text lines with a bold flag for the given 0-based pages (pdfplumber)."""
    import pdfplumber

    out: dict[int, list[Line]] = {}
    with pdfplumber.open(str(path)) as pdf:
        for n in page_numbers:
            if n < 0 or n >= len(pdf.pages):
                continue
            lines = []
            for ln in pdf.pages[n].extract_text_lines(return_chars=True):
                letters = [c for c in ln["chars"] if c["text"].isalpha()] or [
                    c for c in ln["chars"] if c["text"].strip()
                ]
                bold = bool(letters) and sum("bold" in c["fontname"].lower() for c in letters) * 2 > len(letters)
                lines.append(Line(ln["text"], bold))
            out[n] = lines
    return out


# -- contents and page ranges -------------------------------------------------

SCHEDULE_HEADINGS = re.compile(
    r"^(anlageportfolio|anlagebestand|schedule of investments|statement of investments|"
    r"portfolio of investments|inventaire du portefeuille|wertpapierbestand|"
    r"aufstellung des wertpapierbestandes|vermögensaufstellung der teilfonds)",
    re.I,
)
_CONTENTS = re.compile(r"\b(inhalt|inhaltsverzeichnis|contents|table des mati|sommaire)\b", re.I)
_FUNDLIKE = re.compile(r"^(?!.*\b(polic|report|regulation|information|disclosure|remuneration|notes|anhang|bericht))"
                       r".*\b(etf|icav|fund|fonds)\b", re.I)
_TOC_LINE = re.compile(r"^(.*?\S)\s*(?:\.{2,}\s*)?\s(\d{1,4})$")


def contents_pages(texts: list[str], first: int = 12) -> list[int]:
    """Pages of the table of contents: titled as such, or a run of "title  page" lines."""
    out = []
    for i, t in enumerate(texts[:first]):
        numbered = sum(bool(_TOC_LINE.match(x.strip())) for x in t.splitlines())
        if _CONTENTS.search(t[:400]) or numbered >= 8:
            out.append(i)
    return out


def toc_entries(texts: list[str], brand: str) -> tuple[list[tuple[str, int]], int | None]:
    """Sub-fund schedule entries ``(title, printed page)`` and the printed page where they end.

    The entries are the brand-named lines that follow the schedule heading in
    the table of contents; the first numbered line after them that is not a
    sub-fund (for example "Notes to the financial statements 1368") gives the
    end of the last schedule.
    """
    lines: list[str] = []
    for i in contents_pages(texts):
        lines.extend(x.strip() for x in texts[i].splitlines() if x.strip())
    entries: list[tuple[str, int]] = []
    end: int | None = None
    in_sched = False
    buffer = ""
    b = brand.lower()
    for raw in lines:
        if SCHEDULE_HEADINGS.match(raw) and not in_sched:
            in_sched = True
            continue
        if not in_sched:
            continue
        if re.search(r"\(fortsetzung\)|\(continued\)|\(suite\)", raw, re.I) and not raw.lower().startswith(b):
            continue
        m = _TOC_LINE.match(raw)
        if not m:
            buffer = (buffer + " " + raw).strip() if (buffer or raw.lower().startswith(b)) else ""
            continue
        # A numbered line that starts with the brand is a whole title; a buffered
        # line before it is page furniture ("XTRACKERS (IE) PLC", "Seite").
        title = m.group(1) if (not buffer or m.group(1).lower().startswith(b)) else (buffer + " " + m.group(1)).strip()
        buffer = ""
        if title.lower().startswith(b) or _FUNDLIKE.search(title):
            entries.append((title, int(m.group(2))))
        elif entries:
            end = int(m.group(2))
            break
    return entries, end


def page_offset(texts: list[str]) -> int:
    """PDF index minus printed page number, by majority over pages that print one."""
    votes: dict[int, int] = {}
    for i, t in enumerate(texts):
        for x in t.splitlines()[:12] + t.splitlines()[-4:]:
            x = x.strip()
            if x.isdigit() and 0 < int(x) <= len(texts) + 20:
                votes[i - int(x)] = votes.get(i - int(x), 0) + 1
    return max(votes, key=votes.get) if votes else 0


def header_pages(texts: list[str], brand: str, head: int = 8) -> dict[str, list[int]]:
    """Sub-fund pages found from page headers: a brand-named title just above a schedule heading.

    For reports whose contents list only "Schedule of Investments" with one
    page number (L&G), every schedule page repeats the sub-fund name above the
    heading.
    """
    b = brand.lower()
    out: dict[str, list[int]] = {}
    for i, t in enumerate(texts):
        lines = [x.strip() for x in t.splitlines() if x.strip()][:head]
        for j, x in enumerate(lines):
            if SCHEDULE_HEADINGS.match(x):
                title = next((y for y in reversed(lines[:j]) if y.lower().startswith(b)), None)
                if title:
                    out.setdefault(title, []).append(i)
                break
    return out


def subfund_pages(texts: list[str], brand: str) -> dict[str, list[int]]:
    """Map each sub-fund title to its 0-based PDF pages: from the contents, else from page headers."""
    entries, end = toc_entries(texts, brand)
    if not entries:
        return header_pages(texts, brand)
    off = page_offset(texts)
    out: dict[str, list[int]] = {}
    for k, (title, start) in enumerate(entries):
        stop = entries[k + 1][1] if k + 1 < len(entries) else (end or start + 1)
        if stop < start:
            continue
        stop = max(stop, start + 1)
        out.setdefault(title, list(range(start + off, min(stop + off, len(texts)))))
    return out


# -- names --------------------------------------------------------------------

def compact(text: str) -> str:
    """Lower-case letters and digits only: survives the split words pypdf leaves ("SW AP")."""
    t = unicodedata.normalize("NFKD", str(text))
    t = re.sub(r"\*+|\(\d+\)", " ", t).lower()
    t = re.sub(r"(?:^|(?<=[\s,/-]))(?:and|und|&)(?=[\s,/-]|$)", " ", t)   # "and" is often left out of slugs
    return re.sub(r"[^a-z0-9]", "", t)


def title_keys(title: str, brand: str) -> set[str]:
    """Compact forms of a sub-fund title with and without the umbrella prefix."""
    c = compact(title)
    b = compact(brand)
    keys = {c}
    if c.startswith(b):
        keys.add(c[len(b):])
        if c[len(b):].startswith("ii"):
            keys.add(c[len(b) + 2:])
    return keys


def match_title(fund_key: str, titles, brand: str, loose: bool = False) -> str | None:
    """The single title whose compact form equals ``fund_key`` (already compact).

    With ``loose``, a key that equals no title may instead be contained in
    exactly one title (for issuers whose fund-page slugs shorten the name).
    """
    hits = [t for t in titles if fund_key in title_keys(t, brand)]
    if len(set(hits)) == 1:
        return hits[0]
    if loose and not hits and len(fund_key) >= 6:
        core = fund_key[:-8] if fund_key.endswith("ucitsetf") else fund_key
        part = sorted({t for t in titles if core in compact(t)})
        if len(part) > 1:   # "UK Quality Dividends" is also inside "Europe ex-UK Quality Dividends"
            part = [t for t in part if any(k.startswith(core) for k in title_keys(t, brand))]
        if len(part) == 1:
            return part[0]
    return None


# -- line parsing ---------------------------------------------------------------

_PCT = re.compile(r"^\(?[-−–]?\d{1,3}(?:[.,]\d{3})*[.,]\d{2,4}\)?$")   # no "%": "1.45%" is a coupon
_AMOUNT = re.compile(r"^\(?[-−–]?\d{1,3}(?:[.,'’]\d{3})*(?:[.,]\d+)?\)?$")
_DASH = {"—", "–", "-", "−"}
_CCY = re.compile(r"^[A-Z]{3}$")
_DATE = re.compile(r"^\d{2}[./]\d{2}[./]\d{2,4}$")
_QTY_FOOTNOTE = re.compile(r"^(\d{1,3}(?:[.,]\d{3})+)\d(\D.*)$")
_HEADER_WORDS = {"value", "net", "rate", "date", "amount", "gain", "loss", "assets", "€", "$", "£", "%", "of",
                 "coupon", "maturity", "nominal", "description", "holdings", "fair", "unrealised", "notional"}
_ISIN = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b")

ASSET_HEADINGS = [
    (re.compile(r"^(aktien|vorzugsaktien|equities|equity|common stocks?|preferred|shares|actions|"
                r"real estate investment trusts|depositary receipts)\b", re.I), "equity"),
    (re.compile(r"^(reverse repurchase|pensionsgeschäfte|deposits?|time deposits|bankguthaben)\b", re.I), "cash"),
    (re.compile(r"^(money market|geldmarktfonds)\b", re.I), "money market"),
    (re.compile(r"^(collective investment schemes?|investment companies)\b", re.I), "fund"),
    (re.compile(r"^(optionsscheine|warrants)\b", re.I), "derivative (warrants)"),
    (re.compile(r"^(bezugsrechte|rights)\b", re.I), "other"),
    (re.compile(r"^(offene devisentermin|devisentermin|forward|open forward|devisenterminkontrakte)", re.I), "derivative (foreign exchange)"),
    (re.compile(r"^(offene futures|futures|terminkontrakte|finanzterminkontrakte|open futures)", re.I), "derivative (futures)"),
    (re.compile(r"^(swaps?\b|total return swap|tauschgeschäfte|credit default)", re.I), "derivative (swaps)"),
    (re.compile(r"^(optionen|options)\b", re.I), "derivative (options)"),
    (re.compile(r"^(investmentfonds|investment funds|offene fonds|undertakings for collective|organismen|zielfonds|exchange traded funds)", re.I), "fund"),
    (re.compile(r"^(\w*anleihen|\w*bonds?\b|government|corporate|pfandbriefe|covered|geldmarktpapiere|commercial paper|treasury|schuldverschreibungen|zertifikate|certificates)", re.I), "debt"),
]
_INVESTMENTS_TOTAL = re.compile(r"^(summe anlagebestand|total investments|total investment portfolio|total des investissements)\b", re.I)
_RECAP = re.compile(r"^(total investments|summe der anlagen)$", re.I)
_SPLIT_DECIMAL = re.compile(r"(\d) ([.,]\d{2})(?=\)?$)")   # "92 .75" as pypdf/pdfplumber sometimes print it
_SKIP_BOLD = re.compile(r"^(summe|total|sub-?total|gesamt)\b", re.I)
_COLUMN_HEADER = re.compile(
    r"(des fonds|anteil in %|% des|wertpapier$|beschreibung|nennwert|marktwert|fair value|% of net|"
    r"description|holdings?$|währung|rating|fälligkeit|maturity|einstandskosten|plc$|sicav|icav|"
    r"^nicht$|^realisierter$|gewinn anteil|^\(angaben in)|\bassets$|^net$|^% of",
    re.I,
)
_NET_ASSETS = re.compile(
    r"(zuzuordnendes|zuzurechnendes|net assets attributable|attributable to holders|"
    r"^nettovermögen\b|^net assets\b|^total net assets|^fondsvermögen\b)",
    re.I,
)
_RESIDUAL = re.compile(r"^(sonstiges nettovermögen|sonstige vermögenswerte|other net assets|other assets|"
                       r"cash|bankguthaben|barmittel|net current assets)", re.I)
_FX_NET = re.compile(r"(nettogewinn|nettoverlust|net unrealised|net unrealized).*(devisentermin|forward)", re.I)
_FX_GAIN_LOSS = re.compile(r"(nicht realisierter (gewinn|verlust)|unrealised (gain|loss)|unrealized (gain|loss)).*(devisentermin|forward)", re.I)
_SECTION_PCT = re.compile(r"\s[-–]\s*\(?\d[\d.,]*\)?\s*%.*$")


def detect_decimal(lines: list[Line]) -> str:
    """',' when percentages print as 0,15 (German edition), else '.'."""
    comma = dot = 0
    for ln in lines:
        tok = ln.text.split()[-1:] or [""]
        if re.fullmatch(r"\(?\d{1,3},\d{2}\)?", tok[0]):
            comma += 1
        elif re.fullmatch(r"\(?\d{1,3}\.\d{2}\)?", tok[0]):
            dot += 1
    return "," if comma >= dot else "."


def number(tok: str, dec: str) -> float | None:
    t = tok.strip().replace("’", "'").replace("−", "-").replace("–", "-").rstrip("%")
    if t in _DASH or t == "":
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    if dec == ",":
        t = t.replace(".", "").replace("'", "").replace(",", ".")
    else:
        t = t.replace(",", "").replace("'", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _is_amount(tok: str) -> bool:
    return bool(_AMOUNT.match(tok)) or tok in _DASH


def split_line(text: str, dec: str) -> dict | None:
    """Split a regular position line into its parts, or None if it is not one."""
    toks = text.split()
    # A weight printed as an en or em dash means it rounds to nothing (L&G). An ASCII
    # "-" at the end of a line is a wrapped bond name ("3.20% 24 -"), not a weight.
    zero = toks[-1] in ("–", "—")
    if len(toks) < 3 or not (_PCT.match(toks[-1]) or zero) or not _AMOUNT.match(toks[-2]) \
            and not (toks[-2] in _DASH and _PCT.match(toks[-1])):
        return None
    pct = 0.0 if zero else number(toks[-1], dec)
    value = number(toks[-2], dec)
    rest = toks[:-2]
    out = {"pct": pct, "value": value, "qty": None, "currency": None, "name": "", "fx": False}
    # FX forward: settle-date CCY amount CCY amount
    if len(rest) >= 5 and _DATE.match(rest[0]) and _CCY.match(rest[1]) and _CCY.match(rest[3]):
        out.update(fx=True, name=f"FX forward {rest[1]}/{rest[3]} {rest[0]}")
        return out
    # name qty CCY cost (DWS Luxembourg)
    if len(rest) >= 3 and _is_amount(rest[-1]) and _CCY.match(rest[-2]) and _is_amount(rest[-3]):
        out.update(qty=number(rest[-3], dec), currency=rest[-2], name=" ".join(rest[:-3]))
        return out
    # qty name (Irish plc)
    if rest and _AMOUNT.match(rest[0]) and len(rest) >= 2:
        out.update(qty=number(rest[0], dec), name=" ".join(rest[1:]))
        return out
    # qty with a footnote mark glued to the name: "30,0001APA Infrastructure" (L&G)
    m = _QTY_FOOTNOTE.match(rest[0]) if rest else None
    if m:
        out.update(qty=number(m.group(1), dec), name=" ".join([m.group(2)] + rest[1:]))
        return out
    out["name"] = " ".join(rest)
    return out


def _asset_from_name(name: str, section: str | None) -> str:
    if section:
        return section
    if re.search(r"\d+(?:[.,]\d+)?\s?%|\b\d{2}[./]\d{2}[./]\d{2,4}\b|\bzero coupon\b|\bfrn\b", name, re.I):
        return "debt"
    return "other"


def parse_schedule(pages: dict[int, list[Line]], title: str = "") -> Schedule:
    """Parse one sub-fund's schedule pages into a holdings frame."""
    all_lines = [ln for p in sorted(pages) for ln in pages[p]]
    dec = detect_decimal(all_lines)
    rows: list[dict] = []
    total: float | None = None
    section: str | None = None
    group: str | None = None
    fx_net: float | None = None
    fx_parts: list[float] = []
    fx_seen = False
    invest_total: float | None = None
    notes: list[str] = []
    stop = False
    summary = False   # inside a "Total Investments" recap, where only the residual line is new
    for pno in sorted(pages):
        pending = ""
        for ln in pages[pno]:
            text = _SPLIT_DECIMAL.sub(r"\1\2", ln.text.strip())
            if not text:
                continue
            if ln.bold and _RECAP.match(text):
                summary = True
                continue
            if summary and not ln.bold and not _RESIDUAL.match(text):
                continue
            if ln.bold:
                pending = ""
                low = text.lower()
                if _FX_NET.search(text):
                    toks = text.split()
                    fx_net = number(toks[-1], dec) if _PCT.match(toks[-1]) else fx_net
                    continue
                if _FX_GAIN_LOSS.search(text):
                    toks = text.split()
                    if _PCT.match(toks[-1]):
                        fx_parts.append(number(toks[-1], dec) or 0.0)
                    continue
                if _NET_ASSETS.search(text) and not _RESIDUAL.match(text):
                    toks = text.split()
                    if toks and _PCT.match(toks[-1]):
                        total = number(toks[-1], dec)
                        stop = True
                        break
                    continue
                if _RESIDUAL.match(text):
                    parts = split_line(text, dec)
                    if parts:
                        rows.append(_row(re.sub(_SECTION_PCT, "", parts["name"]) or text, parts, "other", True, group, pno))
                    continue
                if _INVESTMENTS_TOTAL.match(text) and invest_total is None:
                    toks = text.split()
                    if _PCT.match(toks[-1]):
                        invest_total = number(toks[-1], dec)
                    continue
                if _SKIP_BOLD.match(low) or re.fullmatch(r"[\d\s.,()%—–-]+", text):
                    continue
                for rx, cls in ASSET_HEADINGS:
                    if rx.match(text):
                        section = cls
                        break
                else:
                    words = set(text.lower().split())
                    if not (SCHEDULE_HEADINGS.match(text) or re.search(r"\d", text) or _COLUMN_HEADER.search(text)
                            or words <= _HEADER_WORDS
                            or (title and compact(text).startswith(compact(title)[:8]))):
                        group = re.sub(r"\s*\((fortsetzung|continued|suite)\)\s*$", "", text, flags=re.I)
                continue
            # regular line
            if not re.search(r"[^\W\d_]", text):     # no letters: a sub-total
                pending = ""
                continue
            if _RESIDUAL.match(text):
                parts = split_line(text, dec)
                if parts:
                    name = re.sub(_SECTION_PCT, "", parts["name"]).strip() or parts["name"]
                    rows.append(_row(name, parts, "other", True, group, pno))
                    pending = ""
                    continue
            parts = split_line(text, dec)
            if parts is None:
                pending = (pending + " " + text).strip() if len(pending) < 200 else text
                continue
            if parts["fx"]:
                fx_seen = True
                pending = ""
                continue
            name = parts["name"]
            if pending:
                lead = pending.split()
                if parts["qty"] is None and lead and _AMOUNT.match(lead[0]):
                    parts["qty"] = number(lead[0], dec)
                    pending = " ".join(lead[1:])
                name = (pending + " " + name).strip()
            pending = ""
            if not name or parts["pct"] is None:
                continue
            rows.append(_row(name, parts, _asset_from_name(name, section), False, group, pno))
        if stop:
            break
    if fx_net is not None or fx_parts:
        w = fx_net if fx_net is not None else sum(fx_parts)
        rows.append({
            "isin": None, "cusip": None, "sedol": None, "ticker": None,
            "name": "Open forward foreign exchange contracts (net unrealised)",
            "weight_pct": w, "asset_class": "derivative (foreign exchange)", "currency": None,
            "country": None, "quantity": None, "market_value": None, "residual": False,
            "group": None, "page": None,
        })
    elif fx_seen:
        notes.append("forward FX lines found but no net total; they are left out")
    frame = pd.DataFrame(rows, columns=HOLDING_COLUMNS + EXTRA_COLUMNS)
    frame["weight_pct"] = pd.to_numeric(frame["weight_pct"], errors="coerce")
    return Schedule(title=title, holdings=frame, total_pct=total, pages=sorted(pages), notes=notes,
                    investments_pct=invest_total)


def _row(name: str, parts: dict, asset_class: str, residual: bool, group: str | None, page: int) -> dict:
    isin = next((m for m in _ISIN.findall(name) if is_valid_isin(m)), None)
    name = re.sub(r"(?<=[A-Za-z.)])\s+\d$", "", name.strip())   # footnote mark: "Blue Bird Corp 1"
    return {
        "isin": isin, "cusip": None, "sedol": None, "ticker": None,
        "name": re.sub(r"\s{2,}", " ", name).strip(),
        "weight_pct": parts["pct"], "asset_class": asset_class, "currency": parts.get("currency"),
        "country": None, "quantity": parts.get("qty"), "market_value": parts.get("value"),
        "residual": residual, "group": group, "page": page + 1,
    }


def stats(schedule: Schedule) -> dict:
    h = schedule.holdings
    pos = h[~h["residual"].astype(bool)] if len(h) else h
    return {
        "lines": int(len(h)),
        "weight_sum": round(float(h["weight_pct"].sum()), 4) if len(h) else 0.0,
        "positions_sum": round(float(pos["weight_pct"].sum()), 4) if len(pos) else 0.0,
        "isin_weight": round(float(h.loc[h["isin"].notna(), "weight_pct"].sum()), 4) if len(h) else 0.0,
        "report_total": schedule.total_pct,
        "investments_total": schedule.investments_pct,
    }


def acceptable(schedule: Schedule) -> bool:
    """A parse is used only if it found lines and they add up to something plausible."""
    s = stats(schedule)
    return s["lines"] > 0 and 50.0 <= s["weight_sum"] <= 150.0
