"""Germany: fund reports in the Bundesanzeiger.

Every German public investment fund (Publikums-AIF and OGAW) must publish its
annual and semi-annual reports in the Bundesanzeiger (KAGB § 107). The free
search on ``www.bundesanzeiger.de`` finds them by ISIN in the
*Kapitalmarkt* section, and serves each report as an HTML page whose
*Vermögensaufstellung* lists every position with its ISIN and share of the
fund's net assets; :mod:`openbasket.sources.kag_reports` reads it.

The site is a Wicket application: result and report links are tied to a
cookie session (they expire after 30 minutes), so a fund is read in one
session: the start page, a search for the ISIN, then the newest annual or
semi-annual report among the results that lists that ISIN. Requests share
openbasket's per-host throttle (one a second by default).

``robots.txt`` disallows only ``/nlp`` and ``/construction_page.html``; the
fund report pages show no CAPTCHA and need no login. The site states no
terms for readers of the free search. The reports are the management
companies' documents, so this source is ``local_only``.

Parsed reports are kept in ``<cache>/bundesanzeiger/``: one Parquet per
report (shared by its share classes) and, per ISIN, which report that was and
when it was looked up, so a report is searched again only after
:data:`SEARCH_MAX_AGE_DAYS`.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from .. import http
from . import kag_reports

logger = logging.getLogger(__name__)

REDISTRIBUTION = "local_only"
HOST = "https://www.bundesanzeiger.de"
START_URL = HOST + "/pub/de/start?0"
SEARCH_URL = HOST + "/pub/de/start"
#: The search form's Wicket listener (a hidden field) and the Kapitalmarkt section.
FORM_FIELD = "0-1.-top~content~panel-left~card-form"
AREA_CAPITAL_MARKET = "7"
SEARCH_MAX_AGE_DAYS = 30
REPORT_TITLE = re.compile(r"^(Halbjahres|Jahres|Rechenschafts)bericht", re.I)
_ROW = re.compile(r'<div class="row(?: back)?">(.*?)<div class="date">\s*([\d.]+)\s*</div>', re.S)
_LINK = re.compile(r'<a href="([^"]*publication~link)"[^>]*>(.*?)</a>(.*?)</div>', re.S)
_DATE = re.compile(r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})")
_MONTHS = {m: i for i, m in enumerate(
    ["januar", "februar", "märz", "april", "mai", "juni", "juli", "august", "september", "oktober", "november", "dezember"], 1)}


@dataclass
class Result:
    title: str
    funds: str
    isins: list[str]
    published: str  # ISO date
    link: str

    @property
    def period_end(self) -> str | None:
        """The last date in the title ("01.10.2025 - 31.03.2026", "zum 30. September 2021")."""
        found = [(int(y), int(m), int(d)) for d, m, y in _DATE.findall(self.title)]
        for d, month, y in re.findall(r"(\d{1,2})\.\s*([A-Za-zä]+)\s+(\d{4})", self.title):
            if month.lower() in _MONTHS:
                found.append((int(y), _MONTHS[month.lower()], int(d)))
        if not found:
            return None
        y, m, d = max(found)
        return date(y, m, d).isoformat()


def _text(fragment: str) -> str:
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse_results(page: str) -> list[Result]:
    """The result rows of one search page, newest first as the site lists them."""
    out = []
    for body, published in _ROW.findall(page):
        m = _LINK.search(body)
        if not m:
            continue
        link, title, rest = m.groups()
        rest = _text(rest)
        isins = kag_reports.ISIN.findall(rest)
        funds = kag_reports.ISIN.sub("", rest).strip(" ;,")
        d, mo, y = published.split(".")
        out.append(Result(_text(title), funds, isins, f"{y}-{mo}-{d}", html_lib.unescape(link)))
    return out


def newest_report(results: list[Result], isin: str) -> Result | None:
    """The most recently published annual or semi-annual report that lists ``isin``."""
    reports = [r for r in results if REPORT_TITLE.match(r.title) and isin in r.isins]
    return max(reports, key=lambda r: (r.published, r.period_end or ""), default=None)


class Client:
    """One cookie session against the Bundesanzeiger, throttled per host."""

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = http.USER_AGENT
        self._started = 0.0

    def _get(self, url: str, **kw) -> requests.Response:
        http._wait(url)
        r = self.session.get(url, timeout=60, **kw)
        r.raise_for_status()
        return r

    def _ensure_session(self) -> None:
        # Links die with the session (30 minutes); start a new one well before.
        if time.time() - self._started > 20 * 60:
            self.session.cookies.clear()
            self._get(START_URL)
            self._started = time.time()

    def search(self, query: str) -> list[Result]:
        self._ensure_session()
        r = self._get(SEARCH_URL, params={FORM_FIELD: "", "fulltext": query, "area_select": AREA_CAPITAL_MARKET,
                                          "search_button": "Suchen"})
        return parse_results(r.text)

    def page(self, link: str) -> str:
        return self._get(link).text


class Store:
    """Parsed reports under ``root``, keyed by report; an ISIN index says which report an ISIN has."""

    def __init__(self, root: str | Path, client: Client | None = None):
        self.root = Path(root)
        self.client = client

    def _index_path(self, isin: str) -> Path:
        return self.root / "isin" / f"{isin}.json"

    def holdings(self, isin: str, today: date | None = None, fund_name: str | None = None) -> tuple[pd.DataFrame, dict] | None:
        """``(lines, meta)`` of the newest report listing ``isin``; ``None`` if there is none or it does not read.

        ``meta`` has ``title``, ``fund``, ``published``, ``as_of``, ``total_pct``.
        """
        today = today or date.today()
        idx = self._index_path(isin)
        if idx.exists():
            meta = json.loads(idx.read_text())
            fresh = (today - date.fromisoformat(meta["checked"])).days < SEARCH_MAX_AGE_DAYS
            if fresh:
                return self._load(meta)
        self.client = self.client or Client()
        results = self.client.search(isin)
        found = newest_report(results, isin)
        meta = {"checked": today.isoformat(), "key": None}
        if found is not None:
            # A report for several funds is one page but a schedule per fund: the key includes the fund.
            key = hashlib.sha1(f"{found.title}|{found.funds}|{found.published}|{fund_name or ''}".encode()).hexdigest()[:16]
            meta.update(key=key, title=found.title, fund=found.funds, published=found.published,
                        as_of=found.period_end)
            path = self.root / "reports" / f"{key}.parquet"
            if not path.exists() or not meta["as_of"]:
                page = self.client.page(found.link)
                meta["as_of"] = meta["as_of"] or schedule_date(page) or found.published
                df = kag_reports.parse_html(page, set(found.isins), fund_name)
                path.parent.mkdir(parents=True, exist_ok=True)
                df.assign(total_pct=kag_reports.total(df)).to_parquet(path, index=False)
        idx.parent.mkdir(parents=True, exist_ok=True)
        idx.write_text(json.dumps(meta, ensure_ascii=False))
        return self._load(meta)

    def _load(self, meta: dict) -> tuple[pd.DataFrame, dict] | None:
        if not meta.get("key"):
            return None
        path = self.root / "reports" / f"{meta['key']}.parquet"
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        total = float(df["total_pct"].iloc[0]) if len(df) else 0.0
        return df.drop(columns=["total_pct"]), {**meta, "total_pct": total}


def schedule_date(page: str) -> str | None:
    """The date in "Vermögensaufstellung zum 31.03.2026" (or "per"), for a report whose title has none."""
    m = re.search(r"Vermögensaufstellung\s+(?:zum|per|am)\s+(\d{1,2})\.(\d{1,2})\.(\d{4})", _text(page))
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat() if m else None
