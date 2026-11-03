import pytest

from fund_lookthrough.identifiers import (
    cusip_check_digit,
    cusip_to_isin,
    is_valid_cusip,
    is_valid_isin,
    isin_to_cusip,
    normalise_ticker,
)


@pytest.mark.parametrize("isin", ["US0378331005", "IE00B5BMR087", "US4642872000", "GB0002634946"])
def test_valid_isins(isin):
    assert is_valid_isin(isin)


@pytest.mark.parametrize("isin", ["US0378331004", "US037833100", "XX", None, 12, "us0378331005x"])
def test_invalid_isins(isin):
    assert not is_valid_isin(isin)


def test_cusip_check_digit():
    assert cusip_check_digit("03783310") == "0"
    assert is_valid_cusip("037833100")
    assert not is_valid_cusip("037833101")


def test_cusip_to_isin_us_and_canada():
    assert cusip_to_isin("037833100") == "US0378331005"
    assert cusip_to_isin("464287200", "US") == "US4642872000"
    # Royal Bank of Canada common shares
    assert cusip_to_isin("780087102", "CA") == "CA7800871021"


def test_cusip_to_isin_refuses_unsafe_cases():
    assert cusip_to_isin("G0084W101") is None  # CINS: issued outside the US
    assert cusip_to_isin("037833100", "GB") is None
    assert cusip_to_isin("N/A") is None
    assert cusip_to_isin("037833101") is None  # bad check digit


def test_isin_to_cusip():
    assert isin_to_cusip("US0378331005") == "037833100"
    assert isin_to_cusip("IE00B5BMR087") is None


def test_normalise_ticker():
    assert normalise_ticker("BRK.B") == normalise_ticker("BRK/B") == normalise_ticker("brk-b") == "BRKB"
    assert normalise_ticker(None) is None
    assert normalise_ticker(float("nan")) is None
