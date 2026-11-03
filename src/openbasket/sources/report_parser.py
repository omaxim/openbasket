"""Read the schedule of investments out of a fund's statutory report PDF.

UCITS funds publish an annual and a semi-annual report; Swiss funds publish
the same under the Collective Investment Schemes Act (KAG). Each report has a
schedule of investments per sub-fund ("Schedule of Investments",
"Statement of investments and other net assets", "Aufstellung des
Wertpapierbestandes", "Inventory of Fund assets", ...). This module turns the
text of such a report into one holdings frame per sub-fund.

It works on the text layer only (``pypdf``). Reports printed to PDF without
a text layer, or with fonts that have no Unicode map, are reported as
unreadable rather than guessed at.

The parser is line based. A holding line is::

    [quantity] name [currency] [quantity] [market value] percent

where ``percent`` is the last number with decimals on the line and is the
weight in percent of net assets (Swiss inventories print percent of total
fund assets; the caller labels that). Totals, sub-totals, sector headers
("Advertising (0.12%) (31 December 2024: 0.17%)") and the analysis of total
assets are skipped; cash and other net assets at the end of a schedule are
kept as residual lines, so a schedule sums to about 100.

Umbrella reports are split into sub-funds by the sub-fund titles printed on
the schedule pages (running headers or section titles), and the requested
sub-fund is matched by name.
"""

from __future__ import annotations

import io
import logging
import unicodedata
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ..identifiers import is_valid_isin

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Text extraction
# --------------------------------------------------------------------------


def extract_pages(content: bytes) -> list[str]:
    """Text of every page, in order, with small typesetting artefacts fixed."""
    import pypdf  # imported here so the module loads without the dependency

    reader = pypdf.PdfReader(io.BytesIO(content))
    pages = []
    for page in reader.pages:
        try:
            pages.append(clean_text(page.extract_text() or ""))
        except Exception as exc:  # a broken page must not lose the report
            logger.warning("Could not read a page: %s", exc)
            pages.append("")
    return pages


_PUA = re.compile(r"[-]")


_SPACED_RUN = re.compile(r"(?<!\S)(?:\S{1,2} ){3,}\S{1,2}(?!\S)")


def _join_spaced(m: re.Match) -> str:
    toks = m.group(0).split(" ")
    if sum(1 for t in toks if len(t) == 1) >= 0.6 * len(toks):
        return "".join(toks)
    return m.group(0)


def unspace(line: str) -> str:
    """'To t a l O i l & G a s' -> 'TotalOil&Gas' (letter-spaced text from some typesetters)."""
    return _SPACED_RUN.sub(_join_spaced, line)


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    # XPP/Word kerning splits: "T otal", "T echnologies", "%o f"
    text = re.sub(r"\bT (?=[a-z])", "T", text)
    text = text.replace("%o f", "% of")
    text = re.sub(r"[ \t]*\.{4,}[ \t]*", " ", text)     # dot leaders
    return text


def text_quality(pages: list[str]) -> float:
    """Share of non-space characters that are readable (not private-use glyphs)."""
    chars = "".join(pages)
    visible = [c for c in chars if not c.isspace()]
    if not visible:
        return 0.0
    bad = sum(1 for c in visible if _PUA.match(c))
    return 1 - bad / len(visible)


# --------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NumberStyle:
    decimal: str = "."
    thousands: str = ","   # ",", ".", " " (space) or "'"


_END_DOT = re.compile(r"\d\.\d{2,4}%?\)?\s*$")
_END_COMMA = re.compile(r"\d,\d{2,4}\s?%?\)?\s*$")
_SPACE_GROUPS = re.compile(r"(?<![\d.,])\d{1,3}(?: \d{3})+[.,]\d{2}\b")
_COMMA_GROUPS = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?\b")
_DOT_GROUPS = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?\b")


def detect_style(lines: list[str]) -> NumberStyle:
    dot = sum(1 for l in lines if _END_DOT.search(l))
    comma = sum(1 for l in lines if _END_COMMA.search(l))
    decimal = "," if comma > dot else "."
    spaces = sum(len(_SPACE_GROUPS.findall(l)) for l in lines)
    if decimal == ".":
        commas = sum(len(_COMMA_GROUPS.findall(l)) for l in lines)
        thousands = " " if spaces > commas else ","
    else:
        dots = sum(len(_DOT_GROUPS.findall(l)) for l in lines)
        thousands = " " if spaces > dots else "."
    return NumberStyle(decimal, thousands)


_NUM_TOKEN = re.compile(r"^\(?[-−–]?\d[\d.,'’]*%?\)?%?$")
_DASH = {"-", "—", "–", "−", "--"}


def is_num(tok: str) -> bool:
    return bool(_NUM_TOKEN.match(tok)) or tok in _DASH


def parse_num(tok: str, style: NumberStyle) -> float | None:
    if tok in _DASH:
        return None
    t = tok.strip()
    neg = t.startswith("(") and t.rstrip("%").endswith(")") or t[:1] in "-−–"
    t = t.strip("()%").lstrip("-−–").rstrip(")")
    t = t.replace("'", "").replace("’", "").replace(" ", "")
    if style.decimal == ",":
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def has_decimals(tok: str, style: NumberStyle) -> bool:
    core = tok.strip("()%").rstrip(")")
    return bool(re.search(r"\%s\d{1,4}$" % style.decimal, core))


def merge_space_groups(tokens: list[str]) -> list[str]:
    """Join numbers whose thousands are separated by spaces: '11 895 555' -> '11895555'."""
    out: list[str] = []
    i = len(tokens) - 1
    while i >= 0:
        cur = tokens[i]
        if re.match(r"^\d{3}(?:[.,]\d+)?%?\)?$", cur):
            merged = cur
            j = i - 1
            while j >= 0 and re.match(r"^\(?-?\d{1,3}$", tokens[j]):
                merged = tokens[j] + merged
                head = tokens[j].lstrip("(-")
                j -= 1
                if len(head) < 3:
                    break
            if j < i - 1:
                out.append(merged)
                i = j
                continue
        out.append(cur)
        i -= 1
    return out[::-1]


# --------------------------------------------------------------------------
# Line classification
# --------------------------------------------------------------------------

_SECTION_HEADER = re.compile(r"\([^()]*\b(?:19|20)\d{2}\s*:")      # "(31 December 2024: 0.17%)"
_SKIP = re.compile(
    r"^\(?(?:total|sub-?total|summe|gesamt|thereof|davon|dont |"
    r"net financial assets|financial assets at fair value|financial liabilities at fair value|"
    r"fair value (?:of )?(?:outperformance |total return |portfolio |equity |interest rate )?(?:swaps?|forwards?|futures?|options?|contracts?)|"
    r"net (?:fair value|gain|loss)|"
    r"finanzielle (?:vermögenswerte|verbindlichkeiten)|"
    r"net unreali[sz]ed|unreali[sz]ed (?:gain|loss|\(loss\)|appreciation|depreciation)|"
    r"nicht realisierte|plus-value|moins-value|"
    r"investments? (?:at|in) |portfolio of investments|investment portfolio|"
    r"analysis of|analyse des|transferable securities|wertpapiere, die|"
    r"\([a-z]\) |market value of|cost of|acquisition cost|"
    r"net asset value|nav per|valeur nette|"
    r"other transferable|officially listed|admitted to an official)",
    re.I,
)
_SKIP_ANY = re.compile(r"\binsgesamt\b|\bin total\b|\bsub-?total\b", re.I)
_STOP = re.compile(
    r"^(?:total net assets|net assets attributable|net assets as at|nettovermögen insgesamt|"
    r"total net fund assets|net fund assets|total fund assets|gesamtfondsvermögen|"
    r"nettofondsvermögen|total de l'actif net|actif net total|net assets$|"
    r"total net asset value|net assets?$|net asset value$|total nettovermögen|nettovermögen$|total de l.actif net)",
    re.I,
)
_RESIDUAL = re.compile(
    r"^(?:cash\b|cash and|cash at|bank (?:overdrafts?|deposits?|balances?|accounts?|borrowings?)|"
    r"deposits? (?:at|with) banks?|time deposits|sight deposits|"
    r"other (?:net )?(?:assets|liabilities)|other assets and liabilities|"
    r"net other (?:assets|liabilities)|net current (?:assets|liabilities)|"
    r"sonstige(?:s)? (?:netto)?(?:vermögen|verbindlichkeiten|aktiva|passiva)|bankguthaben|bankverbindlichkeiten|"
    r"liquide mittel|flüssige mittel|avoirs bancaires|autres actifs|autres passifs|liquidités|"
    r"margin (?:cash|accounts?)|cash collateral|kontokorrent|due (?:to|from) brokers?|andere (?:aktiva|passiva|vermögenswerte|verbindlichkeiten)|übrige (?:aktiva|passiva))",
    re.I,
)
_CASHLIKE = re.compile(r"^(?:cash|bank|deposit|time dep|sight dep|bankguthaben|bankverb|liquide|flüssige|avoirs|liquidités|margin)", re.I)

_SECTION_KEYWORDS = [
    ("derivative", re.compile(r"derivat|future|forward|swap|option|devisentermin|termingesch|contracts? for difference|warrant", re.I)),
    ("fund", re.compile(r"investment funds|collective investment|exchange traded funds|undertakings for collective|investmentanteile|fondsanteile|anteile an investment|organismes de placement|money market funds|units of investment|investment companies \(open|open-ended funds", re.I)),
    ("debt", re.compile(r"\bbonds?\b|anleihen|obligation|fixed income|fixed rate|floating rate|government|treasur|debt|notes\b|money market instruments|commercial paper|certificates of deposit|verzinsliche|renten|titres de créance", re.I)),
    ("equity", re.compile(r"equit|shares|stocks?\b|aktien|actions|preferred|real estate investment trusts|reits?\b|depositary receipts", re.I)),
    ("other", re.compile(r"precious metal|edelmetall|commodit|rohstoff", re.I)),
]

_COUPON = re.compile(r"\d+(?:[.,]\d+)?\s?%")
_MATURITY = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{2}/\d{2}\b|\b(?:19|20)\d{2}\b")

CURRENCIES = set(
    "AED ARS AUD BRL CAD CHF CLP CNH CNY COP CZK DKK EGP EUR GBP HKD HUF IDR ILS INR ISK JPY KRW KWD "
    "MXN MYR NOK NZD PEN PHP PKR PLN QAR RON RUB SAR SEK SGD THB TRY TWD USD VND ZAR".split()
)

_ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b")


@dataclass
class ParsedLine:
    name: str
    weight_pct: float
    market_value: float | None
    quantity: float | None
    currency: str | None
    isin: str | None
    residual: bool
    page: int
    lead_qty: bool = False


_PCT_GROUP = re.compile(r"\((?:[^()]|\([^()]*\))*%\s*\)?\)")


def strip_headers(line: str) -> str:
    """Drop bracketed weight comparisons: 'Other Net Assets (0.42%) (2025: 0.37%) 6,834,221 0.42'."""
    if "%" not in line or "(" not in line or not _SECTION_HEADER.search(line):
        return line
    return " ".join(_PCT_GROUP.sub(" ", line).split())


_NUMERIC_CHUNK = re.compile(r"^[\d\s.,'’()%-]+$")


def _mend_column(chunk: str) -> str:
    """Some typesetters put stray spaces inside numbers in wide columns: '1 ,571 ,377', '2. 18', '1,17 8'."""
    if not _NUMERIC_CHUNK.match(chunk) or not re.search(r"\d", chunk):
        return chunk
    c = re.sub(r"(\d) +([.,]) +(?=\d)", r"\1\2", chunk.strip())
    c = re.sub(r"(\d) +(?=[.,]\d)", r"\1", c)
    c = re.sub(r"(\d[.,]) +(?=\d)", r"\1", c)
    c = re.sub(r"(\d[.,]\d{1,2}) (\d)\b", r"\1\2", c)
    return c


def _tokens(line: str, style: NumberStyle) -> list[str]:
    if re.search(r"\S {3,}\S", line):                   # wide column gaps
        line = "  ".join(_mend_column(c) for c in re.split(r" {3,}", line.strip()))
    line = re.sub(r"(\d)\s+%", r"\1%", line)          # "0,05 %" -> "0,05%"
    line = re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", line))
    if style.thousands != " ":
        return line.split()
    # a run of two or more spaces is a column gap: never join digit groups across it
    toks: list[str] = []
    for chunk in re.split(r" {2,}|\t", line.strip()):
        toks.extend(merge_space_groups(chunk.split()))
    return toks


def parse_holding_line(line: str, style: NumberStyle, page: int = 0, leading_qty: bool = False) -> ParsedLine | None:
    """One holding from one line, or None if the line is not a holding."""
    raw = strip_headers(unspace(line.strip()))
    if len(raw) < 4:
        return None
    toks = _tokens(raw, style)
    # the weight is the last number with decimals; at most one bare integer may follow it
    # (Swiss inventories print a securities-lending quantity after the percent)
    k = len(toks) - 1
    trailing_ints = 0
    while k >= 0 and is_num(toks[k]) and not has_decimals(toks[k], style) and trailing_ints < 1:
        k -= 1
        trailing_ints += 1
    if k < 0 or not is_num(toks[k]) or not has_decimals(toks[k], style):
        return None
    if trailing_ints and not re.match(r"^\d+$", toks[-1]):
        return None
    pct = parse_num(toks[k], style)
    if pct is None or abs(pct) > 150:
        return None
    j = k - 1
    nums: list[str] = []
    while j >= 0 and is_num(toks[j]):
        nums.insert(0, toks[j])
        j -= 1
    if not nums:          # a weight with no amount: a classification line, not a holding
        return None
    name_toks = toks[: j + 1]
    qty = None
    lead = False
    if leading_qty and name_toks and is_num(name_toks[0]) and "%" not in name_toks[0] and len(name_toks) > 1:
        qty = parse_num(name_toks[0], style)
        name_toks = name_toks[1:]
        lead = True
    currency = None
    if len(name_toks) > 2 and name_toks[-2] + name_toks[-1] in CURRENCIES and len(name_toks[-1]) == 1:
        name_toks = name_toks[:-2] + [name_toks[-2] + name_toks[-1]]     # "JP Y"
    if name_toks and name_toks[-1] in CURRENCIES and len(name_toks) > 1:
        currency = name_toks[-1]
        name_toks = name_toks[:-1]
    elif len(name_toks) > 1 and name_toks[0] in CURRENCIES and name_toks[1][:1].isalpha():   # "USD ARBUTUS BIOPHARMA NPV ..."
        currency = name_toks[0]
        name_toks = name_toks[1:]
    name = " ".join(name_toks).strip(" .:*-–")
    if sum(c.isalpha() for c in name) < 2:
        return None
    value = parse_num(nums[-1], style)
    if qty is None and (len(nums) == 2 or (len(nums) > 2 and style.thousands != " ")):
        qty = parse_num(nums[-2], style)
    isin = None
    for cand in _ISIN_RE.findall(name):
        if is_valid_isin(cand):
            isin = cand
            name = " ".join(name.replace(cand, " ").split()).strip(" -–")
            break
    if _SKIP.match(name) or _SKIP_ANY.search(name):
        return None
    residual = bool(_RESIDUAL.match(name)) and qty is None
    return ParsedLine(name, pct, value, qty, currency, isin, residual, page, lead)


_NOT_SECTION = re.compile(
    r"accompanying|integral part|report|bericht|rapport|statement|schedule|expressed|as at|as of|"
    r"page|seite|counterpart|gegenpartei|percentages|prozents|unreali[sz]ed|nicht realisiert|ucits|"
    r"\bnote\b|https?:|kursgewinn|\(-verlust\)|erl\. ?\d|bezeichnung|nominal|bewertung|fair value|"
    r"market value|quantity|anzahl",
    re.I,
)


def section_of(line: str) -> str | None:
    s = line.strip()
    if not s or len(s) > 100 or _NOT_SECTION.search(s):
        return None
    for cls, rx in _SECTION_KEYWORDS:
        if rx.search(s):
            return cls
    return None


def classify(pl: ParsedLine, section: str | None) -> str:
    if pl.residual:
        return "cash" if _CASHLIKE.match(pl.name) else "other"
    if section == "derivative" or re.search(r"derivative|forward|future|swap|option", pl.name, re.I) and pl.quantity is None:
        return "derivative"
    if section in ("fund", "other"):
        return section
    if section == "debt" or (_COUPON.search(pl.name) and _MATURITY.search(pl.name)):
        return "debt"
    return section or "equity"


# --------------------------------------------------------------------------
# Report structure
# --------------------------------------------------------------------------

SCHEDULE_RX = re.compile(
    r"schedules? of investments|statements? of investments|portfolio of investments|portfolio statement|"
    r"inventory of (?:the )?fund(?:'s)? assets|inventaire du portefeuille|inventaire des actifs|"
    r"aufstellung(?:en)? de[sr] (?:wertpapierbest[aä]nd|vermögens|anlagen)|vermögensaufstellung|"
    r"vermögensinventar|wertpapierbestand (?:zum|per|am)|portefeuille[- ]titres|état du portefeuille|"
    r"statement of investments and other net assets|investment portfolio as|anlagenportfolio|anlageportfolio|"
    r"portefeuille d'investissements|état des investissements|wertpapierverzeichnis",
    re.I,
)
_MARKER_LINE = re.compile(r"^\W*\d{0,4}\s*(?:%s)" % SCHEDULE_RX.pattern, re.I)
_TOC_RX = re.compile(r"table of contents|^contents|inhaltsverzeichnis|^inhalt\b|table des matières|sommaire", re.I | re.M)

_DROP_IN_TITLE = re.compile(
    r"\((?:continued|cont'?d|fortsetzung|suite|forts\.)\)|\bcontinued\b|\(note \d+\)|\(anmerkung \d+\)",
    re.I,
)
_TITLE_TAIL = re.compile(
    r"\s*[–-]?\s*(?:unaudited |audited )?(?:semi[- ]?annual|annual|interim|halbjahres|jahres|zwischen)"
    r"[- ]?(?:report|bericht|financial).*$|\s*(?:rapport (?:semi-)?annuel).*$",
    re.I,
)
_NOT_TITLE = re.compile(
    r"report|bericht|rapport|statement|schedule|notes? |accompanying|integral part|financial|"
    r"page|seite|expressed|in % |percent|description|quantity|nominal|market value|fair value|"
    r"holdings|% of|% des|net assets|investments|inventory|aufstellung|vermögens|"
    r"classification|counterpart|gegenpartei|https?:|www\.",
    re.I,
)


def normalise_title(line: str, brands: list[str]) -> str | None:
    if len(line) > 400:
        return None
    s = " ".join(line.split())
    low = s.lower()
    if not any(b.lower() in low for b in brands):
        return None
    s = re.sub(r"^\d{1,4}(?=\s*[A-Za-z])", "", s).strip()          # page number glued on
    s = re.sub(r"^sub-?fund\s*:?\s*", "", s, flags=re.I)
    s = _DROP_IN_TITLE.sub("", s)
    s = _TITLE_TAIL.sub("", s)
    s = re.sub(r"\s+[–-]\s+\d{1,2}[./]\d{1,2}[./]\d{2,4}.*$", "", s)      # "LO Funds - X - 31/03/2026 45"
    s = re.sub(r"\s+\d{1,4}$", "", s).strip(" –-:*")
    if len(re.findall(r"(?<![A-Za-z])\d[\d.,']*(?![A-Za-z])", s)) >= 2:          # holding line with amounts
        return None
    s = " ".join(s.split())
    if not s or len(s) < 5 or len(s) > 130:
        return None
    low = s.lower()
    hit = next((b for b in brands if low.startswith(b.lower())), None)
    if hit is None:
        return None
    # a repeated logo word at the end: "PICTET - EUR BONDS PICTET"
    words = s.split()
    if len(words) > 2 and words[-1].lower() == hit.lower().split()[0]:
        s = " ".join(words[:-1])
    if _NOT_TITLE.search(s) or re.search(r"\d[.,]\d{2}\b", s):
        return None
    return s


def compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("&", "and"))


_GENERIC = {"sicav", "sicaf", "plc", "icav", "fcp", "sa", "s", "a", "the", "of", "subfund", "sub", "fund", "ucits", "etf"}


def title_score(title: str, fund_name: str) -> tuple[float, int]:
    """How well a sub-fund title matches a share-class name: (share of title words found in order, chars).

    A title with an umbrella prefix ("UBS ETF (CH) – UBS SMI ETF") is also tried without it.
    """
    best = _title_score(title, fund_name)
    parts = re.split(r"\s+[–-]\s+", title, maxsplit=1)
    if len(parts) == 2 and len(parts[1]) >= 4:
        tail = _title_score(parts[1], fund_name)
        if tail[0] >= 0.999 and best[0] < 0.999:
            best = tail
    return best


def _title_score(title: str, fund_name: str) -> tuple[float, int]:
    words = [w for w in re.split(r"[^a-z0-9&]+", title.lower().replace("&", "and")) if w]
    target = compact(fund_name)
    pos, found, chars, distinct = 0, 0, 0, 0
    for w in words:
        w = compact(w)
        if not w:
            continue
        generic = w in _GENERIC or w.upper() in CURRENCIES
        i = target.find(w, pos)
        if i >= 0:
            pos = i + len(w)
            chars += len(w)
            found += 0 if generic else 1
        elif not generic:
            return (0.0, 0)
        distinct += 0 if generic else 1
    return (found / distinct, chars) if distinct else (0.0, 0)


@dataclass
class Segment:
    title: str
    pages: list[int] = field(default_factory=list)
    lines: list[tuple[int, str]] = field(default_factory=list)   # (page, line)


def schedule_pages(pages: list[str]) -> list[int]:
    """Pages that carry a schedule of investments (header/footer marker), plus their continuations."""
    out = []
    n = len(pages)
    for i, p in enumerate(pages):
        lines = [l for l in p.split("\n") if l.strip()]
        if not lines:
            continue
        head = "\n".join(lines[:14] + lines[-8:])
        if _TOC_RX.search("\n".join(lines[:6])) or (i < max(12, n // 20) and _looks_like_toc(lines)):
            continue
        if any(_MARKER_LINE.match(l) for l in head.split("\n")):
            out.append(i)
    # add continuation pages that have no marker but are full of holding lines
    have = set(out)
    for i in list(out):
        j = i + 1
        while j < n and j not in have and (
            _holding_density(pages[j]) >= 0.15 or any(_is_stop_line(l) for l in pages[j].split("\n"))
        ):
            have.add(j)
            j += 1
    return sorted(have)


def _looks_like_toc(lines: list[str]) -> bool:
    ends = sum(1 for l in lines if re.search(r"[A-Za-z)][ ._]{1,}\d{1,4}\s*$", l))
    return ends >= max(6, len(lines) // 2)


def _holding_density(text: str) -> float:
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) < 8:
        return 0.0
    style = detect_style(lines)
    hits = sum(1 for l in lines if parse_holding_line(l, style) is not None)
    return hits / len(lines)


def find_titles(pages: list[str], sched: list[int], brands: list[str]) -> dict[int, list[tuple[int, str]]]:
    """Sub-fund titles per schedule page: {page: [(line_no, title)]}."""
    per_page: dict[int, list[tuple[int, str]]] = {}
    counts: Counter = Counter()
    for i in sched:
        seen = []
        for ln, line in enumerate(pages[i].split("\n")):
            t = normalise_title(line, brands)
            if t:
                seen.append((ln, t))
        per_page[i] = seen
        counts.update({compact(t) for _, t in seen})
    # running umbrella headers ("Invesco Markets plc", "WisdomTree Issuer ICAV") are on almost
    # every page; they are not sub-fund titles when other titles exist
    if len(counts) > 1:
        legal = {k for k in counts if re.search(r"(plc|icav|sicav|sicaf|fundsolutions|sa)$", k)}
        if len(legal) < len(counts):
            per_page = {i: [(ln, t) for ln, t in v if compact(t) not in legal] for i, v in per_page.items()}
            counts = Counter({k: c for k, c in counts.items() if k not in legal})
    if len(counts) > 1 and len(sched) >= 5:
        umbrella = {k for k, c in counts.items() if c >= 0.6 * len(sched)}
        if len(umbrella) < len(counts):
            per_page = {i: [(ln, t) for ln, t in v if compact(t) not in umbrella] for i, v in per_page.items()}
    return per_page


def segments(pages: list[str], brands: list[str]) -> list[Segment]:
    """Split the schedule pages of a report into one segment per sub-fund title."""
    sched = schedule_pages(pages)
    titles = find_titles(pages, sched, brands)
    segs: list[Segment] = []
    cur: Segment | None = None
    prev_page = None
    for i in sched:
        lines = pages[i].split("\n")
        tlist = titles.get(i, [])
        if prev_page is not None and i != prev_page + 1 and not tlist:
            cur = None  # a gap without a new title: unknown fund, skip
        prev_page = i
        if not tlist:
            if cur is not None:
                cur.pages.append(i)
                cur.lines.extend((i, l) for l in lines)
            continue
        # the first title on a page applies to the whole page (titles may be extracted
        # after the body), unless the previous fund visibly ends earlier on the page
        first_ln, first_t = tlist[0]
        stop_ln = next((ln for ln, l in enumerate(lines[:first_ln]) if _is_stop_line(l)), None)
        if cur is not None and stop_ln is not None and compact(first_t) != compact(cur.title):
            cur.lines.extend((i, l) for l in lines[: stop_ln + 1])
            start = stop_ln + 1
        else:
            start = 0
        bounds = [(ln, t) for ln, t in tlist]
        for n_t, (ln, t) in enumerate(bounds):
            seg_start = start if n_t == 0 else ln
            seg_end = bounds[n_t + 1][0] if n_t + 1 < len(bounds) else len(lines)
            if cur is None or compact(t) != compact(cur.title):
                cur = Segment(t)
                segs.append(cur)
            if i not in cur.pages:
                cur.pages.append(i)
            cur.lines.extend((i, l) for l in lines[seg_start:seg_end])
    return segs


def _is_stop_line(line: str, style: NumberStyle | None = None) -> bool:
    """'TOTAL NET ASSETS 4,544,407,736 100.00': the end of a sub-fund's schedule (needs an amount)."""
    style = style or NumberStyle()
    toks = _tokens(strip_headers(line.strip()), style)
    if not toks or not is_num(toks[-1]) or toks[-1] in _DASH:
        return False
    return bool(_STOP.match(_tokens_name(line.strip(), style)))


def parse_segment(seg: Segment) -> tuple[pd.DataFrame, dict]:
    """Holdings of one sub-fund segment, plus parse statistics."""
    text_lines = [l for _, l in seg.lines]
    style = detect_style(text_lines)
    # leading-quantity layout ("   498 Wendel SE 40,961 0.02") if most holding lines start with a number
    probe = [parse_holding_line(l, style) for l in text_lines]
    probe = [
        p for p in probe
        if p is not None and not p.residual and not p.name[:1].islower() and not _STOP.match(p.name)
        and not re.match(r"^\d{1,2}[./]\d{1,2}[./]\d{2,4}\b", p.name)
        and not re.search(r"\b[A-Z]{3} \(?-?\d[\d,.' ]*\)?(?:\s|$)", p.name)     # FX forwards: "USD (7,992,763)"
    ]
    lead = sum(1 for p in probe if re.match(r"^\(?\d[\d.,' ]*\)?\s+\S", p.name) and not re.match(r"^\d+(?:[.,]\d+)?\s?%", p.name))
    leading_qty = bool(probe) and lead > 0.5 * len(probe)

    rows = []
    section = None
    pending: str | None = None
    stopped = False
    for page, line in seg.lines:
        s = line.strip()
        if not s:
            continue
        if _is_stop_line(s, style):
            stopped = True
            break
        pl = parse_holding_line(s, style, page, leading_qty)
        if pl is None:
            bare = strip_headers(s)
            if not re.search(r"\d[.,]\d{2}", bare):
                sec = section_of(bare)
                if sec:
                    section = sec
            # keep a text-only line: the next line may continue it ("... exchange" / "contracts 1,234 0.01")
            pending = s if re.search(r"[A-Za-z]", s) and not re.search(r"\d[.,]\d{2}\b", s) and len(s) < 140 else None
            continue
        if pending and (pl.name[:1].islower() or pending.rstrip().endswith(("-", "&", ",", "/", "(", " and", " of"))):
            pl.name = f"{pending.strip()} {pl.name}"
            if _SKIP.match(pl.name) or _SKIP_ANY.search(pl.name):
                pending = None
                continue
            if _STOP.match(pl.name):
                stopped = True
                break
        pending = None
        if leading_qty and not pl.lead_qty and not pl.residual and not re.match(r"^\d{1,2}[./]\d{1,2}[./]\d{2,4}\b", pl.name):
            sec = section_of(pl.name)
            if sec:
                section = sec
            continue      # in a quantity-first layout a line without a quantity is a sub-total
        rows.append(
            {
                "isin": pl.isin,
                "cusip": None,
                "sedol": None,
                "ticker": None,
                "name": pl.name,
                "weight_pct": pl.weight_pct,
                "asset_class": classify(pl, section),
                "currency": pl.currency,
                "country": None,
                "quantity": pl.quantity,
                "market_value": pl.market_value,
                "residual": pl.residual,
                "page": pl.page + 1,
            }
        )
    df = pd.DataFrame(rows, columns=HOLDING_FRAME_COLUMNS)
    stats = {
        "lines": int(len(df)),
        "weight_sum_pct": round(float(df["weight_pct"].sum()), 4) if len(df) else 0.0,
        "invested_weight_pct": round(float(df.loc[~df["residual"], "weight_pct"].sum()), 4) if len(df) else 0.0,
        "isin_share": round(float(df["isin"].notna().mean()), 4) if len(df) else 0.0,
        "found_total_line": stopped,
        "pages": [p + 1 for p in seg.pages],
        "number_style": f"decimal '{style.decimal}', thousands '{style.thousands}'",
    }
    return df, stats


def _tokens_name(s: str, style: NumberStyle) -> str:
    """The text part of a line (numbers stripped), for stop-marker matching."""
    toks = _tokens(strip_headers(s), style)
    while toks and is_num(toks[-1]):
        toks.pop()
    return " ".join(toks)


HOLDING_FRAME_COLUMNS = [
    "isin", "cusip", "sedol", "ticker", "name", "weight_pct", "asset_class", "currency", "country",
    "quantity", "market_value", "residual", "page",
]

# --------------------------------------------------------------------------
# Dates and kind
# --------------------------------------------------------------------------

MONTHS = {
    # English
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
    # German
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "mai": 5, "juni": 6, "juli": 7,
    "oktober": 10, "dezember": 12,
    # French
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "juin": 6, "juillet": 7,
    "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}

_D_DMY = re.compile(r"\b(?:as (?:at|of)|at|zum|per|am|au|des?)\s+(\d{1,2})(?:st|nd|rd|th|er)?\.?\s+([A-Za-zäéûÄ]+)\.?,?\s+((?:19|20)\d{2})\b", re.I)
_D_MDY = re.compile(r"\b(?:as (?:at|of)|at|ended)\s+([A-Za-z]+)\s+(\d{1,2}),?\s+((?:19|20)\d{2})\b", re.I)
_D_NUM = re.compile(r"\b(?:as (?:at|of)|at|zum|per|am|au)\s+(\d{1,2})[./](\d{1,2})[./]((?:19|20)\d{2})\b", re.I)
_D_ENDED = re.compile(r"\b(?:ended|ending|endend|zum|to|bis)\s+(\d{1,2})\.?\s+([A-Za-zäÄ]+)\s+((?:19|20)\d{2})\b", re.I)


def find_dates(text: str) -> list[str]:
    out = []
    for rx, order in ((_D_DMY, "dmy"), (_D_MDY, "mdy"), (_D_NUM, "num"), (_D_ENDED, "dmy")):
        for m in rx.finditer(text):
            try:
                if order == "dmy":
                    d, mon, y = int(m.group(1)), MONTHS.get(m.group(2).lower().rstrip(".")), int(m.group(3))
                elif order == "mdy":
                    mon, d, y = MONTHS.get(m.group(1).lower()), int(m.group(2)), int(m.group(3))
                else:
                    d, mon, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if mon:
                    out.append(date(y, mon, d).isoformat())
            except (TypeError, ValueError):
                continue
    return out


def position_date(pages: list[str], seg_pages: list[int]) -> str | None:
    """The date the schedule is drawn up at, read from the schedule pages themselves."""
    c: Counter = Counter()
    for i in seg_pages[:6]:
        lines = pages[i].split("\n")
        c.update(find_dates("\n".join(lines[:16] + lines[-8:])))
    if c:
        return c.most_common(1)[0][0]
    return report_date(pages)


def report_date(pages: list[str]) -> str | None:
    c: Counter = Counter()
    for p in pages[:4]:
        c.update(find_dates(p))
    return c.most_common(1)[0][0] if c else None


_SEMI = re.compile(r"semi[- ]?annual|interim|half[- ]year|halbjahres|zwischenbericht|semestriel|semi-annuel|ungeprüft", re.I)


def report_kind(pages: list[str]) -> str:
    head = "\n".join(pages[:3])
    return "semi-annual report" if _SEMI.search(head) else "annual report"


# --------------------------------------------------------------------------
# One call
# --------------------------------------------------------------------------


@dataclass
class ScheduleResult:
    title: str | None
    holdings: pd.DataFrame
    as_of: str | None
    kind: str
    stats: dict
    candidates: list[str]
    match_score: float


def subfund_schedule(pages: list[str], fund_name: str, brands: list[str] | None = None, isin: str | None = None) -> ScheduleResult:
    """Find and parse the schedule of investments of the sub-fund that ``fund_name`` belongs to."""
    if not brands:
        brands = [fund_name.split()[0]]
    segs = segments(pages, brands)
    kind = report_kind(pages)
    titles = []
    for s in segs:
        if s.title not in titles:
            titles.append(s.title)
    if not segs:
        return ScheduleResult(None, pd.DataFrame(columns=HOLDING_FRAME_COLUMNS), None, kind, {"lines": 0}, [], 0.0)
    scored = sorted(((title_score(t, fund_name), t) for t in titles), key=lambda x: (x[0][0] >= 0.999, x[0][1], x[0][0]), reverse=True)
    (frac, _chars), best = scored[0]
    if frac == 0 and isin:
        best = _title_by_isin(pages, segs, isin) or best
        frac = 0.5 if best else 0.0
    if frac == 0:
        return ScheduleResult(None, pd.DataFrame(columns=HOLDING_FRAME_COLUMNS), report_date(pages), kind, {"lines": 0}, titles, 0.0)
    chosen = [s for s in segs if compact(s.title) == compact(best)]
    # the same title can head other sections (statistics, portfolio breakdowns); keep the
    # contiguous run of pages that reads most like a full schedule
    runs: list[Segment] = []
    for s in chosen:
        for p in sorted(set(s.pages) | {p for p, _ in s.lines}):
            if not runs or p > runs[-1].pages[-1] + 2:
                runs.append(Segment(best))
            if p not in runs[-1].pages:
                runs[-1].pages.append(p)
        for p, l in s.lines:
            run = next(r for r in runs if p in r.pages)
            run.lines.append((p, l))
    parsed = [(r, *parse_segment(r)) for r in runs]

    def quality(item):
        _, df_, st = item
        total = st["weight_sum_pct"]
        return (st["lines"] > 0, st["found_total_line"] and 90 <= total <= 110, -abs(100 - total) if st["lines"] else -1e9, st["lines"])

    run, df, stats = max(parsed, key=quality)
    as_of = position_date(pages, run.pages)
    return ScheduleResult(best, df, as_of, kind, stats, titles, frac)


def _title_by_isin(pages: list[str], segs: list[Segment], isin: str) -> str | None:
    """If the share-class ISIN is printed near a sub-fund title (share-class tables), use that title."""
    titles = {compact(s.title): s.title for s in segs}
    for p in pages:
        if isin not in p:
            continue
        before = p[: p.index(isin)]
        best = None
        for key, t in titles.items():
            pos = compact(before).rfind(key)
            if pos >= 0 and (best is None or pos > best[0]):
                best = (pos, t)
        if best:
            return best[1]
    return None
