"""Brazil: CVM monthly portfolio composition (CDA) with B3's ISIN register.

Every Brazilian investment fund (each *classe* under CVM Resolution 175) files
a monthly *Composição e Diversificação das Aplicações* (CDA) with the
Comissão de Valores Mobiliários. CVM publishes all of them as open data:

Portfolios
    ``https://dados.cvm.gov.br/dados/FI/DOC/CDA/DADOS/cda_fi_<YYYYMM>.zip``,
    one ZIP per month (about 25 MB) holding semicolon-separated Latin-1 CSVs:
    ``cda_fi_PL`` (net assets per fund), eight position blocks
    ``cda_fi_BLC_1`` .. ``BLC_8``, ``cda_fie`` (structured funds: FIDC, FIP
    and others, net assets on every row) and ``cda_fi_CONFID`` /
    ``cda_fie_CONFID`` (positions the fund may keep confidential for up to 90
    days, given as one total per kind of asset).
Fund ISINs
    CVM data identifies funds by CNPJ only. B3, Brazil's national numbering
    agency, publishes its whole ISIN register as a free download
    (``isinProxy/IsinCall/GetFileDownload``): ``NUMERACA.TXT`` lists every
    ISIN with a four-character issuer code and ``EMISSOR.TXT`` gives each
    issuer's CNPJ. Fund quotas have asset type ``CTF``, so a fund ISIN maps
    to the CNPJ that files its CDA.

The CDA has no weight column. Weights here are the position's market value
(``VL_MERC_POS_FINAL``) divided by the fund's reported net assets
(``VL_PATRIM_LIQ``) for the same month, times 100: never rescaled. Positions
the CDA reports on the liability side (written options, borrowed securities
sold short, other liabilities) are given a negative weight, so the lines sum
to roughly 100%.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

from .. import http
from ..identifiers import is_valid_isin

logger = logging.getLogger(__name__)

CVM_HOST = "dados.cvm.gov.br"
CDA_DIR = f"https://{CVM_HOST}/dados/FI/DOC/CDA/DADOS/"
CDA_URL = CDA_DIR + "cda_fi_{month}.zip"
CDA_DATASET_PAGE = f"https://{CVM_HOST}/dataset/fi-doc-cda"
B3_ISIN_HOST = "sistemaswebb3-listados.b3.com.br"
B3_ISIN_LIST_URL = f"https://{B3_ISIN_HOST}/isinProxy/IsinCall/GetTextDownload/"
B3_ISIN_FILE_URL = f"https://{B3_ISIN_HOST}/isinProxy/IsinCall/GetFileDownload/{{token}}"
B3_ISIN_PAGE = f"https://{B3_ISIN_HOST}/isinPage/"

CREDIT = (
    "Comissão de Valores Mobiliários (CVM), Dados Abertos: Fundos de Investimento, "
    "Composição e Diversificação das Aplicações (CDA); fund ISINs from B3's ISIN register"
)

# dados.cvm.gov.br/robots.txt asks for ``Crawl-Delay: 10``.
http.set_min_interval(CVM_HOST, 10.0)
http.set_min_interval(B3_ISIN_HOST, 2.0)

_B3_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{3}\d{1,2}[A-Z]?$")
_MONTH_RE = re.compile(r'href="cda_fi_(\d{6})\.zip">[^<]*</a>\s+(\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2})')

#: CDA kinds of application (``TP_APLIC``) that are liabilities of the fund.
LIABILITY_APLIC = {
    "Opções - Posições lançadas",
    "Obrigações por ações e outros TVM recebidos em empréstimo",
    "Obrigações por compra a termo a pagar",
    "Obrigações por venda a termo a entregar",
    "Outras operações passivas e exigibilidades",
    "Valores a pagar",
    "DIFERENCIAL DE SWAP A PAGAR",
}

#: Normalised asset class by CDA block, refined by ``TP_APLIC`` below.
_BLOCK_CLASS = {
    "BLC_1": "debt",
    "BLC_2": "fund",
    "BLC_3": "derivative (swap)",
    "BLC_4": "equity",
    "BLC_5": "debt",
    "BLC_6": "debt",
    "BLC_7": "other",
    "BLC_8": "other",
    "FIE": "other",
    "CONFID": "other",
}

_APLIC_CLASS = [
    ("Operações Compromissadas", "repurchase agreement"),
    ("Ações", "equity"),
    ("Brazilian Depository Receipt", "equity"),
    ("Certificado ou recibo de depósito", "equity"),
    ("Debêntures", "debt"),
    ("Opções", "derivative (option)"),
    ("Mercado Futuro", "derivative (future)"),
    ("Termo", "derivative (forward)"),
    ("Cotas de Fundos", "fund"),
    ("Cotas de fundos", "fund"),
    ("Disponibilidades", "cash"),
    ("Depósitos a prazo", "debt"),
    ("Títulos", "debt"),
    ("Investimento no Exterior", "other"),
]

# Common position schema: one row per CDA line, every block mapped onto it.
POSITION_COLUMNS = [
    "month", "as_of", "fund_cnpj", "fund_type", "fund_name", "block", "tp_aplic", "tp_ativo",
    "isin", "code", "name", "held_fund_cnpj", "issuer_cnpj", "country", "maturity",
    "quantity", "value", "confidential_until",
]

# Source column (or SQL expression) for each schema field, per block.
_BLOCK_FIELDS = {
    "BLC_1": {"isin": "CD_ISIN", "code": "CD_SELIC", "name": "TP_TITPUB", "maturity": "DT_VENC"},
    "BLC_2": {"held_fund_cnpj": "CNPJ_FUNDO_CLASSE_COTA", "code": "ID_SUBCLASSE", "name": "NM_FUNDO_CLASSE_SUBCLASSE_COTA"},
    "BLC_3": {"code": "CD_SWAP", "name": "DS_SWAP"},
    "BLC_4": {"isin": "CD_ISIN", "code": "CD_ATIVO", "name": "DS_ATIVO"},
    "BLC_5": {"issuer_cnpj": "CNPJ_EMISSOR", "name": "EMISSOR", "maturity": "DT_VENC"},
    "BLC_6": {"issuer_cnpj": "_PJ_CNPJ", "name": "_PJ_NAME", "code": "TITULO_CETIP", "maturity": "DT_VENC"},
    "BLC_7": {"code": "CD_ATIVO_BV_MERC", "name": ("DS_ATIVO_EXTERIOR", "EMISSOR"), "country": "CD_PAIS", "maturity": "DT_VENC"},
    "BLC_8": {"code": "CD_ATIVO", "name": ("DS_ATIVO", "_PJ_NAME"), "issuer_cnpj": "_PJ_CNPJ", "country": "CD_PAIS", "maturity": "DT_VENC"},
    "FIE": {"code": "CD_ATIVO", "name": ("DS_ATIVO", "_PJ_NAME"), "issuer_cnpj": "_PJ_CNPJ", "country": "CD_PAIS", "maturity": "DT_VENC"},
    "CONFID": {"name": "_CONFID_NAME"},
}


def _block_of(member: str) -> str | None:
    name = Path(member).name
    if not name.endswith(".csv"):
        return None
    if "CONFID" in name:
        return "CONFID"
    if name.startswith("cda_fie_"):
        return "FIE"
    m = re.search(r"_BLC_(\d)_", name)
    if m:
        return f"BLC_{m.group(1)}"
    if "_PL_" in name:
        return "PL"
    return None


# -- downloads ------------------------------------------------------------


def parse_month_listing(html: str) -> dict[str, str]:
    """``{YYYYMM: last-modified}`` from the CDA directory listing."""
    out = {}
    for month, stamp in _MONTH_RE.findall(html):
        out[month] = datetime.strptime(stamp, "%d-%b-%Y %H:%M").strftime("%Y%m%d%H%M")
    return out


def list_months() -> dict[str, str]:
    """Every published CDA month with the time CVM last regenerated its ZIP."""
    r = http.get(CDA_DIR)
    r.raise_for_status()
    return parse_month_listing(r.text)


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = http.get(url, timeout=300)
    r.raise_for_status()
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(r.content)
    tmp.replace(dest)
    return dest


def download_month(month: str, raw_dir: str | Path, stamp: str | None = None) -> Path:
    """Download one month's ZIP unless the same version is cached.

    CVM regenerates old months as confidential positions are released, so the
    cached file name carries the listing's timestamp and a newer one replaces it.
    """
    raw_dir = Path(raw_dir)
    stamp = stamp or list_months().get(month)
    if stamp is None:
        raise ValueError(f"CVM has no CDA file for {month}")
    dest = raw_dir / f"cda_fi_{month}_{stamp}.zip"
    if dest.exists():
        return dest
    for old in raw_dir.glob(f"cda_fi_{month}_*.zip"):
        old.unlink()
    logger.info("Downloading CVM CDA %s", month)
    return _download(CDA_URL.format(month=month), dest)


def parse_isin_listing(payload: dict) -> tuple[int, str]:
    """The id and generation time of B3's complete ISIN file."""
    full = payload["geralPt"]
    return int(full["id"]), full["dataGeracao"]


def download_isin_register(raw_dir: str | Path) -> Path:
    """B3's complete ISIN register, one file per generation date."""
    raw_dir = Path(raw_dir)
    r = http.get(B3_ISIN_LIST_URL)
    r.raise_for_status()
    file_id, generated = parse_isin_listing(r.json())
    dest = raw_dir / f"b3_isin_{generated[:10].replace('-', '')}.zip"
    if dest.exists():
        return dest
    for old in raw_dir.glob("b3_isin_*.zip"):
        old.unlink()
    token = base64.b64encode(json.dumps(file_id).encode()).decode()
    logger.info("Downloading B3 ISIN register %s", generated)
    return _download(B3_ISIN_FILE_URL.format(token=token), dest)


# -- B3 ISIN register -----------------------------------------------------


def _read_b3_csv(raw: bytes, usecols: list[int], names: list[str]) -> pd.DataFrame:
    text = raw.decode("utf-8", errors="replace")
    df = pd.read_csv(io.StringIO(text), header=None, usecols=usecols, dtype=str, keep_default_na=False)
    df.columns = names
    return df


def parse_isin_register(content: bytes) -> pd.DataFrame:
    """Fund-quota ISINs (asset type ``CTF``) with their issuer's CNPJ.

    Columns: ``isin``, ``issuer_code``, ``cnpj``, ``issuer_name``,
    ``description``, ``cfi``, ``active``.
    """
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names = {Path(n).name.upper(): n for n in z.namelist()}
        em = _read_b3_csv(z.read(names["EMISSOR.TXT"]), [0, 1, 2], ["issuer_code", "issuer_name", "cnpj"])
        nu = _read_b3_csv(
            z.read(names["NUMERACA.TXT"]), [2, 3, 4, 5, 20, 42],
            ["isin", "issuer_code", "cfi", "description", "asset_type", "status"],
        )
    nu = nu[nu["asset_type"] == "CTF"]
    em["cnpj"] = em["cnpj"].str.replace(r"\D", "", regex=True).str.zfill(14)
    em.loc[em["cnpj"] == "0" * 14, "cnpj"] = None
    out = nu.merge(em, on="issuer_code", how="left")
    out = out[out["isin"].map(is_valid_isin)]
    out["active"] = out["status"].eq("A")
    return out[["isin", "issuer_code", "cnpj", "issuer_name", "description", "cfi", "active"]].reset_index(drop=True)


# -- CDA to Parquet -------------------------------------------------------


def _transcode(src, dest: Path) -> list[str]:
    """Latin-1 CSV to UTF-8; returns the header fields."""
    with io.TextIOWrapper(src, encoding="latin-1", newline="") as fin, open(dest, "w", encoding="utf-8", newline="") as fout:
        header = fin.readline()
        fout.write(header)
        shutil.copyfileobj(fin, fout, 1 << 20)
    return [h.strip() for h in header.rstrip("\r\n").split(";")]


def _q(c: str) -> str:
    return '"' + c + '"'


def _block_sql(path: Path, block: str, cols: list[str], month: str) -> str:
    have = set(cols)

    def col(c: str) -> str:
        return f"nullif(trim({_q(c)}), '')" if c in have else "NULL"

    # CPF numbers and names of private individuals are personal data and are
    # dropped: only company (PJ) issuers keep their CNPJ and name.
    pj = f"coalesce({col('PF_PJ_EMISSOR')}, 'PJ') = 'PJ'"
    issuer_id = "CPF_CNPJ_EMISSOR" if "CPF_CNPJ_EMISSOR" in have else "CNPJ_EMISSOR"
    macros = {
        "_PJ_CNPJ": f"CASE WHEN {pj} THEN regexp_replace({col(issuer_id)}, '[^0-9]', '', 'g') END",
        "_PJ_NAME": f"CASE WHEN {pj} THEN {col('EMISSOR')} ELSE 'Individual issuer (name withheld)' END",
        "_CONFID_NAME": f"'Confidential positions: ' || coalesce({col('TP_APLIC')}, 'unspecified')",
    }
    fields = _BLOCK_FIELDS.get(block, {})

    def expr(field: str) -> str:
        """The field's source column, or the first non-null of several."""
        src = fields.get(field)
        if src is None:
            return "NULL"
        parts = [macros[c] if c in macros else col(c) for c in ((src,) if isinstance(src, str) else src)]
        return parts[0] if len(parts) == 1 else f"coalesce({', '.join(parts)})"

    num = lambda c: f"try_cast(replace({col(c)}, ',', '.') AS DOUBLE)"  # noqa: E731
    return f"""
        SELECT '{month}' AS month,
               try_cast({col('DT_COMPTC')} AS DATE) AS as_of,
               regexp_replace({col('CNPJ_FUNDO_CLASSE')}, '[^0-9]', '', 'g') AS fund_cnpj,
               {col('TP_FUNDO_CLASSE')} AS fund_type,
               {col('DENOM_SOCIAL')} AS fund_name,
               '{block}' AS block,
               {col('TP_APLIC')} AS tp_aplic,
               {col('TP_ATIVO')} AS tp_ativo,
               upper({expr('isin')}) AS isin,
               CAST({expr('code')} AS VARCHAR) AS code,
               CAST({expr('name')} AS VARCHAR) AS name,
               regexp_replace({expr('held_fund_cnpj')}, '[^0-9]', '', 'g') AS held_fund_cnpj,
               CAST({expr('issuer_cnpj')} AS VARCHAR) AS issuer_cnpj,
               CAST({expr('country')} AS VARCHAR) AS country,
               try_cast({expr('maturity')} AS DATE) AS maturity,
               {num('QT_POS_FINAL')} AS quantity,
               {num('VL_MERC_POS_FINAL')} AS value,
               try_cast({col('DT_CONFID_APLIC')} AS DATE) AS confidential_until
        FROM read_csv('{path}', delim=';', header=true, all_varchar=true, quote='', escape='',
                      null_padding=true, strict_mode=false)
    """


def cda_to_parquet(zip_path: str | Path, out_dir: str | Path, con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """Convert one month's ZIP to ``positions_<YYYYMM>.parquet`` and ``nav_<YYYYMM>.parquet``.

    ``positions`` has :data:`POSITION_COLUMNS`; ``nav`` has ``fund_cnpj``,
    ``fund_type``, ``fund_name``, ``as_of`` and ``net_assets`` for every fund
    that filed, including structured funds whose net assets are on their rows.
    """
    zip_path, out_dir = Path(zip_path), Path(out_dir)
    month = re.search(r"cda_fi_(\d{6})", zip_path.name).group(1)
    out_dir.mkdir(parents=True, exist_ok=True)
    con = con or duckdb.connect()
    pos_path, nav_path = out_dir / f"positions_{month}.parquet", out_dir / f"nav_{month}.parquet"
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(zip_path) as z:
        selects, nav_selects = [], []
        for member in z.namelist():
            block = _block_of(member)
            if block is None:
                continue
            dest = Path(tmp) / Path(member).name
            with z.open(member) as src:
                cols = _transcode(src, dest)
            src_sql = (
                f"read_csv('{dest}', delim=';', header=true, all_varchar=true, quote='', escape='', "
                "null_padding=true, strict_mode=false)"
            )
            if block == "PL" or (block == "FIE" and "VL_PATRIM_LIQ" in cols):
                nav_selects.append(
                    f"SELECT DISTINCT regexp_replace(CNPJ_FUNDO_CLASSE, '[^0-9]', '', 'g') AS fund_cnpj, "
                    f"TP_FUNDO_CLASSE AS fund_type, DENOM_SOCIAL AS fund_name, try_cast(DT_COMPTC AS DATE) AS as_of, "
                    f"try_cast(VL_PATRIM_LIQ AS DOUBLE) AS net_assets FROM {src_sql}"
                )
            if block != "PL":
                selects.append(_block_sql(dest, block, cols, month))
        con.execute(
            f"COPY (SELECT * FROM ({' UNION ALL '.join(selects)}) ORDER BY fund_cnpj) TO '{pos_path}' (FORMAT parquet)"
        )
        con.execute(
            f"COPY (SELECT fund_cnpj, any_value(fund_type) fund_type, any_value(fund_name) fund_name, max(as_of) as_of, "
            f"max(net_assets) net_assets FROM ({' UNION ALL '.join(nav_selects)}) GROUP BY fund_cnpj ORDER BY fund_cnpj) "
            f"TO '{nav_path}' (FORMAT parquet)"
        )
    tick_path = out_dir / f"tickers_{month}.parquet"
    con.execute(ticker_map_sql(f"'{pos_path}'", tick_path))
    n = con.execute(f"SELECT count(*), count(isin) FROM '{pos_path}'").fetchone()
    funds = con.execute(f"SELECT count(*) FROM '{nav_path}'").fetchone()[0]
    return {"month": month, "positions_path": str(pos_path), "nav_path": str(nav_path),
            "tickers_path": str(tick_path), "rows": n[0], "rows_with_isin": n[1], "funds": funds}


def ticker_map_sql(positions: str, dest: Path) -> str:
    """SQL writing B3 ticker -> ISIN pairs that funds themselves reported.

    Block 4 rows carry both the B3 code (``CD_ATIVO``, e.g. ``ITUB4``) and the
    ISIN. Structured and index funds (``cda_fie``) report only the code, so
    their lines get the ISIN that other funds' filings give for it. A code is
    kept only when one ISIN accounts for at least 95% of its rows.
    """
    return f"""
        COPY (
          WITH pairs AS (
            SELECT upper(code) AS code, isin, any_value(name) AS name, count(*) AS n
            FROM {positions}
            WHERE block = 'BLC_4' AND isin IS NOT NULL AND code IS NOT NULL
            GROUP BY 1, 2
          ), ranked AS (
            SELECT *, sum(n) OVER (PARTITION BY code) AS total,
                   row_number() OVER (PARTITION BY code ORDER BY n DESC, isin) AS rk
            FROM pairs
          )
          SELECT code, isin, name, n, total FROM ranked WHERE rk = 1 AND n >= 0.95 * total ORDER BY code
        ) TO '{dest}' (FORMAT parquet)
    """


# -- one fund -------------------------------------------------------------


def asset_class(block: str, tp_aplic: str | None) -> str:
    for prefix, cls in _APLIC_CLASS:
        if tp_aplic and tp_aplic.startswith(prefix):
            return cls
    return _BLOCK_CLASS.get(block, "other")


def _nulls_to_none(df: pd.DataFrame) -> pd.DataFrame:
    """Missing values in text columns as None (pandas 3 would give NaN)."""
    for c in df.columns:
        if not pd.api.types.is_numeric_dtype(df[c]):
            df[c] = pd.Series([None if pd.isna(v) else v for v in df[c]], index=df.index, dtype=object)
    return df


def fund_positions(
    positions: pd.DataFrame,
    net_assets: float,
    fund_isins: dict[str, str] | None = None,
    ticker_isins: dict[str, tuple[str, str | None]] | None = None,
) -> pd.DataFrame:
    """One fund's CDA rows to :data:`~openbasket.sources.issuers.HOLDING_COLUMNS`.

    ISINs come, in order, from the filing itself (``reported``), from B3's
    register for a held fund's CNPJ when that CNPJ has a single active quota
    ISIN (``b3_fund_cnpj``), or from the ISIN other funds reported for the
    same B3 code that month (``cda_ticker``). Extra columns kept for
    traceability: ``isin_source``, ``fund_cnpj`` (of a held fund),
    ``issuer_cnpj``, ``block``, ``tp_aplic``, ``tp_ativo``, ``maturity``,
    ``market_value_brl`` and ``confidential_until``.
    """
    fund_isins = fund_isins or {}
    ticker_isins = ticker_isins or {}
    p = positions.reset_index(drop=True)
    aplic = p["tp_aplic"].fillna("")
    sign = aplic.map(lambda a: -1.0 if a in LIABILITY_APLIC else 1.0)
    weight = p["value"].astype(float) * sign / float(net_assets) * 100.0
    repo = aplic.str.startswith("Operações Compromissadas")

    reported = p["isin"].map(lambda v: v if isinstance(v, str) and is_valid_isin(v) else None)
    reported = reported.where(~repo, None)  # a repo is backed by the bond, not a holding of it
    held = p["held_fund_cnpj"].where(p["held_fund_cnpj"].fillna("") != "")
    via_cnpj = held.map(fund_isins)
    code = p["code"].where(p["block"].isin(["BLC_4", "BLC_8", "FIE"]))
    tick = code.map(lambda c: ticker_isins.get(c.upper()) if isinstance(c, str) else None)
    via_ticker = tick.map(lambda t: t[0] if t else None)
    # Only B3 trading codes (ITUB4, BOVA11) and foreign exchange tickers are
    # tickers; other codes in these blocks are internal numbers.
    b3_code = code.map(lambda c: c.upper() if isinstance(c, str) and _B3_CODE_RE.match(c.upper()) else None)
    foreign = p["code"].where(p["block"] == "BLC_7")
    ticker = b3_code.where(b3_code.notna(), foreign).map(lambda v: v if isinstance(v, str) else None)

    isin = reported.astype(object)
    source = pd.Series(["reported" if pd.notna(v) else None for v in reported], dtype=object)
    for cand, label in ((via_cnpj, "b3_fund_cnpj"), (via_ticker, "cda_ticker")):
        fill = isin.isna() & cand.notna()
        isin[fill] = cand[fill]
        source[fill] = label

    name = p["name"].astype(object)
    name = name.fillna(tick.map(lambda t: t[1] if t else None)).fillna(p["code"]).fillna(p["tp_ativo"]).fillna(aplic)
    repo_name = "Repo (operação compromissada) backed by " + p["name"].fillna("") + " " + p["isin"].fillna("")
    name = name.where(~repo, repo_name)
    out = pd.DataFrame(
        {
            "isin": isin,
            "cusip": None,
            "sedol": None,
            "ticker": ticker,
            "name": name.astype(str).str.strip(),
            "weight_pct": weight,
            "asset_class": [asset_class(b, a) for b, a in zip(p["block"], aplic)],
            "currency": p["block"].map(lambda b: None if b == "BLC_7" else "BRL"),
            "country": p["country"],
            "isin_source": source,
            "fund_cnpj": held,
            "issuer_cnpj": p["issuer_cnpj"],
            "block": p["block"],
            "tp_aplic": p["tp_aplic"],
            "tp_ativo": p["tp_ativo"],
            "maturity": p["maturity"].map(lambda d: None if pd.isna(d) else str(d)[:10]),
            "market_value_brl": p["value"],
            "confidential_until": p["confidential_until"].map(lambda d: None if pd.isna(d) else str(d)[:10]),
        }
    )
    liab = aplic.isin(LIABILITY_APLIC)
    out.loc[liab & ~out["asset_class"].str.startswith("derivative"), "asset_class"] = "other"
    out.loc[out["block"] == "CONFID", "asset_class"] = "other"
    return _nulls_to_none(out)


# -- local store ----------------------------------------------------------


class CdaStore:
    """Downloaded CDA months and the B3 register, converted to Parquet.

    ``refresh`` downloads the newest ``months`` ZIPs (only those CVM has
    regenerated since the last run) and the ISIN register, and converts them.
    Lookups then read Parquet only.
    """

    def __init__(self, root: str | Path, months: int = 3, max_confidential_pct: float = 10.0):
        self.root = Path(root)
        self.raw = self.root / "raw"
        self.parquet = self.root / "parquet"
        self.months = months
        self.max_confidential_pct = max_confidential_pct
        self.con = duckdb.connect()
        self._cache: dict = {}

    # -- building

    def refresh(self) -> pd.DataFrame:
        """Bring the newest ``months`` and the ISIN register up to date; returns a manifest."""
        listing = list_months()
        wanted = sorted(listing)[-self.months:]
        rows = []
        self.parquet.mkdir(parents=True, exist_ok=True)
        for month in wanted:
            zip_path = download_month(month, self.raw, listing[month])
            stamp = self.parquet / f"positions_{month}.source"
            if not stamp.exists() or stamp.read_text() != zip_path.name:
                info = cda_to_parquet(zip_path, self.parquet, self.con)
                stamp.write_text(zip_path.name)
            else:
                info = {"month": month, "positions_path": str(self.parquet / f"positions_{month}.parquet")}
            rows.append({**info, "zip_path": str(zip_path), "listed": listing[month]})
        for old in self.available_months():
            if old not in wanted:
                for f in self.parquet.glob(f"*_{old}.*"):
                    f.unlink()
        reg = download_isin_register(self.raw)
        isin_pq = self.parquet / "b3_fund_isins.parquet"
        stamp = self.parquet / "b3_fund_isins.source"
        if not isin_pq.exists() or not stamp.exists() or stamp.read_text() != reg.name:
            parse_isin_register(reg.read_bytes()).to_parquet(isin_pq, index=False)
            stamp.write_text(reg.name)
        self._cache.clear()
        return pd.DataFrame(rows)

    def is_built(self) -> bool:
        return bool(self.available_months()) and (self.parquet / "b3_fund_isins.parquet").exists()

    # -- reading

    def available_months(self) -> list[str]:
        return sorted(p.stem.split("_")[1] for p in self.parquet.glob("positions_*.parquet"))

    def fund_isins(self) -> pd.DataFrame:
        if "isins" not in self._cache:
            path = self.parquet / "b3_fund_isins.parquet"
            if not path.exists():
                raise FileNotFoundError(f"{path} missing: run the reg_brazil pipeline or CdaStore.refresh()")
            self._cache["isins"] = pd.read_parquet(path)
        return self._cache["isins"]

    def unique_fund_isin(self) -> dict[str, str]:
        """CNPJ -> quota ISIN, for CNPJs with exactly one active quota ISIN."""
        if "unique" not in self._cache:
            f = self.fund_isins()
            f = f[f["active"] & f["cnpj"].notna()]
            one = f[f.groupby("cnpj")["isin"].transform("size") == 1]
            self._cache["unique"] = dict(zip(one["cnpj"], one["isin"]))
        return self._cache["unique"]

    def navs(self) -> pd.DataFrame:
        """Every fund's net assets in every stored month."""
        if "navs" not in self._cache:
            months = self.available_months()
            cols = ["fund_cnpj", "fund_type", "fund_name", "as_of", "net_assets", "month"]
            frames = [pd.read_parquet(self.parquet / f"nav_{m}.parquet").assign(month=m) for m in months]
            self._cache["navs"] = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)
        return self._cache["navs"]

    def products(self) -> pd.DataFrame:
        """Active fund-quota ISINs of funds that filed a CDA in a stored month."""
        months = self.available_months()
        nav = self.navs().sort_values("month").drop_duplicates("fund_cnpj", keep="last")
        f = self.fund_isins()
        f = f[f["active"]].merge(nav, left_on="cnpj", right_on="fund_cnpj", how="inner")
        return pd.DataFrame(
            {
                "issuer": "cvm_brazil",
                "site": "br",
                "portfolio_id": f["cnpj"],
                "isin": f["isin"],
                "ticker": None,
                "fund_name": f["fund_name"],
                "domicile": "BR",
                "currency": "BRL",
                "asset_class": f["fund_type"],
                "replication": None,
                "product_page_url": CDA_DATASET_PAGE,
                "holdings_url": f["month"].map(lambda m: CDA_URL.format(month=m)),
                "share_class": f["description"],
            }
        ).drop_duplicates("isin").reset_index(drop=True) if months else pd.DataFrame()

    def _rows(self, month: str, cnpj: str) -> pd.DataFrame:
        path = self.parquet / f"positions_{month}.parquet"
        return self.con.execute(f"SELECT * FROM '{path}' WHERE fund_cnpj = ?", [cnpj]).df()

    def _tickers(self, month: str) -> dict[str, tuple[str, str | None]]:
        key = ("tickers", month)
        if key not in self._cache:
            path = self.parquet / f"tickers_{month}.parquet"
            t = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=["code", "isin", "name"])
            self._cache[key] = {c: (i, n) for c, i, n in zip(t["code"], t["isin"], t["name"])}
        return self._cache[key]

    def choose_month(self, cnpj: str) -> tuple[str, float, float] | None:
        """The newest month whose confidential share is acceptable.

        Returns ``(month, net_assets, confidential_pct)``. CVM withholds the
        detail of positions a fund keeps confidential (up to 90 days), so the
        newest month can be mostly one "confidential" line; an older month that
        is now public is preferred when the newest one hides more than
        ``max_confidential_pct``.
        """
        nav = self.navs()
        nav = nav[(nav["fund_cnpj"] == cnpj) & (nav["net_assets"] > 0)].sort_values("month", ascending=False)
        best = None
        for row in nav.itertuples(index=False):
            path = self.parquet / f"positions_{row.month}.parquet"
            conf = self.con.execute(
                f"SELECT coalesce(sum(value), 0) FROM '{path}' WHERE fund_cnpj = ? AND block = 'CONFID'", [cnpj]
            ).fetchone()[0]
            pct = 100.0 * conf / row.net_assets
            if pct <= self.max_confidential_pct:
                return row.month, float(row.net_assets), pct
            if best is None or pct < best[2]:
                best = (row.month, float(row.net_assets), pct)
        return best

    def holdings(self, cnpj: str) -> tuple[str, str, pd.DataFrame, list[str]] | None:
        """``(as_of, month, frame, notes)`` for one fund, or None if it filed nothing stored."""
        chosen = self.choose_month(cnpj)
        if chosen is None:
            return None
        month, net_assets, conf_pct = chosen
        frame = fund_positions(self._rows(month, cnpj), net_assets, self.unique_fund_isin(), self._tickers(month))
        nav = self.navs()
        nav = nav[nav["fund_cnpj"] == cnpj]
        as_of = str(nav[nav["month"] == month]["as_of"].iloc[0])[:10]
        notes = [
            f"regulatory filing: CVM monthly portfolio composition (CDA), positions as of {as_of}",
            "weights are market value / net assets from the same filing (VL_MERC_POS_FINAL / VL_PATRIM_LIQ); "
            "liability lines (written options, borrowed securities, payables) are negative",
            "fund ISIN matched to the filing CNPJ through B3's ISIN register",
        ]
        if conf_pct > 0:
            until = frame["confidential_until"].dropna().max()
            notes.append(f"{conf_pct:.2f}% of net assets is confidential in this filing (detail released after {until})")
        newer = sorted(m for m in nav["month"] if m > month)
        if newer:
            notes.append(f"newer filing {newer[-1]} not used: more than {self.max_confidential_pct:g}% of it is confidential")
        return as_of, month, frame, notes


