"""The identity file a local run writes for the public build."""

import pandas as pd
import pytest

from openbasket.pipelines.ingest.nodes import local_only_identity
from openbasket.sources import fund_identity as fi


def _row(isin, issuer="ishares", name="Fund", src="fund_registry"):
    return {"issuer": issuer, "site": "isin", "portfolio_id": isin, "isin": isin, "fund_name": name, "ticker": None,
            "ticker_source": None, "identity_source": src, "identity_url": None, "identity_only": True,
            "listed_on": "2026-11-05"}


def test_write_read_and_validate(tmp_path):
    path = tmp_path / "fund_identity.csv"
    out = fi.write_identity_file(pd.DataFrame([_row("IE00B5BMR087"), _row("LU0274208692", "xtrackers")]), path)
    assert out["isin"].tolist() == ["IE00B5BMR087", "LU0274208692"]
    back = fi.read_identity_file(path, {"ishares"})
    assert back["isin"].tolist() == ["IE00B5BMR087"] and back["identity_only"].tolist() == [True]
    assert fi.read_identity_file(tmp_path / "missing.csv").empty
    with pytest.raises(fi.IdentityError):
        fi.validate_identity(pd.DataFrame([_row("NOTANISIN00")]))
    with pytest.raises(fi.IdentityError):
        fi.validate_identity(pd.DataFrame([_row("IE00B5BMR087", src="ishares_holdings")]))
    with pytest.raises(fi.IdentityError):
        fi.validate_identity(pd.DataFrame([_row("IE00B5BMR087")]).assign(weight_pct=1.0))


def test_public_build_lists_funds_from_the_identity_file(tmp_path):
    path = tmp_path / "fund_identity.csv"
    fi.write_identity_file(pd.DataFrame([_row("IE00B5BMR087", name="iShares Core S&P 500 UCITS ETF")]), path)
    cfg = {"identity": {"registries": {}, "issuer_lists": [], "identity_files": [str(path)], "openfigi_cache": None}}
    rows = local_only_identity(["ishares"], {}, cfg)
    assert rows["isin"].tolist() == ["IE00B5BMR087"] and rows["fund_name"].tolist() == ["iShares Core S&P 500 UCITS ETF"]
    assert local_only_identity(["amundi"], {}, cfg).empty  # only the withheld issuers' funds
