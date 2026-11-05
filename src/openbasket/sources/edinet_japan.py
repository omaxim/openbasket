"""Japan: locating investment trust reports on EDINET (API v2). Client only.

Japanese investment trusts file their securities reports (有価証券報告書,
document type ``120``) and semi-annual reports (半期報告書, ``160``) with
the Financial Services Agency's EDINET. The reports' financial statements end
with a schedule of securities held (有価証券明細表), which is a full list of
the fund's holdings at the period end.

**Gated.** EDINET API v2 needs a free *Subscription-Key*, issued after
registering an account on the EDINET API site (e-mail and phone number). This
project does not register on anyone's behalf: set ``EDINET_API_KEY`` yourself
to use this client. Without it every call raises :class:`EdinetKeyMissing`
(the API answers 401 "invalid subscription key").

Endpoints
    ``GET https://api.edinet-fsa.go.jp/api/v2/documents.json?date=YYYY-MM-DD&type=2``
    lists every document filed that day with ``docID``, ``fundCode``,
    ``docTypeCode``, ``periodEnd``, ``filerName`` and ``docDescription``.
    ``GET https://api.edinet-fsa.go.jp/api/v2/documents/<docID>?type=5`` is
    the document's XBRL as CSV (``type=1`` the XBRL ZIP, ``type=2`` the PDF).

What is not built: parsing the securities schedule out of the report (it is
tagged as a text block, so it needs real documents to develop against), and
matching a fund ISIN (``JP90C...``) to its EDINET ``fundCode``, for which no
open mapping was found. No issuer adapter is registered for this source.
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd

from .. import http

#: Redistribution class (see :func:`openbasket.sources.redistribution`):
#: EDINET's terms of use (revised 2025-04-25) let anyone reuse its content
#: under the Public Data License 1.0 (PDL1.0), crediting EDINET and saying who
#: edited it; scraping is barred, the API is the allowed route.
REDISTRIBUTION = "public"
CREDIT = "出典：EDINET (Financial Services Agency, Japan), https://disclosure2.edinet-fsa.go.jp/, PDL1.0; extracted by openbasket"
LICENCE = "Public Data License 1.0 (https://www.digital.go.jp/resources/open_data/public_data_license_v1.0)"

API = "https://api.edinet-fsa.go.jp/api/v2"
LIST_URL = API + "/documents.json"
DOC_URL = API + "/documents/{doc_id}"
KEY_ENV = "EDINET_API_KEY"

#: Report types that carry a fund's full securities schedule.
FUND_REPORT_TYPES = {"120": "securities report", "160": "semi-annual report"}

LIST_COLUMNS = [
    "docID", "fundCode", "edinetCode", "filerName", "docTypeCode", "ordinanceCode", "formCode",
    "periodStart", "periodEnd", "submitDateTime", "docDescription", "csvFlag",
]


class EdinetKeyMissing(RuntimeError):
    """``EDINET_API_KEY`` is not set: EDINET API v2 needs a free registered key."""


def _key() -> str:
    key = os.environ.get(KEY_ENV)
    if not key:
        raise EdinetKeyMissing(
            f"Set {KEY_ENV} to an EDINET API v2 Subscription-Key (free registration at the EDINET API site)."
        )
    return key


def _check(r) -> None:
    # Not raise_for_status(): its message would carry the URL, key included.
    if r.status_code == 401:
        raise EdinetKeyMissing("EDINET rejected the subscription key (401)")
    if r.status_code >= 400:
        raise RuntimeError(f"EDINET request failed with HTTP {r.status_code}")


def parse_document_list(payload: dict) -> pd.DataFrame:
    """Fund securities and semi-annual reports from one ``documents.json`` response."""
    status = str((payload.get("metadata") or {}).get("status", payload.get("StatusCode", "")))
    if status not in ("200", ""):
        raise RuntimeError(f"EDINET list failed: {payload.get('metadata') or payload}")
    df = pd.DataFrame(payload.get("results") or [], columns=LIST_COLUMNS)
    df = df[df["fundCode"].notna() & df["docTypeCode"].isin(list(FUND_REPORT_TYPES))]
    return df.assign(report_kind=df["docTypeCode"].map(FUND_REPORT_TYPES)).reset_index(drop=True)


def fund_reports(day: date | str) -> pd.DataFrame:
    """Every investment trust securities or semi-annual report filed on ``day``."""
    r = http.get(LIST_URL, params={"date": str(day), "type": 2, "Subscription-Key": _key()})
    _check(r)
    return parse_document_list(r.json())


def download_document(doc_id: str, kind: int = 5) -> bytes:
    """One document: ``kind`` 5 is the XBRL as CSV (ZIP), 1 the XBRL ZIP, 2 the PDF."""
    r = http.get(DOC_URL.format(doc_id=doc_id), params={"type": kind, "Subscription-Key": _key()}, timeout=300)
    _check(r)
    return r.content
