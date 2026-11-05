"""Japan: listed investment trusts' holdings from their EDINET securities reports.

Every Japanese investment trust files an annual securities report
(有価証券報告書, EDINET document type ``120``) whose financial statements end
with a schedule of every security held at the period end (附属明細表, 有価証券明細表).
A fund that invests through a mother fund (マザーファンド) adds the mother
fund's own balance sheet and schedule as reference (参考) information. This
module reads both from the inline XBRL of each listed fund's latest report
(``type=1``) through :mod:`openbasket.sources.edinet_japan`'s API client.

Which funds
    EDINET's fund code list (``Fundcode.zip``) gives every fund's code and,
    for a listed fund, its Tokyo Stock Exchange code (証券コード). Only listed
    investment trusts (内国投資信託受益証券 with a securities code: ETFs) are read.
    A fund ISIN is tied to its report by that code: openbasket asks OpenFIGI
    for the ISIN's Tokyo listing, or takes the ticker from the fund's list.

Weights
    A line's yen value divided by the fund's net assets (純資産合計) from the
    same report's balance sheet, never rescaled. A foreign-currency group is
    converted with the yen subtotal the schedule prints under it, or, without
    one, its stated share of net assets (組入時価比率). Lines held through a
    mother fund are the mother fund's lines times the fund's share in it.

ISINs
    The schedule names holdings without codes. A Japanese company name is
    matched exactly (after NFKC folding and dropping 株式会社) to EDINET's
    company code list (``Edinetcode.zip``), whose securities code gives the
    ISIN through FinanceDatabase's Tokyo listings or the OpenFIGI cache. Names
    in English (US stocks, Treasuries, foreign ETFs) are left to openbasket's
    name matcher.

Terms
    EDINET's terms of use (利用規約, revised 2025-04-25) let anyone reuse its
    content under the Public Data License 1.0 (PDL1.0), crediting EDINET and
    saying who edited it. Scraping the site is barred; this module uses the
    API and the two code-list files EDINET publishes for download.
"""

from __future__ import annotations

import io
import json
import logging
import re
import unicodedata
import zipfile
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

from .. import http
from . import edinet_japan as api

logger = logging.getLogger(__name__)

REDISTRIBUTION = api.REDISTRIBUTION
SOURCE = "edinet_japan"
CODELIST_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/{name}.zip"
#: Where a reader can open a filed document.
VIEWER_URL = "https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?{doc_id}"
CREDIT = ("出典：EDINET (Financial Services Agency, Japan), the fund's securities report (有価証券報告書), "
          "PDL1.0; holdings extracted and matched to ISINs by openbasket")
LICENCE = ("EDINET terms of use: Public Data License 1.0 (PDL1.0), "
           "https://www.digital.go.jp/resources/open_data/public_data_license_v1.0")
#: Annual securities reports; semi-annual reports carry no securities schedule.
REPORT_TYPES = ("120",)
#: Fund types read: domestic investment trust units (ETFs when listed).
FUND_TYPES = ("内国投資信託受益証券",)
STORE_VERSION = 1
#: Reports parsed by an older parser are read again.
PARSER_VERSION = 2
LINE_COLUMNS = ["name", "code", "kind", "currency", "value_jpy", "weight_pct", "asset_class", "isin", "isin_source", "via"]

_NUM = re.compile(r"^\(?[-△▲]?[\d,]+(\.\d+)?\)?$")
_CURRENCY = re.compile(r"(円|ドル|ユーロ|ポンド|フラン|クローネ|クローナ|ウォン|ルピー|ルピア|リンギット|バーツ|"
                       r"ペソ|レアル|ランド|リラ|ズロチ|フォリント|元|シェケル|ディルハム|リヤル|ドン)$")
_YEN = ("日本円", "円", None)
_TSE_CODE = re.compile(r"^\d{3}[0-9A-Z]$")
_KIND = re.compile(r"(株式|証券|債券|受益証券|投資証券|新株予約権|社債|国債|地方債)")


def _fold(text: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(text or "")).split())


def _number(cell: str) -> float | None:
    c = _fold(cell).replace(" ", "")
    if not _NUM.match(c):
        return None
    neg = c.startswith(("-", "△", "▲", "(-"))
    v = float(re.sub(r"[^\d.]", "", c))
    return -v if neg else v


# -- HTML tables --------------------------------------------------------------


class _Tables(HTMLParser):
    """Every ``<table>`` as rows of cell texts, each with the text just before it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[tuple[str, list[list[str]]]] = []
        self._depth = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._before: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self.tables.append((_fold("".join(self._before))[-300:], []))
                self._before = []
        elif tag == "tr" and self._depth:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(_fold("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._depth:
                self.tables[-1][1].append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        elif not self._depth:
            self._before.append(data)


def tables(html: str) -> list[tuple[str, list[list[str]]]]:
    p = _Tables()
    p.feed(html)
    return p.tables


# -- the report ---------------------------------------------------------------


def asset_class(kind: str | None) -> str:
    k = kind or ""
    if "親投資信託" in k or "投資信託" in k or "投資証券" in k or "受益証券" in k:
        return "fund"
    if "債" in k:
        return "debt"
    if "株" in k or "新株予約権" in k:
        return "equity"
    return "other"


def _schedule(rows: list[list[str]]) -> list[dict] | None:
    """The lines of one securities schedule table, or None if ``rows`` is not one."""
    # Headers are written with spaces between the characters too ("銘 柄", "株 数", "通 貨").
    head = next((i for i, r in enumerate(rows[:3]) if any("銘柄" in c.replace(" ", "") for c in r)), None)
    if head is None:
        return None
    header = "".join(rows[head]).replace(" ", "")
    # Not the top-30 table (評価金額, 投資比率), which every report also has.
    if "投資比率" in header or not any(k in header for k in ("株式数", "株数", "券面総額", "評価額")):
        return None
    shares = "株式数" in header or "株数" in header
    kind = "株式" if shares and "種類" not in header else None
    currency = "日本円" if "通貨" not in header else None
    lines: list[dict] = []
    group: list[dict] = []
    pending: tuple[list[dict], float] | None = None  # a closed foreign group waiting for its yen subtotal

    def close(rows_: list[dict], yen: float | None, ratio: float | None, sub: float | None):
        for l in rows_:
            if yen is not None and sub:
                l["value_jpy"] = l["value"] * yen / sub
            elif ratio is not None and sub:
                l["ratio_pct"] = l["value"] / sub * ratio

    for r in rows[head + 1:]:
        cells = [c for c in r]
        # Some reports print each stock's Tokyo code before its name ("1332", "ニッスイ", ...).
        code = None
        lead = [i for i, c in enumerate(cells) if c]
        if len(lead) >= 2 and _TSE_CODE.match(cells[lead[0]]) and _number(cells[lead[1]]) is None:
            code = cells[lead[0]]
            cells = cells[:lead[0]] + [""] + cells[lead[0] + 1:]
        text = "".join(cells).replace(" ", "")  # "合 計", "小 計" are labels too
        nums = [(i, _number(c)) for i, c in enumerate(cells) if c and _number(c) is not None]
        words = [c for i, c in enumerate(cells) if c and _number(c) is None and (not nums or i < nums[0][0])]
        if not nums and not words:
            continue
        if "組入時価比率" in text:
            m = re.search(r"組入時価比率[:：]([\d.]+)%", text)
            if m and pending is not None:
                g, sub = pending
                if g and "value_jpy" not in g[0]:
                    close(g, None, float(m.group(1)), sub)
            continue
        if "小計" in text:
            sub = nums[-1][1] if nums else None
            pending = (group, sub)
            group = []
            continue
        if pending is not None and not words and nums and cells[nums[0][0]].strip().startswith("("):
            g, sub = pending
            close(g, nums[0][1], None, sub)
            continue
        if "合計" in text or "計" == text[:1] or any(w.replace(" ", "").startswith("銘柄数") for w in words):
            continue
        if not nums or not words:
            continue
        name = words[-1]
        for w in words[:-1]:
            if _CURRENCY.search(w):
                currency = w
            elif _KIND.search(w):
                kind = w
        line = {"name": name, "kind": kind, "currency": currency, "value": _value([n for _, n in nums], shares),
                "code": code}
        group.append(line)
        lines.append(line)
    for l in lines:
        # Yen lines are their own yen value; a foreign line has one only from its group's subtotal.
        if l["currency"] in _YEN and "value_jpy" not in l:
            l["value_jpy"] = l["value"]
    return lines


def _value(nums: list[float], shares: bool) -> float:
    """A line's value among its figures: shares, unit price, value (stocks), or face value, value (others).

    Later figures (shares lent, notes) are not the value.
    """
    if shares and len(nums) >= 3:
        for a, b, c in zip(nums, nums[1:], nums[2:]):
            if abs(a * b - c) <= 0.01 * abs(c) + 1:
                return c
        return nums[2]
    return nums[1] if len(nums) >= 2 else nums[0]


def _net_assets(rows: list[list[str]]) -> float | None:
    for r in rows:
        cells = [c for c in r if c]
        if cells and _fold(cells[0]).replace(" ", "") == "純資産合計":
            nums = [_number(c) for c in cells[1:] if _number(c) is not None]
            if nums:
                return nums[-1]
    return None


def _key(name: str) -> str:
    return re.sub(r"[\s・･]", "", _fold(name))


def parse_report(htmls: list[str]) -> dict:
    """``{nav, lines, notes}`` from a report's inline-XBRL parts, in document order.

    The first balance sheet is the fund's; each later one starts a mother
    fund, named by the text before it. A schedule belongs to the last balance
    sheet before it. Lines are the fund's own, with mother-fund units replaced
    by the mother fund's lines.
    """
    entities: list[dict] = []
    for html in htmls:
        for before, rows in tables(html):
            nav = _net_assets(rows)
            if nav is not None:
                entities.append({"nav": nav, "title": before, "lines": []})
                continue
            sched = _schedule(rows)
            if sched:
                if not entities:
                    entities.append({"nav": None, "title": "", "lines": []})
                entities[-1]["lines"].extend(sched)
    notes: list[str] = []
    if not entities or not entities[0]["nav"]:
        return {"nav": None, "lines": [], "notes": ["no balance sheet with 純資産合計 found"]}
    fund, mothers = entities[0], entities[1:]

    def weighted(e: dict, factor: float, via: str | None) -> list[dict]:
        out = []
        for l in e["lines"]:
            if "value_jpy" in l and e["nav"]:
                w = l["value_jpy"] / e["nav"] * 100.0
            elif "ratio_pct" in l:
                w = l["ratio_pct"]
            else:
                continue
            out.append({**l, "weight_pct": w * factor, "via": via, "asset_class": asset_class(l.get("kind"))})
        return out

    lines = []
    for l in weighted(fund, 1.0, None):
        if "親投資信託" in (l.get("kind") or ""):
            # By name only: a report also carries its management company's balance sheet.
            m = next((m for m in mothers if _key(l["name"]) and _key(l["name"]) in _key(m["title"])), None)
            if m is not None and m["lines"]:
                lines.extend(weighted(m, l["weight_pct"] / 100.0, l["name"]))
                notes.append(f"{l['weight_pct']:.2f}% is held through the mother fund {l['name']}, shown by its own holdings")
                continue
            notes.append(f"{l['weight_pct']:.2f}% is in the mother fund {l['name']}, whose holdings the report does not give")
        lines.append(l)
    dropped = sum(1 for e in entities for l in e["lines"] if "value_jpy" not in l and "ratio_pct" not in l)
    if dropped:
        notes.append(f"{dropped} lines in a foreign currency with no yen subtotal were left out")
    return {"nav": fund["nav"], "lines": lines, "notes": notes}


# -- names to ISINs -----------------------------------------------------------


def _company_key(name: object) -> str:
    s = _fold(name)
    s = re.sub(r"株式会社|\(株\)|有限会社|合同会社", "", s)
    return re.sub(r"[\s・･]", "", s)


def parse_company_codes(raw: bytes) -> pd.DataFrame:
    """EDINET's company code list: ``name`` and ``code`` (four-character TSE code) of listed companies."""
    df = pd.read_csv(io.BytesIO(raw), encoding="cp932", skiprows=1, dtype=str)
    df = df[df["証券コード"].fillna("").str.len() >= 4]
    return pd.DataFrame({"name": df["提出者名"], "code": df["証券コード"].str[:4]}).reset_index(drop=True)


def parse_fund_codes(raw: bytes) -> pd.DataFrame:
    """EDINET's fund code list: ``fund_code``, ``code`` (TSE code, listed funds only), ``fund_name``, ``fund_type``, ``issuer``."""
    df = pd.read_csv(io.BytesIO(raw), encoding="cp932", skiprows=1, dtype=str)
    return pd.DataFrame({
        "fund_code": df["ファンドコード"], "code": df["証券コード"].fillna("").str[:4].replace("", None),
        "fund_name": df["ファンド名"], "fund_type": df["特定有価証券区分名"], "issuer": df["発行者名"],
    })


def code_isins(findb_equities: str | Path | None = None, figi_cache: str | Path | None = None) -> dict[str, str]:
    """TSE code -> ISIN, from FinanceDatabase's ``NNNN.T`` listings, then the OpenFIGI cache."""
    out: dict[str, str] = {}
    if findb_equities and Path(findb_equities).is_file():
        e = pd.read_parquet(findb_equities, columns=["symbol", "isin"]).dropna()
        e = e[e["symbol"].str.endswith(".T") & e["isin"].str.startswith("JP")]
        out.update(dict(zip(e["symbol"].str[:-2], e["isin"])))
    if figi_cache and Path(figi_cache).is_file():
        c = pd.read_parquet(figi_cache)
        c = c[(c["id_type"] == "ID_ISIN") & c["id_value"].str.startswith("JP")]
        for isin, res in zip(c["id_value"], c["result"]):
            try:
                listings = json.loads(res) if isinstance(res, str) else list(res or [])
            except ValueError:
                continue
            for x in listings or []:
                x = dict(x)
                if x.get("exchCode") in ("JP", "JT") and x.get("ticker") and x.get("securityType") != "ETP":
                    out.setdefault(str(x["ticker"]), isin)
                    break
    return out


def name_index(companies: pd.DataFrame, codes: dict[str, str]) -> dict[str, str]:
    """Folded Japanese company name -> ISIN, for names that only one listed company has."""
    df = companies.assign(key=companies["name"].map(_company_key), isin=companies["code"].map(codes)).dropna(subset=["isin"])
    df = df[df["key"] != ""]
    counts = df.groupby("key")["isin"].nunique()
    df = df[df["key"].isin(counts[counts == 1].index)]
    return dict(zip(df["key"], df["isin"]))


def resolve(lines: pd.DataFrame, index: dict[str, str], master: dict | None = None,
            findb: dict | None = None, codes: dict[str, str] | None = None) -> pd.DataFrame:
    """ISINs by exact name: Japanese share names through ``index``, other share and fund names
    (``"iShares Core S&P 500 ETF"``) through the N-PORT security master and FinanceDatabase,
    as for CMVM (:func:`openbasket.sources.cmvm_portugal.resolve_isins`)."""
    from . import name_isin

    lines = lines.copy()
    for c in ("isin", "isin_source"):
        lines[c] = lines[c].astype(object).where(lines[c].notna(), None) if c in lines else None
    if codes and "code" in lines:
        by_code = lines["code"].map(lambda c: codes.get(c) if isinstance(c, str) else None)
        got = lines["isin"].isna() & by_code.notna()
        lines.loc[got, "isin"] = by_code[got]
        lines.loc[got, "isin_source"] = "ticker_match"
    keys = lines["name"].map(_company_key)
    hit = lines["isin"].isna() & lines["asset_class"].eq("equity") & keys.isin(index.keys())
    lines.loc[hit, "isin"] = keys[hit].map(index)
    lines.loc[hit, "isin_source"] = "name_match"
    latin = lines["isin"].isna() & lines["asset_class"].isin(["equity", "fund"]) & lines["name"].map(
        lambda n: isinstance(n, str) and n.isascii())
    if latin.any() and (master or findb):
        probe = pd.DataFrame({"isin": None, "name": lines.loc[latin, "name"], "asset_class": "equity",
                              "weight_pct": lines.loc[latin, "weight_pct"]}, index=lines.index[latin])
        if master:
            probe, _ = name_isin.resolve(probe, master)
        if findb:
            left = probe["isin"].isna()
            probe.loc[left, "isin"] = probe.loc[left, "name"].map(lambda n: findb.get(name_isin.normalise(n)))
        got = probe["isin"].notna()
        lines.loc[probe.index[got], "isin"] = probe.loc[got, "isin"]
        lines.loc[probe.index[got], "isin_source"] = "name_match"
    return lines


# -- the store ----------------------------------------------------------------


class EdinetStore:
    """Daily filing lists, code lists and parsed reports under ``root``.

    ``lists/<day>.json``: that day's fund filings (fund documents only).
    ``codes/``: EDINET's fund and company code lists. ``reports/<docID>.parquet``:
    one parsed report. ``reports.parquet``: the latest annual report of each
    listed fund, with its net assets and notes.
    """

    def __init__(self, root: str | Path, client=api):
        self.root = Path(root)
        self.client = client

    # -- refresh ------------------------------------------------------------

    def refresh(self, days: int = 400, today: date | None = None, code_list_days: int = 7) -> dict:
        """Read new days, then download and parse every listed fund's latest annual report not stored yet."""
        today = today or date.today()
        self.root.mkdir(parents=True, exist_ok=True)
        funds = self.fund_codes(refresh_after_days=code_list_days)
        self.company_codes(refresh_after_days=code_list_days)
        listed = funds[funds["code"].notna() & funds["fund_type"].isin(FUND_TYPES)]
        n_days = 0
        for d in pd.date_range(end=today - timedelta(days=1), periods=days):
            p = self.root / "lists" / f"{d.date()}.json"
            if p.exists():
                continue
            docs = self.client.fund_documents(d.date())
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(docs, ensure_ascii=False))
            n_days += 1
        latest = self.latest_reports(listed)
        n_new = n_fail = 0
        for r in latest.to_dict("records"):
            out = self.root / "reports" / f"{r['docID']}.parquet"
            if out.exists() and _parser_version(out) >= PARSER_VERSION:
                continue
            try:
                got = parse_report(_report_htmls(self.client.download_document(r["docID"], 1)))
            except Exception as exc:  # one broken report must not stop the others
                logger.warning("EDINET %s (%s): %s: %s", r["docID"], r["code"], type(exc).__name__, exc)
                n_fail += 1
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            frame = pd.DataFrame(got["lines"]).reindex(columns=LINE_COLUMNS + ["value", "ratio_pct"])
            frame["nav"] = got["nav"]
            frame["notes"] = "; ".join(got["notes"])
            frame["parser"] = PARSER_VERSION
            frame.to_parquet(out, index=False)
            n_new += 1
        latest.to_parquet(self.root / "reports.parquet", index=False)
        (self.root / "source.txt").write_text(f"v{STORE_VERSION} {len(latest)}\n")
        done = {"new_days": n_days, "listed_funds": int(len(listed)), "reports": int(len(latest)),
                "parsed_now": n_new, "failed": n_fail}
        logger.info("EDINET: %s", done)
        return done

    def _code_list(self, name: str, refresh_after_days: int) -> bytes:
        p = self.root / "codes" / f"{name}.csv"
        fresh = p.exists() and (date.today() - date.fromtimestamp(p.stat().st_mtime)).days < refresh_after_days
        if not fresh:
            r = http.get(CODELIST_URL.format(name=name), timeout=120)
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                raw = z.read(next(n for n in z.namelist() if n.endswith(".csv")))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
        return p.read_bytes()

    def fund_codes(self, refresh_after_days: int = 7) -> pd.DataFrame:
        return parse_fund_codes(self._code_list("Fundcode", refresh_after_days))

    def company_codes(self, refresh_after_days: int = 7) -> pd.DataFrame:
        return parse_company_codes(self._code_list("Edinetcode", refresh_after_days))

    def latest_reports(self, listed: pd.DataFrame) -> pd.DataFrame:
        """The newest annual report of each listed fund in the stored days."""
        cols = ["code", "fund_code", "fund_name", "issuer", "docID", "periodEnd", "submitDateTime"]
        docs = [d for p in sorted((self.root / "lists").glob("*.json")) for d in json.loads(p.read_text())]
        df = pd.DataFrame(docs, columns=api.LIST_COLUMNS)
        df = df[df["docTypeCode"].isin(REPORT_TYPES) & df["fundCode"].isin(listed["fund_code"])]
        if df.empty:
            return pd.DataFrame(columns=cols)
        df = df.sort_values(["periodEnd", "submitDateTime"]).drop_duplicates("fundCode", keep="last")
        df = df.merge(listed, left_on="fundCode", right_on="fund_code")
        return df[cols].sort_values("code").reset_index(drop=True)

    # -- read -----------------------------------------------------------------

    def is_built(self) -> bool:
        return (self.root / "reports.parquet").is_file()

    def reports(self) -> pd.DataFrame:
        return pd.read_parquet(self.root / "reports.parquet") if self.is_built() else pd.DataFrame()

    def holdings(self, code: str, index: dict[str, str] | None = None, master: dict | None = None,
                 findb: dict | None = None, codes: dict[str, str] | None = None) -> dict | None:
        """The latest report of the fund listed as ``code``: ``{as_of, lines, url, doc_id, fund_name, notes}``."""
        reps = self.reports()
        if reps.empty:
            return None
        row = reps[reps["code"] == str(code)]
        if row.empty:
            return None
        r = row.iloc[0]
        p = self.root / "reports" / f"{r['docID']}.parquet"
        if not p.exists():
            return None
        lines = pd.read_parquet(p)
        notes = [n for n in str(lines["notes"].iloc[0] if len(lines) else "").split("; ") if n]
        lines = lines.drop(columns=["notes", "nav", "parser"], errors="ignore")
        if index or master or findb or codes:
            lines = resolve(lines, index or {}, master, findb, codes)
        return {"as_of": str(r["periodEnd"]), "lines": lines, "url": VIEWER_URL.format(doc_id=r["docID"]),
                "doc_id": r["docID"], "fund_name": r["fund_name"], "notes": notes}


def _parser_version(path: Path) -> int:
    try:
        cols = pd.read_parquet(path, columns=None).columns
        return int(pd.read_parquet(path, columns=["parser"])["parser"].iloc[0]) if "parser" in cols else 1
    except Exception:  # an unreadable file is parsed again
        return 0


def _report_htmls(zip_bytes: bytes) -> list[str]:
    """The report body's inline-XBRL parts (``PublicDoc/*honbun*``), in document order."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = sorted(n for n in z.namelist() if "/PublicDoc/" in n and "honbun" in n and n.endswith(".htm"))
        return [z.read(n).decode("utf-8", "replace") for n in names]
