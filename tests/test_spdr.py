import io

import openpyxl
import pandas as pd
import pytest

from fund_lookthrough.lookthrough import look_through
from fund_lookthrough.providers import DataProviders
from fund_lookthrough.sources import spdr


def _xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "holdings"
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


US_FILE = _xlsx(
    [
        ["Fund Name:", "SPDR S&P 500 ETF Trust"],
        ["Ticker Symbol:", "SPY"],
        ["Holdings:", "As of 02-Nov-2026"],
        [],
        ["Name", "Ticker", "Identifier", "SEDOL", "Weight", "Sector", "Shares Held", "Local Currency"],
        ["APPLE INC", "AAPL", "037833100", "2046251", 60.0, "-", 1, "USD"],
        ["SAMSUNG ELECTR GDR REG S", "SMSN", "494281900", "4942818", 30.0, "-", 1, "USD"],
        ["ASML HOLDING NV", "ASML", "INTERNAL1", "B929F46", 9.5, "-", 1, "EUR"],
        ["US DOLLAR", "CASH_USD", "-", "-", 0.5, "-", 1, "USD"],
        [],
        ["Before investing, consider the fund's objectives."],
    ]
)

EMEA_FILE = _xlsx(
    [
        ["Fund Name:", "SPDR S&P 500 UCITS ETF"],
        ["ISIN:", "IE00B6YX5C33"],
        ["Ticker Symbol:", "SPY5 GY"],
        ["Holdings As Of:", "02-Nov-2026"],
        [],
        ["ISIN", "SEDOL", "Security Name", "Currency", "Number of Shares", "Percent of Fund"],
        ["US0378331005", "2046251", "Apple Inc.", "USD", 1, 99.9],
        ["Unassigned", "Unassigned", "U.S. Dollar", "USD", 1, "-"],
        [],
        ["Marketing communication"],
    ]
)


def test_parse_us_file():
    as_of, df = spdr.parse_holdings(US_FILE)
    assert as_of == "2026-11-02"
    assert len(df) == 4
    assert df["cusip"].tolist()[:2] == ["037833100", "494281900"]
    assert pd.isna(df.loc[2, "cusip"])  # internal code, not a CUSIP
    assert df["weight_pct"].sum() == pytest.approx(100.0)
    assert df["isin"].isna().all()


def test_parse_emea_file():
    as_of, df = spdr.parse_holdings(EMEA_FILE)
    assert as_of == "2026-11-02"
    assert df["isin"].tolist()[0] == "US0378331005"
    assert pd.isna(df.loc[1, "isin"]) and pd.isna(df.loc[1, "weight_pct"])


def test_parse_fund_finder():
    payload = {"data": {"funds": {"etfs": {"datas": [
        {"fundName": "SPDR S&P 500 ETF Trust", "fundFilter": "SPY", "fundUri": "/us/en/spy",
         "keywords": "SPDR S&P 500 ETF Trust, SPY, US78462F1030, 78462F103",
         "documentPdf": [{"docType": "Holdings", "docs": [{"path": "/library-content/x/holdings-daily-us-en-spy.xlsx"}]}]},
        {"fundName": "No file", "fundFilter": "XXX", "keywords": "US78462F1030", "documentPdf": []},
    ]}}}}
    df = spdr.parse_fund_finder(payload, "us")
    assert df[["isin", "portfolio_id", "ticker"]].values.tolist() == [["US78462F1030", "us-en-spy", "SPY"]]
    assert df["holdings_url"].iloc[0].endswith("holdings-daily-us-en-spy.xlsx")


class FakeFigi:
    def __init__(self, known):
        self.known = known

    def map(self, id_type, values, exch_code=None):
        return {v: ([{"name": "x"}] if v in self.known else []) for v in values}


def _providers(monkeypatch, figi):
    from fund_lookthrough.datasets import connection

    empty = connection().sql(
        "select null::varchar as isin, null::varchar as cusip, null::varchar as issuer_lei, null::varchar as series_id where false"
    )
    products = pd.DataFrame([{
        "issuer": "spdr", "site": "us", "portfolio_id": "us-en-spy", "isin": "US78462F1030", "ticker": "SPY",
        "fund_name": "SPDR S&P 500 ETF Trust", "holdings_url": "https://example.invalid/holdings-daily-us-en-spy.xlsx",
    }])
    monkeypatch.setattr(spdr, "holdings", lambda url, cache_dir=None: spdr.IssuerHoldings(*spdr.parse_holdings(US_FILE), url=url))
    return DataProviders(
        empty, empty, pd.DataFrame(columns=["series_id", "series_name", "series_lei", "ticker"]),
        pd.DataFrame(columns=["ticker", "isin"]), figi, source_order=["issuer"], issuer_products=products,
        cusip_isin={"037833100": ("US0378331005", "reported_for_cusip")},
        sedol_isin={"B929F46": "NL0010273215"},
    )


def test_spdr_isin_order_and_derived_check(monkeypatch):
    # Samsung's CUSIP-shaped code derives an ISIN that OpenFIGI does not know.
    r = look_through("US78462F1030", _providers(monkeypatch, FakeFigi(known=set())))
    by = {x["name"]: (x["constituent_isin"], x["isin_source"]) for x in r.leaves}
    assert by["APPLE INC"] == ("US0378331005", "nport_cusip")
    assert by["ASML HOLDING NV"] == ("NL0010273215", "nport_sedol")
    assert "SAMSUNG ELECTR GDR REG S" not in by
    assert r.summary["source"] == "spdr" and r.summary["matched_via"] == "spdr"
    assert r.summary["resolved_pct"] == pytest.approx(69.5)
    assert {x["name"]: x["asset_class"] for x in r.unresolved}["US DOLLAR"] == "cash"


def test_spdr_derived_isin_kept_when_openfigi_knows_it(monkeypatch):
    from fund_lookthrough.identifiers import cusip_to_isin

    figi = FakeFigi(known={cusip_to_isin("494281900")})
    r = look_through("US78462F1030", _providers(monkeypatch, figi))
    assert r.summary["resolved_pct"] == pytest.approx(99.5)


@pytest.mark.network
def test_live_spdr_us():
    p = spdr.products("us")
    url = p.loc[p["ticker"] == "SPY", "holdings_url"].iloc[0]
    got = spdr.holdings(url)
    assert got.holdings["weight_pct"].sum() == pytest.approx(100, abs=1)
    assert got.holdings["cusip"].notna().mean() > 0.95
