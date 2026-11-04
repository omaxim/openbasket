"""EDINET Japan: securities reports of Japanese investment trusts (client only, needs an API key)."""

import pytest

from openbasket.sources import edinet_japan


def test_edinet_needs_key(monkeypatch):
    monkeypatch.delenv("EDINET_API_KEY", raising=False)
    with pytest.raises(edinet_japan.EdinetKeyMissing):
        edinet_japan.fund_reports("2026-10-30")


def test_edinet_document_list():
    payload = {
        "metadata": {"status": "200"},
        "results": [
            {"docID": "S100AAAA", "fundCode": "G00001", "docTypeCode": "120", "periodEnd": "2026-07-31",
             "filerName": "Fictive AM", "docDescription": "有価証券報告書（内国投資信託受益証券）"},
            {"docID": "S100BBBB", "fundCode": None, "docTypeCode": "120", "filerName": "Company"},
            {"docID": "S100CCCC", "fundCode": "G00002", "docTypeCode": "030", "filerName": "Fictive AM"},
        ],
    }
    df = edinet_japan.parse_document_list(payload)
    assert df["docID"].tolist() == ["S100AAAA"]
    assert df["report_kind"].iloc[0] == "securities report"
