"""Name-to-ISIN matching for holdings named without identifiers (statutory reports)."""

import json

import pandas as pd
import pytest

from openbasket.datasets import connection
from openbasket.lookthrough import look_through
from openbasket.matching import (
    ISIN_SOURCE, MIN_SCORE, Named, NameMatcher, bond_terms, index_of, name_key, peer_candidates,
)
from openbasket.pipelines.lookthrough.nodes import name_matching_options
from openbasket.providers import DataProviders
from openbasket.sources import issuers
from openbasket.sources.issuers import PRODUCT_COLUMNS, IssuerAdapter, IssuerHoldings

# A small security master in the shape N-PORT builds it.
SM = pd.DataFrame(
    [
        ("US0378331005", "Apple Inc", "Apple Inc", "EC", "CORP", "US"),
        ("US02079K3059", "Alphabet Inc", "Alphabet Inc - Class A", "EC", "CORP", "US"),
        ("US02079K1079", "Alphabet Inc", "Alphabet Inc - Class C", "EC", "CORP", "US"),
        ("GB0007188757", "Rio Tinto PLC", "Rio Tinto PLC", "EC", "CORP", "GB"),
        ("AU000000RIO1", "Rio Tinto Ltd", "Rio Tinto Ltd", "EC", "CORP", "AU"),
        ("CH0038863350", "Nestle SA", "Nestle SA", "EC", "CORP", "CH"),
        ("CH0012032048", "Roche Holding AG", "Roche Holding AG", "EC", "CORP", "CH"),
        ("CH0012032113", "Roche Holding AG", "ROCHE HOLDING AG-BR", "EC", "CORP", "CH"),
        ("JP3635000007", "Toyota Tsusho Corp", "Toyota Tsusho Corp", "EC", "CORP", "JP"),
        ("JP3735400008", "NTT Inc", "NTT Inc", "EC", "CORP", "JP"),
        ("KYG017191142", "Alibaba Group Holding Ltd", "Alibaba Group Holding Ltd", "EC", "CORP", "CN"),
        ("US01609W1027", "Alibaba Group Holding Ltd", "Alibaba Group Holding Ltd ADR", "EC", "CORP", "CN"),
        ("SE0000115446", "Volvo AB", "Volvo AB - Class B", "EC", "CORP", "SE"),
        ("SE0000115420", "Volvo AB", "Volvo AB - Class A", "EC", "CORP", "SE"),
        ("DE0007664039", "Volkswagen AG", "Volkswagen AG", "EP", "CORP", "DE"),
        ("DE0007664005", "Volkswagen AG", "Volkswagen AG", "EC", "CORP", "DE"),
        ("US30231G1022", "Exxon Mobil Corp", "Exxon Mobil Corp", "EC", "CORP", "US"),
        ("US30233Q1085", "ExxonMobil Holdings Corp", "EXXON MOBIL CORP", "EC", "CORP", "US"),
        ("IE00B4L5Y983", "iShares Core MSCI World UCITS ETF", "iShares Core MSCI World", "EC", "RF", "IE"),
        ("XS2809222420", "Carnival Corp", "Carnival Corp", "DBT", "CORP", "PA"),
        ("XS0000000001", "Grifols SA", "GRIFOLS SA", "DBT", "CORP", "ES"),
        ("XS0000000002", "Grifols SA", "GRIFOLS SA 144A", "DBT", "CORP", "ES"),
        ("XS0000000003", "Iliad Holding SAS", "Iliad Holding SAS", "DBT", "CORP", "FR"),
        ("XS0000000004", "Iliad Holding SAS", "Iliad Holding SAS", "DBT", "CORP", "FR"),
    ],
    columns=["isin", "issuer_name", "title", "asset_cat", "issuer_type", "country"],
)
TERMS = pd.DataFrame(
    [
        ("CH0038863350", "CHF", None, None),
        ("XS2809222420", "EUR", 5.75, "2030-01-15"),
        ("XS0000000001", "EUR", 3.875, "2028-10-15"),
        ("XS0000000002", "EUR", 3.875, "2028-10-15"),
        ("XS0000000003", "EUR", 5.625, "2028-10-15"),
        ("XS0000000004", "EUR", 5.625, "2028-10-15"),
    ],
    columns=["isin", "currency", "coupon", "maturity"],
)
FIGI = pd.DataFrame(
    {
        "id_type": ["ID_ISIN", "ID_ISIN"],
        "id_value": ["CH0012005267", "US9154362084"],
        "result": [
            json.dumps([{"name": "NOVARTIS AG-REG", "marketSector": "Equity", "securityType": "Common Stock"}]),
            json.dumps([{"name": "UPM-KYMMENE OYJ-SPONS ADR", "marketSector": "Equity", "securityType": "ADR"}]),
        ],
    }
)


@pytest.fixture(scope="module")
def matcher():
    return NameMatcher.from_frames(SM, TERMS, FIGI)


def m(matcher, name, **kw):
    got = matcher.match(Named(name, kw.pop("asset_class", "equity"), **kw))
    return got.isin if got else None


def test_names_are_normalised():
    assert name_key("ROCHE HLDGS AG CHF- PRP").tokens == ("ROCHE", "HOLDING")
    assert name_key("ROCHE HLDGS AG CHF- PRP").share_class == "PART"
    assert name_key("SWISS RE AG CHF0.10").exact == name_key("Swiss Re AG").exact
    assert name_key("KUEHNE&NAGEL INTL CHF1(REGD)(POST-SUBD)").exact == name_key("Kuehne + Nagel International AG").exact
    assert name_key("Nestlé S.A.").exact == "NESTLE"
    assert name_key("ARGEN-X ADR-SPONS").exact == name_key("argenx SE ADR").exact == "ARGENX"
    assert name_key("ARGEN-X ADR-SPONS").depositary
    assert name_key("Meta Platforms Inc - Class A").share_class == "A"
    assert name_key("Legend Holdings Corp 'H' '144A'").share_class == "H"
    assert name_key("Volkswagen AG Vorzug").share_class == "PREF"
    assert name_key("BASF N").share_class is None                 # N: the registered share
    assert name_key("The Home Depot Inc").exact == "HOMEDEPOT"
    assert name_key("New York Times Co").exact == "NEWYORKTIMES"
    assert name_key("Rio Tinto Ltd").legal == "LTD" and name_key("Rio Tinto PLC").legal == "PLC"
    assert name_key("Keppel REIT").exact != name_key("Keppel Ltd").exact
    assert name_key("Equinix Inc (REIT)").exact == "EQUINIX"
    assert name_key("A") is None


def test_bond_terms():
    t = bond_terms("CARNIVAL CORP-REG-S 5.75000% 24-15.01.30")
    assert (t.issuer.exact, t.coupon, t.maturity, t.tranche) == ("CARNIVAL", 5.75, "2030-01-15", "REGS")
    assert bond_terms("IREN 0.25% 01-06-32 CV").maturity == "2032-06-01"
    assert bond_terms("Apple Inc 4.5% 05/15/2030").maturity == "2030-05-15"
    t = bond_terms("Example Corp 2.25% 2031")
    assert t.maturity is None and t.maturity_year == 2031
    assert bond_terms("BANK OF MONTREAL-REG-S 0.947%/VAR 25-18.09.31") is None     # floating: not matched
    assert bond_terms("BAIDU ZCP 12-03-32 CV") is None                             # zero coupon
    assert bond_terms("Microsoft Corp") is None


def test_exact_names_match_and_are_scored(matcher):
    got = matcher.match(Named("Apple, Inc", "equity", "US", None, 7.0))
    assert got.isin == "US0378331005" and got.score == 1.0                  # confirmed by country
    got = matcher.match(Named("APPLE INC", "equity"))
    assert got.isin == "US0378331005" and MIN_SCORE <= got.score < 1.0      # nothing to confirm it
    assert m(matcher, "Toyota Tsusho Corp.") == "JP3635000007"
    assert m(matcher, "NESTLE SA CHF0.10(REGD)", currency="CHF") == "CH0038863350"
    assert m(matcher, "NOVARTIS AG CHF0.49 (REGD)") == "CH0012005267"         # an OpenFIGI name


def test_ambiguity_is_never_broken_by_a_guess(matcher):
    assert m(matcher, "Alphabet Inc") is None                                # class A or C?
    assert m(matcher, "Alphabet Inc Class C") == "US02079K1079"
    assert m(matcher, "Roche Holding AG") is None                            # participation or bearer?
    assert m(matcher, "ROCHE HLDGS AG CHF- PRP") is None                     # no candidate says PRP
    assert m(matcher, "Volvo AB") is None and m(matcher, "Volvo AB Class B") == "SE0000115446"
    assert m(matcher, "Volkswagen AG") is None                               # a preference share exists
    assert m(matcher, "Volkswagen AG Pref") == "DE0007664039"
    assert m(matcher, "Exxon Mobil Corp") is None                            # old and new ISIN, same name


def test_short_names_need_confirmation(matcher):
    assert m(matcher, "NTT, Inc.") is None
    assert m(matcher, "NTT, Inc.", country="JP") == "JP3735400008"


def test_receipts_legal_forms_and_countries(matcher):
    assert m(matcher, "Alibaba Group Holding Ltd ADR") == "US01609W1027"
    assert m(matcher, "Alibaba Group Holding Ltd", country="KY") == "KYG017191142"
    assert m(matcher, "Rio Tinto PLC") == "GB0007188757" and m(matcher, "Rio Tinto Ltd") == "AU000000RIO1"
    assert m(matcher, "Rio Tinto Ltd", country="CH") is None                 # the report's country disagrees
    # an ADR filed under the company's own name is not the ordinary share
    assert m(matcher, "UPM-Kymmene OYJ") is None
    # funds, cash and derivatives are never matched by name
    assert m(matcher, "iShares Core MSCI World UCITS ETF", asset_class="fund") is None
    assert m(matcher, "Apple Inc", asset_class="cash") is None


def test_bonds_match_on_issuer_coupon_and_maturity(matcher):
    assert m(matcher, "CARNIVAL CORP-REG-S 5.75000% 24-15.01.30", asset_class="debt", currency="EUR") == "XS2809222420"
    assert m(matcher, "CARNIVAL CORP-REG-S 5.75000% 24-15.01.30", asset_class="debt", currency="USD") is None
    assert m(matcher, "CARNIVAL CORP 5.50000% 24-15.01.30", asset_class="debt") is None      # other coupon
    # Reg S and 144A tranches: the one whose own name says 144A is not the Reg S line
    assert m(matcher, "GRIFOLS SA-REG-S 3.87500% 21-15.10.28", asset_class="debt") == "XS0000000001"
    # two tranches and nothing to tell them apart
    assert m(matcher, "ILIAD HOLDING SAS-REG-S 5.62500% 21-15.10.28", asset_class="debt") is None


def test_peers_narrow_the_field_and_confirm(matcher):
    peer = peer_candidates([("SE0015961909", "HEXAGON CLASS B", 0.021), ("SE0000115446", "VOLVO CLASS B", 0.054),
                            ("US30233Q1085", "EXXON MOBIL CORP", 0.6)])
    # the index holds one Hexagon line, at the report's weight: that is the line
    [hex_] = matcher.match_all([Named("Hexagon AB", "equity", None, None, 0.02)], peers=peer)
    assert hex_.isin == "SE0015961909" and hex_.via == "peer"
    # ... but not at a different weight
    assert matcher.match_all([Named("Hexagon AB", "equity", None, None, 0.9)], peers=peer) == [None]
    # Volvo A is also filed as plain "Volvo AB", and the peer's Exxon ISIN is a newer line than the
    # report's: a peer does not overrule another ISIN filed under the same plain name
    out = matcher.match_all([Named("Volvo AB", "equity", None, None, 0.06),
                             Named("Exxon Mobil Corp", "equity", None, None, 0.6)], peers=peer)
    assert out == [None, None]
    assert matcher.match_all([Named("Volvo AB Class B", "equity", None, None, 0.06)], peers=peer)[0].isin == "SE0000115446"


def test_collisions_are_dropped(matcher):
    lines = [Named("Apple Inc", "equity", "US"), Named("APPLE INC", "equity", "US")]
    assert matcher.match_all(lines) == [None, None]
    assert matcher.match_all([Named("Apple Inc", "equity", "US")], reported={"US0378331005"}) == [None]


def test_index_of():
    assert index_of("UBS Core MSCI World UCITS ETF USD acc") == "MSCI WORLD"
    assert index_of("iShares Core MSCI World UCITS ETF") == "MSCI WORLD"
    assert index_of("State Street® SPDR® S&P 500 UCITS ETF (Acc)") == "S AND P 500"
    assert index_of("UBS MSCI World Socially Responsible UCITS ETF") is None
    assert index_of("iShares Core MSCI World UCITS ETF EUR Hedged (Acc)") is None


# --------------------------------------------------------------------------
# Through the providers
# --------------------------------------------------------------------------


def report_holdings(product, ctx):
    rows = [
        {"isin": None, "cusip": None, "sedol": None, "ticker": None, "name": "Apple, Inc", "weight_pct": 60.0,
         "asset_class": "equity", "currency": None, "country": "United States"},
        {"isin": None, "cusip": None, "sedol": None, "ticker": None, "name": "Alphabet Inc", "weight_pct": 30.0,
         "asset_class": "equity", "currency": None, "country": "United States"},
        {"isin": None, "cusip": None, "sedol": None, "ticker": None, "name": "Cash at bank", "weight_pct": 10.0,
         "asset_class": "cash", "currency": "USD", "country": None, "residual": True},
    ]
    return IssuerHoldings("2026-06-30", pd.DataFrame(rows), "https://reports.example/ar.pdf")


REPORTS = IssuerAdapter(name="reportco", title="ReportCo (statutory reports)", homepage="https://reports.example/",
                        credit="x", licence="x", regions=("LU",), products=lambda: pd.DataFrame(), holdings=report_holdings)


@pytest.fixture
def registry(monkeypatch):
    issuers.safe_discover()
    monkeypatch.setattr(issuers, "_REGISTRY", {"reportco": REPORTS})
    return issuers._REGISTRY


def _providers(tmp_path, matching: bool):
    sm_path = tmp_path / "sm.parquet"
    SM.assign(n_positions=1, issuer_lei=None).to_parquet(sm_path)
    terms = tmp_path / "terms.parquet"
    TERMS.to_parquet(terms)
    fm = pd.DataFrame(columns=["series_id", "series_name", "series_lei", "ticker", "has_nport", "cik"])
    nh = connection().from_df(pd.DataFrame({c: pd.Series([], dtype=object) for c in ("series_id", "isin", "cusip", "issuer_lei")}))
    products = pd.DataFrame([{"issuer": "reportco", "site": "six", "portfolio_id": "P1", "isin": "LU0000000001",
                              "ticker": None, "fund_name": "ReportCo Global Equity Fund P USD", "domicile": "LU",
                              "currency": "USD", "asset_class": "Equity", "replication": None,
                              "product_page_url": None, "holdings_url": None}])[PRODUCT_COLUMNS]
    params = {"name_matching": matching, "name_matching_terms": str(terms), "use_openfigi": False}
    return DataProviders(nh, connection().read_parquet(str(sm_path)), fm, pd.DataFrame(columns=["ticker", "isin"]), None,
                         source_order=["issuer"], issuer_products=products, adapters={"reportco": REPORTS},
                         name_matching=name_matching_options(params))


def test_providers_match_names_behind_the_flag(registry, tmp_path):
    off = look_through("LU0000000001", _providers(tmp_path, False))
    assert off.summary["resolved_pct"] == 0 and off.summary["unresolved_pct"] == 100

    res = look_through("LU0000000001", _providers(tmp_path, True))
    [leaf] = res.leaves
    assert leaf["constituent_isin"] == "US0378331005" and leaf["isin_source"] == ISIN_SOURCE
    assert leaf["match_score"] == 1.0 and leaf["weight_pct"] == 60.0
    assert {r["name"] for r in res.unresolved} == {"Alphabet Inc", "Cash at bank"}   # ambiguous; cash
    assert res.summary["resolved_pct"] == 60.0 and res.summary["name_matched_pct"] == 60.0
    assert "matched by the holding's name" in res.summary["note"]


def test_name_matching_options():
    assert name_matching_options({"name_matching": False}) is None
    o = name_matching_options({"name_matching": True, "openfigi_cache": "f.parquet", "name_matching_peers": {"X": ["Y"]}})
    assert o["figi_cache"] == "f.parquet" and o["peers"] == {"X": ["Y"]} and o["index_peers"] is True
