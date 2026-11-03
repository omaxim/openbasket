"""Data providers for the look-through engine.

Matching an input ISIN to a fund, in order:

1. an issuer's own fund list (iShares UK; SPDR US and EMEA, including SPY);
2. a configured proxy (for funds with no free holdings, e.g. swap-based);
3. N-PORT: a holding with this ISIN, or with the CUSIP inside it, whose
   issuer LEI is a fund series' LEI;
4. OpenFIGI: the ISIN's US ticker, looked up in SEC's fund ticker list.

Holdings sources are tried in ``source_order``; see
:func:`fund_lookthrough.lookthrough.choose` for how one is picked.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from functools import lru_cache

import duckdb
import pandas as pd

from .identifiers import cusip_to_isin, is_valid_isin, isin_to_cusip, normalise_ticker
from .lookthrough import FundRef, Holdings, Line
from .sources import ishares, nport, spdr
from .sources.openfigi import OpenFIGI
from .sources.tickerinside_source import fetch_fund

logger = logging.getLogger(__name__)

SOURCES = ("issuer", "tickerinside", "nport", "top_holdings")


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
    ):
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
        self.issuer_cache_dir = issuer_cache_dir
        self.cusip_isin = cusip_isin or {}
        self.sedol_isin = sedol_isin or {}

        fm = fund_master.copy()
        self.series = fm.drop_duplicates("series_id").set_index("series_id")
        self.series_by_lei = fm.dropna(subset=["series_lei"]).drop_duplicates("series_lei").set_index("series_lei")["series_id"].to_dict()
        tickers = fm.dropna(subset=["ticker"])
        self.series_by_ticker = {normalise_ticker(t): s for t, s in zip(tickers["ticker"], tickers["series_id"])}
        self.tickers_by_series = tickers.groupby("series_id")["ticker"].apply(list).to_dict()
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

    def _issuer_ref(self, isin: str, via: str = "ishares") -> FundRef | None:
        if isin not in self.issuer_products.index:
            return None
        p = self.issuer_products.loc[isin]
        issuer = p.get("issuer") if isinstance(p.get("issuer"), str) else "ishares"
        return FundRef(
            series_id=None, ticker=p["ticker"], name=p["fund_name"], via=issuer,
            issuer_id=f"{issuer}-{p['site']}:{p['portfolio_id']}",
        )

    def resolve(self, isin: str) -> FundRef | None:
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
        cusip = isin_to_cusip(isin)
        rows = duckdb_rows(
            self.holdings_rel,
            "select issuer_lei, count(*) n from rel where (isin = ? or cusip = ?) and issuer_lei is not null "
            "group by 1 order by n desc",
            [isin, cusip or ""],
        )
        for lei, _ in rows:
            if lei in self.series_by_lei:
                return self.series_by_lei[lei]
        return None

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
        return Holdings("security_master", None, [line], source_ref=f"held in {n} N-PORT positions")

    # -- holdings -----------------------------------------------------------

    def holdings(self, ref: FundRef) -> list[Holdings]:
        out = []
        for source in self.source_order:
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

    @lru_cache(maxsize=None)
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
        return Holdings("spdr", got.as_of, lines, source_ref=f"SPDR holdings {got.url}")

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

    @lru_cache(maxsize=None)
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
        return Holdings("ishares", got.as_of, lines, source_ref=f"iShares holdings {got.url}")

    @lru_cache(maxsize=None)
    def _nport_lines(self, series_id: str) -> tuple[pd.DataFrame, str | None]:
        df = self.holdings_rel.filter(f"series_id = '{series_id}'").df()
        df = df.astype(object).where(df.notna(), None)
        as_of = pd.Timestamp(df["report_date"].iloc[0]).date().isoformat() if len(df) else None
        return df, as_of

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
            )
            for r in df.itertuples(index=False)
        ]
        accession = df["accession_number"].iloc[0]
        return Holdings("nport", as_of, lines, source_ref=f"SEC Form N-PORT {accession}")

    @lru_cache(maxsize=None)
    def _tickerinside(self, ticker: str | None):
        return fetch_fund(ticker) if ticker else None

    def _from_tickerinside(self, ref: FundRef) -> Holdings | None:
        tickers = [ref.ticker] if ref.ticker else []
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
            return Holdings("tickerinside", f.as_of, lines, source_ref=f.cite_as)
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


def duckdb_rows(rel: duckdb.DuckDBPyRelation, sql: str, params: list) -> list[tuple]:
    """Run ``sql`` against ``rel`` (named ``rel``) with positional parameters."""
    from .datasets import connection

    con = connection()
    con.register("rel", rel)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.unregister("rel")
