"""Data providers for the look-through engine.

Matching an input ISIN to a fund, in order:

1. an issuer's own fund list (iShares UK; SPDR US and EMEA, including SPY);
2. a configured proxy (for funds with no free holdings, e.g. swap-based);
3. N-PORT: a holding with this ISIN, or with the CUSIP inside it, whose
   issuer LEI is a fund series' LEI;
4. OpenFIGI: the ISIN's US ticker, looked up in SEC's fund ticker list.

Holdings sources are tried in ``source_order``; see
:func:`openbasket.lookthrough.choose` for how one is picked.

A fund can also be named directly by its SEC series ID (``S000004310``) or
its issuer ID (``ishares-uk:253743``), which is how a universe run refers to
funds it already knows.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from functools import lru_cache

import duckdb
import pandas as pd

from .datasets import connection
from .identifiers import cusip_to_isin, is_valid_isin, isin_to_cusip, normalise_ticker
from .lookthrough import FundRef, Holdings, Line
from .sources import ishares, nport, spdr
from .sources.openfigi import OpenFIGI
from .sources.tickerinside_source import fetch_fund

logger = logging.getLogger(__name__)

SOURCES = ("issuer", "tickerinside", "nport", "top_holdings")

SERIES_RE = re.compile(r"^S\d{9}$")
ISSUER_ID_RE = re.compile(r"^(ishares|spdr)-([a-z]+):(.+)$", re.I)
NPORT_DATASETS_URL = "https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets"


class DataProviders:
    def __init__(
        self,
        nport_holdings: duckdb.DuckDBPyRelation,
        security_master: duckdb.DuckDBPyRelation,
        fund_master: pd.DataFrame,
        ticker_isin_map: pd.DataFrame,
        figi: OpenFIGI | None,
        source_order: list[str],
        proxies: dict | None = None,
        issuer_products: pd.DataFrame | None = None,
        issuer_cache_dir: str | None = None,
        cusip_isin: dict[str, tuple[str, str]] | None = None,
        sedol_isin: dict[str, str] | None = None,
        stop_at_pct: float | None = None,
        tickerinside_tickers: set[str] | None = None,
    ):
        """``stop_at_pct`` stops reading further sources once one resolves that
        much, which spares a universe run the network calls whose answer
        :func:`~openbasket.lookthrough.choose` would ignore anyway.
        ``tickerinside_tickers`` limits TickerInside requests to the tickers it
        lists in its issuer layer."""
        unknown = set(source_order) - set(SOURCES)
        if unknown:
            raise ValueError(f"Unknown sources {sorted(unknown)}; choose from {SOURCES}")
        self.holdings_rel = nport_holdings
        self.security_rel = security_master
        self.figi = figi
        self.source_order = list(source_order)
        self.proxies = {k.upper(): v for k, v in (proxies or {}).items()}
        ip = issuer_products if issuer_products is not None else pd.DataFrame(columns=["isin"])
        self.issuer_products = ip.drop_duplicates("isin").set_index("isin")
        self.issuer_by_id = {
            issuer_id(r.get("issuer"), r["site"], r["portfolio_id"]): r for r in ip.to_dict("records")
        }
        self.stop_at_pct = stop_at_pct
        self.tickerinside_tickers = {t.upper() for t in tickerinside_tickers} if tickerinside_tickers is not None else None
        self._fund_holding_index: tuple[dict, dict, list] | None = None
        self.issuer_cache_dir = issuer_cache_dir
        self.cusip_isin = cusip_isin or {}
        self.sedol_isin = sedol_isin or {}

        fm = fund_master.copy()
        self.series = fm.drop_duplicates("series_id").set_index("series_id")
        self.series_by_lei = fm.dropna(subset=["series_lei"]).drop_duplicates("series_lei").set_index("series_lei")["series_id"].to_dict()
        tickers = fm.dropna(subset=["ticker"])
        self.series_by_ticker = {normalise_ticker(t): s for t, s in zip(tickers["ticker"], tickers["series_id"])}
        self.tickers_by_series = tickers.groupby("series_id")["ticker"].apply(list).to_dict()
        self.cik_by_series = (
            fm.dropna(subset=["cik"]).drop_duplicates("series_id").set_index("series_id")["cik"].to_dict()
            if "cik" in fm else {}
        )
        self.global_ticker_isin = {
            normalise_ticker(t): i for t, i in zip(ticker_isin_map["ticker"], ticker_isin_map["isin"])
        }

    # -- matching -----------------------------------------------------------

    def _ref(self, series_id: str | None, ticker: str | None, via: str, note: str | None = None) -> FundRef:
        name = None
        if series_id and series_id in self.series.index:
            name = self.series.at[series_id, "series_name"]
        if ticker is None and series_id:
            ticker = (self.tickers_by_series.get(series_id) or [None])[0]
        return FundRef(series_id=series_id, ticker=ticker, name=name or ticker, via=via, note=note)

    def _ref_from_ticker(self, ticker: str, via: str, note: str | None = None) -> FundRef:
        series_id = self.series_by_ticker.get(normalise_ticker(ticker))
        return self._ref(series_id, ticker.upper(), via, note)

    def _issuer_ref(self, isin: str) -> FundRef | None:
        if isin not in self.issuer_products.index:
            return None
        return self._issuer_ref_from(self.issuer_products.loc[isin].to_dict())

    @staticmethod
    def _issuer_ref_from(p: dict) -> FundRef:
        iid = issuer_id(p.get("issuer"), p["site"], p["portfolio_id"])
        ticker = p.get("ticker") if isinstance(p.get("ticker"), str) else None
        return FundRef(series_id=None, ticker=ticker, name=p["fund_name"], via=iid.split("-")[0], issuer_id=iid)

    def resolve_ref(self, fund: str) -> FundRef | None:
        """A fund named by series ID or issuer ID; ``None`` if ``fund`` is neither or is unknown."""
        fund = fund.strip()
        if SERIES_RE.match(fund.upper()):
            sid = fund.upper()
            return self._ref(sid, None, "series_id") if sid in self.series.index else None
        m = ISSUER_ID_RE.match(fund)
        if m:
            p = self.issuer_by_id.get(issuer_id(m.group(1), m.group(2), m.group(3)))
            return self._issuer_ref_from(p) if p is not None else None
        return None

    def resolve(self, isin: str) -> FundRef | None:
        ref = self.resolve_ref(isin)
        if ref is not None:
            return ref
        isin = isin.strip().upper()
        ref = self._issuer_ref(isin)
        if ref is not None:
            return ref
        if isin in self.proxies:
            p = self.proxies[isin]
            note = f"proxy: {p.get('note', '')}".strip()
            if p.get("ticker"):
                return self._ref_from_ticker(p["ticker"], "proxy", note)
            if p.get("isin"):
                inner = self.resolve(p["isin"])
                return replace(inner, via="proxy", note=note) if inner else None
        if not is_valid_isin(isin):
            logger.warning("%s is not a valid ISIN", isin)
            return None
        series_id = self._series_from_nport(isin)
        if series_id:
            return self._ref(series_id, None, "nport_lei")
        if self.figi is not None:
            listings = self.figi.map("ID_ISIN", [isin])[isin]
            for row in listings:
                if row.get("exchCode") == "US" and row.get("ticker"):
                    ref = self._ref_from_ticker(row["ticker"], "openfigi_ticker")
                    if ref.series_id or self._tickerinside(ref.ticker):
                        return ref
        return None

    def _series_from_nport(self, isin: str) -> str | None:
        """The series whose LEI is the issuer of most holdings with this ISIN (or its CUSIP)."""
        by_isin, by_cusip, groups = self._fund_holdings()
        cusip = isin_to_cusip(isin)
        hits = set(by_isin.get(isin, ())) | set(by_cusip.get(cusip, ()) if cusip else ())
        counts: dict[str, int] = {}
        for g in hits:
            lei, n = groups[g]
            counts[lei] = counts.get(lei, 0) + n
        if not counts:
            return None
        return self.series_by_lei[max(counts, key=counts.get)]

    def _fund_holdings(self) -> tuple[dict, dict, list]:
        """Holdings whose issuer is a fund series, indexed by ISIN and CUSIP.

        Built with one scan of N-PORT; a query per ISIN would scan all of it
        each time, which a universe run would do thousands of times.
        """
        if self._fund_holding_index is None:
            con = connection()
            con.register("_series_leis", pd.DataFrame({"lei": list(self.series_by_lei)}, dtype=object))
            con.register("rel", self.holdings_rel)
            try:
                rows = con.execute(
                    "select isin, cusip, issuer_lei, count(*) from rel "
                    "where issuer_lei in (select lei from _series_leis) group by all"
                ).fetchall()
            finally:
                con.unregister("rel")
                con.unregister("_series_leis")
            by_isin: dict[str, list[int]] = {}
            by_cusip: dict[str, list[int]] = {}
            groups = []
            for i, (isin, cusip, lei, n) in enumerate(rows):
                groups.append((lei, n))
                if isin:
                    by_isin.setdefault(isin, []).append(i)
                if cusip:
                    by_cusip.setdefault(cusip, []).append(i)
            self._fund_holding_index = (by_isin, by_cusip, groups)
        return self._fund_holding_index

    def resolve_underlying(self, isin: str) -> FundRef | None:
        """The fund a derivative references, from local data only (issuer lists, N-PORT)."""
        isin = isin.strip().upper()
        ref = self._issuer_ref(isin)
        if ref is not None:
            return ref
        series_id = self._series_from_nport(isin)
        return self._ref(series_id, None, "nport_lei") if series_id else None

    def resolve_line(self, line: Line) -> FundRef | None:
        if line.isin:
            ref = self._issuer_ref(line.isin)
            if ref is not None:
                return ref
        if line.issuer_lei and line.issuer_lei in self.series_by_lei:
            return self._ref(self.series_by_lei[line.issuer_lei], None, "nport_lei")
        if line.us_ticker and line.ticker and normalise_ticker(line.ticker) in self.series_by_ticker:
            return self._ref_from_ticker(line.ticker, "sec_ticker")
        if line.fund_hint and line.isin and not line.issuer_lei:
            series_id = self._series_from_nport(line.isin)
            if series_id:
                return self._ref(series_id, None, "nport_lei")
        return None

    def security(self, isin: str) -> Holdings | None:
        isin = isin.strip().upper()
        rows = duckdb_rows(
            self.security_rel,
            "select isin, issuer_name, title, issuer_lei, cusip, ticker, asset_cat, isin_source, n_positions "
            "from rel where isin = ?",
            [isin],
        )
        if not rows:
            return None
        isin, issuer, title, lei, cusip, ticker, cat, src, n = rows[0]
        line = Line(
            weight_pct=100.0, isin=isin, name=issuer if cat in ("EC", "EP") else (title or issuer),
            issuer_name=issuer, issuer_lei=lei, title=title, cusip=cusip, ticker=ticker,
            asset_class=nport.ASSET_CLASS.get(cat, cat), isin_source=src,
        )
        return Holdings("security_master", None, [line], source_ref=f"held in {n} N-PORT positions",
                        source_url=NPORT_DATASETS_URL)

    # -- holdings -----------------------------------------------------------

    def holdings(self, ref: FundRef) -> list[Holdings]:
        out = []
        for source in self.source_order:
            if self.stop_at_pct is not None and out and out[-1].resolved_pct >= self.stop_at_pct:
                break
            h = None
            if source == "issuer" and ref.issuer_id:
                h = self._from_issuer(ref.issuer_id)
            elif source == "tickerinside":
                h = self._from_tickerinside(ref)
            elif source == "nport" and ref.series_id:
                h = self._from_nport(ref.series_id)
            # "top_holdings" has no client yet and is skipped.
            if h is not None and h.lines:
                out.append(h)
        return out

    def _from_issuer(self, issuer_id: str) -> Holdings | None:
        issuer, rest = issuer_id.split("-", 1)
        if issuer == "spdr":
            return self._from_spdr(issuer_id)
        return self._from_ishares(issuer_id)

    def _product_page(self, iid: str) -> str | None:
        page = (self.issuer_by_id.get(iid) or {}).get("product_page_url")
        return page if isinstance(page, str) and page else None

    @lru_cache(maxsize=256)
    def _from_spdr(self, issuer_id: str) -> Holdings | None:
        site, portfolio_id = issuer_id.removeprefix("spdr-").split(":")
        match = self.issuer_products[
            (self.issuer_products["issuer"] == "spdr") & (self.issuer_products["portfolio_id"] == portfolio_id)
        ]
        if match.empty:
            return None
        url = match["holdings_url"].iloc[0]
        try:
            got = spdr.holdings(url, cache_dir=self.issuer_cache_dir)
        except Exception as exc:  # a broken issuer file must not stop the run
            logger.warning("SPDR %s: %s", issuer_id, exc)
            return None
        df = got.holdings.astype(object).where(got.holdings.notna(), None)
        records = df.to_dict("records")
        found = self._issuer_isins([(r.get("isin"), r.get("cusip"), r.get("sedol")) for r in records])
        lines = []
        for r, (isin, how) in zip(records, found):
            name = r.get("name") or ""
            cash = bool(re.search(r"cash|^(u\.s\. dollar|euro|british pounds?|[a-z]+ (peso|pound|yen|franc|krona|krone|dollar|rupee|won|real|rand))$", name, re.I))
            lines.append(
                Line(
                    weight_pct=float(r.get("weight_pct") or 0.0),
                    isin=isin, name=r.get("name"), ticker=r.get("ticker"), cusip=r.get("cusip"),
                    asset_class="cash" if cash and not isin else ("debt" if r.get("is_debt") else "equity" if isin else None),
                    isin_source=how,
                    fund_hint="MONEY MARKET" in name.upper() or " ETF" in name.upper(),
                )
            )
        return Holdings("spdr", got.as_of, lines, source_ref=f"SPDR holdings {got.url}",
                        source_url=self._product_page(issuer_id) or got.url)

    def _issuer_isins(self, ids: list[tuple[str | None, str | None, str | None]]) -> list[tuple[str | None, str | None]]:
        """ISIN and how it was found for each ``(isin, cusip, sedol)`` of an issuer file.

        Issuers use CUSIP-shaped internal codes for some securities (SPDR does
        for foreign shares), so an ISIN derived from a CUSIP is kept only if
        OpenFIGI knows it.
        """
        found = [self._isin_for(*x) for x in ids]
        derived = [i for i, how in found if how == "derived_cusip"]
        known = set()
        if derived and self.figi is not None:
            known = {i for i, rows in self.figi.map("ID_ISIN", derived).items() if rows}
        return [(None, None) if how == "derived_cusip" and isin not in known else (isin, how) for isin, how in found]

    def _isin_for(self, isin: str | None, cusip: str | None, sedol: str | None) -> tuple[str | None, str | None]:
        """Best ISIN for an issuer line, and how it was found."""
        if isin:
            return isin, "reported"
        if cusip and cusip in self.cusip_isin:
            return self.cusip_isin[cusip][0], "nport_cusip"
        if sedol and sedol.upper() in self.sedol_isin:
            return self.sedol_isin[sedol.upper()], "nport_sedol"
        derived = cusip_to_isin(cusip) if cusip else None
        if derived:
            return derived, "derived_cusip"
        return None, None

    @lru_cache(maxsize=256)
    def _from_ishares(self, issuer_id: str) -> Holdings | None:
        site, portfolio_id = issuer_id.removeprefix("ishares-").split(":")
        try:
            got = ishares.holdings(portfolio_id, site, cache_dir=self.issuer_cache_dir)
        except Exception as exc:  # a broken issuer file must not stop the run
            logger.warning("iShares %s: %s", issuer_id, exc)
            return None
        if len(got.holdings) >= 20000:
            logger.warning("iShares %s returned %d lines, possibly capped by the site", issuer_id, len(got.holdings))
        df = got.holdings.astype(object).where(got.holdings.notna(), None)
        records = df.to_dict("records")
        found = self._issuer_isins([(r.get("isin"), r.get("cusip"), r.get("sedol")) for r in records])
        lines = [
            Line(
                weight_pct=float(r.get("holdingPercent") or 0.0),
                isin=isin,
                name=r.get("issueName"),
                ticker=r.get("ticker"),
                cusip=r.get("cusip"),
                asset_class=ishares.ASSET_CLASS.get(r.get("assetClass"), (r.get("assetClass") or "").lower() or None),
                isin_source=how,
                fund_hint=r.get("assetClass") == "Money Market" or " ETF" in (r.get("issueName") or "").upper(),
            )
            for r, (isin, how) in zip(records, found)
        ]
        return Holdings("ishares", got.as_of, lines, source_ref=f"iShares holdings {got.url}",
                        source_url=self._product_page(issuer_id) or got.url)

    @lru_cache(maxsize=1024)
    def _nport_lines(self, series_id: str) -> tuple[pd.DataFrame, str | None]:
        # nport_holdings is sorted by series_id, so DuckDB reads only the row groups that hold it.
        df = self.holdings_rel.filter(f"series_id = '{series_id}'").df()
        df = df.astype(object).where(df.notna(), None)
        as_of = pd.Timestamp(df["report_date"].iloc[0]).date().isoformat() if len(df) else None
        return df, as_of

    @staticmethod
    def _underlying_isin(r) -> str | None:
        """ISIN of the instrument a derivative line references, if N-PORT gives one."""
        if r.asset_cat not in nport.DERIVATIVE_CATS:
            return None
        isin = getattr(r, "underlying_isin", None)
        if isin and is_valid_isin(isin):
            return isin
        cusip = getattr(r, "ref_cusip", None)
        return cusip_to_isin(cusip) if cusip else None

    def _from_nport(self, series_id: str) -> Holdings | None:
        df, as_of = self._nport_lines(series_id)
        if df.empty:
            return None
        lines = [
            Line(
                weight_pct=float(r.weight_pct or 0.0),
                isin=r.isin,
                name=r.issuer_title if r.asset_cat not in ("EC", "EP") else r.issuer_name,
                issuer_name=r.issuer_name,
                issuer_lei=r.issuer_lei,
                title=r.issuer_title,
                cusip=r.cusip,
                ticker=r.reported_ticker,
                asset_class=nport.ASSET_CLASS.get(r.asset_cat, r.asset_cat),
                isin_source=r.isin_source,
                fund_hint=r.issuer_type in nport.FUND_ISSUER_TYPES,
                underlying_isin=self._underlying_isin(r),
                underlying_name=getattr(r, "ref_name", None),
            )
            for r in df.itertuples(index=False)
        ]
        accession = df["accession_number"].iloc[0]
        return Holdings("nport", as_of, lines, source_ref=f"SEC Form N-PORT {accession}",
                        source_url=edgar_url(series_id, accession, self.cik_by_series.get(series_id)))

    @lru_cache(maxsize=4096)
    def _tickerinside(self, ticker: str | None):
        if not ticker or (self.tickerinside_tickers is not None and ticker.upper() not in self.tickerinside_tickers):
            return None
        return fetch_fund(ticker)

    def _from_tickerinside(self, ref: FundRef) -> Holdings | None:
        # Issuer tickers outside the US are local codes that can collide with
        # a different US fund's ticker on TickerInside.
        us_listed = not ref.issuer_id or ref.issuer_id.split(":")[0].endswith("-us")
        tickers = [ref.ticker] if ref.ticker and us_listed else []
        tickers += [t for t in self.tickers_by_series.get(ref.series_id, []) if t not in tickers][:3]
        for t in tickers:
            f = self._tickerinside(t)
            if f is None:
                continue
            if f.layer != "issuer":
                logger.info("TickerInside %s is from %s; N-PORT is read directly instead", t, f.layer)
                return None
            mapping = self._ticker_isin_for(ref.series_id)
            lines = []
            for tk, w in f.holdings.items():
                isin = mapping.get(normalise_ticker(tk)) or self.global_ticker_isin.get(normalise_ticker(tk))
                lines.append(Line(weight_pct=w, isin=isin, name=None, ticker=tk, asset_class="equity",
                                  isin_source="ticker_match" if isin else None, us_ticker=True))
            names = self._names_for(ref.series_id)
            for l in lines:
                if l.isin:
                    l.name = names.get(l.isin)
            return Holdings("tickerinside", f.as_of, lines, source_ref=f.cite_as, source_url=f.page_url)
        return None

    def _ticker_isin_for(self, series_id: str | None) -> dict[str, str]:
        """Ticker to ISIN for one fund, from its own N-PORT lines and OpenFIGI."""
        if not series_id:
            return {}
        df, _ = self._nport_lines(series_id)
        if df.empty:
            return {}
        out = {normalise_ticker(t): i for t, i in zip(df["reported_ticker"], df["isin"]) if t and i}
        equity_isins = df.loc[df["asset_cat"].isin(["EC", "EP"]) & df["isin"].notna(), "isin"].tolist()
        if self.figi is not None and equity_isins:
            for isin, rows in self.figi.map("ID_ISIN", equity_isins).items():
                for row in rows:
                    if row.get("exchCode") == "US" and row.get("ticker"):
                        out.setdefault(normalise_ticker(row["ticker"]), isin)
        return out

    def _names_for(self, series_id: str | None) -> dict[str, str]:
        if not series_id:
            return {}
        df, _ = self._nport_lines(series_id)
        return dict(zip(df["isin"], df["issuer_name"]))


def issuer_id(issuer: str | None, site: str, portfolio_id: object) -> str:
    """``ishares-uk:253743`` style ID of an issuer fund; lists without an issuer column are iShares."""
    issuer = issuer if isinstance(issuer, str) and issuer else "ishares"
    return f"{issuer.lower()}-{str(site).lower()}:{portfolio_id}"


def edgar_url(series_id: str, accession: str | None, cik: str | None) -> str:
    """The filing's EDGAR folder when the registrant's CIK is known, else the series' N-PORT filings."""
    if cik and accession and str(cik).isdigit():
        return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
    return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={series_id}&type=NPORT-P"


def duckdb_rows(rel: duckdb.DuckDBPyRelation, sql: str, params: list) -> list[tuple]:
    """Run ``sql`` against ``rel`` (named ``rel``) with positional parameters."""
    con = connection()
    con.register("rel", rel)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.unregister("rel")
