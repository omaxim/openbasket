"""Sweden: Finansinspektionen's quarterly holdings of every Swedish UCITS fund.

Finansinspektionen (FI) publishes, as open data, the full portfolio that each
Swedish UCITS fund (*värdepappersfond*) reports to it at every quarter end
("Fondinnehav per kvartal"):

Page
    ``https://www.fi.se/sv/vara-register/fondinnehav-per-kvartal/`` lists one
    ZIP per quarter from 2018 Q4, linked as
    ``/FondInnehavLista/download?filnamn=Fondinnehav_<YYYY>Q<q>_<timestamp>.zip``.
    The timestamp is when FI last rebuilt that quarter, and FI republishes
    old quarters under a new name when a fund revises its report, so the file
    name is always read from the page, never built.
ZIP
    One XML per fund, in one folder per fund company, with entry names such
    as ``<company>_<company id>_<ts>\\<fund>_<fund id>_VPFO_<YYYY>Q<q>_<ts>.xml``
    (backslashes). Namespace ``http://schemas.fi.se/publika/vardepappersfonder/20200331``.
Per fund
    ``Rapportinformation/Kvartalsslut`` (position date); the fund company's
    name, FI institute number and LEI; the fund's institute number, ISIN,
    name and status; net assets (``Fondförmögenhet``), cash
    (``Likvida_medel``) and other assets and liabilities, all in SEK; and one
    ``FinansielltInstrument`` per position with its UCITS asset type
    (``Tillgångsslag_enligt_LVF_5_kap``), name, ISIN, issuer country,
    currency, quantity or nominal, price, FX rate, market value in SEK and
    ``Andel_av_fondförmögenhet_instrument``: the position's share of net
    assets **in percent**, two decimals.

Weights are the reported ``Andel`` exactly: never rescaled. Cash and other
assets and liabilities are added as two unidentified lines whose weight is
their SEK amount over net assets (``weight_derived = True``), so a fund's lines
add up to about 100%.

The files hold company names and LEIs only, no personal data.
"""

from __future__ import annotations

import html
import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import quote

import pandas as pd

from .. import http
from ..identifiers import is_valid_isin

logger = logging.getLogger(__name__)

HOST = "https://www.fi.se"
PAGE_URL = HOST + "/sv/vara-register/fondinnehav-per-kvartal/"
PAGE_URL_EN = HOST + "/en/our-registers/quarterly-fund-holdings/"
DOWNLOAD_URL = HOST + "/FondInnehavLista/download?filnamn={name}"
LICENCE_URL = HOST + "/sv/om-fi/om-webbplatsen/oppen-data/"
CREDIT = "Källa: Finansinspektionen (FI), Fondinnehav per kvartal"
LICENCE = (
    "FI open data. FI's open data page says: \"FI:s information är fri att användas. Ange källa, FI och datum. "
    "FI:s data är fritt tillgänglig utan avgift och får fritt bearbetas och vidareutnyttjas utan att något "
    "särskilt medgivande eller avtal krävs.\" (FI's information is free to use; give the source, FI, and the "
    "date; FI's data may be freely processed and reused without any special permission or agreement.) "
    f"{LICENCE_URL}"
)

NS = "{http://schemas.fi.se/publika/vardepappersfonder/20200331}"
_LINK_RE = re.compile(r'href="/FondInnehavLista/download\?filnamn=(Fondinnehav_(\d{4})Q([1-4])_[^"]+?\.zip)"', re.I)

#: UCITS asset type (LVF chapter 5) to the normalised asset class. Transferable
#: securities with a nominal amount are bonds (see :func:`_asset_class`).
ASSET_CLASS = {
    "ÖverlåtbartVärdepapper": "equity",
    "Penningmarknadsinstrument": "debt",
    "Fondandel": "fund",
    "DerivatinstrumentSamtTeknikerOchInstrument": "derivative",
}

#: Fund companies that belong to a banking group, by the name FI gives.
#: Used for the ``bank_group`` product column; anything else is ``None``.
BANK_GROUPS = {
    "Swedbank AB": "Swedbank",
    "Swedbank Robur Fonder AB": "Swedbank",
    "SEB Funds AB": "SEB",
    "Skandinaviska Enskilda Banken AB": "SEB",
    "Handelsbanken Fonder AB": "Handelsbanken",
    "Nordea Funds Ab": "Nordea",
    "Länsförsäkringar Fondförvaltning AB": "Länsförsäkringar",
    "Avanza Fonder AB": "Avanza",
    "Nordnet Fonder AB": "Nordnet",
}

FUND_COLUMNS = [
    "quarter", "as_of", "entry", "manager", "manager_id", "manager_lei", "fund_id", "fund_isin",
    "fund_isin_reported", "fund_name", "status", "net_assets_sek", "cash_sek", "other_sek",
    "std_24m", "active_risk", "benchmark", "share_classes",
]
POSITION_COLUMNS = [
    "fund_id", "line", "lvf_asset_type", "name", "isin", "isin_reported", "country", "currency",
    "quantity", "nominal", "price", "fx_rate", "market_value_sek", "weight_pct", "industry_code",
    "industry",
]


# -- listing and download ---------------------------------------------------


def parse_index(text: str) -> dict[str, str]:
    """``{"2026Q2": "Fondinnehav_2026Q2_2026-10-14 08.12.zip", ...}`` from the index page."""
    out: dict[str, str] = {}
    for name, year, q in _LINK_RE.findall(text):
        key = f"{year}Q{q}"
        name = html.unescape(name)
        if name > out.get(key, ""):
            out[key] = name
    return out


def list_quarters() -> dict[str, str]:
    r = http.get(PAGE_URL)
    r.raise_for_status()
    return parse_index(r.text)


def download_url(name: str) -> str:
    return DOWNLOAD_URL.format(name=quote(name))


def download_quarter(name: str, raw_dir: str | Path) -> Path:
    """Stream one quarter's ZIP to ``raw_dir/<FI file name>`` (kept if already there)."""
    dest = Path(raw_dir) / name
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading FI fund holdings %s", name)
    r = http.get(download_url(name), timeout=300, stream=True)
    r.raise_for_status()
    tmp = dest.with_suffix(".part")
    with open(tmp, "wb") as fp:
        for chunk in r.iter_content(1 << 16):
            fp.write(chunk)
    if not zipfile.is_zipfile(tmp):
        tmp.unlink()
        raise ValueError(f"FI {name}: expected a ZIP, got {r.headers.get('content-type')}")
    tmp.replace(dest)
    return dest


# -- XML ----------------------------------------------------------------------


def _t(el: ET.Element | None, tag: str) -> str | None:
    if el is None:
        return None
    v = el.findtext(NS + tag)
    if v is None:
        return None
    v = v.strip()
    return v or None


def _num(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v.replace(",", ".").replace(" ", ""))
    except ValueError:
        return None


def _isin(v: str | None) -> str | None:
    if not v:
        return None
    v = v.strip().upper()
    return v if is_valid_isin(v) else None


def parse_fund(data: bytes, entry: str = "") -> tuple[dict, list[dict]]:
    """One fund's XML to ``(fund row, position rows)`` (:data:`FUND_COLUMNS`, :data:`POSITION_COLUMNS`)."""
    root = ET.fromstring(data)
    rep = root.find(NS + "Rapportinformation")
    co = root.find(NS + "Bolagsinformation")
    infos = [f for f in root.findall(NS + "Fondinformation") if len(f)]
    if not infos:
        raise ValueError(f"{entry}: no Fondinformation")
    f = infos[0]
    as_of = _t(rep, "Kvartalsslut")
    classes = [c.strip() for c in (e.text or "" for e in f.iter(NS + "Andelsklass_namn")) if c.strip()]
    bench = [b.strip() for b in (e.text or "" for e in f.iter(NS + "Jämförelseindex")) if b.strip()]
    fund = {
        "quarter": f"{as_of[:4]}Q{(int(as_of[5:7]) - 1) // 3 + 1}" if as_of else None,
        "as_of": as_of,
        "entry": entry,
        "manager": _t(co, "Fondbolag_namn"),
        "manager_id": _t(co, "Fondbolag_institutnummer"),
        "manager_lei": _t(co, "Fondbolag_LEI-kod"),
        "fund_id": _t(f, "Fond_institutnummer"),
        "fund_isin": _isin(_t(f, "Fond_ISIN-kod")),
        "fund_isin_reported": _t(f, "Fond_ISIN-kod"),
        "fund_name": _t(f, "Fond_namn"),
        "status": _t(f, "Fond_status"),
        "net_assets_sek": _num(_t(f, "Fondförmögenhet")),
        "cash_sek": _num(_t(f, "Likvida_medel")),
        "other_sek": _num(_t(f, "Övriga_tillgångar_och_skulder")),
        "std_24m": _num(_t(f, "Standardavvikelse_24_månader")),
        "active_risk": _num(_t(f, "Aktiv_risk")),
        "benchmark": "; ".join(b for b in bench if b != "Jämförelseindex saknas") or None,
        "share_classes": "; ".join(classes) or None,
    }
    rows = []
    for n, i in enumerate(f.iter(NS + "FinansielltInstrument")):
        b = i.find(NS + "Bransch")
        rows.append(
            {
                "fund_id": fund["fund_id"],
                "line": n,
                "lvf_asset_type": _t(i, "Tillgångsslag_enligt_LVF_5_kap"),
                "name": _t(i, "Instrumentnamn"),
                "isin": _isin(_t(i, "ISIN-kod_instrument")),
                "isin_reported": _t(i, "ISIN-kod_instrument"),
                "country": _t(i, "Landkod_Emittent"),
                "currency": _t(i, "Valuta"),
                "quantity": _num(_t(i, "Antal")),
                "nominal": _num(_t(i, "Nominellt_belopp")),
                "price": _num(_t(i, "Kurs_som_använts_vid_värdering_av_instrumentet")),
                "fx_rate": _num(_t(i, "Valutakurs_instrument")),
                "market_value_sek": _num(_t(i, "Marknadsvärde_instrument")),
                "weight_pct": _num(_t(i, "Andel_av_fondförmögenhet_instrument")),
                "industry_code": _t(b, "Branschkod_instrument"),
                "industry": _t(b, "Bransch_namn_instrument"),
            }
        )
    return fund, rows


def read_zip(path_or_bytes: str | Path | bytes) -> dict[str, pd.DataFrame]:
    """Every fund file in one quarter's ZIP to ``{"funds": ..., "positions": ...}``."""
    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes
    funds, rows = [], []
    with zipfile.ZipFile(src) as z:
        for name in z.namelist():
            if not name.lower().endswith(".xml"):
                continue
            try:
                fund, pos = parse_fund(z.read(name), entry=name.replace("\\", "/"))
            except (ET.ParseError, ValueError) as e:
                logger.warning("FI: skipping %s: %s", name, e)
                continue
            funds.append(fund)
            rows.extend(pos)
    if not funds:
        raise ValueError("ZIP has no FI fund holdings XML")
    pos = pd.DataFrame(rows, columns=POSITION_COLUMNS)
    pos["line"] = pos["line"].astype("int64")
    return {"funds": pd.DataFrame(funds, columns=FUND_COLUMNS), "positions": pos}


# -- one fund -----------------------------------------------------------------


def _nulls_to_none(df: pd.DataFrame) -> pd.DataFrame:
    """Missing values in text columns as None (pandas 3 would give NaN)."""
    for c in df.columns:
        if not pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c]):
            df[c] = pd.Series([None if pd.isna(v) else v for v in df[c]], index=df.index, dtype=object)
    return df


def _asset_class(lvf: str | None, nominal: float | None) -> str:
    if lvf == "ÖverlåtbartVärdepapper" and nominal is not None and not pd.isna(nominal) and nominal != 0:
        return "debt"
    return ASSET_CLASS.get(lvf or "", "other")


def fund_lines(positions: pd.DataFrame, fund: dict) -> pd.DataFrame:
    """One fund's positions in :data:`~openbasket.sources.issuers.HOLDING_COLUMNS` plus extras.

    ``fund`` is that fund's row of the ``funds`` frame.
    """
    p = positions.sort_values("line").reset_index(drop=True)
    out = pd.DataFrame(
        {
            "isin": p["isin"],
            "cusip": None,
            "sedol": None,
            "ticker": None,
            "name": p["name"].fillna(p["lvf_asset_type"]),
            "weight_pct": p["weight_pct"].astype(float),
            "asset_class": [_asset_class(a, n) for a, n in zip(p["lvf_asset_type"], p["nominal"])],
            "currency": p["currency"],
            "country": p["country"],
            "lvf_asset_type": p["lvf_asset_type"],
            "isin_reported": p["isin_reported"],
            "quantity": p["quantity"],
            "nominal": p["nominal"],
            "market_value_sek": p["market_value_sek"],
            "industry": p["industry"],
            "weight_derived": False,
        }
    )
    total = fund.get("net_assets_sek")
    extra = []
    for field, label, cls in (
        ("cash_sek", "Likvida medel (cash, from the fund's report)", "cash"),
        ("other_sek", "Övriga tillgångar och skulder (other assets and liabilities)", "other"),
    ):
        v = fund.get(field)
        if total and v is not None and not pd.isna(v) and v != 0:
            extra.append({"isin": None, "cusip": None, "sedol": None, "ticker": None, "name": label,
                          "weight_pct": round(float(v) / float(total) * 100.0, 4), "asset_class": cls,
                          "currency": "SEK", "country": None, "lvf_asset_type": None, "isin_reported": None,
                          "quantity": None, "nominal": None, "market_value_sek": float(v), "industry": None,
                          "weight_derived": True})
    if extra:
        out = pd.concat([out, pd.DataFrame(extra)], ignore_index=True)
    out["weight_derived"] = out["weight_derived"].astype(bool)
    return _nulls_to_none(out)


def classify(positions: pd.DataFrame, net_assets: pd.Series) -> pd.DataFrame:
    """Per fund: ``asset_class`` (65% rule) and ``replication`` from its own lines."""
    p = positions.copy()
    p["cls"] = [_asset_class(a, n) for a, n in zip(p["lvf_asset_type"], p["nominal"])]
    w = p.pivot_table(index="fund_id", columns="cls", values="weight_pct", aggfunc="sum", fill_value=0.0)
    for c in ("equity", "debt", "fund", "derivative"):
        if c not in w:
            w[c] = 0.0
    label = pd.Series("Mixed", index=w.index, dtype=object)
    label[w["fund"] >= 65] = "Fund of funds"
    label[w["debt"] >= 65] = "Fixed Income"
    label[w["equity"] >= 65] = "Equity"
    has_deriv = p[p["cls"] == "derivative"].groupby("fund_id").size().reindex(w.index, fill_value=0) > 0
    repl = pd.Series(None, index=w.index, dtype=object)
    repl[~has_deriv] = "physical"
    return pd.DataFrame({"asset_class": label, "replication": repl})


class FiStore:
    """FI quarterly ZIPs and their Parquet under ``root`` (``raw/`` and ``parquet/<quarter>/``)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._cache: tuple[str, dict[str, pd.DataFrame]] | None = None

    def refresh(self, quarter: str | None = None) -> dict:
        """Download the newest quarter (or ``quarter``) if FI has a newer file, and parse it."""
        quarters = list_quarters()
        if not quarters:
            raise ValueError("No quarterly ZIP found on FI's fund holdings page")
        quarter = quarter or max(quarters)
        name = quarters[quarter]
        out = self.root / "parquet" / quarter
        stamp = out / "source.txt"
        if not (stamp.exists() and stamp.read_text().strip() == name and (out / "positions.parquet").exists()):
            zip_path = download_quarter(name, self.root / "raw")
            frames = read_zip(zip_path)
            out.mkdir(parents=True, exist_ok=True)
            for k, df in frames.items():
                df.to_parquet(out / f"{k}.parquet", index=False)
            stamp.write_text(name)
            for old in (self.root / "raw").glob(f"Fondinnehav_{quarter}_*.zip"):
                if old.name != name:
                    old.unlink()
        self._cache = None
        funds = pd.read_parquet(out / "funds.parquet")
        return {"quarter": quarter, "file": name, "url": download_url(name), "parquet_dir": str(out),
                "funds": len(funds), "as_of": funds["as_of"].dropna().max()}

    def quarters(self) -> list[str]:
        d = self.root / "parquet"
        return sorted(p.name for p in d.iterdir() if (p / "positions.parquet").exists()) if d.exists() else []

    def is_built(self) -> bool:
        return bool(self.quarters())

    def frames(self, quarter: str | None = None) -> tuple[str, dict[str, pd.DataFrame]]:
        got = self.quarters()
        if not got:
            raise FileNotFoundError(f"No FI fund holdings under {self.root}: run FiStore.refresh()")
        quarter = quarter or got[-1]
        if self._cache is None or self._cache[0] != quarter:
            d = self.root / "parquet" / quarter
            self._cache = (quarter, {k: pd.read_parquet(d / f"{k}.parquet") for k in ("funds", "positions")})
        return self._cache

    def source_url(self, quarter: str) -> str:
        stamp = self.root / "parquet" / quarter / "source.txt"
        return download_url(stamp.read_text().strip()) if stamp.exists() else PAGE_URL

    def products(self, quarter: str | None = None) -> pd.DataFrame:
        """Every active fund with a valid ISIN in the quarter (one row per fund: FI gives one ISIN)."""
        quarter, f = self.frames(quarter)
        funds = f["funds"]
        funds = funds[funds["status"].isna() & funds["fund_isin"].notna()]
        cls = classify(f["positions"], funds.set_index("fund_id")["net_assets_sek"])
        funds = funds.join(cls, on="fund_id")
        df = pd.DataFrame(
            {
                "issuer": "sweden_fi",
                "site": "se",
                "portfolio_id": funds["fund_id"],
                "isin": funds["fund_isin"],
                "ticker": None,
                "fund_name": funds["fund_name"],
                "domicile": funds["fund_isin"].str[:2],
                "currency": "SEK",
                "asset_class": funds["asset_class"],
                "replication": funds["replication"],
                "product_page_url": PAGE_URL,
                "holdings_url": self.source_url(quarter),
                "manager": funds["manager"],
                "manager_lei": funds["manager_lei"],
                "bank_group": funds["manager"].map(BANK_GROUPS),
                "net_assets_sek": funds["net_assets_sek"],
                "share_classes": funds["share_classes"],
                "as_of": funds["as_of"],
            }
        )
        return _nulls_to_none(df.drop_duplicates("isin").reset_index(drop=True))

    def holdings(self, portfolio_id: str, quarter: str | None = None) -> tuple[str, pd.DataFrame, list[str], str]:
        """``(as_of, lines, notes, source url)`` for one fund (FI institute number)."""
        quarter, f = self.frames(quarter)
        funds = f["funds"]
        row = funds[funds["fund_id"] == str(portfolio_id)]
        if row.empty:
            raise LookupError(f"FI has no fund {portfolio_id} in {quarter}")
        fund = {k: (None if not isinstance(v, str) and pd.isna(v) else v) for k, v in row.iloc[0].to_dict().items()}
        if fund.get("status") or not fund.get("net_assets_sek"):
            raise LookupError(f"FI fund {portfolio_id} reports no holdings in {quarter} ({fund.get('status')})")
        lines = fund_lines(f["positions"][f["positions"]["fund_id"] == str(portfolio_id)], fund)
        as_of = fund["as_of"]
        notes = [
            f"regulatory filing: Finansinspektionen quarterly fund holdings (Fondinnehav per kvartal), "
            f"positions as of {as_of}",
            "weights are the reported Andel av fondförmögenhet (percent of net assets, two decimals), not rescaled; "
            "the cash and other assets and liabilities lines are SEK amount / Fondförmögenhet (weight_derived)",
            f"fund company: {fund.get('manager')} (LEI {fund.get('manager_lei')})",
        ]
        if fund.get("share_classes"):
            notes.append(f"one portfolio for all share classes: {fund['share_classes']}")
        return as_of, lines, notes, self.source_url(quarter)
