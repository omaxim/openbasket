"""Universe building, batch robustness and fund refs, on tiny hand-made tables."""

import time

import pandas as pd
import pytest

from openbasket import throttle
from openbasket.datasets import connection
from openbasket.lookthrough import FundRef, Holdings, Line
from openbasket.pipelines.universe.nodes import build_universe, expand_fund, run_universe, select_universe
from openbasket.providers import DataProviders

PRODUCTS = pd.DataFrame(
    [
        # Two share classes of one UCITS portfolio; the larger one names the fund.
        {"issuer": "ishares", "site": "uk", "portfolio_id": "253743", "isin": "IE00B5BMR087", "ticker": "CSPX",
         "fund_name": "iShares Core S&P 500 UCITS ETF", "total_net_assets": 9e10, "product_page_url": "https://ishares.example/cspx"},
        {"issuer": "ishares", "site": "uk", "portfolio_id": "253743", "isin": "IE00BYX5MX67", "ticker": "CSPX2",
         "fund_name": "iShares Core S&P 500 UCITS ETF (Dist)", "total_net_assets": 1e9, "product_page_url": None},
        # US ETFs that also file N-PORT: IVV by ticker, AGG by ISIN.
        {"issuer": "ishares", "site": "us", "portfolio_id": "239726", "isin": "US4642872000", "ticker": "IVV",
         "fund_name": "iShares Core S&P 500 ETF", "total_net_assets": 7e11, "product_page_url": "https://ishares.example/ivv"},
        {"issuer": "ishares", "site": "us", "portfolio_id": "239458", "isin": "US4642872265", "ticker": "AGG.X",
         "fund_name": "iShares Core U.S. Aggregate Bond ETF", "total_net_assets": 1e11, "product_page_url": None},
        # A UCITS ticker that happens to equal a US mutual fund's ticker must not merge.
        {"issuer": "spdr", "site": "emea", "portfolio_id": "emea-en-abcdx", "isin": "IE00B6YX5C33", "ticker": "ABCDX",
         "fund_name": "SPDR S&P 500 UCITS ETF", "total_net_assets": None, "product_page_url": None,
         "holdings_url": "https://spdr.example/abcdx.xlsx"},
        # Two share classes with their own portfolio ids but one name: one fund.
        {"issuer": "ishares", "site": "uk", "portfolio_id": "287338", "isin": "IE00BYXYYJ35", "ticker": "LQDA",
         "fund_name": "iShares $ Corp Bond UCITS ETF", "total_net_assets": 5.4e9, "domicile": "Ireland"},
        {"issuer": "ishares", "site": "uk", "portfolio_id": "251832", "isin": "IE0032895942", "ticker": "LQDE",
         "fund_name": "iShares\u00a0$ Corp Bond UCITS ETF", "total_net_assets": 3.7e9, "domicile": "Ireland"},
        # A US ETF whose N-PORT series has no ticker and no known ISIN: matched by name.
        {"issuer": "ishares", "site": "us", "portfolio_id": "330000", "isin": "US09290C8064", "ticker": "THRO",
         "fund_name": "iShares U.S. Thematic Rotation Active ETF", "total_net_assets": 5e9, "domicile": "United States"},
    ]
).assign(listed_on="2026-11-03")

FUND_MASTER = pd.DataFrame(
    [
        {"series_id": "S000004310", "series_name": "iShares Core S&P 500 ETF", "series_lei": "LEI-IVV", "ticker": "IVV", "has_nport": True, "cik": "1100663"},
        {"series_id": "S000004362", "series_name": "iShares Core U.S. Aggregate Bond ETF", "series_lei": "LEI-AGG", "ticker": "AGG", "has_nport": True, "cik": None},
        {"series_id": "S000000002", "series_name": "Some Mutual Fund", "series_lei": "LEI-MF", "ticker": "ABCDX", "has_nport": True, "cik": None},
        {"series_id": "S000000002", "series_name": "Some Mutual Fund", "series_lei": "LEI-MF", "ticker": "ABCEX", "has_nport": True, "cik": None},
        {"series_id": "S000000003", "series_name": "Private Feeder", "series_lei": None, "ticker": None, "has_nport": True, "cik": None},
        {"series_id": "S000000009", "series_name": "No Filing Yet", "series_lei": "LEI-NF", "ticker": "NOFX", "has_nport": False, "cik": None},
        {"series_id": "S000074569", "series_name": "iShares U.S. Thematic Rotation Active ETF", "series_lei": None, "ticker": None,
         "has_nport": True, "cik": None},
    ]
).assign(registrant_name=lambda d: d["series_id"].map({"S000000002": "Some Funds Trust"}))


def security_master():
    df = pd.DataFrame(
        [
            {"isin": "US4642872265", "issuer_lei": "LEI-AGG", "issuer_type": "RF", "n_positions": 40},
            {"isin": "US0000000AB1", "issuer_lei": "LEI-MF", "issuer_type": "RF", "n_positions": 3},
            {"isin": "US0000000AA2", "issuer_lei": "LEI-MF", "issuer_type": "RF", "n_positions": 9},
            # The fund's own notes are not a share class.
            {"isin": "US0000000NT3", "issuer_lei": "LEI-MF", "issuer_type": "CORP", "n_positions": 50},
        ]
    )
    return connection().from_df(df)


@pytest.fixture
def universe():
    return build_universe(PRODUCTS, FUND_MASTER, security_master(), {"issuers": {"ishares": "iShares", "spdr": "SPDR"}})


def test_share_classes_are_aliases_of_one_fund(universe):
    cspx = universe[universe["issuer_id"] == "ishares-uk:253743"]
    assert len(cspx) == 1
    assert cspx["id"].iloc[0] == "IE00B5BMR087"
    assert list(cspx["isins"].iloc[0]) == ["IE00B5BMR087", "IE00BYX5MX67"]
    assert cspx["issuer"].iloc[0] == "iShares"


def test_issuer_and_nport_fund_is_one_fund(universe):
    ivv = universe[universe["ticker"] == "IVV"]
    assert len(ivv) == 1
    assert ivv[["id", "series_id", "series_match", "universe_source"]].iloc[0].tolist() == [
        "US4642872000", "S000004310", "ticker", "ishares"]
    agg = universe[universe["id"] == "US4642872265"].iloc[0]
    assert (agg["series_id"], agg["series_match"]) == ("S000004362", "isin")
    assert not ((universe["universe_source"] == "nport") & universe["series_id"].isin(["S000004310", "S000004362"])).any()


def test_series_keyed_by_isin_when_known_else_series_id(universe):
    mf = universe[universe["series_id"] == "S000000002"].iloc[0]
    assert mf["universe_source"] == "nport"
    assert mf["id"] == "US0000000AA2"  # most-held share class first
    assert list(mf["isins"]) == ["US0000000AA2", "US0000000AB1"]
    feeder = universe[universe["series_id"] == "S000000003"].iloc[0]
    assert feeder["id"] == "S000000003" and pd.isna(feeder["isin"])
    assert "S000000009" not in set(universe["series_id"])  # no N-PORT filing


def test_same_name_share_classes_are_one_fund(universe):
    lqd = universe[universe["isins"].map(lambda x: "IE0032895942" in list(x))]
    assert len(lqd) == 1
    assert lqd["id"].iloc[0] == "IE00BYXYYJ35" and list(lqd["isins"].iloc[0]) == ["IE00BYXYYJ35", "IE0032895942"]
    assert list(lqd["share_class_ids"].iloc[0]) == ["ishares-uk:287338", "ishares-uk:251832"]


def test_series_without_ticker_or_isin_matches_by_unique_name(universe):
    thro = universe[universe["id"] == "US09290C8064"].iloc[0]
    assert (thro["series_id"], thro["series_match"]) == ("S000074569", "name")
    assert not (universe["id"] == "S000074569").any()


def test_nport_fund_issuer_is_the_registrant(universe):
    mf = universe[universe["series_id"] == "S000000002"].iloc[0]
    assert mf["issuer"] == "Some Funds Trust"


def test_non_us_issuer_ticker_does_not_merge(universe):
    ucits = universe[universe["id"] == "IE00B6YX5C33"].iloc[0]
    assert ucits["series_id"] is None or pd.isna(ucits["series_id"])


def test_every_isin_names_one_fund(universe):
    isins = [i for x in universe["isins"] for i in x]
    assert len(isins) == len(set(isins))
    assert universe["id"].is_unique


def test_select_sources_sample_limit(universe):
    assert set(select_universe(universe, {"sources": ["nport"]})["universe_source"]) == {"nport"}
    sample = select_universe(universe, {"sample": 1, "seed": 1})
    assert sample["universe_source"].value_counts().to_dict() == {"ishares": 1, "nport": 1, "spdr": 1}
    assert len(select_universe(universe, {"limit": 2})) == 2
    assert select_universe(universe, {"ids": ["S000000003"]})["id"].tolist() == ["S000000003"]
    with pytest.raises(ValueError):
        select_universe(universe, {"sources": ["vanguard"]})


# -- the batch ----------------------------------------------------------------


def nport_holdings():
    df = pd.DataFrame(
        [
            {"series_id": "S000000002", "report_date": pd.Timestamp("2026-07-31"), "accession_number": "0000000001-26-000001",
             "isin": "US0378331005", "cusip": "037833100", "issuer_name": "Apple Inc", "issuer_title": "Apple Inc",
             "issuer_lei": "L-AAPL", "weight_pct": 60.0, "asset_cat": "EC", "issuer_type": "CORP",
             "reported_ticker": "AAPL", "isin_source": "reported", "country": "US"},
            {"series_id": "S000000002", "report_date": pd.Timestamp("2026-07-31"), "accession_number": "0000000001-26-000001",
             "isin": "US4642872000", "cusip": "464287200", "issuer_name": "iShares Core S&P 500 ETF",
             "issuer_title": "iShares Core S&P 500 ETF", "issuer_lei": "LEI-IVV", "weight_pct": 30.0, "asset_cat": "EC",
             "issuer_type": "RF", "reported_ticker": "IVV", "isin_source": "reported"},
            {"series_id": "S000000002", "report_date": pd.Timestamp("2026-07-31"), "accession_number": "0000000001-26-000001",
             "isin": None, "cusip": None, "issuer_name": "Cash", "issuer_title": "Cash", "issuer_lei": None,
             "weight_pct": 10.5, "asset_cat": "STIV", "issuer_type": "RF", "reported_ticker": None, "isin_source": None},
        ]
    )
    return connection().from_df(df)


def issuer_holdings(self, iid):
    if iid == "ishares-us:239458":
        raise RuntimeError("issuer site is down")
    lines = [Line(99.0, "US0378331005", name="APPLE", asset_class="equity"), Line(1.0, None, name="USD CASH")]
    return Holdings("ishares", "2026-11-02", lines, source_ref=f"iShares holdings {iid}", source_url=f"https://ishares.example/{iid}")


SOURCES = {
    "ishares": {"name": "iShares", "url": "https://www.ishares.com/", "licence": "x", "credit": "Holdings data from iShares by BlackRock"},
    "nport": {"name": "SEC Form N-PORT", "url": "https://www.sec.gov/", "licence": "public", "credit": "SEC Form N-PORT"},
}


FAILING_FUND = "US0000000AA2"


def _run(universe, monkeypatch, tmp_path, fail: str | None = None, **params):
    """Run the fake universe; the fund ``fail`` raises inside the engine."""
    monkeypatch.setattr(DataProviders, "_from_issuer", issuer_holdings)
    if fail:
        from openbasket.pipelines.universe import nodes as unodes

        real = unodes.look_through

        def engine_that_breaks(isin, *a, **kw):
            if isin == fail:
                raise RuntimeError("engine broke")
            return real(isin, *a, **kw)

        monkeypatch.setattr(unodes, "look_through", engine_that_breaks)
    lt = {"source_order": ["issuer", "nport"], "max_depth": 4, "min_resolved_pct": 90.0, "use_openfigi": False}
    up = {"work_dir": str(tmp_path / "parts"), "rows_per_part": 2, "log_every": 2, **params}
    empty = connection().sql("select null::varchar cusip, null::varchar isin, null::varchar isin_source where false")
    return run_universe(
        universe, nport_holdings(), security_master(), FUND_MASTER, pd.DataFrame(columns=["ticker", "isin"]),
        PRODUCTS, empty, pd.DataFrame(columns=["sedol", "isin"]), lt, up, SOURCES,
    )


def test_one_failing_fund_does_not_stop_the_batch(universe, monkeypatch, tmp_path):
    funds, constituents, run = _run(universe, monkeypatch, tmp_path, fail=FAILING_FUND)
    by_id = funds.set_index("id")
    assert len(funds) == len(universe) and run["n_attempted"] == len(universe)
    assert "engine broke" in by_id.at[FAILING_FUND, "error"]
    assert "engine_that_breaks" in by_id.at[FAILING_FUND, "traceback"]
    assert by_id.at[FAILING_FUND, "status"] == "error"
    assert run["n_failed"] == 1 and run["n_ok"] == len(universe) - 1
    # AGG's issuer site raised: that is a source failure, not a fund failure.
    agg = by_id.loc["US4642872265"]
    assert pd.isna(agg["error"]) and agg["status"] == "no_holdings"
    assert "issuer=error" in agg["sources_status"]
    assert agg["note"] == "iShares lists this fund, but no holdings were available"
    # No holdings: dated the day the list was read, credited as listed only.
    assert agg["as_of"] == "2026-11-03"
    assert agg["credit"] == "Fund listed by iShares; no holdings were available from any source"
    rows = constituents.df()
    unres = rows[rows["fund_id"] == "US4642872265"].iloc[0]
    assert pd.isna(unres["constituent_isin"]) and unres["source"] == "ishares" and unres["as_of"] == "2026-11-03"
    assert unres["note"] == agg["note"] and unres["source_url"]
    assert by_id["seconds"].ge(0).all()
    ivv = by_id.loc["US4642872000"]
    assert ivv["source"] == "ishares" and ivv["resolved_pct"] == pytest.approx(99.0)
    assert ivv["source_url"] == "https://ishares.example/ishares-us:239726"
    assert ivv["credit"] == "Holdings data from iShares by BlackRock"


def test_fund_of_funds_rows_and_unresolved_row(universe, monkeypatch, tmp_path):
    funds, constituents, _ = _run(universe, monkeypatch, tmp_path)
    df = constituents.df()
    mf = df[df["fund_id"] == "US0000000AA2"]
    apple = mf[mf["constituent_isin"] == "US0378331005"].set_index("source")
    # 60% held directly per N-PORT, 30% x 99% through IVV's issuer file: one row per source.
    assert apple.at["nport", "weight_pct"] == pytest.approx(60) and apple.at["nport", "depth"] == 0
    assert apple.at["ishares", "weight_pct"] == pytest.approx(0.3 * 99) and apple.at["ishares", "depth"] == 1
    assert apple.at["ishares", "via_fund"] == "iShares Core S&P 500 ETF"
    unres = mf[mf["constituent_isin"].isna()].iloc[0]
    assert unres["asset_class"] == "unresolved" and unres["weight_pct"] == pytest.approx(100 - 89.7)
    assert set(mf["source"].dropna()) == {"nport", "ishares"}
    assert set(df.columns) >= {"source_ref", "source_url", "as_of", "depth", "via_fund", "isin_source", "figi_name",
                               "composite_figi", "name_source"}
    credit = funds.set_index("id").at["US0000000AA2", "credit"]
    assert credit.startswith("SEC Form N-PORT") and "iShares" in credit
    feeder = funds.set_index("id").loc["S000000003"]
    assert feeder["note"] == "the fund's latest N-PORT filing lists no holdings" and feeder["source"] == "nport"
    assert feeder["status"] == "no_holdings" and feeder["matched_via"] == "series_id"
    assert feeder["source_url"].startswith("https://www.sec.gov/cgi-bin/browse-edgar")


def test_expand_fund_catches_provider_errors():
    class Broken:
        def holdings(self, ref):
            raise KeyError("boom")

        def resolve_line(self, line):
            return None

    rec, rows = expand_fund(
        {"id": "S000000001", "series_id": "S000000001", "universe_source": "nport", "name": "X"},
        Broken(), {"max_depth": 4, "min_resolved_pct": 90.0},
    )
    assert rec["error"].startswith("KeyError") and rows.empty


# -- providers ----------------------------------------------------------------


def providers(**kw):
    return DataProviders(
        nport_holdings(), security_master(), FUND_MASTER, pd.DataFrame(columns=["ticker", "isin"]), None,
        source_order=kw.pop("source_order", ["issuer", "nport"]), issuer_products=PRODUCTS, **kw,
    )


def test_resolve_by_series_or_issuer_id():
    p = providers()
    assert p.resolve("S000000002").series_id == "S000000002"
    assert p.resolve("ishares-uk:253743").issuer_id == "ishares-uk:253743"
    assert p.resolve("S999999999") is None


def test_indexed_series_lookup_matches_holdings():
    p = providers()
    assert p._series_from_nport("US4642872000") == "S000004310"
    assert p._series_from_nport("US0378331005") is None


def test_stop_early_skips_later_sources(monkeypatch):
    monkeypatch.setattr(DataProviders, "_from_issuer", issuer_holdings)
    ref = FundRef("S000000002", None, "x", "test", issuer_id="ishares-us:239726")
    assert [h.source for h in providers().holdings(ref)] == ["ishares", "nport"]
    assert [h.source for h in providers(stop_at_pct=90).holdings(ref)] == ["ishares"]


def test_tickerinside_not_asked_for_local_or_unlisted_tickers(monkeypatch):
    asked = []
    monkeypatch.setattr("openbasket.providers.fetch_fund", lambda t: asked.append(t))
    p = providers(source_order=["tickerinside"], tickerinside_tickers={"ABCEX"})
    p.holdings(FundRef(None, "CSPX", "x", "ishares", issuer_id="ishares-uk:253743"))
    p.holdings(FundRef("S000000002", "ABCDX", "x", "nport"))
    assert asked == ["ABCEX"]


def test_throttle_spaces_requests_per_host():
    t = throttle.Throttle(default_s=0.05)
    start = time.monotonic()
    for _ in range(3):
        t.wait("https://a.example/x")
    t.wait("https://b.example/x")
    assert time.monotonic() - start >= 0.1
    assert t.requests == 4 and t.waited_s >= 0.09
