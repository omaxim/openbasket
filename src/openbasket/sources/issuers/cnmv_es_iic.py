"""CNMV: portfolios of every Spanish investment fund (FI) and SICAV.

The Spanish securities regulator (Comisión Nacional del Mercado de Valores)
publishes the quarterly filings of every Spanish UCI as XML, one ZIP per
month, on its "Descarga de información individual" page:

Listing
    ``https://www.cnmv.es/portal/publicaciones/descarga-informacion-individual``
    (``?ejercicio=YYYY`` for earlier years) has a table with one link per
    month, ``https://www.cnmv.es/webservices/verdocumento/ver?e=<token>``,
    whose ``title`` is the month name in Spanish. ``HEAD`` returns 405, so
    the ZIP is fetched with ``GET``.
ZIP of a quarter-end month (March, June, September, December)
    ``FONDREGISTRO`` / ``SOCREGISTRO``: fund (or SICAV), compartment and
    share-class (series) ISINs, ETF flag, management company.
    ``FONDTRIM`` / ``SOCTRIM``: per class currency, net assets, NAV, fees,
    the fund category (``ClaseFondo``) and investment vocation.
    ``FONDPATRIMDISVAR`` / ``SOCPATRIMDISVAR``: per compartment breakdown of
    net assets: domestic and foreign portfolio, accrued interest, doubtful
    assets, cash (``Liquidez``), other (``Resto``) and ``TotalPatrimonio``.
    ``FONDCART`` / ``SOCCART``: every cash-market security held at quarter
    end, with ``CodigoISIN``, ``DescripcionValor``, ``Divisa`` and
    ``ValorMercado``.
    ``FONDDERI`` / ``SOCDERI``: derivatives, as nominal commitment only.
    Other months carry only ``FONDREGISTRO`` and ``FONDMENS``.

Records are keyed by ``(Tipo, NumeroRegistro, NumeroCompartimento)``; that
triple is used as ``portfolio_id`` (``FI-9-0``), so every share class of a
compartment shares it.

The files report market values, not weights. Here
``weight_pct = ValorMercado / TotalPatrimonio * 100`` for the compartment,
both read from the same quarter's files and both in the compartment's
currency. That is the only computation applied; values are never rescaled.
"""

from __future__ import annotations

import calendar
import html
import io
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date
from hashlib import sha1
from pathlib import Path

import pandas as pd
import requests

from ... import http
from ...identifiers import is_valid_isin
from . import (
    HOLDING_COLUMNS,
    PRODUCT_COLUMNS,
    FetchContext,
    IssuerAdapter,
    IssuerHoldings,
    register,
)

logger = logging.getLogger(__name__)

NAME = "cnmv-es-iic"
HOST = "https://www.cnmv.es"
LISTING_URL = HOST + "/portal/publicaciones/descarga-informacion-individual"
FUND_PAGE_URL = HOST + "/portal/consultas/iic/fondo?isin={isin}"

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
QUARTER_END_MONTHS = (12, 9, 6, 3)

#: Links in the listing table: ``id="..._lnkZip" href="..." title="<Mes>"``.
_LINK_RE = re.compile(
    r'<a[^>]*id="[^"]*lnkZip"[^>]*href="(?P<href>[^"]*verdocumento/ver\?e=[^"]+)"[^>]*title="(?P<title>[^"]+)"',
    re.I,
)
_YEAR_RE = re.compile(r'lblEjercicio"[^>]*>\s*(\d{4})\s*<')
_FILE_RE = re.compile(r"^(FOND|SOC)(REGISTRO|TRIM|PATRIMDISVAR|CART|DERI)_(\d{6})\.xml$", re.I)
_SWAP_RE = re.compile(r"permuta|swap|\bTRS\b|\bCDS\b", re.I)
#: Interest-rate swaps (and futures CNMV files list under *permutas*) change
#: duration, not what the fund holds, so they do not make a fund swap-based.
_RATE_RE = re.compile(r"\bIRS\b|tipo de inter|euribor|sonia|\bsofr\b|\bestr\b|\bFUT\b|FUT\.|futuro", re.I)


def _fold(text: str | None) -> str:
    """Lower case without accents, for matching CNMV's labels."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).strip().lower()


def _num(text: str | None) -> float | None:
    if text is None or not text.strip():
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _text(el: ET.Element, tag: str) -> str | None:
    v = el.findtext(tag)
    v = v.strip() if v is not None else None
    return v or None


# --------------------------------------------------------------------------
# Listing page


def parse_listing(page: str) -> list[dict]:
    """Monthly ZIP links on one listing page: ``[{year, month, url}]``, newest first."""
    m = _YEAR_RE.search(page)
    year = int(m.group(1)) if m else None
    out = []
    for link in _LINK_RE.finditer(page):
        month = MONTHS.get(_fold(html.unescape(link.group("title"))))
        if month is None or year is None:
            continue
        out.append({"year": year, "month": month, "url": html.unescape(link.group("href"))})
    return sorted(out, key=lambda d: (d["year"], d["month"]), reverse=True)


def list_documents(year: int | None = None) -> list[dict]:
    """Fetch one year's listing (the current year when ``year`` is None)."""
    url = LISTING_URL if year is None else f"{LISTING_URL}?ejercicio={year}"
    r = http.get(url)
    r.raise_for_status()
    return parse_listing(r.text)


# --------------------------------------------------------------------------
# Quarter ZIP


@dataclass
class Quarter:
    """Parsed contents of one quarter-end ZIP."""

    period: str                     # "YYYYMM"
    url: str
    registry: list[dict] = field(default_factory=list)   # one row per share class
    classes: dict = field(default_factory=dict)          # (key, class no) -> trim row
    compartments: dict = field(default_factory=dict)     # key -> trim compartment row
    patrimonio: dict = field(default_factory=dict)       # key -> PATRIMDISVAR row
    portfolio: dict = field(default_factory=dict)        # key -> list of CART rows
    derivatives: dict = field(default_factory=dict)      # key -> list of DERI rows

    @property
    def as_of(self) -> str:
        y, m = int(self.period[:4]), int(self.period[4:])
        return date(y, m, calendar.monthrange(y, m)[1]).isoformat()


def _entities(data: bytes):
    """Yield every ``Entidad`` element of a CNMV XML file, then free it."""
    for _, el in ET.iterparse(io.BytesIO(data), events=("end",)):
        if el.tag == "Entidad":
            yield el
            el.clear()


def _key(ent: ET.Element, comp: ET.Element) -> tuple[str, str, str]:
    return (_text(ent, "Tipo") or "", _text(ent, "NumeroRegistro") or "", _text(comp, "NumeroCompartimento") or "")


def _parse_registro(data: bytes, q: Quarter) -> None:
    for ent in _entities(data):
        gestora = ent.find("Gestora")
        group = gestora.find("GrupoGestora") if gestora is not None else None
        for comp in ent.findall("Compartimento"):
            key = _key(ent, comp)
            # FONDREGISTRO calls share classes Clase, SOCREGISTRO calls them Serie.
            classes = comp.findall("Clase") + comp.findall("Serie")
            for cl in classes:
                q.registry.append({
                    "key": key,
                    "class_no": _text(cl, "NumeroClase") or _text(cl, "NumeroSerie"),
                    "class_name": _text(cl, "DenominacionClase") or _text(cl, "DenominacionSerie"),
                    "n_classes": len(classes),
                    "isin": (_text(cl, "ISIN") or "").upper() or None,
                    "entity_name": _text(ent, "Denominacion"),
                    "compartment_name": _text(comp, "DenominacionCompartimento"),
                    "etf": _fold(_text(ent, "ETF")) == "si",
                    "manager": _text(gestora, "DenominacionGestora") if gestora is not None else None,
                    "manager_group": _text(group, "DenominacionGrupoGestora") if group is not None else None,
                })


def _parse_trim(data: bytes, q: Quarter) -> None:
    for ent in _entities(data):
        cur_iic = _text(ent, "CodigoDivisaIIC")
        for comp in ent.findall("Compartimento"):
            key = _key(ent, comp)
            q.compartments[key] = {
                "currency": cur_iic,
                "category": _text(comp, "ClaseFondo") or _text(comp, "ClaseSociedad"),
                "vocation": _text(comp, "VocacionInversora"),
            }
            for cl in comp.findall("Clase") + comp.findall("Serie"):
                no = _text(cl, "NumeroClase") or _text(cl, "NumeroSerie")
                q.classes[(key, no)] = {
                    "currency": _text(cl, "CodigoDivisa") or cur_iic,
                    "patrimonio": _num(cl.findtext("Patrimonio")),
                }


def _parse_patrimonio(data: bytes, q: Quarter) -> None:
    fields = ("CarteraInterior", "CarteraExterior", "InteresesCartera", "InversionesDudosas", "Liquidez", "Resto", "TotalPatrimonio")
    for ent in _entities(data):
        cur = _text(ent, "CodigoDivisaIIC")
        for comp in ent.findall("Compartimento"):
            row = {f: _num(comp.findtext(f)) for f in fields}
            row["currency"] = cur
            q.patrimonio[_key(ent, comp)] = row


def _parse_cartera(data: bytes, q: Quarter) -> None:
    for ent in _entities(data):
        for comp in ent.findall("Compartimento"):
            rows = q.portfolio.setdefault(_key(ent, comp), [])
            for inv in comp.findall("InversionesFinancieras"):
                rows.append({
                    "clase_if": _text(inv, "ClaseIF"),
                    "descripcion_if": _text(inv, "DescripcionIF"),
                    "isin": _text(inv, "CodigoISIN"),
                    "descripcion": _text(inv, "DescripcionValor"),
                    "divisa": _text(inv, "Divisa"),
                    "valor": _num(inv.findtext("ValorMercado")),
                })


def _parse_derivados(data: bytes, q: Quarter) -> None:
    for ent in _entities(data):
        for comp in ent.findall("Compartimento"):
            rows = q.derivatives.setdefault(_key(ent, comp), [])
            for d in comp.findall("OperativaDerivados"):
                rows.append({
                    "descripcion": _text(d, "Descripcion"),
                    "subyacente": _text(d, "Subyacente"),
                    "instrumento": _text(d, "Instrumento"),
                    "importe": _num(d.findtext("Importe")),
                    "objetivo": _text(d, "Objetivo"),
                })


_PARSERS = {
    "REGISTRO": _parse_registro,
    "TRIM": _parse_trim,
    "PATRIMDISVAR": _parse_patrimonio,
    "CART": _parse_cartera,
    "DERI": _parse_derivados,
}


def parse_zip(content: bytes, url: str = "") -> Quarter:
    """Parse a monthly ZIP. Raises ``ValueError`` unless it holds a quarter's portfolios."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"CNMV document is not a ZIP: {url}") from exc
    found: dict[tuple[str, str], str] = {}
    periods = set()
    for name in zf.namelist():
        m = _FILE_RE.match(Path(name).name)
        if m:
            found[(m.group(1).upper(), m.group(2).upper())] = name
            periods.add(m.group(3))
    if ("FOND", "CART") not in found:
        raise ValueError(f"CNMV ZIP has no FONDCART file (not a quarter-end month): {url}")
    if len(periods) != 1:
        raise ValueError(f"CNMV ZIP mixes periods {sorted(periods)}: {url}")
    q = Quarter(period=periods.pop(), url=url)
    # Registry first so the other files can be joined to it; order otherwise free.
    for kind in ("REGISTRO", "TRIM", "PATRIMDISVAR", "CART", "DERI"):
        for family in ("FOND", "SOC"):
            name = found.get((family, kind))
            if name:
                _PARSERS[kind](zf.read(name), q)
    if not q.registry or not q.portfolio:
        raise ValueError(f"CNMV ZIP parsed to no funds or no holdings: {url}")
    return q


_MEMO: dict[str, Quarter] = {}


def _cache_path(cache_dir: str | Path | None, url: str) -> Path | None:
    if not cache_dir:
        return None
    return Path(cache_dir) / f"{NAME}_{sha1(url.encode()).hexdigest()[:12]}_{date.today().isoformat()}.zip"


def load_quarter(url: str, cache_dir: str | Path | None = None) -> Quarter:
    """Download (or read from today's cache) and parse one monthly ZIP."""
    if url in _MEMO:
        return _MEMO[url]
    cache = _cache_path(cache_dir, url)
    if cache is not None and cache.exists():
        q = parse_zip(cache.read_bytes(), url)
    else:
        logger.info("Fetching CNMV ZIP %s", url)
        r = http.get(url, timeout=300)
        r.raise_for_status()
        q = parse_zip(r.content, url)  # raises before anything is cached
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(r.content)
    _MEMO[url] = q
    return q


def latest_quarter(cache_dir: str | Path | None = None, today: date | None = None) -> Quarter:
    """The newest quarter-end ZIP on the listing that holds portfolio files.

    Data is published about four months after the quarter, so this walks the
    current year's page and then the previous year's.
    """
    year = (today or date.today()).year
    for y in (None, year - 1):
        for doc in list_documents(y):
            if doc["month"] not in QUARTER_END_MONTHS:
                continue
            try:
                return load_quarter(doc["url"], cache_dir)
            except ValueError as exc:
                logger.warning("Skipping CNMV document %s-%02d: %s", doc["year"], doc["month"], exc)
    raise RuntimeError("No CNMV quarter-end ZIP with portfolio files found")


# --------------------------------------------------------------------------
# Products


def _replication(q: Quarter, key: tuple, etf: bool) -> str | None:
    """``swap`` / ``physical`` from the filings, ``None`` when they do not say.

    CNMV files have no replication field. A compartment is labelled ``swap``
    when the nominal of its total-return, index, credit-default or structured
    swaps (``FONDDERI`` lines naming a swap, *permuta*, TRS or CDS, but not
    interest-rate swaps) is at least half its net assets: its securities then
    do not show its exposure. An index-tracking compartment (ETF flag or
    ``ClaseFondo`` "replica o reproduce un índice") whose reported securities
    are at least 80% of net assets is labelled ``physical``.
    """
    pat = q.patrimonio.get(key) or {}
    total = pat.get("TotalPatrimonio") or 0.0
    if total <= 0:
        return None
    swaps = 0.0
    for d in q.derivatives.get(key, []):
        text = f"{d['instrumento'] or ''} {d['subyacente'] or ''}"
        if _SWAP_RE.search(text) and not _RATE_RE.search(text):
            swaps += d["importe"] or 0.0
    if swaps >= 0.5 * total:
        return "swap"
    tracks = etf or "replica o reproduce" in _fold((q.compartments.get(key) or {}).get("category"))
    held = sum(r["valor"] or 0.0 for r in q.portfolio.get(key, []))
    if tracks and held >= 0.8 * total:
        return "physical"
    return None


def _fund_name(r: dict) -> str:
    parts = [r["entity_name"] or ""]
    comp = r["compartment_name"]
    if comp and not re.fullmatch(r"COMPARTIMENTO \d+", comp.strip(), re.I):
        parts = [comp]  # compartment names already start with the umbrella name
    cls = r["class_name"]
    if cls and (r["n_classes"] > 1 or not re.fullmatch(r"(CLASE|SERIE) 0", cls.strip(), re.I)):
        parts.append(cls)
    return " - ".join(p for p in parts if p)


def build_products(q: Quarter) -> pd.DataFrame:
    """One row per share-class ISIN in the quarter's registry files."""
    rows = []
    for r in q.registry:
        if not is_valid_isin(r["isin"]):
            continue
        key = r["key"]
        cls = q.classes.get((key, r["class_no"])) or {}
        comp = q.compartments.get(key) or {}
        rows.append({
            "issuer": NAME,
            "site": "es",
            "portfolio_id": "-".join(key),
            "isin": r["isin"],
            "ticker": None,
            "fund_name": _fund_name(r),
            "domicile": "ES",
            "currency": cls.get("currency") or comp.get("currency"),
            "asset_class": comp.get("vocation"),
            "replication": _replication(q, key, r["etf"]),
            "product_page_url": FUND_PAGE_URL.format(isin=r["isin"]),
            "holdings_url": q.url,
        })
    df = pd.DataFrame(rows, columns=PRODUCT_COLUMNS)
    return df.drop_duplicates("isin").reset_index(drop=True)


def products(cache_dir: str | Path | None = None) -> pd.DataFrame:
    """Every Spanish FI and SICAV share class in the latest quarter's files."""
    return build_products(latest_quarter(cache_dir))


# --------------------------------------------------------------------------
# Holdings


def _asset_class(descripcion_if: str | None, clase_if: str | None) -> str:
    d = _fold(descripcion_if).replace(" ", "")
    if _fold(clase_if) == "dudosas" or d.startswith("inversionesdudosas"):
        return "other"
    if d.startswith("rentavariable"):
        return "equity"
    if d.startswith("rentafija") or d.startswith("deudapublica"):
        return "debt"
    if d.startswith("iic") or d.startswith("entidadesdecapitalriesgo"):
        return "fund"
    if d.startswith("depositos") or d.startswith("adquisiciontemporal"):
        return "cash"
    return "other"


#: Net-asset lines from PATRIMDISVAR that are not securities, shown so the
#: fund's lines account for its whole net assets.
_BALANCE_LINES = (
    ("Liquidez", "Liquidez (tesorería)", "cash"),
    ("InteresesCartera", "Intereses de la cartera de inversión", "other"),
    ("Resto", "Resto (otros activos y pasivos)", "other"),
)


def build_holdings(q: Quarter, portfolio_id: str) -> tuple[pd.DataFrame, list[str]]:
    """Holdings frame (HOLDING_COLUMNS plus ``line_type``) and notes for one compartment."""
    key = tuple(portfolio_id.split("-", 2))
    pat = q.patrimonio.get(key)
    total = (pat or {}).get("TotalPatrimonio")
    notes = [
        "Weights are derived: ValorMercado / TotalPatrimonio of the compartment x 100, "
        "from the same quarter's CNMV files (the filings report values, not percentages)."
    ]
    if not total or total <= 0:
        # Fall back to the sum of the classes' net assets when they share one currency.
        classes = [v for (k, _), v in q.classes.items() if k == key]
        curs = {c["currency"] for c in classes}
        if classes and len(curs) == 1 and all(c["patrimonio"] for c in classes):
            total = sum(c["patrimonio"] for c in classes)
            notes.append("TotalPatrimonio missing; net assets taken as the sum of the share classes' Patrimonio.")
    if not total or total <= 0:
        notes.append("No net assets reported for this compartment, so no weights can be given.")
        return pd.DataFrame(columns=HOLDING_COLUMNS + ["line_type"]), notes

    rows = []
    for r in q.portfolio.get(key, []):
        code = (r["isin"] or "").upper()
        asset = _asset_class(r["descripcion_if"], r["clase_if"])
        name = r["descripcion"]
        isin = code if is_valid_isin(code) else None
        if _fold(r["descripcion_if"]).startswith("adquisicion temporal") and isin:
            # A reverse repo: the code is the collateral's ISIN, not a holding of it.
            name = f"{name} (colateral {isin})"
            isin = None
        rows.append({
            "isin": isin, "cusip": None, "sedol": None, "ticker": None, "name": name,
            "weight_pct": (r["valor"] or 0.0) / total * 100.0 if r["valor"] is not None else None,
            "asset_class": asset, "currency": r["divisa"], "country": None, "line_type": "security",
        })
    if not rows:
        notes.append("No security lines filed in FONDCART/SOCCART for this compartment this quarter.")
    for fld, label, asset in _BALANCE_LINES:
        v = (pat or {}).get(fld)
        if v:
            rows.append({
                "isin": None, "cusip": None, "sedol": None, "ticker": None, "name": label,
                "weight_pct": v / total * 100.0, "asset_class": asset,
                "currency": (pat or {}).get("currency"), "country": None, "line_type": "balance",
            })
    deri = q.derivatives.get(key, [])
    if deri:
        nominal = sum(d["importe"] or 0.0 for d in deri)
        notes.append(
            f"{len(deri)} derivative positions (nominal commitment {nominal / total * 100:.2f}% of net assets) "
            "are filed separately in FONDDERI/SOCDERI with nominal amounts only and are not listed as lines."
        )
    if key[0].upper() == "SICAV":
        notes.append("SICAV files report values rounded to thousands of euros.")
    df = pd.DataFrame(rows, columns=HOLDING_COLUMNS + ["line_type"])
    text = [c for c in df.columns if c != "weight_pct"]
    df[text] = df[text].astype(object).where(df[text].notna(), None)  # None, not NaN
    df["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce")
    return df, notes


def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
    """Full quarter-end portfolio of the product's compartment."""
    cache_dir = ctx.cache_dir if ctx else None
    url = product.get("holdings_url")
    q = None
    if url:
        try:
            q = load_quarter(url, cache_dir)
        except (ValueError, requests.RequestException) as exc:
            logger.warning("CNMV document %s unusable (%s); using the latest quarter", url, exc)
    if q is None:
        q = latest_quarter(cache_dir)
    df, notes = build_holdings(q, product["portfolio_id"])
    return IssuerHoldings(as_of=q.as_of, holdings=df, url=q.url, notes=notes)


ADAPTER = register(IssuerAdapter(
    name=NAME,
    title="CNMV (Spanish funds and SICAVs)",
    homepage=LISTING_URL,
    credit="Fuente: CNMV (Comisión Nacional del Mercado de Valores), ficheros de información individual de IIC",
    licence=(
        "CNMV legal notice (Nota legal): reuse beyond private consultation is authorised if the information "
        "is reproduced faithfully, without manipulating or altering it; calculations from it may be published; "
        "anyone selling it must tell buyers it is free on cnmv.es. Data are the funds' own filings, for whose "
        "quality the filers are responsible. Weights here are calculated from the reported values."
    ),
    regions=("ES",),
    products=products,
    holdings=holdings,
    frequency="quarterly",
))
