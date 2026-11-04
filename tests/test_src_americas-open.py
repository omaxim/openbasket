"""Americas open regulator data: SFC Colombia Formato 351 pension and severance fund portfolios."""

import json
from pathlib import Path

import pandas as pd
import pytest

from openbasket.pipelines.americas_open import create_pipeline
from openbasket.sources import americas_open as ao
from openbasket.sources.issuers import HOLDING_COLUMNS, PRODUCT_COLUMNS, validate_holdings

FIX = Path(__file__).parent / "fixtures" / "americas-open"


def _load(name: str) -> list[dict]:
    return json.loads((FIX / name).read_text("utf-8"))


@pytest.fixture
def fake_soda(monkeypatch):
    """Answer SODA queries from the fixtures, the way datos.gov.co would."""
    data = {
        "ur2p-h4yf": _load("fpo_portfolio.json"),
        "hdp5-pgem": _load("fpv_portfolio.json"),
        "hds9-4524": _load("fpo_valuation.json"),
        "gpzw-wmxd": _load("fpv_valuation.json"),
    }
    calls = []

    def get(dataset, params, timeout=300):
        calls.append((dataset, dict(params)))
        rows = data[dataset]
        field = "fecha_de_corte" if dataset in ao.PORTFOLIO_DATASETS.values() else "fecha_corte"
        if params.get("$select", "").startswith("max("):
            return [{"d": max(r[field] for r in rows)}]
        if params.get("$offset", 0):
            return []
        return rows

    monkeypatch.setattr(ao, "_get", get)
    return calls


@pytest.fixture
def store(tmp_path, fake_soda):
    s = ao.SfcColombiaStore(tmp_path)
    manifest = s.refresh()
    assert list(manifest["period"]) == ["2026-08-31", "2026-06-30"]
    return s


def test_convert_drops_personal_data():
    df = ao.convert_portfolio("FPV", _load("fpv_portfolio.json"))
    assert list(df.columns) == ao.POSITION_COLUMNS
    # The issuer of the payroll loan is an individual (id type 1): the name is not kept.
    assert "NOMBRE PERSONA EJEMPLO" not in df["issuer"].tolist()
    assert df["issuer"].tolist() == ["EJEMPLO GLOBAL BOND FUND", None, "FIC ABIERTO EJEMPLO"]
    assert df["isin"].tolist() == ["LU00000000E4", None, None]
    assert df["manager"].iloc[0] == "FIDUCIARIA BANCO EJEMPLO S.A."


def test_isin_from_custodian_code_or_local_code():
    rows = [{"no_iden_asignado_custodio": "N/A", "nemotecnico": "US00000000B5"},
            {"no_iden_asignado_custodio": "CO0000000AA7", "nemotecnico": "TFIT1"}]  # bad check digit
    df = ao.convert_portfolio("FPO", rows)
    assert df["isin"].tolist() == ["US00000000B5", None]


@pytest.mark.parametrize(
    "code, expected",
    [("TSTF", "debt"), ("CDT", "debt"), ("AAEVS", "equity"), ("AEE", "equity"), ("FINDI", "fund"),
     ("CCA", "fund"), ("FCPE", "fund"), ("DEPVN", "cash"), ("TDCEPAGLIB", "debt"), ("TPMTIN", "other")],
)
def test_asset_class(code, expected):
    assert ao.asset_class(code) == expected


def test_refresh_writes_only_converted_columns(store, tmp_path):
    pos = pd.read_parquet(tmp_path / "FPV" / "2026-06-30" / "positions.parquet")
    assert list(pos.columns) == ao.POSITION_COLUMNS
    text = (tmp_path / "FPV" / "2026-06-30" / "positions.parquet").read_bytes()
    assert b"NOMBRE PERSONA EJEMPLO" not in text
    meta = json.loads((tmp_path / "FPO" / "2026-08-31" / "meta.json").read_text())
    assert meta["valuation_date"] == "2026-08-31" and meta["lines"] == 6


def test_products(store):
    p = store.products()
    assert set(PRODUCT_COLUMNS) <= set(p.columns)
    assert sorted(p["portfolio_id"]) == ["FPO:23:91:1", "FPO:23:91:1000", "FPV:5:92:500001"]
    assert p["isin"].isna().all()  # pension funds have no ISIN
    row = p.set_index("portfolio_id").loc["FPO:23:91:1000"]
    assert row["net_assets_cop"] == 1000 and row["lines"] == 5 and row["lines_with_isin"] == 3
    assert row["as_of"] == "2026-08-31" and row["domicile"] == "CO"


def test_holdings_weights_are_value_over_net_assets(store):
    as_of, f, notes = store.holdings("FPO:23:91:1000")
    assert as_of == "2026-08-31"
    assert set(HOLDING_COLUMNS) <= set(f.columns)
    assert validate_holdings(f) == []
    w = f.set_index(f["isin"].fillna(f["name"]))["weight_pct"]
    assert w["CO0000000AA6"] == pytest.approx(50.0)  # two lots of one bond, summed
    assert w["IE00000000C8"] == pytest.approx(30.0)
    assert w["EJEMPLO PRIVATE EQUITY FUND IV"] == pytest.approx(10.0)
    assert f["weight_pct"].sum() == pytest.approx(95.0)  # never rescaled
    assert f.loc[f["isin"] == "CO0000000AA6", "name"].item() == "MINISTERIO DE EJEMPLO (TFIT00000000)"
    assert any("Formato 351" in n for n in notes) and any("CC BY-SA" in n for n in notes)


def test_cesantias_and_voluntary(store):
    _, ces, _ = store.holdings("FPO:23:91:1")
    assert ces["weight_pct"].sum() == pytest.approx(100.0)
    as_of, vol, _ = store.holdings("FPV:5:92:500001")
    assert as_of == "2026-06-30"
    assert sorted(vol["weight_pct"].round(6)) == [15.0, 25.0, 60.0]
    assert "Pagarés Libranzas" in vol["name"].tolist()


def test_net_asset_query_uses_close_lines(fake_soda, tmp_path):
    ao.fetch_net_assets("FPO", "2026-08-31")
    where = [p["$where"] for d, p in fake_soda if d == "hds9-4524" and "$where" in p and "cod_renglon" in p["$where"]]
    assert where and "cod_renglon = 305" in where[0] and "cod_renglon = 110" in where[0]


def test_missing_fund(store):
    with pytest.raises(LookupError):
        store.holdings("FPO:23:91:9999")


def test_pipeline_nodes():
    p = create_pipeline()
    assert {n.name for n in p.nodes} == {"refresh_sfc_colombia", "build_sfc_colombia_products"}
    assert "americas_open_products" in p.all_outputs()


@pytest.mark.network
def test_network_one_fund():
    """Skandia's alternative mandatory pension fund, latest month, straight from datos.gov.co."""
    ds = ao.PORTFOLIO_DATASETS["FPO"]
    day = ao.latest_date(ds)
    assert day and day >= "2026-01-01"
    rows = ao.fetch_portfolio(ds, day, where="c_digo_tipo_entidad = 9 AND c_digo_patrimonio = 8000")
    assert len(rows) > 50
    pos = ao.convert_portfolio("FPO", rows)
    vday, na = ao.fetch_net_assets("FPO", day)
    total = next(float(r["net_assets_cop"]) for r in na if r["entity_code"] == "9" and r["fund_code"] == "8000")
    f = ao.fund_lines(pos, total)
    assert validate_holdings(f) == []
    assert 90 < f["weight_pct"].sum() < 105
    assert f.loc[f["isin"].notna(), "weight_pct"].sum() > 50
