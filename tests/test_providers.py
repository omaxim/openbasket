"""DataProviders on small hand-made tables: match order, fund ISINs, placeholders, TickerInside and issuer failures."""

import pandas as pd
import pytest
import requests

from openbasket.datasets import connection
from openbasket.lookthrough import FundRef, Line, look_through
from openbasket.providers import DataProviders
from openbasket.sources import ishares

FUND_MASTER = pd.DataFrame(
    [
        {"series_id": "S000000001", "series_name": "US Equity Fund", "series_lei": "LEI-EQ", "ticker": "IGLD", "has_nport": True, "cik": None},
        {"series_id": "S000000002", "series_name": "Large Cap Index Portfolio", "series_lei": "LEI-LC", "ticker": None, "has_nport": True, "cik": None},
        {"series_id": "S000000004", "series_name": "Other Fund", "series_lei": "LEI-OT", "ticker": None, "has_nport": True, "cik": None},
    ]
)

PRODUCTS = pd.DataFrame(
    [
        # A UCITS ETC whose local ticker is also a US fund's ticker.
        {"issuer": "ishares", "site": "uk", "portfolio_id": "1", "isin": "IE00B4ND3602", "ticker": "IGLD",
         "fund_name": "iShares Physical Gold ETC", "domicile": "Ireland"},
        # A swap ETF the issuer lists.
        {"issuer": "ishares", "site": "uk", "portfolio_id": "2", "isin": "IE000OY7BDY9", "ticker": "NASW",
         "fund_name": "iShares MSCI North America Swap UCITS ETF", "domicile": "Ireland", "replication": "swap"},
    ]
)


def _line(series, isin, cusip, lei, issuer, w=1.0, src="reported", itype="CORP", cat="EC"):
    return {"series_id": series, "report_date": pd.Timestamp("2026-07-31"), "accession_number": "0001-26-1",
            "isin": isin, "cusip": cusip, "issuer_name": issuer, "issuer_title": issuer, "issuer_lei": lei,
            "weight_pct": w, "asset_cat": cat, "issuer_type": itype, "reported_ticker": None, "isin_source": src,
            "country": "US"}


def nport_holdings():
    rows = [
        _line("S000000001", "US0378331005", "037833100", "L-AAPL", "Apple Inc", 60.0),
        # The real share class of the Large Cap portfolio, held by another fund.
        _line("S000000004", "US0000000LC4", "00000LC04", "LEI-LC", "Large Cap Index Portfolio", 5.0, itype="RF"),
    ]
    # A money market sweep line, as dozens of filers report it: CUSIP 000000000,
    # an ISIN copied in from one filing, and a different issuer each time; the
    # Large Cap LEI is one of them.
    for i in range(6):
        rows.append(_line("S000000004", "IE000S9YS762", "000000000", f"LEI-MM{i}" if i else "LEI-LC",
                          f"Cash sweep {i}", 1.0, src="reported_for_cusip", itype="RF", cat="STIV"))
    return connection().from_df(pd.DataFrame(rows))


def security_master():
    return connection().from_df(pd.DataFrame(
        [
            {"isin": "US0378331005", "issuer_name": "Apple Inc", "title": "Apple Inc", "issuer_lei": "L-AAPL", "cusip": "037833100",
             "ticker": "AAPL", "asset_cat": "EC", "isin_source": "reported", "n_positions": 900, "issuer_type": "CORP", "country": "US"},
            {"isin": "LU0176039062", "issuer_name": "JPM USD Liquidity LVNAV", "title": "JPM USD Liquidity", "issuer_lei": "L-JPM",
             "cusip": None, "ticker": None, "asset_cat": "STIV", "isin_source": "reported", "n_positions": 40, "issuer_type": "RF",
             "country": "LU"},
            {"isin": "IE00BK5BQT80", "issuer_name": "Vanguard FTSE All-World UCITS ETF", "title": None, "issuer_lei": "L-VWRA",
             "cusip": None, "ticker": None, "asset_cat": "EC", "isin_source": "reported", "n_positions": 12, "issuer_type": "RF",
             "country": "IE"},
        ]
    ))


def providers(**kw):
    return DataProviders(
        nport_holdings(), security_master(), FUND_MASTER, pd.DataFrame(columns=["ticker", "isin"]), kw.pop("figi", None),
        source_order=kw.pop("source_order", ["issuer", "tickerinside", "nport"]), issuer_products=PRODUCTS, **kw,
    )


def test_placeholder_cusip_isin_does_not_name_a_fund():
    p = providers()
    # Reported under six issuer LEIs next to CUSIP 000000000: not one fund's share class.
    assert p._series_from_nport("IE000S9YS762") is None
    assert p._series_from_nport("US0000000LC4") == "S000000002"


def test_placeholder_cusip_isin_is_dropped_from_nport_lines():
    lines = providers()._from_nport("S000000004").lines
    assert {l.isin for l in lines} == {"US0000000LC4", None}
    assert providers().resolve_line(Line(1.0, None, cusip="000000000", fund_hint=True)) is None


def test_placeholder_cusips_are_not_mapped():
    p = providers(cusip_isin={"000000000": ("IE000S9YS762", "reported_for_cusip"), "037833100": ("US0378331005", "reported")})
    assert "000000000" not in p.cusip_isin
    assert p._isin_for(None, "000000000", None) == (None, None)
    assert p._isin_for(None, "037833100", None) == ("US0378331005", "nport_cusip")


def test_fund_isin_is_not_a_security():
    for isin in ("LU0176039062", "IE00BK5BQT80"):
        sec = providers().security(isin)
        assert sec.is_fund and sec.note
        r = look_through(isin, providers())
        s = r.summary
        assert s["matched"] and s["matched_via"] == "security_master" and s["fund_as_security"]
        assert s["resolved_pct"] == 0 and s["unresolved_pct"] == 100 and s["status"] == "no_holdings"
        assert "fund" in s["note"] and r.leaves == []
    stock = look_through("US0378331005", providers()).summary
    assert stock["resolved_pct"] == 100 and stock["status"] == "ok" and not stock["fund_as_security"]


def test_tickerinside_never_asked_for_a_non_us_issuer_fund(monkeypatch):
    asked = []
    monkeypatch.setattr("openbasket.providers.fetch_fund", lambda t: asked.append(t))
    monkeypatch.setattr(DataProviders, "_from_issuer", lambda self, iid: None)
    p = providers()
    ref = p.resolve("IE00B4ND3602")
    assert ref.issuer_id == "ishares-uk:1" and ref.ticker == "IGLD"
    # Even with the colliding US series attached, TickerInside is not asked.
    p.holdings(ref)
    p.holdings(FundRef("S000000001", "IGLD", "x", "ishares", issuer_id="ishares-uk:1"))
    assert asked == []
    p.holdings(FundRef("S000000001", "IGLD", "x", "series_id"))
    assert asked == ["IGLD"]


def test_proxy_overrides_issuer_listing():
    p = providers(proxies={"IE000OY7BDY9": {"ticker": "IGLD", "note": "physical stand-in"}})
    ref = p.resolve("IE000OY7BDY9")
    assert ref.via == "proxy" and ref.series_id == "S000000001" and "stand-in" in ref.note
    assert p.proxy_ref("IE000OY7BDY9").via == "proxy"
    swap = providers().resolve("IE000OY7BDY9")
    assert swap.via == "ishares" and "swap-based" in swap.note


@pytest.mark.parametrize(
    "exc, status, words",
    [
        (ishares.NoFullHoldings("only ten-largest"), "top10_only", "ten largest holdings"),
        (requests.HTTPError(response=type("R", (), {"status_code": 403})()), "http_403", "HTTP 403"),
        (ValueError("bad json"), "parse_error", "could not be read"),
        (requests.ConnectionError("down"), "network_error", "did not answer"),
    ],
)
def test_issuer_failure_is_a_status_and_a_plain_note(monkeypatch, exc, status, words):
    def broken(self, iid):
        raise exc

    monkeypatch.setattr(DataProviders, "_from_issuer", broken)
    p = providers(source_order=["issuer", "nport"])
    ref = FundRef(None, None, "Gold", "ishares", issuer_id="ishares-uk:1")
    assert p.holdings(ref) == []
    assert p.source_status(ref) == f"issuer={status}"
    assert words in p.explain(ref)
    r = look_through("IE00B4ND3602", p, ref=ref)
    assert r.summary["status"] == "no_holdings" and words in r.summary["note"]


def test_issuer_failure_falls_back_to_nport(monkeypatch):
    def broken(self, iid):
        raise requests.HTTPError(response=type("R", (), {"status_code": 503})())

    monkeypatch.setattr(DataProviders, "_from_issuer", broken)
    p = providers(source_order=["issuer", "nport"])
    ref = FundRef("S000000001", None, "US Equity Fund", "ishares", issuer_id="ishares-uk:1")
    assert [h.source for h in p.holdings(ref)] == ["nport"]
    assert p.source_status(ref) == "issuer=http_503; nport=ok"


def test_issuer_ids_with_hyphens_resolve():
    prods = pd.DataFrame([{"issuer": "bnp-easy", "site": "en-gb", "portfolio_id": "F123", "isin": "LU1291098827",
                           "fund_name": "BNP Paribas Easy S&P 500", "domicile": "LU"}])
    p = DataProviders(nport_holdings(), security_master(), FUND_MASTER, pd.DataFrame(columns=["ticker", "isin"]), None,
                      source_order=["issuer"], issuer_products=prods)
    assert p.resolve("LU1291098827").issuer_id == "bnp-easy-en-gb:F123"
    assert p.resolve("LU1291098827").via == "bnp-easy"
    assert p.resolve_ref("bnp-easy-en-gb:F123").via == "issuer_id"
