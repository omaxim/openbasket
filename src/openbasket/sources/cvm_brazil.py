"""Brazil: CVM monthly portfolio composition (CDA) with B3's ISIN register.

Every Brazilian investment fund (each *classe* under CVM Resolution 175) files
a monthly *Composição e Diversificação das Aplicações* (CDA) with the
Comissão de Valores Mobiliários. CVM publishes all of them as open data:

Portfolios
    ``https://dados.cvm.gov.br/dados/FI/DOC/CDA/DADOS/cda_fi_<YYYYMM>.zip``,
    one ZIP per month (about 25 MB) holding semicolon-separated Latin-1 CSVs:
    ``cda_fi_PL`` (net assets per fund), eight position blocks
    ``cda_fi_BLC_1`` .. ``BLC_8``, ``cda_fie`` (ETFs and structured funds:
    FIDC, FIP and others, net assets on every row) and ``cda_fi_CONFID`` /
    ``cda_fie_CONFID`` (positions the fund may keep confidential for up to 90
    days, given as one total per kind of asset).
Fund ISINs
    CVM data identifies funds by CNPJ only. B3, Brazil's national numbering
    agency, publishes its whole ISIN register as a free download
    (``isinProxy/IsinCall/GetFileDownload``): ``NUMERACA.TXT`` lists every
    ISIN with a four-character issuer code and ``EMISSOR.TXT`` gives each
    issuer's CNPJ. Fund quotas have asset type ``CTF``, so a fund ISIN maps
    to the CNPJ that files its CDA.
Tickers
    B3's daily instruments file (``InstrumentsConsolidatedFile`` from
    ``arquivos.b3.com.br/api/download``) maps every listed code to its ISIN.
    It gives listed funds (ETFs, FI-Infra, FIIs ...) their trading code and
    category, and is a fallback for the ISIN of a constituent that a filing
    names only by its B3 code.

The CDA has no weight column. Weights here are the position's market value
(``VL_MERC_POS_FINAL``) divided by the fund's reported net assets
(``VL_PATRIM_LIQ``) for the same month, times 100: never rescaled. Positions
the CDA reports on the liability side (written options, borrowed securities
sold short, other liabilities) are given a negative weight, so the lines sum
to roughly 100%.

``dados.cvm.gov.br`` asks for 10 seconds between requests (``Crawl-Delay: 10``)
and disallows ``/api/``; only files under ``/dados/`` are read. The two B3
hosts have no robots.txt.
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
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from .. import http
from ..identifiers import is_valid_isin

logger = logging.getLogger(__name__)

#: Redistribution class (see :func:`openbasket.sources.redistribution`).
#: The CDA dataset page (https://dados.cvm.gov.br/dataset/fi-doc-cda, read
#: 2026-11-03) gives its licence as "Licença Aberta para Bases de Dados (ODbL)
#: do Open Data Commons": the Open Database License 1.0. A derived database
#: may be published if CVM is credited and it is offered under the ODbL too
#: (see docs/source/data-licence.md).
REDISTRIBUTION = "public"
LICENCE = (
    "CVM open data under the Open Database License (ODbL 1.0): credit CVM and offer derived databases "
    "under the ODbL. B3's ISIN register and instruments file are free public downloads; their terms were "
    "not found on the download pages, and only identifiers (ISIN, trading code, category) are taken from them."
)

CVM_HOST = "dados.cvm.gov.br"
CDA_DIR = f"https://{CVM_HOST}/dados/FI/DOC/CDA/DADOS/"
CDA_URL = CDA_DIR + "cda_fi_{month}.zip"
CDA_DATASET_PAGE = f"https://{CVM_HOST}/dataset/fi-doc-cda"
B3_ISIN_HOST = "sistemaswebb3-listados.b3.com.br"
B3_ISIN_LIST_URL = f"https://{B3_ISIN_HOST}/isinProxy/IsinCall/GetTextDownload/"
B3_ISIN_FILE_URL = f"https://{B3_ISIN_HOST}/isinProxy/IsinCall/GetFileDownload/{{token}}"
B3_ISIN_PAGE = f"https://{B3_ISIN_HOST}/isinPage/"
B3_FILES_HOST = "arquivos.b3.com.br"
B3_FILES_API = f"https://{B3_FILES_HOST}/api/download/"

CREDIT = (
    "Comissão de Valores Mobiliários (CVM), Dados Abertos: Fundos de Investimento, "
    "Composição e Diversificação das Aplicações (CDA); fund ISINs and tickers from B3"
)

# dados.cvm.gov.br/robots.txt asks for ``Crawl-Delay: 10``.
http.set_min_interval(CVM_HOST, 10.0)
http.set_min_interval(B3_ISIN_HOST, 2.0)
http.set_min_interval(B3_FILES_HOST, 2.0)

_B3_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{3}\d{1,2}[A-Z]?$")
#: A foreign exchange ticker as funds file it on offshore lines: ``IAU``, ``VXUS US``.
_FOREIGN_TICKER_RE = re.compile(r"^(?=[A-Z0-9.\-]*[A-Z])[A-Z0-9.\-]{1,10}( [A-Z]{2})?$")
_ISIN_IN_TEXT_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b")
# The directory index is either preformatted text or a table; the date follows the link either way.
_MONTH_RE = re.compile(r'href="cda_fi_(\d{6})\.zip">[^<]*</a>(?:\s|<[^>]+>)*(\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2})')

#: CDA kinds of application (``TP_APLIC``) that are liabilities of the fund.
#: Checked on the June to September 2026 files: with these negated, 97.5% to
#: 97.9% of the about 26,000 fund classes with net assets over BRL 1m add up
#: to within 0.5 points of 100% (95% to 97% of the ETFs).
#: "Obrigações por venda a termo a entregar" is left positive: the bond ETFs
#: that sell forward (3PRE11, 03BK11, PACB11) only add up to 100% that way.
#: Futures lines are left as reported: their value is the daily margin
#: adjustment and already carries its sign.
LIABILITY_APLIC = {
    "Opções - Posições lançadas",
    "Obrigações por ações e outros TVM recebidos em empréstimo",
    "Obrigações por compra a termo a pagar",
    "Outras operações passivas e exigibilidades",
    "Valores a pagar",
    "DIFERENCIAL DE SWAP A PAGAR",
}

OFFSHORE_APLIC = "Investimento no Exterior"

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
    ("Compras a termo", "derivative (forward)"),
    ("Vendas a termo", "derivative (forward)"),
    ("Obrigações por compra a termo", "derivative (forward)"),
    ("Obrigações por venda a termo", "derivative (forward)"),
    ("DIFERENCIAL DE SWAP", "derivative (swap)"),
    ("Cotas de Fundos", "fund"),
    ("Cotas de fundos", "fund"),
    ("Disponibilidades", "cash"),
    ("Depósitos a prazo", "debt"),
    ("Títulos", "debt"),
]

#: Asset class of an offshore line by ``TP_ATIVO``.
_OFFSHORE_CLASS = (("Fundo", "fund"), ("Aç", "equity"), ("BDR", "equity"), ("Título", "debt"), ("Bond", "debt"),
                   ("Deriv", "derivative"))

#: Classes that give an equity ETF its exposure physically, and lines that give it synthetically.
_PHYSICAL_CLASSES = ("equity", "fund")
_SYNTHETIC_PREFIXES = ("Mercado Futuro", "DIFERENCIAL DE SWAP")

# Common position schema: one row per CDA line, every block mapped onto it.
POSITION_COLUMNS = [
    "month", "as_of", "fund_cnpj", "fund_type", "fund_name", "block", "tp_aplic", "tp_ativo",
    "isin", "code", "name", "held_fund_cnpj", "issuer_cnpj", "country", "maturity",
    "quantity", "value", "confidential_until", "selic",
]

# Source column (or SQL expression) for each schema field, per block.
_BLOCK_FIELDS = {
    "BLC_1": {"isin": "CD_ISIN", "code": "CD_SELIC", "name": "TP_TITPUB", "maturity": "DT_VENC", "selic": "CD_SELIC"},
    "BLC_2": {"held_fund_cnpj": "CNPJ_FUNDO_CLASSE_COTA", "code": "ID_SUBCLASSE", "name": "NM_FUNDO_CLASSE_SUBCLASSE_COTA"},
    "BLC_3": {"code": "CD_SWAP", "name": "DS_SWAP"},
    "BLC_4": {"isin": "CD_ISIN", "code": "CD_ATIVO", "name": "DS_ATIVO"},
    "BLC_5": {"issuer_cnpj": "CNPJ_EMISSOR", "name": "EMISSOR", "maturity": "DT_VENC"},
    "BLC_6": {"issuer_cnpj": "_PJ_CNPJ", "name": "_PJ_NAME", "code": "TITULO_CETIP", "maturity": "DT_VENC"},
    "BLC_7": {"code": "CD_ATIVO_BV_MERC", "name": ("DS_ATIVO_EXTERIOR", "EMISSOR"), "country": "CD_PAIS", "maturity": "DT_VENC"},
    "BLC_8": {"code": "CD_ATIVO", "name": ("DS_ATIVO", "_PJ_NAME"), "issuer_cnpj": "_PJ_CNPJ", "country": "CD_PAIS", "maturity": "DT_VENC"},
    "FIE": {"code": "CD_ATIVO", "name": ("DS_ATIVO", "_PJ_NAME"), "issuer_cnpj": "_PJ_CNPJ", "country": "CD_PAIS",
            "maturity": "DT_VENC", "selic": "CD_SELIC"},
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


def parse_instruments(content: bytes) -> pd.DataFrame:
    """B3 instruments file to ``ticker, isin, segment, category, name``.

    Equity-market (``CASH``) rows come first when a ticker appears twice.
    Fixed-income ETFs such as ``IMAB11`` are listed in the ``FORWARD``
    segment, so no segment is dropped except the ETF primary market
    (creations and redemptions).
    """
    text = content.decode("latin-1")
    start = text.find("RptDt;")
    if start < 0:
        raise ValueError("B3 instruments file has no RptDt header")
    df = pd.read_csv(io.StringIO(text[start:]), sep=";", dtype=str, usecols=["TckrSymb", "SgmtNm", "SctyCtgyNm", "ISIN", "CrpnNm"])
    df = df[df["ISIN"].map(is_valid_isin) & (df["SgmtNm"] != "ETF PRIMARY MARKET")]
    df = df.assign(_o=(df["SgmtNm"] != "CASH").astype(int)).sort_values("_o", kind="stable")
    df = df.rename(columns={"TckrSymb": "ticker", "ISIN": "isin", "SgmtNm": "segment", "SctyCtgyNm": "category", "CrpnNm": "name"})
    return df[["ticker", "isin", "segment", "category", "name"]].drop_duplicates("ticker").reset_index(drop=True)


def download_instruments(raw_dir: str | Path, today: date | None = None, days: int = 7) -> Path:
    """The newest B3 instruments file of the last ``days`` days; one file is kept."""
    raw_dir = Path(raw_dir)
    day = today or date.today()
    for _ in range(days):
        dest = raw_dir / f"b3_instruments_{day:%Y%m%d}.csv"
        if dest.exists():
            return dest
        r = http.get(B3_FILES_API + "requestname", params={"fileName": "InstrumentsConsolidatedFile", "date": day.isoformat()}, timeout=60)
        token = r.json().get("token") if r.ok and r.content else None
        if token:
            f = http.get(B3_FILES_API, params={"token": token}, timeout=300)
            if f.ok and len(f.content) > 1000:
                parse_instruments(f.content)  # raises before a bad file is kept
                raw_dir.mkdir(parents=True, exist_ok=True)
                for old in raw_dir.glob("b3_instruments_*.csv"):
                    old.unlink()
                dest.write_bytes(f.content)
                return dest
        day -= timedelta(days=1)
    raise ValueError(f"No B3 instruments file in the last {days} days")


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
               try_cast({col('DT_CONFID_APLIC')} AS DATE) AS confidential_until,
               CAST({expr('selic')} AS VARCHAR) AS selic
        FROM read_csv('{path}', delim=';', header=true, all_varchar=true, quote='', escape='',
                      null_padding=true, strict_mode=false)
    """


def cda_to_parquet(zip_path: str | Path, out_dir: str | Path, con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """Convert one month's ZIP to Parquet files named ``<kind>_<YYYYMM>.parquet``.

    ``positions`` has :data:`POSITION_COLUMNS`; ``nav`` has ``fund_cnpj``,
    ``fund_type``, ``fund_name``, ``as_of`` and ``net_assets`` for every fund
    that filed, including structured funds whose net assets are on their rows;
    ``tickers`` and ``selic`` are the lookups of :func:`ticker_map_sql` and
    :func:`selic_map_sql`.
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
    tick_path, selic_path = out_dir / f"tickers_{month}.parquet", out_dir / f"selic_{month}.parquet"
    con.execute(ticker_map_sql(f"'{pos_path}'", tick_path))
    con.execute(selic_map_sql(f"'{pos_path}'", selic_path))
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


def selic_map_sql(positions: str, dest: Path) -> str:
    """SQL writing ``(SELIC code, maturity) -> ISIN`` pairs from ordinary funds' government bonds.

    Index and structured funds (``cda_fie``) give a government bond only by
    its SELIC code and maturity; ordinary funds' block 1 gives its ISIN as
    well. Repos are left out (their SELIC code is the collateral), and the
    ISIN reported most often for a pair is kept.
    """
    return f"""
        COPY (
          WITH pairs AS (
            SELECT selic, maturity, isin, count(*) AS n
            FROM {positions}
            WHERE block = 'BLC_1' AND isin IS NOT NULL AND selic IS NOT NULL AND maturity IS NOT NULL
              AND NOT starts_with(coalesce(tp_aplic, ''), 'Operações Compromissadas')
            GROUP BY 1, 2, 3
          )
          SELECT selic, maturity, isin FROM (
            SELECT *, row_number() OVER (PARTITION BY selic, maturity ORDER BY n DESC, isin) AS rk FROM pairs
          ) WHERE rk = 1 ORDER BY selic, maturity
        ) TO '{dest}' (FORMAT parquet)
    """


# -- one fund -------------------------------------------------------------


def asset_class(block: str, tp_aplic: str | None, tp_ativo: str | None = None) -> str:
    if tp_aplic == OFFSHORE_APLIC:
        return next((c for k, c in _OFFSHORE_CLASS if isinstance(tp_ativo, str) and k in tp_ativo), "other")
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


def _isin_in_text(text: object) -> str | None:
    """A valid ISIN written inside a name, e.g. ``GALAXY BBG BITC ETF - KYG376981000 - NAV``."""
    if not isinstance(text, str):
        return None
    return next((m for m in _ISIN_IN_TEXT_RE.findall(text.upper()) if is_valid_isin(m)), None)


def _date_text(d: object) -> str | None:
    return None if d is None or pd.isna(d) else str(d)[:10]


def fund_positions(
    positions: pd.DataFrame,
    net_assets: float,
    fund_isins: dict[str, str] | None = None,
    ticker_isins: dict[str, tuple[str, str | None]] | None = None,
    selic_isins: dict[tuple[str, str], str] | None = None,
    b3_isins: dict[str, tuple[str, str | None]] | None = None,
) -> pd.DataFrame:
    """One fund's CDA rows to :data:`~openbasket.sources.issuers.HOLDING_COLUMNS`.

    ISINs come, in order, from the filing itself (``reported``); for a
    government bond given only by SELIC code and maturity (index funds), from
    the ISIN ordinary funds report for that pair (``cda_selic``); from B3's
    register for a held fund's CNPJ when that CNPJ has a single active quota
    ISIN (``b3_fund_cnpj``); from the ISIN other funds reported for the same
    B3 code that month (``cda_ticker``); from B3's instruments file for that
    code (``b3_ticker``); or, for an offshore line, from an ISIN written in
    its name (``name_text``). A repo gets none: its ISIN is the collateral's.

    Extra columns kept for traceability: ``isin_source``, ``fund_cnpj`` (of a
    held fund), ``issuer_cnpj``, ``block``, ``tp_aplic``, ``tp_ativo``,
    ``maturity``, ``market_value_brl`` and ``confidential_until``.
    """
    fund_isins = fund_isins or {}
    ticker_isins = ticker_isins or {}
    selic_isins = selic_isins or {}
    b3_isins = b3_isins or {}
    p = positions.reset_index(drop=True)
    if "selic" not in p:
        p["selic"] = None
    aplic = p["tp_aplic"].fillna("")
    sign = aplic.map(lambda a: -1.0 if a in LIABILITY_APLIC else 1.0)
    weight = p["value"].astype(float) * sign / float(net_assets) * 100.0
    repo = aplic.str.startswith("Operações Compromissadas")
    offshore = aplic.eq(OFFSHORE_APLIC)
    maturity = p["maturity"].map(_date_text)

    reported = p["isin"].map(lambda v: v if isinstance(v, str) and is_valid_isin(v) else None)
    reported = reported.where(~repo, None)  # a repo is backed by the bond, not a holding of it
    via_selic = pd.Series(
        [None if r or s is None or pd.isna(s) or m is None else selic_isins.get((str(s), m))
         for r, s, m in zip(repo, p["selic"], maturity)], dtype=object,
    )
    held = p["held_fund_cnpj"].where(p["held_fund_cnpj"].fillna("") != "")
    # Index-fund filings give a held fund only as the issuer of a "Cotas" line.
    units = aplic.str.contains("Cotas") & p["issuer_cnpj"].fillna("").ne("")
    held = held.where(held.notna() | ~units, p["issuer_cnpj"])
    via_cnpj = held.map(fund_isins)
    # Some index-fund filings put the B3 code in the description instead of CD_ATIVO.
    desc_code = p["name"].map(lambda v: v.strip().upper() if isinstance(v, str) and _B3_CODE_RE.match(v.strip().upper()) else None)
    code = p["code"].where(p["block"].isin(["BLC_4", "BLC_8", "FIE"])).where(~offshore)
    code = code.where(code.notna(), desc_code.where(p["block"].isin(["BLC_4", "BLC_8", "FIE"]) & ~offshore))
    tick = code.map(lambda c: ticker_isins.get(c.upper()) if isinstance(c, str) else None)
    via_ticker = tick.map(lambda t: t[0] if t else None)
    b3 = code.map(lambda c: b3_isins.get(c.upper()) if isinstance(c, str) else None)
    via_b3 = b3.map(lambda t: t[0] if t else None)
    via_text = pd.Series([_isin_in_text(n) if o else None for n, o in zip(p["name"], offshore)], dtype=object)
    # Only B3 trading codes (ITUB4, BOVA11) and foreign exchange tickers are
    # tickers; other codes in these blocks are internal numbers.
    b3_code = code.map(lambda c: c.upper() if isinstance(c, str) and _B3_CODE_RE.match(c.upper()) else None)
    foreign = p["code"].where(offshore | (p["block"] == "BLC_7")).map(
        lambda c: c.strip().upper() if isinstance(c, str) and _FOREIGN_TICKER_RE.match(c.strip().upper()) else None
    )
    ticker = b3_code.where(b3_code.notna(), foreign).map(lambda v: v if isinstance(v, str) else None)

    isin = reported.astype(object)
    source = pd.Series(["reported" if pd.notna(v) else None for v in reported], dtype=object)
    for cand, label in ((via_selic, "cda_selic"), (via_cnpj, "b3_fund_cnpj"), (via_ticker, "cda_ticker"),
                        (via_b3, "b3_ticker"), (via_text, "name_text")):
        fill = isin.isna() & cand.notna()
        isin[fill] = cand[fill]
        source[fill] = label

    # A name that is only a number is CVM's internal issuer code: show the ticker or kind instead.
    name = p["name"].astype(object).map(lambda v: None if not isinstance(v, str) or v.strip().isdigit() else v)
    # A name that is just the B3 code gives way to the security's own name.
    name = name.mask(desc_code.notna() & (tick.notna() | b3.notna()))
    name = name.fillna(tick.map(lambda t: t[1] if t else None)).fillna(b3.map(lambda t: t[1] if t else None))
    name = name.fillna(ticker).fillna(p["code"]).fillna(p["tp_ativo"]).fillna(aplic)
    bond = p["selic"].notna() & ~repo & p["name"].isna()
    name = name.where(~bond, "Título público SELIC " + p["selic"].fillna("") + " " + maturity.fillna(""))
    repo_name = "Repo (operação compromissada) backed by " + p["name"].fillna("") + " " + p["isin"].fillna("")
    name = name.where(~repo, repo_name)
    country = p["country"].map(lambda c: c.strip().upper() if isinstance(c, str) and c.strip().isalpha() and len(c.strip()) == 2 else None)
    out = pd.DataFrame(
        {
            "isin": isin,
            "cusip": None,
            "sedol": None,
            "ticker": ticker,
            "name": name.astype(str).str.strip(),
            "weight_pct": weight,
            "asset_class": [asset_class(b, a, t) for b, a, t in zip(p["block"], aplic, p["tp_ativo"])],
            "currency": [None if b == "BLC_7" or o else "BRL" for b, o in zip(p["block"], offshore)],
            "country": country,
            "isin_source": source,
            "fund_cnpj": held,
            "issuer_cnpj": p["issuer_cnpj"],
            "block": p["block"],
            "tp_aplic": p["tp_aplic"],
            "tp_ativo": p["tp_ativo"],
            "maturity": maturity,
            "market_value_brl": p["value"],
            "confidential_until": p["confidential_until"].map(_date_text),
        }
    )
    liab = aplic.isin(LIABILITY_APLIC)
    out.loc[liab & ~out["asset_class"].str.startswith("derivative"), "asset_class"] = "other"
    out.loc[out["block"] == "CONFID", "asset_class"] = "other"
    return _nulls_to_none(out)


def replication(positions: pd.DataFrame, net_assets: float, equity_etf: bool) -> str | None:
    """``physical`` or ``swap`` (synthetic: futures or swaps) for an ETF; ``None`` when unclear.

    A filing that does not add up to within 10% of its net assets is too
    unreliable to tell. An equity, foreign-index or crypto ETF whose equities
    and fund units are less than half its net assets, but which holds futures
    or swaps, gets its exposure from derivatives over a collateral of bonds or
    repos: ``swap``. Anything else that adds up is ``physical``.
    """
    if not net_assets or positions.empty:
        return None
    aplic = positions["tp_aplic"].fillna("")
    sign = aplic.map(lambda a: -1.0 if a in LIABILITY_APLIC else 1.0)
    value = positions["value"].astype(float).fillna(0.0)
    if not 90 <= (value * sign).sum() / net_assets * 100 <= 110:
        return None
    if equity_etf:
        cls = [asset_class(b, a, t) for b, a, t in zip(positions["block"], aplic, positions["tp_ativo"])]
        physical = value[[c in _PHYSICAL_CLASSES for c in cls]].sum() / net_assets * 100
        if physical < 50 and aplic.map(lambda a: a.startswith(_SYNTHETIC_PREFIXES)).any():
            return "swap"
    return "physical"


# -- local store ----------------------------------------------------------


class CdaStore:
    """Downloaded CDA months and the B3 files, converted to Parquet.

    ``refresh`` downloads the newest ``months`` ZIPs (only those CVM has
    regenerated since the last run), the ISIN register and the instruments
    file, and converts them. Lookups then read Parquet only.
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
        """Bring the newest ``months``, the ISIN register and the instruments file up to date; returns a manifest."""
        listing = list_months()
        wanted = sorted(listing)[-self.months:]
        rows = []
        self.parquet.mkdir(parents=True, exist_ok=True)
        for month in wanted:
            zip_path = download_month(month, self.raw, listing[month])
            stamp = self.parquet / f"positions_{month}.source"
            if not stamp.exists() or stamp.read_text() != zip_path.name or not (self.parquet / f"selic_{month}.parquet").exists():
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
        self._convert(reg, "b3_fund_isins", lambda p: parse_isin_register(p.read_bytes()))
        try:
            ins = download_instruments(self.raw)
            self._convert(ins, "b3_instruments", lambda p: parse_instruments(p.read_bytes()))
        except Exception as exc:  # tickers are a nice-to-have; holdings do not need them
            logger.warning("B3 instruments file unavailable, tickers left empty: %s", exc)
        self._cache.clear()
        return pd.DataFrame(rows)

    def _convert(self, raw: Path, name: str, parse) -> None:
        out, stamp = self.parquet / f"{name}.parquet", self.parquet / f"{name}.source"
        if not out.exists() or not stamp.exists() or stamp.read_text() != raw.name:
            parse(raw).to_parquet(out, index=False)
            stamp.write_text(raw.name)

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

    def instruments(self) -> pd.DataFrame:
        """B3's instruments file (``ticker, isin, segment, category, name``); empty if never downloaded."""
        if "instruments" not in self._cache:
            path = self.parquet / "b3_instruments.parquet"
            cols = ["ticker", "isin", "segment", "category", "name"]
            self._cache["instruments"] = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=cols)
        return self._cache["instruments"]

    def b3_ticker_isins(self) -> dict[str, tuple[str, str | None]]:
        if "b3_tickers" not in self._cache:
            t = self.instruments()
            self._cache["b3_tickers"] = {c: (i, n if isinstance(n, str) else None) for c, i, n in zip(t["ticker"], t["isin"], t["name"])}
        return self._cache["b3_tickers"]

    def listings(self) -> pd.DataFrame:
        """One B3 listing per fund ISIN: its trading code and category (the cash-market row first)."""
        t = self.instruments()
        t = t[t["isin"].str[6:9].eq("CTF")] if len(t) else t
        if not len(t):
            return pd.DataFrame(columns=["ticker", "category"], index=pd.Index([], name="isin", dtype=object))
        return t.drop_duplicates("isin").set_index("isin")[["ticker", "category"]]

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

    def _replications(self, etfs: pd.DataFrame) -> dict[str, str | None]:
        """Replication of listed index funds, from their newest stored filing."""
        out: dict[str, str | None] = {}
        for month, g in etfs.groupby("month"):
            path = self.parquet / f"positions_{month}.parquet"
            cnpjs = sorted(set(g["cnpj"]))
            rows = self.con.execute(
                f"SELECT fund_cnpj, block, tp_aplic, tp_ativo, value FROM '{path}' WHERE fund_cnpj IN ({', '.join('?' * len(cnpjs))})",
                cnpjs,
            ).df()
            by = dict(tuple(rows.groupby("fund_cnpj")))
            for r in g.itertuples(index=False):
                lines = by.get(r.cnpj)
                out[r.cnpj] = replication(lines, r.net_assets, r.equity_etf) if lines is not None else None
        return out

    def products(self) -> pd.DataFrame:
        """Active fund-quota ISINs of funds that filed a CDA in a stored month.

        Listed funds get their B3 trading code and category; an index fund
        (ETF) also gets its replication read from the filing.
        """
        months = self.available_months()
        if not months:
            return pd.DataFrame()
        nav = self.navs().sort_values("month").drop_duplicates("fund_cnpj", keep="last")
        f = self.fund_isins()
        f = f[f["active"]].merge(nav, left_on="cnpj", right_on="fund_cnpj", how="inner").drop_duplicates("isin")
        listed = self.listings()
        f = f.join(listed, on="isin")
        category = f["category"].fillna("")
        name = f["fund_name"].fillna("").str.upper()
        f["equity_etf"] = category.str.startswith("ETF")
        etf = f["equity_etf"] | (f["ticker"].notna() & name.str.contains("ÍNDICE|INDICE", regex=True))
        rep = self._replications(f[etf]) if etf.any() else {}
        return pd.DataFrame(
            {
                "issuer": "cvm_brazil",
                "site": "br",
                "portfolio_id": f["cnpj"],
                "isin": f["isin"],
                "ticker": f["ticker"],
                "fund_name": f["fund_name"],
                "domicile": "BR",
                "currency": "BRL",
                "asset_class": f["category"].fillna(f["fund_type"]),
                "replication": [rep.get(c) if e else None for c, e in zip(f["cnpj"], etf)],
                "product_page_url": CDA_DATASET_PAGE,
                "holdings_url": f["month"].map(lambda m: CDA_URL.format(month=m)),
                "share_class": f["description"],
                "fund_type": f["fund_type"],
                "net_assets_brl": f["net_assets"],
                "cda_month": f["month"],
            }
        ).reset_index(drop=True)

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

    def _selic(self, month: str) -> dict[tuple[str, str], str]:
        key = ("selic", month)
        if key not in self._cache:
            path = self.parquet / f"selic_{month}.parquet"
            t = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=["selic", "maturity", "isin"])
            self._cache[key] = {(str(s), _date_text(m)): i for s, m, i in zip(t["selic"], t["maturity"], t["isin"])}
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
        frame = fund_positions(
            self._rows(month, cnpj), net_assets, self.unique_fund_isin(), self._tickers(month), self._selic(month),
            self.b3_ticker_isins(),
        )
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
        total = frame["weight_pct"].sum()
        if abs(total - 100) > 5:
            notes.append(f"lines add up to {total:.1f}% of the net assets the fund reported")
        return as_of, month, frame, notes
