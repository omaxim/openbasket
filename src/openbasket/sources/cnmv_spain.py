"""Spain: CNMV quarterly security-by-security portfolios of every fund and SICAV.

The Comisión Nacional del Mercado de Valores publishes the information Spanish
collective investment schemes file under Circular 4/2008 as "ficheros de
información individual de IIC":

Page
    ``https://www.cnmv.es/portal/publicaciones/descarga-informacion-individual``
    (current year; ``?ejercicio=YYYY`` for earlier years) links one ZIP per
    month through ``/webservices/verdocumento/ver?e=<token>``. The ZIP for a
    quarter-end month (March, June, September, December) also carries the
    quarterly files.
Files used, all XML with an XSD and an explanatory PDF in the same ZIP
    ``FONDREGISTRO`` / ``SOCREGISTRO``: every fund (FI) and SICAV with its
    compartments and the ISIN of each share class or series.
    ``FONDCART`` / ``SOCCART``: every security held at quarter end, with
    ``ClaseIF`` (domestic, foreign, doubtful), ``DescripcionIF`` (kind of
    investment), ``CodigoISIN``, ``DescripcionValor``, ``Divisa`` and
    ``ValorMercado`` in euros.
    ``FONDPATRIMDISVAR`` / ``SOCPATRIMDISVAR``: net assets
    (``TotalPatrimonio``) per compartment, split into financial investments,
    accrued interest, cash (``Liquidez``) and the rest.

Weights are ``ValorMercado / TotalPatrimonio * 100`` for the same compartment
and quarter: never rescaled. Cash, accrued interest and "rest" from the net
asset breakdown are added as unidentified lines so the weights add up to the
fund. Derivatives (``FONDDERI``) are reported as commitment amounts, not market
values, and are not included.
"""

from __future__ import annotations

import html
import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pandas as pd

from .. import http
from ..identifiers import is_valid_isin

logger = logging.getLogger(__name__)

#: Redistribution class (see :func:`openbasket.sources.redistribution`):
#: CNMV allows faithful reproduction and calculations on its public data.
REDISTRIBUTION = "public"

HOST = "https://www.cnmv.es"
PAGE_URL = HOST + "/portal/publicaciones/descarga-informacion-individual"
DOC_URL = HOST + "/webservices/verdocumento/ver?e={token}"
CREDIT = (
    "Comisión Nacional del Mercado de Valores (CNMV), ficheros de información individual de IIC "
    "(FONDCART/SOCCART quarterly portfolios)"
)

_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
_LINK_RE = re.compile(r'href="(https://www\.cnmv\.es/webservices/verdocumento/ver\?e=([^"]+))"[^>]*title="([^"]+)"', re.I)
_YEAR_RE = re.compile(r"Ficheros de informaci[^(]*\((\d{4})\)", re.I)

#: CNMV ``DescripcionIF`` values to the normalised asset class.
ASSET_CLASS = {
    "Renta Variable Cotizada": "equity",
    "Renta Variable No Cotizada": "equity",
    "Renta Fija Cotizada Mas 1 Anio": "debt",
    "Renta Fija Cotizada Menos 1 Anio": "debt",
    "Renta Fija No Cotizada": "debt",
    "Deuda Publica Cotizada Mas 1 Anio": "debt",
    "Deuda Publica Cotizada Memos 1 Anio": "debt",
    "Deuda Publica Cotizada Menos 1 Anio": "debt",
    "Adquisicion Temporal Activos": "repurchase agreement",
    "Depositos": "cash",
    "IIC": "fund",
    "Entidades de Capital Riesgo": "fund",
    "InversionesDudosas": "debt",
}

POSITION_COLUMNS = [
    "period", "kind", "registro", "compartimento", "clase_if", "descripcion_if", "isin", "descripcion",
    "divisa", "valor_mercado",
]


def parse_page(text: str) -> dict[str, str]:
    """``{YYYYMM: token}`` for every monthly ZIP linked on one year's page."""
    year = _YEAR_RE.search(html.unescape(re.sub(r"<[^>]+>", " ", text)))
    if not year:
        return {}
    out = {}
    for _, token, title in _LINK_RE.findall(text):
        m = _MONTHS.get(html.unescape(title).strip().lower())
        if m:
            out[f"{year.group(1)}{m:02d}"] = html.unescape(token)
    return out


def list_periods(years: int = 2) -> dict[str, str]:
    """Monthly ZIP tokens for the current year and ``years - 1`` before it."""
    r = http.get(PAGE_URL)
    r.raise_for_status()
    out = parse_page(r.text)
    if out:
        first = int(min(out)[:4])
        for y in range(first - 1, first - years, -1):
            r = http.get(PAGE_URL, params={"ejercicio": y})
            if r.ok:
                out.update(parse_page(r.text))
    return out


def latest_quarter(periods: dict[str, str]) -> str | None:
    q = [p for p in periods if p[4:] in ("03", "06", "09", "12")]
    return max(q) if q else None


def download_period(period: str, token: str, raw_dir: str | Path) -> Path:
    dest = Path(raw_dir) / f"cnmv_iic_{period}.zip"
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading CNMV individual IIC files %s", period)
    r = http.get(DOC_URL.format(token=token), timeout=300)
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        raise ValueError(f"CNMV {period}: expected a ZIP, got {r.headers.get('content-type')}")
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(r.content)
    tmp.replace(dest)
    return dest


# -- XML ------------------------------------------------------------------


def _text(el: ET.Element, tag: str) -> str | None:
    child = el.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip() or None


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def _entities(fp):
    """Yield ``(period, Entidad element)`` without building the whole tree."""
    period = None
    for event, el in ET.iterparse(fp, events=("end",)):
        if el.tag == "FechaDatos":
            period = (el.text or "").strip()
        elif el.tag == "Entidad":
            yield period, el
            el.clear()


def parse_cart(fp) -> pd.DataFrame:
    """``FONDCART`` / ``SOCCART`` to one row per security (:data:`POSITION_COLUMNS`)."""
    rows = []
    for period, ent in _entities(fp):
        kind, reg = _text(ent, "Tipo"), _text(ent, "NumeroRegistro")
        for comp in ent.findall("Compartimento"):
            nc = _text(comp, "NumeroCompartimento")
            for inv in comp.findall("InversionesFinancieras"):
                rows.append(
                    {
                        "period": period, "kind": kind, "registro": reg, "compartimento": nc,
                        "clase_if": _text(inv, "ClaseIF"), "descripcion_if": _text(inv, "DescripcionIF"),
                        "isin": _text(inv, "CodigoISIN"), "descripcion": _text(inv, "DescripcionValor"),
                        "divisa": _text(inv, "Divisa"), "valor_mercado": _num(_text(inv, "ValorMercado")),
                    }
                )
    return pd.DataFrame(rows, columns=POSITION_COLUMNS)


def parse_patrimonio(fp) -> pd.DataFrame:
    """``FONDPATRIMDISVAR`` / ``SOCPATRIMDISVAR``: net assets and their split per compartment."""
    rows = []
    fields = ["DPInversionesFinancieras", "InteresesCartera", "Liquidez", "Resto", "TotalPatrimonio"]
    for period, ent in _entities(fp):
        kind, reg, cur = _text(ent, "Tipo"), _text(ent, "NumeroRegistro"), _text(ent, "CodigoDivisaIIC")
        for comp in ent.findall("Compartimento"):
            row = {"period": period, "kind": kind, "registro": reg,
                   "compartimento": _text(comp, "NumeroCompartimento"), "currency": cur}
            row.update({f: _num(_text(comp, f)) for f in fields})
            rows.append(row)
    return pd.DataFrame(rows)


def parse_registro(fp) -> pd.DataFrame:
    """``FONDREGISTRO`` / ``SOCREGISTRO``: one row per share class (or SICAV series) ISIN."""
    rows = []
    for period, ent in _entities(fp):
        kind, reg = _text(ent, "Tipo"), _text(ent, "NumeroRegistro")
        name, etf = _text(ent, "Denominacion"), _text(ent, "ETF")
        g = ent.find("Gestora")
        manager = _text(g, "DenominacionGestora") if g is not None else None
        for comp in ent.findall("Compartimento"):
            nc, cname = _text(comp, "NumeroCompartimento"), _text(comp, "DenominacionCompartimento")
            for cls in list(comp.findall("Clase")) + list(comp.findall("Serie")):
                rows.append(
                    {
                        "period": period, "kind": kind, "registro": reg, "compartimento": nc,
                        "fund_name": name, "compartment_name": cname,
                        "class_name": _text(cls, "DenominacionClase") or _text(cls, "DenominacionSerie"),
                        "isin": _text(cls, "ISIN"), "etf": etf == "SI", "manager": manager,
                    }
                )
    return pd.DataFrame(rows)


def read_zip(content: bytes) -> dict[str, pd.DataFrame]:
    """Positions, net assets and share classes of funds and SICAVs from one quarterly ZIP."""
    out: dict[str, list[pd.DataFrame]] = {"positions": [], "patrimonio": [], "registro": []}
    parsers = {"CART": ("positions", parse_cart), "PATRIMDISVAR": ("patrimonio", parse_patrimonio),
               "REGISTRO": ("registro", parse_registro)}
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        for name in z.namelist():
            m = re.match(r"^(?:.*/)?(FOND|SOC)(CART|PATRIMDISVAR|REGISTRO)_(\d{6})\.xml$", name, re.I)
            if not m:
                continue
            key, parse = parsers[m.group(2).upper()]
            with z.open(name) as fp:
                out[key].append(parse(fp))
    if not out["positions"]:
        raise ValueError("ZIP has no FONDCART/SOCCART file: not a quarter-end month")
    return {k: pd.concat(v, ignore_index=True) if v else pd.DataFrame() for k, v in out.items()}


# -- one fund -------------------------------------------------------------


def _nulls_to_none(df: pd.DataFrame) -> pd.DataFrame:
    """Missing values in text columns as None (pandas 3 would give NaN)."""
    for c in df.columns:
        if not pd.api.types.is_numeric_dtype(df[c]):
            df[c] = pd.Series([None if pd.isna(v) else v for v in df[c]], index=df.index, dtype=object)
    return df


def fund_positions(positions: pd.DataFrame, patrimonio: dict) -> pd.DataFrame:
    """One compartment's lines in :data:`~openbasket.sources.issuers.HOLDING_COLUMNS`.

    ``patrimonio`` is that compartment's row of :func:`parse_patrimonio`.
    """
    total = float(patrimonio["TotalPatrimonio"])
    p = positions.reset_index(drop=True)
    repo = p["descripcion_if"].eq("Adquisicion Temporal Activos")
    isin = p["isin"].map(lambda v: v.upper() if isinstance(v, str) and is_valid_isin(v.upper()) else None)
    # A reverse repo is cash lent against the bond: the ISIN is the collateral.
    name = p["descripcion"].fillna(p["descripcion_if"]).astype(str)
    name = name.where(~repo, "Reverse repo backed by " + p["descripcion"].fillna("") + " " + p["isin"].fillna(""))
    out = pd.DataFrame(
        {
            "isin": isin.where(~repo, None),
            "cusip": None,
            "sedol": None,
            "ticker": None,
            "name": name.str.replace("|", " ", regex=False).str.strip(),
            "weight_pct": p["valor_mercado"].astype(float) / total * 100.0,
            "asset_class": p["descripcion_if"].map(ASSET_CLASS).fillna("other"),
            "currency": p["divisa"],
            "country": None,
            "portfolio_section": p["clase_if"],
            "kind": p["descripcion_if"],
            "market_value_eur": p["valor_mercado"],
        }
    )
    extra = []
    for field, label, cls in (
        ("Liquidez", "Cash (liquidez, from the net asset breakdown)", "cash"),
        ("InteresesCartera", "Accrued interest on the portfolio", "other"),
        ("Resto", "Other assets and liabilities (resto, from the net asset breakdown)", "other"),
    ):
        v = patrimonio.get(field)
        if v is not None and not pd.isna(v) and v != 0:
            extra.append({"isin": None, "cusip": None, "sedol": None, "ticker": None, "name": label,
                          "weight_pct": float(v) / total * 100.0, "asset_class": cls,
                          "currency": patrimonio.get("currency"), "country": None,
                          "portfolio_section": "PATRIMONIO", "kind": field, "market_value_eur": v})
    if extra:
        out = pd.concat([out, pd.DataFrame(extra)], ignore_index=True)
    return _nulls_to_none(out)


def quarter_end(period: str) -> str:
    return (pd.Period(f"{period[:4]}-{period[4:]}", "M").end_time.date()).isoformat()


class CnmvStore:
    """The latest quarterly ZIP, parsed to Parquet under ``root``."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._frames: dict[str, pd.DataFrame] | None = None
        self._period: str | None = None

    def refresh(self) -> dict:
        periods = list_periods()
        period = latest_quarter(periods)
        if period is None:
            raise ValueError("No quarter-end ZIP found on the CNMV page")
        zip_path = download_period(period, periods[period], self.root / "raw")
        out = self.root / "parquet" / period
        if not (out / "positions.parquet").exists():
            frames = read_zip(zip_path.read_bytes())
            out.mkdir(parents=True, exist_ok=True)
            for k, df in frames.items():
                df.to_parquet(out / f"{k}.parquet", index=False)
        self._frames = None
        return {"period": period, "zip_path": str(zip_path), "parquet_dir": str(out)}

    def latest(self) -> str | None:
        d = self.root / "parquet"
        got = sorted(p.name for p in d.iterdir() if (p / "positions.parquet").exists()) if d.exists() else []
        return got[-1] if got else None

    def is_built(self) -> bool:
        return self.latest() is not None

    def frames(self) -> dict[str, pd.DataFrame]:
        if self._frames is None:
            period = self.latest()
            if period is None:
                raise FileNotFoundError(f"No CNMV data under {self.root}: run CnmvStore.refresh()")
            d = self.root / "parquet" / period
            self._frames = {k: pd.read_parquet(d / f"{k}.parquet") for k in ("positions", "patrimonio", "registro")}
            self._period = period
        return self._frames

    def products(self) -> pd.DataFrame:
        f = self.frames()
        reg = f["registro"]
        reg = reg[reg["isin"].map(lambda v: isinstance(v, str) and is_valid_isin(v))]
        has = f["patrimonio"][["kind", "registro", "compartimento", "currency"]]
        reg = reg.merge(has, on=["kind", "registro", "compartimento"], how="inner")
        name = reg["fund_name"].where(
            reg["compartment_name"].fillna("").str.upper().str.startswith("COMPARTIMENTO 0"),
            reg["fund_name"] + " - " + reg["compartment_name"].fillna(""),
        )
        return pd.DataFrame(
            {
                "issuer": "cnmv_spain",
                "site": "es",
                "portfolio_id": reg["kind"] + ":" + reg["registro"] + ":" + reg["compartimento"],
                "isin": reg["isin"],
                "ticker": None,
                "fund_name": name + " " + reg["class_name"].fillna(""),
                "domicile": "ES",
                "currency": reg["currency"],
                "asset_class": reg["etf"].map({True: "ETF", False: None}),
                "replication": None,
                "product_page_url": PAGE_URL,
                "holdings_url": PAGE_URL,
                "manager": reg["manager"],
            }
        ).drop_duplicates("isin").reset_index(drop=True)

    def holdings(self, portfolio_id: str) -> tuple[str, pd.DataFrame, list[str]]:
        kind, reg, comp = portfolio_id.split(":")
        f = self.frames()
        key = lambda df: (df["kind"] == kind) & (df["registro"] == reg) & (df["compartimento"] == comp)  # noqa: E731
        pat = f["patrimonio"][key(f["patrimonio"])]
        if pat.empty or not pat.iloc[0]["TotalPatrimonio"]:
            raise LookupError(f"CNMV has no net assets for {portfolio_id}")
        pat = pat.iloc[0].to_dict()
        frame = fund_positions(f["positions"][key(f["positions"])], pat)
        as_of = quarter_end(self._period)
        notes = [
            f"regulatory filing: CNMV quarterly portfolio (FONDCART/SOCCART), positions as of {as_of}",
            "weights are market value / total net assets (TotalPatrimonio) of the compartment in the same filing; "
            "cash, accrued interest and other net assets come from the filing's net asset breakdown",
            "derivatives are not included: CNMV reports them as commitment amounts (FONDDERI), not market values",
        ]
        if pat.get("currency") not in (None, "EUR"):
            notes.append(f"fund currency is {pat['currency']}; CNMV gives market values in euros, so weights "
                         "may be off by the exchange rate")
        return as_of, frame, notes
