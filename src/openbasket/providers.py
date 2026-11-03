"""Data providers for the look-through engine.

Matching an input ISIN to a fund, in order:

1. a configured proxy (for funds with no free holdings, e.g. swap-based);
   a proxy overrides an issuer listing, so a swap ETF an issuer lists can
   still be shown through a physical stand-in;
2. an issuer's own fund list (iShares, SPDR and every registered issuer
   adapter, see :mod:`openbasket.sources.issuers`);
3. N-PORT: a holding with this ISIN, or with the CUSIP inside it, whose
   issuer LEI is a fund series' LEI. ISINs and CUSIPs that filings report
   under many different series LEIs are ignored here: they are placeholders
   or reporting errors, not one fund (a trust's or a mistyped LEI next to
   the series' own does not count against it);
4. OpenFIGI: the ISIN's US ticker, looked up in SEC's fund ticker list.

Holdings sources are tried in ``source_order``; see
:func:`openbasket.lookthrough.choose` for how one is picked. Each source's
outcome for a fund (``ok``, ``empty``, ``top10_only``, ``http_403``,
``parse_error`` ...) is kept, and :meth:`DataProviders.explain` turns the
first failure into a sentence for the fund's note.

In the public build (``mode="public"``) a ``local_only`` issuer's holdings
are never fetched: the issuer source answers ``local_only`` without a
request, the other sources (TickerInside, N-PORT) are still read, and when
none has the fund, :meth:`DataProviders.explain` says where its holdings
are: "Holdings for this fund are available from <issuer> when you run
openbasket yourself".

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
import requests

from .countries import country_code, country_label
from .datasets import connection
from .identifiers import cusip_to_isin, is_placeholder_cusip, is_valid_isin, isin_to_cusip, normalise_ticker
from .lookthrough import FundRef, Holdings, Line
from .sources import FULL_MODE, PUBLIC_MODE, ishares, nport, publish_mode, redistribution, spdr
from .sources.issuers import PUBLIC, FetchContext, IssuerAdapter, validate_holdings
from .sources.openfigi import OpenFIGI
from .sources.tickerinside_source import fetch_fund

logger = logging.getLogger(__name__)

SOURCES = ("issuer", "tickerinside", "nport", "top_holdings")

SERIES_RE = re.compile(r"^S\d{9}$")
NPORT_DATASETS_URL = "https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets"

#: Issuers read by code in this package rather than by a registered adapter.
BUILTIN_ISSUERS = {"ishares": "iShares", "spdr": "SPDR"}

#: Where the docs explain running openbasket on your own machine.
RUN_LOCALLY_URL = "https://openbasket.leithresearch.org/docs/running-locally.html"

#: Issuer types and asset categories that mean an N-PORT holding is a fund.
FUND_ASSET_CATS = ("STIV",)


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
        adapters: dict[str, IssuerAdapter] | None = None,
        issuer_series: dict[str, str] | None = None,
        max_issuer_leis: int = 3,
        mode: str = FULL_MODE,
        run_locally_url: str | None = None,
    ):
        """``stop_at_pct`` stops reading further sources once one resolves that
        much, which spares a universe run the network calls whose answer
        :func:`~openbasket.lookthrough.choose` would ignore anyway.
        ``tickerinside_tickers`` limits TickerInside requests to the tickers it
        lists in its issuer layer. ``adapters`` are the registered issuer
        adapters to read holdings with, by name. ``issuer_series`` maps an
        issuer fund ID to the N-PORT series it was matched to, so a fund
        reached both ways is one fund. An ISIN or CUSIP that N-PORT reports
        under more than ``max_issuer_leis`` fund series LEIs is not used to match
        a fund. ``mode`` is ``publish.mode``: ``"public"`` never reads a
        ``local_only`` issuer's holdings; ``run_locally_url`` is the page
        its funds' notes point to."""
        unknown = set(source_order) - set(SOURCES)
        if unknown:
            raise ValueError(f"Unknown sources {sorted(unknown)}; choose from {SOURCES}")
        self.holdings_rel = nport_holdings
        self.security_rel = security_master
        self.figi = figi
        self.source_order = list(source_order)
        self.proxies = {k.upper(): v for k, v in (proxies or {}).items()}
        ip = issuer_products.copy() if issuer_products is not None else pd.DataFrame(columns=["isin", "site", "portfolio_id"])
        if "issuer" not in ip:
            ip["issuer"] = "ishares"
        ip["issuer"] = ip["issuer"].fillna("ishares").astype(str).str.lower()
        ip = ip.astype(object).where(ip.notna(), None)
        self.issuer_products = ip.drop_duplicates("isin").set_index("isin")
        # One product row per issuer fund: the first share class listed, with
        # a product page borrowed from another class when it has none.
        self.issuer_by_id: dict[str, dict] = {}
        for r in ip.to_dict("records"):
            first = self.issuer_by_id.setdefault(issuer_id(r["issuer"], r["site"], r["portfolio_id"]), r)
            if not first.get("product_page_url") and r.get("product_page_url"):
                first["product_page_url"] = r["product_page_url"]
        self._iid_lower = {k.lower(): k for k in self.issuer_by_id}
        self.adapters = dict(adapters or {})
        self.issuer_series = dict(issuer_series or {})
        self.stop_at_pct = stop_at_pct
        self.tickerinside_tickers = {t.upper() for t in tickerinside_tickers} if tickerinside_tickers is not None else None
        self.max_issuer_leis = max_issuer_leis
        self.mode = publish_mode(mode)
        self.run_locally_url = run_locally_url or RUN_LOCALLY_URL
        self._fund_holding_index: tuple[dict, dict, list] | None = None
        self.issuer_cache_dir = issuer_cache_dir
        self.cusip_isin = {c: v for c, v in (cusip_isin or {}).items() if not is_placeholder_cusip(c)}
        self.sedol_isin = sedol_isin or {}
        #: fund key -> {source: outcome of the last read}, and fund key -> a plain-language reason.
        self._status: dict[str, dict[str, str]] = {}
        self._why: dict[str, str] = {}

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
        p = self.issuer_products.loc[isin].to_dict()
        p["isin"] = isin
        return self._issuer_ref_from(p)

    def _issuer_ref_from(self, p: dict) -> FundRef:
        iid = issuer_id(p.get("issuer"), p["site"], p["portfolio_id"])
        ticker = p.get("ticker") if isinstance(p.get("ticker"), str) else None
        note = None
        if p.get("replication") == "swap":
            note = "the issuer says this fund is swap-based: its holdings are a substitute basket, not what the fund tracks"
        series_id = self.issuer_series.get(iid)
        identity = _identity_only(p)
        if series_id is None and (is_us_product(p) or identity) and is_valid_isin(p.get("isin")):
            series_id = self._series_from_nport(p["isin"])
        if identity and series_id:
            # Only the fund's name and ISIN came from the issuer: an N-PORT series is the fund itself.
            return self._ref(series_id, None, "nport_lei")
        via = str(p.get("issuer") or "ishares")
        if identity and isinstance(p.get("identity_source"), str):
            via = p["identity_source"]  # the registry or list the name and ISIN came from
        return FundRef(series_id=series_id, ticker=ticker, name=p.get("fund_name"), via=via, issuer_id=iid, note=note)

    def resolve_ref(self, fund: str) -> FundRef | None:
        """A fund named by series ID or issuer ID; ``None`` if ``fund`` is neither or is unknown."""
        fund = fund.strip()
        if SERIES_RE.match(fund.upper()):
            sid = fund.upper()
            return self._ref(sid, None, "series_id") if sid in self.series.index else None
        p = self.issuer_by_id.get(fund) or self.issuer_by_id.get(self._iid_lower.get(fund.lower(), ""))
        if p is not None:
            return replace(self._issuer_ref_from(p), via="issuer_id")
        return None

    def proxy_ref(self, isin: str) -> FundRef | None:
        """The configured stand-in for ``isin``, or ``None``."""
        p = self.proxies.get(isin.strip().upper())
        if not p:
            return None
        note = f"proxy: {p.get('note', '')}".strip()
        if p.get("ticker"):
            return self._ref_from_ticker(p["ticker"], "proxy", note)
        if p.get("isin"):
            inner = self.resolve(p["isin"])
            return replace(inner, via="proxy", note=note) if inner else None
        return None

    def resolve(self, isin: str) -> FundRef | None:
        ref = self.resolve_ref(isin)
        if ref is not None:
            return ref
        isin = isin.strip().upper()
        ref = self.proxy_ref(isin)
        if ref is not None:
            return ref
        ref = self._issuer_ref(isin)
        if ref is not None:
            return ref
        if not is_valid_isin(isin):
            logger.warning("%s is not a valid ISIN", isin)
            return None
        series_id = self._series_from_nport(isin)
        if series_id:
            return self._ref(series_id, None, "nport_lei")
        if self.figi is not None:
            listings = self.figi.map("ID_ISIN", [isin]).get(isin, [])
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
        Placeholder CUSIPs, and ISINs and CUSIPs reported under more than
        ``max_issuer_leis`` different series LEIs, are left out. Holdings
        filed under any other LEI (a trust's, a typo) are not counted.
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
            # Ambiguity counts series LEIs only: the same fund is often also filed
            # under its trust's LEI or a mistyped one, which says nothing about
            # which series it is.
            n = int(self.max_issuer_leis)
            leis_isin: dict[str, set[str]] = {}
            leis_cusip: dict[str, set[str]] = {}
            for isin, cusip, lei, _ in rows:
                if isin:
                    leis_isin.setdefault(isin, set()).add(lei)
                if cusip:
                    leis_cusip.setdefault(cusip, set()).add(lei)
            amb_isin = {k for k, v in leis_isin.items() if len(v) > n}
            amb_cusip = {k for k, v in leis_cusip.items() if len(v) > n}
            if amb_isin or amb_cusip:
                logger.info("Fund matching ignores %d ISINs and %d CUSIPs reported under more than %d series LEIs",
                            len(amb_isin), len(amb_cusip), n)
            by_isin: dict[str, list[int]] = {}
            by_cusip: dict[str, list[int]] = {}
            groups = []
            for i, (isin, cusip, lei, n_rows) in enumerate(rows):
                groups.append((lei, n_rows))
                if isin and isin not in amb_isin:
                    by_isin.setdefault(isin, []).append(i)
                if cusip and cusip not in amb_cusip and not is_placeholder_cusip(cusip):
                    by_cusip.setdefault(cusip, []).append(i)
            self._fund_holding_index = (by_isin, by_cusip, groups)
        return self._fund_holding_index

    def resolve_underlying(self, isin: str) -> FundRef | None:
        """The fund a derivative references, from local data only (proxies, issuer lists, N-PORT)."""
        isin = isin.strip().upper()
        ref = self.proxy_ref(isin) or self._issuer_ref(isin)
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
        """A stock or bond as a one-line list at 100%.

        An ISIN that N-PORT filings list as a fund (issuer type RF or PF, or
        a short-term investment vehicle) is not a security: it comes back
        with ``is_fund`` set, no weight and a note, instead of as 100% of
        itself.
        """
        isin = isin.strip().upper()
        rows = duckdb_rows(
            self.security_rel,
            "select isin, issuer_name, title, issuer_lei, cusip, ticker, asset_cat, isin_source, n_positions, "
            "issuer_type, country from rel where isin = ?",
            [isin],
        )
        if not rows:
            return None
        isin, issuer, title, lei, cusip, ticker, cat, src, n, itype, country = rows[0]
        name = issuer if cat in ("EC", "EP") else (title or issuer)
        if itype in nport.FUND_ISSUER_TYPES or cat in FUND_ASSET_CATS:
            kind = "a money market or short-term fund" if cat in FUND_ASSET_CATS else (
                "a private fund" if itype == "PF" else "a registered fund")
            note = (f"N-PORT filings list this ISIN as {kind}, but no source openbasket reads publishes its holdings; "
                    "it is not shown as a security")
            line = Line(weight_pct=0.0, isin=isin, name=issuer or title, issuer_name=issuer, issuer_lei=lei,
                        asset_class="fund", isin_source=src)
            return Holdings("security_master", None, [line], source_ref=f"held in {n} N-PORT positions",
                            source_url=NPORT_DATASETS_URL, is_fund=True, note=note)
        line = Line(
            weight_pct=100.0, isin=isin, name=name,
            issuer_name=issuer, issuer_lei=lei, title=title, cusip=cusip, ticker=ticker,
            asset_class=nport.ASSET_CLASS.get(cat, cat), isin_source=src, country=country_label(country),
        )
        return Holdings("security_master", None, [line], source_ref=f"held in {n} N-PORT positions",
                        source_url=NPORT_DATASETS_URL)

    # -- holdings -----------------------------------------------------------

    def holdings(self, ref: FundRef) -> list[Holdings]:
        out = []
        withheld = self.local_only_issuer(ref)
        if withheld:
            # Recorded whether or not "issuer" is in source_order, so the note can say where the holdings are.
            self._status.setdefault(ref.key, {})["issuer"] = "local_only"
            self._why.setdefault(ref.key, self._local_only_note(withheld))
        for source in self.source_order:
            if self.stop_at_pct is not None and out and out[-1].resolved_pct >= self.stop_at_pct:
                break
            h, status = None, None
            if source == "issuer" and ref.issuer_id and not withheld:
                h, status, why = self._read_issuer(ref.issuer_id)
                if why:
                    self._why.setdefault(ref.key, why)
            elif source == "tickerinside":
                h = self._from_tickerinside(ref)
                status = None if h is None else "ok"
            elif source == "nport" and ref.series_id:
                h = self._from_nport(ref.series_id)
                status = "ok" if h is not None else "empty"
            # "top_holdings" has no client yet and is skipped.
            if status is not None:
                self._status.setdefault(ref.key, {})[source] = "empty" if status == "ok" and not (h and h.lines) else status
            if h is not None and h.lines:
                out.append(h)
        return out

    @property
    def public(self) -> bool:
        return self.mode == PUBLIC_MODE

    def issuer_redistribution(self, issuer: str | None) -> str:
        """The redistribution class of an issuer's holdings: its adapter's, else its module's."""
        name = str(issuer or "ishares").lower()
        a = self.adapters.get(name)
        if a is not None:
            return a.redistribution
        return redistribution(name)

    def local_only_issuer(self, ref: FundRef) -> str | None:
        """In the public build, the name of the ``local_only`` issuer whose list ``ref`` is on; else ``None``."""
        if not self.public or not ref.issuer_id:
            return None
        p = self.issuer_by_id.get(ref.issuer_id)
        if p is None:
            return None
        issuer = str(p.get("issuer") or "ishares")
        return self._title(issuer) if self.issuer_redistribution(issuer) != PUBLIC else None

    def _local_only_note(self, title: str) -> str:
        return (f"Holdings for this fund are available from {title} when you run openbasket yourself "
                f"(see {self.run_locally_url})")

    def source_status(self, ref: FundRef) -> str | None:
        """``source=outcome`` for every source read for ``ref``, e.g. ``issuer=top10_only; nport=ok``."""
        return "; ".join(f"{s}={st}" for s, st in self._status.get(ref.key, {}).items()) or None

    def explain(self, ref: FundRef) -> str | None:
        """Why ``ref`` has no holdings, in plain language, when a source said so."""
        if ref.key in self._why:
            return self._why[ref.key]
        if self._status.get(ref.key, {}).get("nport") == "empty":
            return "the fund's latest N-PORT filing lists no holdings"
        return None

    def _product_page(self, iid: str) -> str | None:
        page = (self.issuer_by_id.get(iid) or {}).get("product_page_url")
        return page if isinstance(page, str) and page else None

    def _title(self, issuer: str) -> str:
        a = self.adapters.get(issuer)
        return a.title if a else BUILTIN_ISSUERS.get(issuer, issuer)

    def _from_issuer(self, iid: str) -> Holdings | None:
        """One issuer fund's holdings, read by the issuer's own code or its registered adapter; may raise."""
        p = self.issuer_by_id.get(iid)
        if p is None:
            return None
        issuer = str(p.get("issuer") or "ishares")
        if self.public and self.issuer_redistribution(issuer) != PUBLIC:
            raise LocalOnly(self._title(issuer), self._local_only_note(self._title(issuer)))
        if _identity_only(p):
            raise AdapterOff(f"{self._title(issuer)} lists this fund, but only its name and ISIN are known here")
        if issuer == "spdr":
            return self._from_spdr(iid, p)
        if issuer == "ishares":
            return self._from_ishares(iid, p)
        if issuer in self.adapters:
            return self._from_adapter(iid, p, self.adapters[issuer])
        raise AdapterOff(f"{self._title(issuer)} lists this fund, but its adapter is not enabled")

    @lru_cache(maxsize=512)
    def _read_issuer(self, iid: str) -> tuple[Holdings | None, str, str | None]:
        """``(holdings, status, plain-language reason)`` for one issuer fund; never raises."""
        p = self.issuer_by_id.get(iid)
        if p is None:
            return None, "not_listed", None
        title = self._title(str(p.get("issuer") or "ishares"))
        try:
            h = self._from_issuer(iid)
        except LocalOnly as exc:
            return None, "local_only", exc.plain
        except AdapterOff as exc:
            return None, "adapter_off", str(exc)
        except Exception as exc:  # a broken issuer file must not stop the run
            status = _failure_status(exc)
            logger.warning("%s %s: %s", title, iid, exc)
            why = getattr(exc, "plain", None) or _plain_failure(title, status)
            return None, status, why
        if h is None or not h.lines:
            return h, "empty", f"{title} lists this fund but its holdings file has no lines"
        return h, "ok", None

    def _from_spdr(self, iid: str, p: dict) -> Holdings | None:
        url = p.get("holdings_url")
        if not url:
            return None
        got = spdr.holdings(url, cache_dir=self.issuer_cache_dir)
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
                    country=country_label(r.get("country")), sector=_text(r.get("sector")),
                )
            )
        return Holdings("spdr", got.as_of, lines, source_ref=f"SPDR holdings {got.url}",
                        source_url=self._product_page(iid) or got.url)

    def _issuer_isins(self, ids: list[tuple[str | None, str | None, str | None]]) -> list[tuple[str | None, str | None]]:
        """ISIN and how it was found for each ``(isin, cusip, sedol)`` of an issuer file.

        Issuers use CUSIP-shaped internal codes for some securities (SPDR does
        for foreign shares), so an ISIN derived from a CUSIP is kept only if
        OpenFIGI knows it. When OpenFIGI cannot be asked, derived ISINs are
        left out.
        """
        found = [self._isin_for(*x) for x in ids]
        derived = [i for i, how in found if how == "derived_cusip"]
        known = set()
        if derived and self.figi is not None:
            known = {i for i, rows in self.figi.map("ID_ISIN", derived).items() if rows}
        return [(None, None) if how == "derived_cusip" and isin not in known else (isin, how) for isin, how in found]

    def _isin_for(self, isin: str | None, cusip: str | None, sedol: str | None) -> tuple[str | None, str | None]:
        """Best ISIN for an issuer line, and how it was found."""
        if isin and is_valid_isin(isin):
            return isin.strip().upper(), "reported"
        if cusip and is_placeholder_cusip(cusip):
            cusip = None
        if cusip and cusip in self.cusip_isin:
            return self.cusip_isin[cusip][0], "nport_cusip"
        if sedol and str(sedol).upper() in self.sedol_isin:
            return self.sedol_isin[str(sedol).upper()], "nport_sedol"
        derived = cusip_to_isin(cusip) if cusip else None
        if derived:
            return derived, "derived_cusip"
        return None, None

    def _from_ishares(self, iid: str, p: dict) -> Holdings | None:
        site, portfolio_id = str(p["site"]).lower(), str(p["portfolio_id"])
        got = ishares.holdings(portfolio_id, site, cache_dir=self.issuer_cache_dir)
        if len(got.holdings) >= 20000:
            logger.warning("iShares %s returned %d lines, possibly capped by the site", iid, len(got.holdings))
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
                country=country_label(r.get("countryOfRisk") or r.get("location")),
                sector=_text(r.get("sectorName") or r.get("sector")),
            )
            for r, (isin, how) in zip(records, found)
        ]
        return Holdings("ishares", got.as_of, lines, source_ref=f"iShares holdings {got.url}",
                        source_url=self._product_page(iid) or got.url)

    def _from_adapter(self, iid: str, p: dict, adapter: IssuerAdapter) -> Holdings | None:
        """Holdings from a registered adapter, with CUSIPs and SEDOLs turned into ISINs as for iShares and SPDR."""
        product = {k: v for k, v in p.items() if not (isinstance(v, float) and pd.isna(v))}
        got = adapter.holdings(product, FetchContext(cache_dir=self.issuer_cache_dir))
        df = got.holdings
        problems = validate_holdings(df)
        if problems:
            logger.warning("%s %s holdings: %s", adapter.title, iid, "; ".join(problems))
        for note in got.notes or []:
            logger.info("%s %s: %s", adapter.title, iid, note)
        df = df.astype(object).where(df.notna(), None)
        records = df.to_dict("records")
        found = self._issuer_isins([(r.get("isin"), r.get("cusip"), r.get("sedol")) for r in records])
        lines = []
        for r, (isin, how) in zip(records, found):
            name = r.get("name") or ""
            ac = (r.get("asset_class") or None)
            ac = ac.lower() if isinstance(ac, str) else None
            lines.append(
                Line(
                    weight_pct=float(r.get("weight_pct") or 0.0),
                    isin=isin, name=r.get("name"), issuer_name=r.get("issuer_name"), ticker=r.get("ticker"),
                    cusip=r.get("cusip"), asset_class=ac, isin_source=how,
                    fund_hint=ac in ("fund", "money market") or " ETF" in name.upper(),
                    country=country_label(r.get("country")), sector=_text(r.get("sector")),
                )
            )
        return Holdings(adapter.name, got.as_of, lines, source_ref=f"{adapter.title} holdings {got.url}",
                        source_url=self._product_page(iid) or got.url)

    @lru_cache(maxsize=1024)
    def _nport_lines(self, series_id: str) -> tuple[pd.DataFrame, str | None]:
        # nport_holdings is sorted by series_id, so DuckDB reads only the row groups that hold it.
        df = self.holdings_rel.filter(f"series_id = '{series_id}'").df()
        df = df.astype(object).where(df.notna(), None)
        if len(df) and "isin_source" in df and "cusip" in df:
            # An ISIN another filing reported next to a placeholder CUSIP is not this line's.
            bad = df["cusip"].map(is_placeholder_cusip) & (df["isin_source"] != "reported")
            df.loc[bad, "isin"] = None
            df.loc[bad, "isin_source"] = None
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
                country=country_label(getattr(r, "country", None)),
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
        # TickerInside covers US ETFs by US ticker. A fund listed by a non-US
        # issuer site is never sent there: its local ticker can be a
        # different US fund's ticker, and that fund's holdings would win.
        if ref.issuer_id and not is_us_product(self.issuer_by_id.get(ref.issuer_id) or {}):
            return None
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


class AdapterOff(RuntimeError):
    """A fund is listed by an issuer whose adapter is not enabled."""


class LocalOnly(RuntimeError):
    """The public build may not read this issuer's holdings: its terms restrict republication.

    ``issuer`` is the issuer's name and ``plain`` the sentence for the fund's note.
    """

    def __init__(self, issuer: str, plain: str):
        super().__init__(plain)
        self.issuer = issuer
        self.plain = plain


def _identity_only(p: dict) -> bool:
    """True for an ``issuer_products`` row that carries only a fund's identity (the public build)."""
    return p.get("identity_only") in (True,)


def _text(v: object) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip()
    return None if s in ("", "-", "--", "N/A", "n/a", "Unassigned", "nan") else s


def _failure_status(exc: BaseException) -> str:
    """A short code for why an issuer file could not be read."""
    if isinstance(exc, ishares.NoFullHoldings) or getattr(exc, "plain", None):
        return "top10_only" if isinstance(exc, ishares.NoFullHoldings) else "unavailable"
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"http_{exc.response.status_code}"
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return "network_error"
    if isinstance(exc, (ValueError, KeyError, IndexError, TypeError)):
        return "parse_error"
    return "error"


def _plain_failure(title: str, status: str) -> str:
    if status.startswith("http_"):
        return f"{title} lists this fund, but its holdings could not be downloaded (HTTP {status[5:]})"
    if status == "network_error":
        return f"{title} lists this fund, but its holdings could not be downloaded (the site did not answer)"
    if status == "parse_error":
        return f"{title} lists this fund, but its holdings file could not be read"
    return f"{title} lists this fund, but no holdings were available"


def is_us_product(p: dict) -> bool:
    """True for a fund listed on an issuer's US site or domiciled in the US."""
    return str(p.get("site") or "").lower() == "us" or country_code(p.get("domicile")) == "US"


def issuer_id(issuer: str | None, site: object, portfolio_id: object) -> str:
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
