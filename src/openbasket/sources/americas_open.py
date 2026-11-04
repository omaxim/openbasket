"""Open regulator datasets with fund holdings in the Americas beyond the US and Brazil.

Only one dataset in this region was found that publishes security-level fund
portfolios under a licence that allows reuse and republication: the
Superintendencia Financiera de Colombia's *Formato 351* (composition of the
investment portfolio) for Colombian pension and severance funds, published on
the national open data portal ``datos.gov.co`` under Creative Commons
Attribution-ShareAlike 4.0. Canada (SEDAR+), Chile (CMF), Mexico (CNBV) and
Peru (SMV) were checked and are not built; the docs page
``docs/source/sources/americas-open.md`` records why.

Datasets used (Socrata SODA API, no key needed)
    ``ur2p-h4yf``  Formato 351 of the mandatory pension funds (Moderado,
    Conservador, Mayor Riesgo, Retiro Programado, Alternativo) and the long-term
    severance (cesantías) portfolios. Monthly.
    ``hdp5-pgem``  Formato 351 of the voluntary pension funds run by pension
    fund managers, trust companies (fiduciarias, most of them bank-owned) and
    life insurers. Semi-annual (June and December).
    ``hds9-4524``  daily valuation of the mandatory funds (Formato 491): the
    fund value at close used as net assets.
    ``gpzw-wmxd``  daily valuation of the voluntary funds: the fund value at
    close used as net assets.

Formato 351 gives market values in pesos and no weights, so a line's weight is
``valor_mercado_o_pres_pesos`` over the fund value at close on the same date,
times 100. Never rescaled: what the portfolio does not cover (receivables,
payables, derivatives reported on other formats) is left unresolved.

These funds are not securities and have no ISIN. Products are keyed by the
SFC's own codes, ``<FPO|FPV>:<entity type>:<entity code>:<fund code>``.

Personal data. Some issuers in the voluntary funds are individuals (private
loans and *libranzas*). Issuer tax ids are never downloaded, the entity's own
line number is dropped, and the issuer name is kept only when the issuer id is
of a kind used for legal entities (:data:`ENTITY_ID_TYPES`); otherwise the line
is named after its investment class.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .. import http
from ..identifiers import is_valid_isin

logger = logging.getLogger(__name__)

HOST = "https://www.datos.gov.co"
RESOURCE = HOST + "/resource/{id}.json"
DATASET_PAGE = HOST + "/d/{id}"

#: Formato 351 datasets by fund family.
PORTFOLIO_DATASETS = {
    "FPO": "ur2p-h4yf",  # mandatory pensions and long-term cesantías, monthly
    "FPV": "hdp5-pgem",  # voluntary pensions, semi-annual
}
#: Daily valuation datasets that give each fund's value at close.
VALUATION_DATASETS = {"FPO": "hds9-4524", "FPV": "gpzw-wmxd"}

FAMILY_LABEL = {"FPO": "mandatory pension or severance fund", "FPV": "voluntary pension fund"}

LICENCE = (
    "Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0), as stated on each "
    "dataset's page on datos.gov.co (\"Licencia: Creative Commons Attribution | Share Alike 4.0 "
    "International\"). Reuse and republication are allowed with credit to the Superintendencia "
    "Financiera de Colombia, a link to the licence, an indication of changes, and adapted data shared "
    "under the same licence."
)
LICENCE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
CREDIT = (
    "Superintendencia Financiera de Colombia (SFC), Formato 351 – Composición del portafolio de "
    "inversiones, via datos.gov.co, CC BY-SA 4.0; weights computed by openbasket"
)

#: ``tipo_id_emisor`` codes seen only on legal entities: 3 Colombian tax id (NIT), 4 and 5 foreign
#: or special-purpose entities, 7 foreign issuers and funds, 8 local funds. 0 (none), 1 (personal
#: id card) and 2 are treated as possibly personal and the issuer name is not kept.
ENTITY_ID_TYPES = frozenset({"3", "4", "5", "7", "8"})

#: Columns read from both Formato 351 datasets. Issuer tax ids, guarantor and custodian ids and
#: the entity's own line numbers are deliberately not requested.
PORTFOLIO_FIELDS = [
    "fecha_de_corte", "tipo_de_entidad", "c_digo_tipo_entidad", "nombre_de_entidad",
    "c_digo_patrimonio", "nombre_patrimonio", "unidad_captura", "tipo_id_emisor", "razon_social_emisor",
    "clase_inversion", "descripcion_clase_inversion", "nemotecnico", "no_iden_asignado_custodio",
    "codigo_moneda", "valor_mercado_o_pres_pesos",
]

POSITION_COLUMNS = [
    "family", "period", "entity_type", "entity_code", "manager", "fund_code", "fund_name", "isin",
    "local_code", "issuer", "investment_class", "investment_class_name", "currency", "market_value_cop",
]

PAGE_SIZE = 50_000


# -- SODA ------------------------------------------------------------------------


def _get(dataset: str, params: dict, timeout: int = 300) -> list[dict]:
    r = http.get(RESOURCE.format(id=dataset), params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _ts(day: str) -> str:
    return f"{day}T00:00:00.000"


def latest_date(dataset: str, field: str = "fecha_de_corte", on_or_before: str | None = None) -> str | None:
    """Newest ``field`` value in ``dataset`` (ISO date), optionally not after ``on_or_before``."""
    params = {"$select": f"max({field}) as d"}
    if on_or_before:
        params["$where"] = f"{field} <= '{_ts(on_or_before)}'"
    rows = _get(dataset, params)
    d = rows[0].get("d") if rows else None
    return d[:10] if d else None


def fetch_portfolio(dataset: str, day: str, where: str | None = None) -> list[dict]:
    """Every Formato 351 line of one cut-off date, only :data:`PORTFOLIO_FIELDS`, in pages.

    ``where`` narrows the query further (SoQL), e.g. to one fund.
    """
    out: list[dict] = []
    offset = 0
    cond = f"fecha_de_corte = '{_ts(day)}'" + (f" AND ({where})" if where else "")
    while True:
        page = _get(dataset, {
            "$select": ",".join(PORTFOLIO_FIELDS),
            "$where": cond,
            "$order": ":id",
            "$limit": PAGE_SIZE,
            "$offset": offset,
        })
        out.extend(page)
        if len(page) < PAGE_SIZE:
            return out
        offset += PAGE_SIZE


def fetch_net_assets(family: str, day: str) -> tuple[str | None, list[dict]]:
    """Fund values at close on ``day``, or on the latest valuation date before it."""
    ds = VALUATION_DATASETS[family]
    vday = latest_date(ds, "fecha_corte", on_or_before=day)
    if vday is None:
        return None, []
    if family == "FPO":
        # Formato 491, value column (2). Pension funds: capture unit 4, line 305, the fund value at
        # close after the day's returns. Cesantías: unit 3, line 110, the same for the portfolio.
        rows = _get(ds, {
            "$select": "fecha_corte,tipo_entidad,codigo_entidad,codigo_patrimonio,cod_unid_capt,cod_renglon,sum_valor",
            "$where": (f"fecha_corte = '{_ts(vday)}' AND codigo_columna = 2 AND "
                       "((cod_unid_capt = 4 AND cod_renglon = 305) OR (cod_unid_capt = 3 AND cod_renglon = 110))"),
            "$limit": PAGE_SIZE,
        })
        return vday, [{"entity_type": r.get("tipo_entidad"), "entity_code": r.get("codigo_entidad"),
                       "fund_code": r.get("codigo_patrimonio"), "net_assets_cop": r.get("sum_valor")} for r in rows]
    rows = _get(ds, {
        "$select": "fecha_corte,tipo_entidad,codigo_entidad,codigo_patrimonio,valor_fondo_cierre_dia_t_2",
        "$where": f"fecha_corte = '{_ts(vday)}'",
        "$limit": PAGE_SIZE,
    })
    return vday, [{"entity_type": r.get("tipo_entidad"), "entity_code": r.get("codigo_entidad"),
                   "fund_code": r.get("codigo_patrimonio"), "net_assets_cop": r.get("valor_fondo_cierre_dia_t_2")}
                  for r in rows]


# -- conversion ------------------------------------------------------------------


def _str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return None if s in ("", "N/A", "NA", "0") or set(s) == {"0"} else s


def _num(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _isin(*candidates) -> str | None:
    for c in candidates:
        s = _str(c)
        if s and is_valid_isin(s.upper()):
            return s.upper()
    return None


def _clean_name(s: str | None) -> str | None:
    return " ".join(s.replace('"', " ").split()) if s else None


def convert_portfolio(family: str, rows: list[dict]) -> pd.DataFrame:
    """Raw SODA rows to :data:`POSITION_COLUMNS`, with personal data removed."""
    out = []
    for r in rows:
        id_type = _str(r.get("tipo_id_emisor"))
        issuer = _clean_name(r.get("razon_social_emisor")) if id_type in ENTITY_ID_TYPES else None
        out.append({
            "family": family,
            "period": (r.get("fecha_de_corte") or "")[:10] or None,
            "entity_type": _str(r.get("tipo_de_entidad")),
            "entity_code": _str(r.get("c_digo_tipo_entidad")),
            "manager": _clean_name(r.get("nombre_de_entidad")),
            "fund_code": _str(r.get("c_digo_patrimonio")),
            "fund_name": _clean_name(r.get("nombre_patrimonio")),
            "isin": _isin(r.get("no_iden_asignado_custodio"), r.get("nemotecnico")),
            "local_code": _str(r.get("nemotecnico")),
            "issuer": issuer,
            "investment_class": _str(r.get("clase_inversion")),
            "investment_class_name": _clean_name(r.get("descripcion_clase_inversion")),
            "currency": _str(r.get("codigo_moneda")),
            "market_value_cop": _num(r.get("valor_mercado_o_pres_pesos")),
        })
    return _nulls_to_none(pd.DataFrame(out, columns=POSITION_COLUMNS))


def _nulls_to_none(df: pd.DataFrame) -> pd.DataFrame:
    """Missing values in text columns as None (pandas 3 would give NaN)."""
    for c in df.columns:
        if not pd.api.types.is_numeric_dtype(df[c]):
            df[c] = pd.Series([None if pd.isna(v) else v for v in df[c]], index=df.index, dtype=object)
    return df


def asset_class(code: str | None, label: str | None = None) -> str:
    """SFC ``clase_inversion`` to the normalised asset class."""
    c = (code or "").upper()
    if c.startswith("DEPV"):
        return "cash"
    if c.startswith(("AAE", "ABE", "AME", "AEE", "CRA")):
        return "equity"
    if c.startswith(("CC", "PCC", "FCP", "FIND", "PFIMM", "PFMU", "PFPC", "REITS")):
        return "fund"
    if c.startswith(("BO", "BG", "BHIP", "BPEN", "CDT", "CDE", "TS", "TDP", "TDE", "ORF", "TCC", "TDCE", "PEE")):
        return "debt"
    if c.startswith(("TPE", "TPM", "DPAT")):
        return "other"
    lab = (label or "").lower()
    if any(w in lab for w in ("forward", "swap", "opcion", "opción", "futuro")):
        return "derivative"
    if any(w in lab for w in ("repo", "simultánea", "simultanea")):
        return "repurchase agreement"
    return "other"


def _key(df: pd.DataFrame) -> pd.Series:
    return df["family"] + ":" + df["entity_type"].fillna("") + ":" + df["entity_code"].fillna("") + ":" + df["fund_code"].fillna("")


def fund_lines(positions: pd.DataFrame, net_assets: float) -> pd.DataFrame:
    """One fund's lines in :data:`~openbasket.sources.issuers.HOLDING_COLUMNS`.

    Lines that are the same security (same ISIN or local code, issuer, class and currency) are
    summed, which only merges lots: the weights are still value / net assets.
    """
    p = positions.copy()
    keys = ["isin", "local_code", "issuer", "investment_class", "investment_class_name", "currency"]
    for k in keys:
        p[k] = p[k].fillna("")
    g = p.groupby(keys, sort=False, as_index=False)["market_value_cop"].sum()
    g = g.replace({k: {"": None} for k in keys})
    name = g["issuer"].fillna(g["investment_class_name"]).fillna("Unnamed position")
    with_code = g["local_code"].notna() & (g["local_code"] != g["isin"])
    name = name.where(~with_code, name + " (" + g["local_code"].fillna("") + ")")
    out = pd.DataFrame({
        "isin": g["isin"],
        "cusip": None,
        "sedol": None,
        "ticker": None,
        "name": name,
        "weight_pct": g["market_value_cop"].astype(float) / float(net_assets) * 100.0,
        "asset_class": [asset_class(c, n) for c, n in zip(g["investment_class"], g["investment_class_name"])],
        "currency": g["currency"],
        "country": None,
        "local_code": g["local_code"],
        "investment_class": g["investment_class"],
        "investment_class_name": g["investment_class_name"],
        "market_value_cop": g["market_value_cop"],
    })
    return _nulls_to_none(out.sort_values("weight_pct", ascending=False, ignore_index=True))


# -- store -----------------------------------------------------------------------


class SfcColombiaStore:
    """The latest Formato 351 cut-off of each fund family, converted to Parquet under ``root``.

    Layout: ``<root>/<family>/<YYYY-MM-DD>/positions.parquet`` and ``net_assets.parquet``, plus
    ``meta.json`` with the valuation date used. Raw API rows are never written to disk.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._cache: dict[str, tuple[str, dict, pd.DataFrame, pd.DataFrame]] = {}

    # building
    def refresh(self, families: tuple[str, ...] = ("FPO", "FPV")) -> pd.DataFrame:
        rows = []
        for fam in families:
            ds = PORTFOLIO_DATASETS[fam]
            day = latest_date(ds)
            if day is None:
                raise ValueError(f"datos.gov.co dataset {ds} returned no cut-off date")
            out = self.root / fam / day
            if not (out / "positions.parquet").exists():
                logger.info("Downloading SFC Formato 351 %s for %s", fam, day)
                positions = convert_portfolio(fam, fetch_portfolio(ds, day))
                vday, na = fetch_net_assets(fam, day)
                net = pd.DataFrame(na, columns=["entity_type", "entity_code", "fund_code", "net_assets_cop"])
                net["net_assets_cop"] = pd.to_numeric(net["net_assets_cop"], errors="coerce")
                out.mkdir(parents=True, exist_ok=True)
                positions.to_parquet(out / "positions.parquet", index=False)
                net.to_parquet(out / "net_assets.parquet", index=False)
                meta = {"family": fam, "period": day, "valuation_date": vday, "portfolio_dataset": ds,
                        "valuation_dataset": VALUATION_DATASETS[fam], "lines": len(positions),
                        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                (out / "meta.json").write_text(json.dumps(meta, indent=2))
            meta = json.loads((out / "meta.json").read_text())
            rows.append(meta)
        self._cache.clear()
        return pd.DataFrame(rows)

    def latest(self, family: str) -> str | None:
        d = self.root / family
        got = sorted(p.name for p in d.iterdir() if (p / "positions.parquet").exists()) if d.exists() else []
        return got[-1] if got else None

    def is_built(self) -> bool:
        return all(self.latest(f) for f in PORTFOLIO_DATASETS)

    def _load(self, family: str) -> tuple[str, dict, pd.DataFrame, pd.DataFrame]:
        if family not in self._cache:
            period = self.latest(family)
            if period is None:
                raise FileNotFoundError(f"No SFC {family} data under {self.root}: run SfcColombiaStore.refresh()")
            d = self.root / family / period
            pos = pd.read_parquet(d / "positions.parquet")
            net = pd.read_parquet(d / "net_assets.parquet")
            for c in ("entity_type", "entity_code", "fund_code"):
                net[c] = net[c].astype(str)
            meta = json.loads((d / "meta.json").read_text())
            self._cache[family] = (period, meta, pos, net)
        return self._cache[family]

    # reading
    def products(self) -> pd.DataFrame:
        frames = []
        for fam in PORTFOLIO_DATASETS:
            if self.latest(fam) is None:
                continue
            period, meta, pos, net = self._load(fam)
            funds = (pos.groupby(["family", "entity_type", "entity_code", "fund_code"], dropna=False)
                     .agg(manager=("manager", "first"), fund_name=("fund_name", "first"),
                          lines=("market_value_cop", "size"), portfolio_value_cop=("market_value_cop", "sum"),
                          lines_with_isin=("isin", "count"))
                     .reset_index())
            funds = funds.merge(net, on=["entity_type", "entity_code", "fund_code"], how="left")
            funds["portfolio_id"] = _key(funds)
            funds["as_of"] = period
            frames.append(funds)
        if not frames:
            return pd.DataFrame()
        f = pd.concat(frames, ignore_index=True)
        return pd.DataFrame({
            "issuer": "sfc_colombia",
            "site": "co",
            "portfolio_id": f["portfolio_id"],
            "isin": None,
            "ticker": None,
            "fund_name": f["fund_name"],
            "domicile": "CO",
            "currency": "COP",
            "asset_class": f["family"].map(FAMILY_LABEL),
            "replication": None,
            "product_page_url": f["family"].map(lambda x: DATASET_PAGE.format(id=PORTFOLIO_DATASETS[x])),
            "holdings_url": f["family"].map(lambda x: RESOURCE.format(id=PORTFOLIO_DATASETS[x])),
            "manager": f["manager"],
            "family": f["family"],
            "as_of": f["as_of"],
            "lines": f["lines"],
            "lines_with_isin": f["lines_with_isin"],
            "net_assets_cop": f["net_assets_cop"],
            "portfolio_value_cop": f["portfolio_value_cop"],
        }).sort_values(["family", "manager", "fund_name"], ignore_index=True)

    def holdings(self, portfolio_id: str) -> tuple[str, pd.DataFrame, list[str]]:
        family, etype, ecode, fcode = portfolio_id.split(":")
        period, meta, pos, net = self._load(family)
        sel = pos[(pos["entity_type"] == etype) & (pos["entity_code"] == ecode) & (pos["fund_code"] == fcode)]
        if sel.empty:
            raise LookupError(f"SFC Formato 351 has no lines for {portfolio_id} on {period}")
        na = net[(net["entity_type"] == etype) & (net["entity_code"] == ecode) & (net["fund_code"] == fcode)]
        total = float(na["net_assets_cop"].iloc[0]) if len(na) and pd.notna(na["net_assets_cop"].iloc[0]) else None
        if not total:
            raise LookupError(f"No fund value at close for {portfolio_id} on {meta.get('valuation_date')}")
        frame = fund_lines(sel, total)
        notes = [
            f"regulatory filing: SFC Formato 351 portfolio of a Colombian {FAMILY_LABEL[family]}, "
            f"positions as of {period}",
            f"weights are market value in pesos / fund value at close on {meta.get('valuation_date')} "
            f"({VALUATION_DATASETS[family]}), computed by openbasket: the filing gives values only",
            "derivatives, receivables and payables are not in Formato 351; the remainder is unresolved",
            f"licence: CC BY-SA 4.0 ({LICENCE_URL}); adapted data must be shared under the same licence",
        ]
        return period, frame, notes
