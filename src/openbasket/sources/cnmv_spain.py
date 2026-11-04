"""Spain: CNMV quarterly security-by-security portfolios of every fund and SICAV.

The Comisión Nacional del Mercado de Valores publishes the information Spanish
collective investment schemes file under Circular 4/2008 as "ficheros de
información individual de IIC":

Page
    ``https://www.cnmv.es/portal/publicaciones/descarga-informacion-individual``
    (current year; ``?ejercicio=YYYY`` for earlier years) links one ZIP per
    month through ``/webservices/verdocumento/ver?e=<token>``, with the month
    name in Spanish as the link title. ``HEAD`` returns 405, so the ZIP is
    fetched with ``GET``. The ZIP of a quarter-end month (March, June,
    September, December) also carries the quarterly files; other months carry
    only ``FONDREGISTRO`` and ``FONDMENS``.
Files used, all XML with an XSD and an explanatory PDF in the same ZIP
    ``FONDREGISTRO`` / ``SOCREGISTRO``: every fund (FI) and SICAV with its
    compartments, the ISIN of each share class (``Clase``) or series
    (``Serie``), the ETF flag and the management company.
    ``FONDTRIM`` / ``SOCTRIM``: per share class its currency and net assets,
    per compartment the fund category (``ClaseFondo``) and investment
    vocation (``VocacionInversora``).
    ``FONDPATRIMDISVAR`` / ``SOCPATRIMDISVAR``: net assets
    (``TotalPatrimonio``) per compartment, split into domestic and foreign
    portfolio, accrued interest, doubtful assets, cash (``Liquidez``) and the
    rest (``Resto``).
    ``FONDCART`` / ``SOCCART``: every cash-market security held at quarter
    end, with ``ClaseIF`` (domestic, foreign, doubtful), ``DescripcionIF``
    (kind of investment), ``CodigoISIN``, ``DescripcionValor``, ``Divisa``
    and ``ValorMercado``.
    ``FONDDERI`` / ``SOCDERI``: derivatives, as nominal commitment only. Each
    swap is filed twice, once among the rights (``Derechos``) and once among
    the obligations (``Obligaciones``), with the same nominal.

Records are keyed by ``(Tipo, NumeroRegistro, NumeroCompartimento)``; that
triple is the ``portfolio_id`` (``FI:208:0``), so every share class of a
compartment shares it.

Weights are ``ValorMercado / TotalPatrimonio * 100`` for the same compartment
and quarter: never rescaled. Cash, accrued interest and "rest" from the net
asset breakdown are added as unidentified lines so the weights add up to the
fund. Derivatives are reported as commitment amounts, not market values, and
are not lines; a note gives their nominal per side.
"""

from __future__ import annotations

import html
import io
import json
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pandas as pd

from .. import http
from ..identifiers import is_valid_isin

logger = logging.getLogger(__name__)

#: Redistribution class (see :func:`openbasket.sources.redistribution`).
#: CNMV's legal notice (https://www.cnmv.es/portal/Utilidades/NotaLegal,
#: read 2026-11-03) authorises reuse of the information on its website,
#: including commercial reuse and publishing calculations made from it, on
#: condition that it is reproduced faithfully without altering its content,
#: and that anyone selling a product built on it tells buyers the information
#: is free on cnmv.es.
REDISTRIBUTION = "public"

HOST = "https://www.cnmv.es"
PAGE_URL = HOST + "/portal/publicaciones/descarga-informacion-individual"
DOC_URL = HOST + "/webservices/verdocumento/ver?e={token}"
#: CNMV's own page for one share class (links to cnmv.es must open in a new window).
FUND_PAGE_URL = HOST + "/portal/consultas/iic/fondo?isin={isin}"
CREDIT = (
    "Comisión Nacional del Mercado de Valores (CNMV), ficheros de información individual de IIC "
    "(FONDCART/SOCCART quarterly portfolios)"
)
LICENCE = (
    "CNMV legal notice (Nota legal): reuse beyond private consultation, including publishing calculations "
    "made from the information, is authorised if the information is reproduced faithfully, without altering "
    "it; anyone selling it must tell buyers it is free on cnmv.es. Weights here are calculated from the "
    "reported values; names and codes are CNMV's."
)

#: Version of the Parquet layout under ``<root>/parquet/<period>``.
STORE_VERSION = 2

_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
_LINK_RE = re.compile(r'href="(https://www\.cnmv\.es/webservices/verdocumento/ver\?e=([^"]+))"[^>]*title="([^"]+)"', re.I)
_YEAR_RE = re.compile(r"Ficheros de informaci[^(]*\((\d{4})\)", re.I)
_FILE_RE = re.compile(r"^(FOND|SOC)(CART|PATRIMDISVAR|REGISTRO|TRIM|DERI)_(\d{6})\.xml$", re.I)
_SWAP_RE = re.compile(r"permuta|swap|\bTRS\b|\bCDS\b", re.I)
#: Interest-rate swaps (and futures CNMV files under *permutas*) change
#: duration, not what the fund holds, so they do not make a fund swap-based.
_RATE_RE = re.compile(r"\bIRS\b|tipo de inter|euribor|sonia|\bsofr\b|\bestr\b|\bFUT\b|FUT\.|futuro", re.I)

POSITION_COLUMNS = [
    "period", "kind", "registro", "compartimento", "clase_if", "descripcion_if", "isin", "descripcion",
    "divisa", "valor_mercado",
]
_PATRIMONIO_FIELDS = [
    "DPInversionesFinancieras", "CarteraInterior", "CarteraExterior", "InteresesCartera",
    "InversionesDudosas", "Liquidez", "Resto", "TotalPatrimonio",
]
_FRAMES = ("positions", "patrimonio", "registro", "trim", "derivatives")


def _fold(text: object) -> str:
    """Lower case without accents, for matching CNMV's labels."""
    if not isinstance(text, str) or not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).strip().lower()


# -- listing and downloads ------------------------------------------------


def parse_page(text: str) -> dict[str, str]:
    """``{YYYYMM: token}`` for every monthly ZIP linked on one year's page."""
    year = _YEAR_RE.search(html.unescape(re.sub(r"<[^>]+>", " ", text)))
    if not year:
        return {}
    out = {}
    for _, token, title in _LINK_RE.findall(text):
        m = _MONTHS.get(_fold(html.unescape(title)))
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


def quarter_periods(periods: dict[str, str]) -> list[str]:
    """Quarter-end months among ``periods``, newest first."""
    return sorted((p for p in periods if p[4:] in ("03", "06", "09", "12")), reverse=True)


def latest_quarter(periods: dict[str, str]) -> str | None:
    q = quarter_periods(periods)
    return q[0] if q else None


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


def _text(el: ET.Element | None, tag: str) -> str | None:
    if el is None:
        return None
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
    """Yield ``(period, Entidad element)`` without building the whole tree.

    ``period`` is the file's ``FechaDatos`` (``YYYYMM``), which comes before
    the first ``Entidad``.
    """
    period = None
    for _, el in ET.iterparse(fp, events=("end",)):
        if el.tag == "FechaDatos":
            period = (el.text or "").strip()
        elif el.tag == "Entidad":
            yield period, el
            el.clear()


def _classes(comp: ET.Element) -> list[ET.Element]:
    # FOND files call share classes Clase, SOC files call them Serie.
    return list(comp.findall("Clase")) + list(comp.findall("Serie"))


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
    for period, ent in _entities(fp):
        kind, reg, cur = _text(ent, "Tipo"), _text(ent, "NumeroRegistro"), _text(ent, "CodigoDivisaIIC")
        for comp in ent.findall("Compartimento"):
            row = {"period": period, "kind": kind, "registro": reg,
                   "compartimento": _text(comp, "NumeroCompartimento"), "currency": cur}
            row.update({f: _num(_text(comp, f)) for f in _PATRIMONIO_FIELDS})
            rows.append(row)
    return pd.DataFrame(rows, columns=["period", "kind", "registro", "compartimento", "currency", *_PATRIMONIO_FIELDS])


def parse_registro(fp) -> pd.DataFrame:
    """``FONDREGISTRO`` / ``SOCREGISTRO``: one row per share class (or SICAV series) ISIN."""
    rows = []
    for period, ent in _entities(fp):
        kind, reg = _text(ent, "Tipo"), _text(ent, "NumeroRegistro")
        name, etf = _text(ent, "Denominacion"), _text(ent, "ETF")
        g = ent.find("Gestora")
        group = g.find("GrupoGestora") if g is not None else None
        for comp in ent.findall("Compartimento"):
            nc, cname = _text(comp, "NumeroCompartimento"), _text(comp, "DenominacionCompartimento")
            classes = _classes(comp)
            for cls in classes:
                rows.append(
                    {
                        "period": period, "kind": kind, "registro": reg, "compartimento": nc,
                        "class_no": _text(cls, "NumeroClase") or _text(cls, "NumeroSerie"),
                        "fund_name": name, "compartment_name": cname,
                        "class_name": _text(cls, "DenominacionClase") or _text(cls, "DenominacionSerie"),
                        "n_classes": len(classes),
                        "isin": (_text(cls, "ISIN") or "").upper() or None, "etf": _fold(etf) == "si",
                        "manager": _text(g, "DenominacionGestora"),
                        "manager_group": _text(group, "DenominacionGrupoGestora"),
                    }
                )
    return pd.DataFrame(rows)


def parse_trim(fp) -> pd.DataFrame:
    """``FONDTRIM`` / ``SOCTRIM``: per share class its currency and net assets, with the compartment's category."""
    rows = []
    for period, ent in _entities(fp):
        kind, reg, cur = _text(ent, "Tipo"), _text(ent, "NumeroRegistro"), _text(ent, "CodigoDivisaIIC")
        for comp in ent.findall("Compartimento"):
            base = {
                "period": period, "kind": kind, "registro": reg, "compartimento": _text(comp, "NumeroCompartimento"),
                "category": _text(comp, "ClaseFondo") or _text(comp, "ClaseSociedad"),
                "vocation": _text(comp, "VocacionInversora"), "currency_iic": cur,
            }
            for cls in _classes(comp):
                rows.append({
                    **base,
                    "class_no": _text(cls, "NumeroClase") or _text(cls, "NumeroSerie"),
                    "class_currency": _text(cls, "CodigoDivisa") or cur,
                    "class_patrimonio": _num(_text(cls, "Patrimonio")),
                })
    return pd.DataFrame(rows)


def parse_derivatives(fp) -> pd.DataFrame:
    """``FONDDERI`` / ``SOCDERI``: one row per derivative line, nominal commitment in ``importe``."""
    rows = []
    for period, ent in _entities(fp):
        kind, reg = _text(ent, "Tipo"), _text(ent, "NumeroRegistro")
        for comp in ent.findall("Compartimento"):
            nc = _text(comp, "NumeroCompartimento")
            for d in comp.findall("OperativaDerivados"):
                rows.append({
                    "period": period, "kind": kind, "registro": reg, "compartimento": nc,
                    "descripcion": _text(d, "Descripcion"), "subyacente": _text(d, "Subyacente"),
                    "instrumento": _text(d, "Instrumento"), "importe": _num(_text(d, "Importe")),
                    "objetivo": _text(d, "Objetivo"),
                })
    return pd.DataFrame(rows)


_PARSERS = {
    "CART": ("positions", parse_cart),
    "PATRIMDISVAR": ("patrimonio", parse_patrimonio),
    "REGISTRO": ("registro", parse_registro),
    "TRIM": ("trim", parse_trim),
    "DERI": ("derivatives", parse_derivatives),
}


def read_zip(content: bytes) -> dict[str, pd.DataFrame]:
    """Positions, net assets, share classes and derivatives of funds and SICAVs from one quarterly ZIP.

    Raises ``ValueError`` unless the ZIP holds a quarter's portfolios.
    """
    out: dict[str, list[pd.DataFrame]] = {k: [] for k in _FRAMES}
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("CNMV document is not a ZIP") from exc
    with z:
        for name in z.namelist():
            m = _FILE_RE.match(Path(name).name)
            if not m:
                continue
            key, parse = _PARSERS[m.group(2).upper()]
            with z.open(name) as fp:
                out[key].append(parse(fp))
    if not out["positions"]:
        raise ValueError("ZIP has no FONDCART/SOCCART file: not a quarter-end month")
    frames = {k: pd.concat(v, ignore_index=True) if v else pd.DataFrame() for k, v in out.items()}
    if frames["registro"].empty or frames["positions"].empty:
        raise ValueError("CNMV ZIP parsed to no funds or no holdings")
    return frames


def data_period(frames: dict[str, pd.DataFrame]) -> str:
    """The ``FechaDatos`` of the portfolio files (``YYYYMM``); raises if they disagree."""
    periods = set(frames["positions"]["period"].dropna()) | set(frames["patrimonio"].get("period", pd.Series()).dropna())
    if len(periods) != 1:
        raise ValueError(f"CNMV portfolio files give FechaDatos {sorted(periods)}, expected one period")
    return periods.pop()


# -- derivatives and replication ------------------------------------------


def derivative_sides(deri: pd.DataFrame) -> dict[str, float]:
    """Nominal commitment of one compartment's derivatives, per side.

    Returns ``rights``, ``obligations`` and ``swaps``. CNMV files every swap
    once among the rights (``Derechos``) and once among the obligations
    (``Obligaciones``) with the same nominal, so adding all lines counts each
    swap twice. ``swaps`` is the larger side's nominal of total-return, index,
    credit-default and structured swaps (lines naming a swap, *permuta*, TRS
    or CDS, but not interest-rate swaps or futures): one leg per swap.
    """
    out = {"rights": 0.0, "obligations": 0.0, "swaps": 0.0}
    if deri is None or deri.empty:
        return out
    swap_side = {"rights": 0.0, "obligations": 0.0}
    for r in deri.itertuples(index=False):
        side = "obligations" if "obligacion" in _fold(r.descripcion) else "rights"
        amount = float(r.importe or 0.0)
        out[side] += amount
        text = f"{r.instrumento or ''} {r.subyacente or ''}"
        if _SWAP_RE.search(text) and not _RATE_RE.search(text):
            swap_side[side] += amount
    out["swaps"] = max(swap_side.values())
    return out


def replication(total: float | None, held: float, deri: pd.DataFrame, tracks_index: bool) -> str | None:
    """``swap`` / ``physical`` from the filings, ``None`` when they do not say.

    CNMV files have no replication field. A compartment is ``swap`` when the
    nominal of its non-rate swaps, one leg each (:func:`derivative_sides`), is
    at least half its net assets: its securities then do not show its
    exposure. An index-tracking compartment (ETF flag or ``ClaseFondo``
    "replica o reproduce un índice") whose reported securities are at least
    80% of net assets is ``physical``.
    """
    if not total or total <= 0:
        return None
    if derivative_sides(deri)["swaps"] >= 0.5 * total:
        return "swap"
    if tracks_index and held >= 0.8 * total:
        return "physical"
    return None


# -- one fund -------------------------------------------------------------


def asset_class(descripcion_if: str | None, clase_if: str | None = None) -> str:
    """CNMV ``DescripcionIF`` (and ``ClaseIF``) to the normalised asset class."""
    d = _fold(descripcion_if).replace(" ", "")
    if _fold(clase_if) == "dudosas" or d.startswith("inversionesdudosas"):
        return "other"
    if d.startswith("adquisiciontemporal"):
        return "repurchase agreement"
    if d.startswith("rentavariable"):
        return "equity"
    if d.startswith("rentafija") or d.startswith("deudapublica"):
        return "debt"
    if d.startswith("iic") or d.startswith("entidadesdecapitalriesgo"):
        return "fund"
    if d.startswith("depositos"):
        return "cash"
    return "other"


def _nulls_to_none(df: pd.DataFrame) -> pd.DataFrame:
    """Missing values in text columns as None (pandas 3 would give NaN)."""
    for c in df.columns:
        if not pd.api.types.is_numeric_dtype(df[c]):
            df[c] = pd.Series([None if pd.isna(v) else v for v in df[c]], index=df.index, dtype=object)
    return df


def fund_positions(positions: pd.DataFrame, patrimonio: dict, total: float | None = None) -> pd.DataFrame:
    """One compartment's lines in :data:`~openbasket.sources.issuers.HOLDING_COLUMNS`.

    ``patrimonio`` is that compartment's row of :func:`parse_patrimonio`;
    ``total`` overrides its ``TotalPatrimonio``.
    """
    total = float(total if total is not None else patrimonio["TotalPatrimonio"])
    p = positions.reset_index(drop=True)
    repo = p["descripcion_if"].map(lambda v: _fold(v).startswith("adquisicion temporal"))
    isin = p["isin"].map(lambda v: v.upper() if isinstance(v, str) and is_valid_isin(v.upper()) else None)
    # Names are CNMV's as filed (its legal notice asks for faithful reproduction).
    # A reverse repo is cash lent against a bond: the code is the collateral's
    # ISIN, so it is moved into the name instead of being the line's ISIN.
    name = p["descripcion"].fillna(p["descripcion_if"]).astype(str)
    name = name.where(~repo, name + " (colateral " + p["isin"].fillna("?") + ")")
    out = pd.DataFrame(
        {
            "isin": isin.where(~repo, None),
            "cusip": None,
            "sedol": None,
            "ticker": None,
            "name": name.str.strip(),
            "weight_pct": p["valor_mercado"].astype(float) / total * 100.0,
            "asset_class": [asset_class(d, c) for d, c in zip(p["descripcion_if"], p["clase_if"])],
            "currency": p["divisa"],
            "country": None,
            "portfolio_section": p["clase_if"],
            "kind": p["descripcion_if"],
            "market_value_eur": p["valor_mercado"],
        }
    )
    extra = []
    for fld, label, cls in (
        ("Liquidez", "Cash (liquidez, from the net asset breakdown)", "cash"),
        ("InteresesCartera", "Accrued interest on the portfolio", "other"),
        ("Resto", "Other assets and liabilities (resto, from the net asset breakdown)", "other"),
    ):
        v = patrimonio.get(fld)
        if v is not None and not pd.isna(v) and v != 0:
            extra.append({"isin": None, "cusip": None, "sedol": None, "ticker": None, "name": label,
                          "weight_pct": float(v) / total * 100.0, "asset_class": cls,
                          "currency": patrimonio.get("currency"), "country": None,
                          "portfolio_section": "PATRIMONIO", "kind": fld, "market_value_eur": v})
    if extra:
        out = pd.concat([out, pd.DataFrame(extra)], ignore_index=True) if len(out) else pd.DataFrame(extra)
    return _nulls_to_none(out)


def quarter_end(period: str) -> str:
    return (pd.Period(f"{period[:4]}-{period[4:]}", "M").end_time.date()).isoformat()


def fund_name(fund: str | None, compartment: str | None, class_name: str | None, n_classes: int = 1) -> str:
    """Display name: the compartment's name when it has one (it already starts
    with the umbrella's), else the fund's; plus the class unless it is the only,
    unnamed one."""
    parts = [fund or ""]
    if compartment and not re.fullmatch(r"COMPARTIMENTO \d+", compartment.strip(), re.I):
        parts = [compartment]
    if class_name and (n_classes > 1 or not re.fullmatch(r"(CLASE|SERIE) 0", class_name.strip(), re.I)):
        parts.append(class_name)
    return " - ".join(p for p in parts if p)


class CnmvStore:
    """The latest quarterly ZIP, parsed to Parquet under ``root``."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._frames: dict[str, pd.DataFrame] | None = None
        self._meta: dict | None = None

    # -- building

    def refresh(self) -> dict:
        """Download and parse the newest quarter-end ZIP that holds portfolio files."""
        periods = list_periods()
        errors = []
        for period in quarter_periods(periods):
            zip_path = download_period(period, periods[period], self.root / "raw")
            out = self.root / "parquet" / period
            meta_path = out / "meta.json"
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            if meta.get("version") != STORE_VERSION:
                try:
                    frames = read_zip(zip_path.read_bytes())
                    fecha = data_period(frames)
                except ValueError as exc:
                    logger.warning("CNMV %s unusable, trying the quarter before: %s", period, exc)
                    errors.append(f"{period}: {exc}")
                    zip_path.unlink(missing_ok=True)
                    continue
                if fecha != period:
                    logger.warning("CNMV ZIP listed as %s holds FechaDatos %s; using %s", period, fecha, fecha)
                out.mkdir(parents=True, exist_ok=True)
                for k, df in frames.items():
                    df.to_parquet(out / f"{k}.parquet", index=False)
                meta = {"version": STORE_VERSION, "period": fecha, "listed_as": period,
                        "url": DOC_URL.format(token=periods[period]), "zip": zip_path.name}
                meta_path.write_text(json.dumps(meta))
            self._frames, self._meta = None, None
            return {"period": meta["period"], "zip_path": str(zip_path), "parquet_dir": str(out), "url": meta["url"]}
        raise ValueError(f"No quarter-end ZIP with portfolio files on the CNMV page: {errors}")

    def latest(self) -> str | None:
        d = self.root / "parquet"
        got = sorted(p.name for p in d.iterdir() if (p / "meta.json").exists()) if d.exists() else []
        return got[-1] if got else None

    def is_built(self) -> bool:
        return self.latest() is not None

    # -- reading

    def meta(self) -> dict:
        self.frames()
        return dict(self._meta or {})

    def frames(self) -> dict[str, pd.DataFrame]:
        if self._frames is None:
            folder = self.latest()
            if folder is None:
                raise FileNotFoundError(f"No CNMV data under {self.root}: run the reg_spain pipeline or CnmvStore.refresh()")
            d = self.root / "parquet" / folder
            self._frames = {k: pd.read_parquet(d / f"{k}.parquet") if (d / f"{k}.parquet").exists() else pd.DataFrame()
                            for k in _FRAMES}
            self._meta = json.loads((d / "meta.json").read_text())
        return self._frames

    @staticmethod
    def _key(df: pd.DataFrame) -> pd.Series:
        return df["kind"].astype(str) + ":" + df["registro"].astype(str) + ":" + df["compartimento"].astype(str)

    def _by_key(self, name: str) -> dict[str, pd.DataFrame]:
        df = self.frames()[name]
        if df.empty:
            return {}
        return {k: g for k, g in df.groupby(self._key(df))}

    def products(self) -> pd.DataFrame:
        """One row per share-class ISIN in the quarter's registry files that has net assets."""
        f = self.frames()
        reg = f["registro"]
        reg = reg[reg["isin"].map(lambda v: isinstance(v, str) and is_valid_isin(v))].copy()
        pat = f["patrimonio"][["kind", "registro", "compartimento", "currency", "TotalPatrimonio"]]
        reg = reg.merge(pat, on=["kind", "registro", "compartimento"], how="left")
        trim = f["trim"]
        if not trim.empty:
            reg = reg.merge(
                trim[["kind", "registro", "compartimento", "class_no", "class_currency", "class_patrimonio",
                      "category", "vocation"]],
                on=["kind", "registro", "compartimento", "class_no"], how="left",
            )
        else:
            reg = reg.assign(class_currency=None, class_patrimonio=None, category=None, vocation=None)
        # Only compartments that report net assets, in the breakdown or per class.
        reg = reg[reg["TotalPatrimonio"].notna() | reg["class_patrimonio"].notna()]
        reg["portfolio_id"] = self._key(reg)
        held = f["positions"].groupby(self._key(f["positions"]))["valor_mercado"].sum().to_dict()
        deri = self._by_key("derivatives")
        rep = {}
        for pid, g in reg.groupby("portfolio_id"):
            r = g.iloc[0]
            tracks = bool(g["etf"].any()) or "replica o reproduce" in _fold(r["category"])
            rep[pid] = replication(r["TotalPatrimonio"], held.get(pid, 0.0), deri.get(pid), tracks)
        names = [fund_name(a, b, c, int(n or 1)) for a, b, c, n in
                 zip(reg["fund_name"], reg["compartment_name"], reg["class_name"], reg["n_classes"])]
        meta = self.meta()
        return pd.DataFrame(
            {
                "issuer": "cnmv_spain",
                "site": "es",
                "portfolio_id": reg["portfolio_id"],
                "isin": reg["isin"],
                "ticker": None,
                "fund_name": names,
                "domicile": "ES",
                "currency": reg["class_currency"].fillna(reg["currency"]),
                "asset_class": reg["vocation"],
                "replication": reg["portfolio_id"].map(rep),
                "product_page_url": reg["isin"].map(lambda i: FUND_PAGE_URL.format(isin=i)),
                "holdings_url": meta.get("url", PAGE_URL),
                "manager": reg["manager"],
                "manager_group": reg["manager_group"],
                "etf": reg["etf"],
                "category": reg["category"],
            }
        ).drop_duplicates("isin").reset_index(drop=True)

    def holdings(self, portfolio_id: str) -> tuple[str, pd.DataFrame, list[str]]:
        """``(as_of, frame, notes)`` for one compartment."""
        kind, reg, comp = portfolio_id.split(":")
        f = self.frames()

        def key(df):
            if df.empty:
                return pd.Series(dtype=bool)
            return (df["kind"] == kind) & (df["registro"] == reg) & (df["compartimento"] == comp)

        pat = f["patrimonio"][key(f["patrimonio"])]
        pat = pat.iloc[0].to_dict() if len(pat) else {}
        total = pat.get("TotalPatrimonio")
        notes = []
        if not total or pd.isna(total) or total <= 0:
            # Fall back to the sum of the classes' net assets when they share one currency.
            trim = f["trim"][key(f["trim"])] if not f["trim"].empty else pd.DataFrame()
            if len(trim) and trim["class_currency"].nunique() == 1 and trim["class_patrimonio"].notna().all():
                total = float(trim["class_patrimonio"].sum())
                pat.setdefault("currency", trim["class_currency"].iloc[0])
                notes.append("TotalPatrimonio missing; net assets taken as the sum of the share classes' Patrimonio")
        if not total or pd.isna(total) or total <= 0:
            raise LookupError(f"CNMV has no net assets for {portfolio_id}")
        frame = fund_positions(f["positions"][key(f["positions"])], pat, total)
        meta = self.meta()
        as_of = quarter_end(meta["period"])
        notes = [
            f"regulatory filing: CNMV quarterly portfolio (FONDCART/SOCCART), positions as of {as_of}",
            "weights are market value / total net assets (TotalPatrimonio) of the compartment in the same filing; "
            "cash, accrued interest and other net assets come from the filing's net asset breakdown",
            *notes,
        ]
        deri = f["derivatives"][key(f["derivatives"])] if not f["derivatives"].empty else pd.DataFrame()
        if len(deri):
            s = derivative_sides(deri)
            notes.append(
                f"derivatives are not included: CNMV reports them as nominal commitment (FONDDERI/SOCDERI), not "
                f"market values; {len(deri)} lines, rights {s['rights'] / total * 100:.2f}% and obligations "
                f"{s['obligations'] / total * 100:.2f}% of net assets (CNMV files each swap once on each side)"
            )
        else:
            notes.append("derivatives are not included: CNMV reports them as commitment amounts (FONDDERI), "
                         "not market values; none filed for this compartment")
        if not key(f["positions"]).any():
            notes.append("no security lines filed in FONDCART/SOCCART for this compartment this quarter")
        if kind.upper() == "SICAV":
            notes.append("SICAV files report values rounded to thousands of euros")
        if pat.get("currency") not in (None, "EUR"):
            notes.append(f"fund currency is {pat['currency']}: its market values and net assets are both filed in "
                         "that currency, although CNMV's explanatory notes speak of euros")
        return as_of, frame, notes
