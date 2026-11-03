"""ISIN and CUSIP helpers.

An ISIN is a two-letter country code, a nine-character national number and a
check digit. For the US and Canada the national number is the CUSIP, so a
valid CUSIP plus the country gives the ISIN.
"""

from __future__ import annotations

import re

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_CUSIP_RE = re.compile(r"^[A-Z0-9*@#]{8}[0-9]$")

#: Countries whose ISIN national number is the CUSIP.
CUSIP_COUNTRIES = frozenset({"US", "CA"})


def _char_value(ch: str) -> int:
    if ch.isdigit():
        return int(ch)
    if ch.isalpha():
        return ord(ch) - ord("A") + 10
    return {"*": 36, "@": 37, "#": 38}[ch]


def isin_check_digit(payload: str) -> str:
    """Return the check digit for the first 11 characters of an ISIN."""
    digits = "".join(str(_char_value(ch)) for ch in payload.upper())
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
        total += d // 10 + d % 10
    return str((10 - total % 10) % 10)


def is_valid_isin(value: object) -> bool:
    """True when ``value`` is a well-formed ISIN with a correct check digit."""
    if not isinstance(value, str):
        return False
    value = value.strip().upper()
    return bool(_ISIN_RE.match(value)) and isin_check_digit(value[:11]) == value[11]


def cusip_check_digit(payload: str) -> str:
    """Return the check digit for the first 8 characters of a CUSIP."""
    total = 0
    for i, ch in enumerate(payload.upper()):
        v = _char_value(ch)
        if i % 2 == 1:
            v *= 2
        total += v // 10 + v % 10
    return str((10 - total % 10) % 10)


def is_valid_cusip(value: object) -> bool:
    """True when ``value`` is a well-formed CUSIP with a correct check digit."""
    if not isinstance(value, str):
        return False
    value = value.strip().upper()
    return bool(_CUSIP_RE.match(value)) and cusip_check_digit(value[:8]) == value[8]


#: CUSIP values filers use when a holding has none.
PLACEHOLDER_CUSIPS = frozenset({"N/A", "NA", "NONE", "NULL", "0", "-", "UNKNOWN"})


def is_placeholder_cusip(cusip: object) -> bool:
    """True for CUSIPs that stand for "no CUSIP": ``000000000``, nine equal characters, ``N/A``.

    N-PORT filers put ``000000000`` on cash sweeps, money market lines and
    private funds alike, so one ISIN reported next to it in one filing must
    not be spread to every other holding with that "CUSIP".
    """
    if not isinstance(cusip, str):
        return False
    c = cusip.strip().upper()
    return c in PLACEHOLDER_CUSIPS or c.startswith("000000") or (len(c) >= 6 and len(set(c)) == 1)


#: The same test in SQL, for a column named ``cusip``.
PLACEHOLDER_CUSIP_SQL = (
    "(upper(trim(cusip)) in ('N/A', 'NA', 'NONE', 'NULL', '0', '-', 'UNKNOWN') "
    "or upper(trim(cusip)) like '000000%' "
    "or (length(trim(cusip)) >= 6 and length(replace(upper(trim(cusip)), left(upper(trim(cusip)), 1), '')) = 0))"
)


def cusip_to_isin(cusip: object, country: object = "US") -> str | None:
    """Derive an ISIN from a CUSIP, or ``None`` when that is not safe.

    Only US and Canadian CUSIPs are converted. CINS numbers (first character a
    letter) belong to securities issued elsewhere whose ISIN uses another
    country code, so they are left alone.
    """
    if not is_valid_cusip(cusip):
        return None
    cusip = str(cusip).strip().upper()
    country = (str(country).strip().upper() if isinstance(country, str) else "") or "US"
    if country not in CUSIP_COUNTRIES or cusip[0].isalpha():
        return None
    payload = country + cusip
    return payload + isin_check_digit(payload)


def isin_to_cusip(isin: object) -> str | None:
    """Return the CUSIP inside a US or Canadian ISIN, else ``None``."""
    if not is_valid_isin(isin):
        return None
    isin = str(isin).strip().upper()
    return isin[2:11] if isin[:2] in CUSIP_COUNTRIES else None


def normalise_ticker(ticker: object) -> str | None:
    """Fold share-class punctuation so ``BRK.B``, ``BRK/B`` and ``BRK-B`` match."""
    if not isinstance(ticker, str) or not ticker.strip():
        return None
    return re.sub(r"[\s./\-]", "", ticker.strip().upper())
