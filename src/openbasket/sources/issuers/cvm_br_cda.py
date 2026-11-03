"""Brazilian funds from the CVM's monthly portfolio filings (CDA).

The CVM (Comissão de Valores Mobiliários) publishes every Brazilian fund's
month-end portfolio, the *Composição e Diversificação das Aplicações*, as open
data. This adapter covers the funds listed on B3 that have an ISIN: ETFs
(equity, fixed income, real-estate and crypto), listed infrastructure
(FI-Infra), private-equity (FIP) and credit-receivable (FIDC) funds.

Fund list
    B3's listed-funds directory gives each listed fund's CNPJ (the Brazilian
    company number, which is the CVM's fund key) and trading code:
    ``fundsListedProxy/Search/GetListFunds`` per fund type, then
    ``GetDetailFund`` per fund. B3's daily instruments file
    (``InstrumentsConsolidatedFile``) maps the trading code to the ISIN.
Holdings
    ``https://dados.cvm.gov.br/dados/FI/DOC/CDA/DADOS/cda_fi_{YYYYMM}.zip``,
    one ZIP per month with ``;``-separated latin-1 CSVs: ``cda_fi_BLC_1`` to
    ``BLC_8`` (one per asset block) and ``cda_fi_PL`` (net assets) for
    ordinary funds, ``cda_fie`` for ETFs and FIDCs (net assets in every row),
    and ``*_CONFID`` for positions whose details are withheld for now.

Weights are derived: ``VL_MERC_POS_FINAL / VL_PATRIM_LIQ * 100``, i.e. the
reported market value as a percent of the reported net assets, with liability
lines (payables, borrowed securities, written options) counted as negative.
Nothing is rescaled.

``dados.cvm.gov.br`` asks for 10 seconds between requests (``Crawl-Delay: 10``)
and disallows ``/api/``; only files under ``/dados/`` are read.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import zipfile
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd

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

CVM_HOST = "dados.cvm.gov.br"
CDA_DIR = f"https://{CVM_HOST}/dados/FI/DOC/CDA/DADOS/"
CDA_ZIP = CDA_DIR + "cda_fi_{ym}.zip"
DATASET_PAGE = f"https://{CVM_HOST}/dataset/fi-doc-cda"

B3_FUNDS_API = "https://sistemaswebb3-listados.b3.com.br/fundsListedProxy/Search/"
B3_FUND_PAGE = "https://sistemaswebb3-listados.b3.com.br/fundsListedPage/funds-main/{id}/{acronym}/{type}/funds-details"
B3_FILES_API = "https://arquivos.b3.com.br/api/download/"

#: B3 listed-fund types read, with the asset-class label used for products.
B3_FUND_TYPES = {
    "ETF": "Equity",
    "ETF-RF": "Fixed Income",
    "ETF-FII": "Real Estate",
    "ETF-CRIPTO": "Crypto",
    "FI-INFRA": "Fixed Income",
    "FIP": "Private Equity",
    "FIDC": "Credit Receivables",
}

#: Liability lines: reported as positive values, they reduce net assets.
#: Checked on the June to September 2026 files: with these negated, 97.5% to
#: 97.9% of the about 26,000 fund classes with net assets over BRL 1m add up
#: to within 0.5 points of 100% (95% to 97% of the ETFs).
#: "Obrigações por venda a termo a entregar" is left positive: the bond ETFs
#: that sell forward (3PRE11, 03BK11, PACB11) only add up to 100% that way.
#: Futures lines are left as reported: their value is the daily margin
#: adjustment and already carries its sign.
NEGATIVE_APLIC = frozenset(
    {
        "Valores a pagar",
        "DIFERENCIAL DE SWAP A PAGAR",
        "Obrigações por ações e outros TVM recebidos em empréstimo",
        "Obrigações por compra a termo a pagar",
        "Opções - Posições lançadas",
        "Outras operações passivas e exigibilidades",
    }
)

#: ``TP_APLIC`` (asset block) to the normalised asset classes.
ASSET_CLASS = {
    "Ações": "equity",
    "Ações e outros TVM cedidos em empréstimo": "equity",
    "Obrigações por ações e outros TVM recebidos em empréstimo": "equity",
    "Brazilian Depository Receipt - BDR": "equity",
    "Certificado ou recibo de depósito de valores mobiliários": "equity",
    "Títulos Públicos": "debt",
    "Debêntures": "debt",
    "Títulos de Crédito Privado": "debt",
    "Títulos ligados ao agronegócio": "debt",
    "Depósitos a prazo e outros títulos de IF": "debt",
    "Operações Compromissadas": "cash",
    "Disponibilidades": "cash",
    "Valores a receber": "cash",
    "Valores a pagar": "cash",
    "Cotas de Fundos": "fund",
    "Cotas de fundos de investimento - Instrução Nº 409": "fund",
    "Cotas de fundos de renda variável": "fund",
    "Mercado Futuro - Posições compradas": "derivative (futures)",
    "Mercado Futuro - Posições vendidas": "derivative (futures)",
    "Opções - Posições titulares": "derivative (options)",
    "Opções - Posições lançadas": "derivative (options)",
    "DIFERENCIAL DE SWAP A RECEBER": "derivative (swap)",
    "DIFERENCIAL DE SWAP A PAGAR": "derivative (swap)",
    "Compras a termo a receber": "derivative (forward)",
    "Vendas a termo a receber": "derivative (forward)",
    "Obrigações por compra a termo a pagar": "derivative (forward)",
    "Obrigações por venda a termo a entregar": "derivative (forward)",
}

#: Lines that give an ETF its index exposure synthetically.
_DERIVATIVE_APLIC = frozenset(a for a, c in ASSET_CLASS.items() if c.startswith("derivative (futures") or c.startswith("derivative (swap"))
_PHYSICAL_EQUITY = frozenset({"equity", "fund", "offshore"})

#: Columns kept from every CDA CSV, renamed. Missing ones are left empty.
_COLS = {
    "TP_FUNDO_CLASSE": "fund_type",
    "CNPJ_FUNDO_CLASSE": "cnpj",
    "DENOM_SOCIAL": "fund_name",
    "DT_COMPTC": "as_of",
    "VL_PATRIM_LIQ": "pl",
    "TP_APLIC": "tp_aplic",
    "TP_ATIVO": "tp_ativo",
    "VL_MERC_POS_FINAL": "value",
    "QT_POS_FINAL": "quantity",
    "DT_CONFID_APLIC": "confidential_until",
    "CD_ISIN": "cd_isin",
    "CD_SELIC": "cd_selic",
    "DT_VENC": "maturity",
    "TP_TITPUB": "tp_titpub",
    "CD_ATIVO": "cd_ativo",
    "DS_ATIVO": "ds_ativo",
    "CNPJ_FUNDO_CLASSE_COTA": "target_cnpj",
    "NM_FUNDO_CLASSE_SUBCLASSE_COTA": "target_name",
    "CNPJ_EMISSOR": "issuer_cnpj",
    "CPF_CNPJ_EMISSOR": "issuer_cnpj2",
    "EMISSOR": "issuer",
    "CD_SWAP": "cd_swap",
    "DS_SWAP": "ds_swap",
    "CD_PAIS": "cd_pais",
    "CD_ATIVO_BV_MERC": "cd_ativo_bv",
    "DS_ATIVO_EXTERIOR": "ds_ativo_ext",
}
#: Extra holding columns: the CNPJ of a held fund or of the issuer, the
#: signed market value in BRL, the local code, and the CVM asset block and file.
EXTRA_HOLDING_COLUMNS = ["cnpj", "value_brl", "code", "tp_aplic", "block"]
_LINE_COLS = [c for c in _COLS.values() if c not in ("fund_type", "fund_name", "pl")] + ["block"]
_ZIP_RE = re.compile(r'href="(cda_fi_(\d{6})\.zip)"[^\n]*?(\d{2}-[A-Za-z]{3}-\d{4})')
_OFFSHORE_CLASS = (("Fundo", "fund"), ("Aç", "equity"), ("BDR", "equity"), ("Título", "debt"), ("Bond", "debt"), ("Deriv", "derivative"))

http.set_min_interval(CVM_HOST, 10.0)

#: In-process memory of the B3 directory (CNPJ -> fund ISIN), filled by ``products``.
_FUND_ISIN_BY_CNPJ: dict[str, str] = {}


def _digits(v: object) -> str | None:
    if not isinstance(v, str):
        return None
    d = re.sub(r"\D", "", v)
    return d.zfill(14) if d else None


def _clean(v: object) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


# --------------------------------------------------------------------------
# CVM monthly files
# --------------------------------------------------------------------------


def parse_directory(html: str) -> list[tuple[str, str]]:
    """``[(YYYYMM, last-modified ISO date), ...]`` from the CDA directory, newest first."""
    out = {}
    for _, ym, mod in _ZIP_RE.findall(html):
        out[ym] = pd.to_datetime(mod, format="%d-%b-%Y").date().isoformat()
    return sorted(out.items(), reverse=True)


@lru_cache(maxsize=1)
def available_months() -> tuple[tuple[str, str], ...]:
    r = http.get(CDA_DIR)
    r.raise_for_status()
    months = parse_directory(r.text)
    if not months:
        raise ValueError(f"No cda_fi_YYYYMM.zip files listed at {CDA_DIR}")
    return tuple(months)


def _read_csv(z: zipfile.ZipFile, name: str) -> pd.DataFrame:
    with z.open(name) as f:
        head = f.readline().decode("latin-1").strip().split(";")
    use = [c for c in head if c in _COLS]
    with z.open(name) as f:
        df = pd.read_csv(f, sep=";", encoding="latin-1", dtype=str, usecols=use, keep_default_na=False, na_values=[""])
    return df.rename(columns=_COLS)


def parse_month(content: bytes) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One monthly ZIP to ``(lines, funds)``.

    ``lines`` has one row per reported position (``_LINE_COLS``, ``value`` as
    a float in BRL, ``block`` naming the CSV it came from). ``funds`` has one
    row per fund class with its ``cnpj`` (14 digits), name, type, ``as_of`` and
    ``pl`` (net assets in BRL).
    """
    z = zipfile.ZipFile(io.BytesIO(content))
    lines, funds = [], []
    for name in z.namelist():
        m = re.match(r"cda_(fie?)_(BLC_\d|PL|CONFID)?_?(\d{6})\.csv$", name)
        if not m:
            continue
        kind, part = m.group(1), m.group(2)
        df = _read_csv(z, name)
        df["cnpj"] = df["cnpj"].map(_digits)
        if kind == "fi" and part == "PL":
            funds.append(df[["cnpj", "fund_name", "fund_type", "as_of", "pl"]])
            continue
        if kind == "fie" and part is None:
            funds.append(df[["cnpj", "fund_name", "fund_type", "as_of", "pl"]].drop_duplicates("cnpj"))
        df["block"] = f"{kind}_{part}" if part else kind
        lines.append(df.reindex(columns=_LINE_COLS))
    if not funds or not lines:
        raise ValueError("CDA ZIP has no PL or position files")
    lines = pd.concat(lines, ignore_index=True)
    lines["value"] = pd.to_numeric(lines["value"], errors="coerce")
    lines["quantity"] = pd.to_numeric(lines["quantity"], errors="coerce")
    for c in ("target_cnpj", "issuer_cnpj", "issuer_cnpj2"):
        lines[c] = lines[c].map(_digits)
    funds = pd.concat(funds, ignore_index=True).drop_duplicates("cnpj")
    funds["pl"] = pd.to_numeric(funds["pl"], errors="coerce")
    return lines, funds.reset_index(drop=True)


class Month:
    """One month of CDA filings with the lookups built from it."""

    def __init__(self, ym: str, url: str, lines: pd.DataFrame, funds: pd.DataFrame):
        self.ym, self.url = ym, url
        self.lines = lines
        self.funds = funds.set_index("cnpj")
        self._by_cnpj = lines.groupby("cnpj").indices
        # Government bonds in the ETF file carry only the SELIC code and the
        # maturity; ordinary funds' government-bond block gives the ISIN too.
        gov = lines.loc[lines["cd_isin"].notna() & lines["cd_selic"].notna(), ["cd_selic", "maturity", "cd_isin"]]
        gov = gov[gov["cd_isin"].map(is_valid_isin)]
        self.isin_by_selic = gov.groupby(["cd_selic", "maturity"])["cd_isin"].agg(lambda s: s.mode().iat[0]).to_dict()
        # Equity and debenture codes with the ISIN ordinary funds report for them.
        code = lines.loc[lines["cd_isin"].notna() & lines["cd_ativo"].notna(), ["cd_ativo", "cd_isin"]].drop_duplicates()
        code = code[code["cd_isin"].map(is_valid_isin)]
        uniq = code.groupby("cd_ativo")["cd_isin"].nunique()
        self.isin_by_code = code[code["cd_ativo"].isin(uniq.index[uniq == 1])].set_index("cd_ativo")["cd_isin"].to_dict()

    def fund_lines(self, cnpj: str) -> pd.DataFrame:
        idx = self._by_cnpj.get(cnpj)
        return self.lines.iloc[idx] if idx is not None else self.lines.iloc[0:0]


_MONTHS: dict[str, Month] = {}


def load_month(ym: str, modified: str | None = None, cache_dir: str | Path | None = None) -> Month:
    """Download (or read from cache) and parse one monthly ZIP."""
    key = f"{ym}_{modified or 'latest'}"
    if key in _MONTHS:
        return _MONTHS[key]
    url = CDA_ZIP.format(ym=ym)
    cache = Path(cache_dir) / f"cvm_cda_{key}" if cache_dir else None
    if cache is not None and (cache / "lines.parquet").exists():
        lines = pd.read_parquet(cache / "lines.parquet")
        funds = pd.read_parquet(cache / "funds.parquet")
    else:
        logger.info("Fetching CVM CDA %s", url)
        r = http.get(url, timeout=300)
        r.raise_for_status()
        lines, funds = parse_month(r.content)
        if cache is not None:
            cache.mkdir(parents=True, exist_ok=True)
            lines.to_parquet(cache / "lines.parquet", index=False)
            funds.to_parquet(cache / "funds.parquet", index=False)
    month = Month(ym, url, lines, funds)
    _MONTHS[key] = month
    return month


# --------------------------------------------------------------------------
# B3 directory and instruments
# --------------------------------------------------------------------------


def _b3(method: str, payload: dict) -> dict | None:
    token = base64.b64encode(json.dumps(payload).encode()).decode()
    r = http.get(B3_FUNDS_API + method + "/" + token, timeout=60)
    r.raise_for_status()
    return r.json() if r.content else None


def b3_listed_funds(types: tuple[str, ...] = tuple(B3_FUND_TYPES)) -> list[dict]:
    """Every listed fund of the given B3 types, with its detail record (CNPJ, trading code)."""
    out = []
    for t in types:
        page = 1
        while True:
            j = _b3("GetListFunds", {"language": "pt-br", "typeFund": t, "pageNumber": page, "pageSize": 120, "keyword": ""})
            out += [{"type": t, **x} for x in (j or {}).get("results", [])]
            if page >= ((j or {}).get("page", {}).get("totalPages") or 0):
                break
            page += 1
    for x in out:
        try:
            x["detail"] = _b3("GetDetailFund", {"language": "pt-br", "idFNET": str(x["id"]), "idCEM": x["acronym"], "typeFund": x["type"]})
        except Exception as e:  # one bad record should not lose the list
            logger.warning("B3 detail for %s failed: %s", x.get("acronym"), e)
            x["detail"] = None
    return out


def parse_instruments(content: bytes) -> pd.DataFrame:
    """B3 instruments file to ``ticker, isin, segment, category, name``.

    Equity-market rows come first when a ticker appears twice. Fixed-income
    ETFs such as ``IMAB11`` are listed in the ``FORWARD`` segment, so no
    segment is dropped except the ETF primary market (creations/redemptions).
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


@lru_cache(maxsize=1)
def b3_instruments() -> pd.DataFrame:
    """The newest B3 instruments file of the last week (ticker to ISIN)."""
    day = date.today()
    for _ in range(7):
        r = http.get(B3_FILES_API + "requestname", params={"fileName": "InstrumentsConsolidatedFile", "date": day.isoformat()}, timeout=60)
        if r.ok and r.content and r.json().get("token"):
            f = http.get(B3_FILES_API, params={"token": r.json()["token"]}, timeout=300)
            if f.ok and len(f.content) > 1000:
                return parse_instruments(f.content)
        day -= timedelta(days=1)
    raise ValueError("No B3 instruments file in the last 7 days")


# --------------------------------------------------------------------------
# Products
# --------------------------------------------------------------------------


def replication(lines: pd.DataFrame, pl: float, b3_type: str) -> str | None:
    """``physical`` or ``swap`` (synthetic: futures or swaps) for an ETF; ``None`` otherwise.

    An equity or crypto ETF whose holdings are less than half equities,
    offshore funds or fund units, but which holds futures or swaps, gets its
    index exposure from derivatives over a collateral of bonds or repos.
    """
    if not b3_type.startswith("ETF") or not pl or lines.empty:
        return None
    sign = lines["tp_aplic"].isin(NEGATIVE_APLIC).map({True: -1.0, False: 1.0})
    if not 90 <= (lines["value"] * sign).sum() / pl * 100 <= 110:
        return None  # the filing does not add up to its net assets: too unreliable to tell
    if b3_type in ("ETF", "ETF-CRIPTO"):
        cls = lines["tp_aplic"].map(ASSET_CLASS).fillna("other")
        cls = cls.where(lines["tp_aplic"] != "Investimento no Exterior", "offshore")
        physical = lines.loc[cls.isin(_PHYSICAL_EQUITY), "value"].sum() / pl * 100
        if physical < 50 and lines["tp_aplic"].isin(_DERIVATIVE_APLIC).any():
            return "swap"
    return "physical"


def build_products(listed: list[dict], instruments: pd.DataFrame, months: list[Month]) -> pd.DataFrame:
    """Join B3 listed funds (CNPJ, code) to ISINs and to their latest CDA filing."""
    isin_by_ticker = instruments.set_index("ticker")["isin"].to_dict()
    rows = []
    for x in listed:
        d = x.get("detail") or {}
        cnpj = _digits(d.get("cnpj"))
        if not cnpj:
            continue
        codes = [c.strip() for c in re.split(r"[,; ]+", d.get("tradingCode") or "") if c.strip()]
        codes += [f"{x['acronym']}11"]
        ticker = next((c for c in codes if c in isin_by_ticker), None)
        if ticker is None:
            continue
        month = next((m for m in months if cnpj in m.funds.index), None)
        if month is None:
            continue
        pl = month.funds.at[cnpj, "pl"]
        rows.append(
            {
                "issuer": "cvm",
                "site": "br",
                "portfolio_id": cnpj,
                "isin": isin_by_ticker[ticker],
                "ticker": ticker,
                "fund_name": d.get("fundName") or x.get("fundName") or month.funds.at[cnpj, "fund_name"],
                "domicile": "BR",
                "currency": "BRL",
                "asset_class": B3_FUND_TYPES.get(x["type"]),
                "replication": replication(month.fund_lines(cnpj), pl, x["type"]),
                "product_page_url": B3_FUND_PAGE.format(id=x["id"], acronym=x["acronym"], type=x["type"]),
                "holdings_url": month.url,
                "cnpj": f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}",
                "b3_type": x["type"],
                "cda_month": month.ym,
                "net_assets_brl": pl,
                "manager": d.get("positionManager"),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=PRODUCT_COLUMNS)
    df = df.drop_duplicates("isin").reset_index(drop=True)
    return df[PRODUCT_COLUMNS + [c for c in df.columns if c not in PRODUCT_COLUMNS]]


def products(cache_dir: str | Path | None = None, months_back: int = 4) -> pd.DataFrame:
    """Every B3-listed fund with an ISIN and a CDA filing in the last ``months_back`` months.

    Four months, because a fund may ask for its whole portfolio to be kept
    confidential for a while; it then has no net assets in the newest files
    and its holdings come from the newest month that discloses them.
    """
    listed = None
    cache = Path(cache_dir) / f"cvm_b3_funds_{date.today().isoformat()}.json" if cache_dir else None
    if cache is not None and cache.exists():
        listed = json.loads(cache.read_text())
    if listed is None:
        listed = b3_listed_funds()
        if cache is not None and any(x.get("detail") for x in listed):
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(listed, ensure_ascii=False))
    months = [load_month(ym, mod, cache_dir) for ym, mod in available_months()[:months_back]]
    df = build_products(listed, b3_instruments(), months)
    _FUND_ISIN_BY_CNPJ.update(dict(zip(df["portfolio_id"], df["isin"])))
    return df


# --------------------------------------------------------------------------
# Holdings
# --------------------------------------------------------------------------


def _name(r) -> str | None:
    if r.block in ("fi_CONFID", "fie_CONFID"):
        return f"Confidential position ({r.tp_aplic}), withheld until {r.confidential_until}"
    if r.tp_titpub and r.maturity:
        return f"{r.tp_titpub} {r.maturity}"
    if r.tp_aplic == "Títulos Públicos" and r.cd_selic:
        return f"Título público SELIC {r.cd_selic} {r.maturity or ''}".strip()
    if r.target_name:
        return r.target_name
    if r.block == "fi_BLC_3":
        return " ".join(v for v in (r.tp_aplic, r.ds_swap, r.cd_swap) if v)
    for v in (r.ds_ativo_ext, r.issuer, r.ds_ativo, r.tp_ativo):
        if v and v not in ("Outros",):
            return v
    return r.tp_aplic


def _fund_isins(cache_dir: str | Path | None, instruments: pd.DataFrame | None) -> dict[str, str]:
    """CNPJ to ISIN of listed funds: from ``products`` in this process, else its cached B3 directory."""
    if _FUND_ISIN_BY_CNPJ or not cache_dir or instruments is None:
        return _FUND_ISIN_BY_CNPJ
    files = sorted(Path(cache_dir).glob("cvm_b3_funds_*.json"))
    if files:
        isin_by_ticker = instruments.set_index("ticker")["isin"].to_dict()
        for x in json.loads(files[-1].read_text()):
            d = x.get("detail") or {}
            cnpj, isin = _digits(d.get("cnpj")), isin_by_ticker.get(d.get("tradingCode") or f"{x['acronym']}11")
            if cnpj and isin:
                _FUND_ISIN_BY_CNPJ[cnpj] = isin
    return _FUND_ISIN_BY_CNPJ


def build_holdings(lines: pd.DataFrame, pl: float, month: Month, instruments: pd.DataFrame | None = None,
                   fund_isins: dict[str, str] | None = None) -> pd.DataFrame:
    """CDA lines of one fund to :data:`HOLDING_COLUMNS` plus :data:`EXTRA_HOLDING_COLUMNS`.

    The ISIN is, in order: the one the filing gives (government bonds,
    equities and debentures in ordinary funds' blocks); for a government bond
    with only a SELIC code and maturity (the ETF file), the ISIN other funds
    report for that pair; for a code, the B3 instruments file's ISIN for that
    ticker, then the ISIN other funds report next to that code; for fund
    units, the listed fund's ISIN found by its CNPJ.
    """
    if instruments is not None:
        isin_by_ticker = instruments.set_index("ticker")["isin"].to_dict()
        name_by_ticker = instruments.dropna(subset=["name"]).set_index("ticker")["name"].to_dict()
    else:
        isin_by_ticker, name_by_ticker = {}, {}
    fund_isins = fund_isins or {}
    out = []
    for r in lines.fillna("").itertuples(index=False):
        r = r._replace(**{k: (v or None) for k, v in r._asdict().items() if isinstance(v, str)})
        offshore = r.tp_aplic == "Investimento no Exterior"
        repo = r.tp_aplic == "Operações Compromissadas"
        cls = ASSET_CLASS.get(r.tp_aplic, "other")
        if offshore:
            cls = next((c for k, c in _OFFSHORE_CLASS if r.tp_ativo and k in r.tp_ativo), "other")
        code = r.cd_ativo
        if code is None and r.ds_ativo and (r.ds_ativo in isin_by_ticker or r.ds_ativo in month.isin_by_code):
            code = r.ds_ativo  # some ETF filings put the ticker in the description
        listed = bool(code) and code in isin_by_ticker
        isin = r.cd_isin if is_valid_isin(r.cd_isin) else None
        if isin is None and r.cd_selic and r.maturity and not repo:
            # A repo's SELIC code is its collateral, not what the fund holds.
            isin = month.isin_by_selic.get((r.cd_selic, r.maturity))
        if isin is None and code and not offshore:
            isin = isin_by_ticker.get(code) or month.isin_by_code.get(code)
        target = r.target_cnpj or (r.issuer_cnpj2 if r.tp_aplic and "Cotas" in r.tp_aplic else None)
        if isin is None and target:
            isin = fund_isins.get(target)
        value = float(r.value) if r.value not in (None, "") else float("nan")
        sign = -1.0 if r.tp_aplic in NEGATIVE_APLIC else 1.0
        equity_code = cls == "equity" and bool(code) and code.isalnum() and len(code) <= 7
        name = _name(r)
        if listed and name in (r.tp_ativo, r.ds_ativo, r.tp_aplic, None):
            name = name_by_ticker.get(code, name)
        if repo:
            name = f"Repo (Operações Compromissadas), collateral SELIC {r.cd_selic or '?'} {r.maturity or ''}".strip()
        country = r.cd_pais if offshore and r.cd_pais and r.cd_pais.isalpha() else None
        out.append(
            {
                "isin": isin if is_valid_isin(isin) else None,
                "cusip": None,
                "sedol": None,
                "ticker": code if (listed or equity_code) and not offshore else (r.cd_ativo_bv if offshore else None),
                "name": name,
                "weight_pct": sign * value / pl * 100 if pl else float("nan"),
                "asset_class": cls,
                "currency": None if offshore else "BRL",
                "country": isin[:2] if isin else country,
                "cnpj": target or r.issuer_cnpj or r.issuer_cnpj2,
                "value_brl": sign * value,
                "code": code,
                "tp_aplic": r.tp_aplic,
                "block": r.block,
            }
        )
    df = pd.DataFrame(out, columns=HOLDING_COLUMNS + EXTRA_HOLDING_COLUMNS)
    df["weight_pct"] = pd.to_numeric(df["weight_pct"], errors="coerce").astype(float)
    return df


def holdings(product: dict, ctx: FetchContext | None = None, max_confidential_pct: float = 5.0, lookback: int = 4) -> IssuerHoldings:
    """The latest CDA portfolio of one fund.

    Months are tried newest first. If more than ``max_confidential_pct`` of
    net assets sits in positions still withheld as confidential, up to
    ``lookback`` older months are tried for one that is more complete (the
    CVM republishes them once the confidentiality lapses).
    """
    ctx = ctx or FetchContext()
    cnpj = _digits(str(product["portfolio_id"]))
    first = None
    notes = []
    for ym, mod in available_months()[:lookback]:
        month = load_month(ym, mod, ctx.cache_dir)
        if cnpj not in month.funds.index:
            if len(month.fund_lines(cnpj)):
                # Filed, but every position (and the net assets) withheld for now.
                notes.append(f"{ym}: whole portfolio confidential, older month used")
            continue
        lines = month.fund_lines(cnpj)
        pl = float(month.funds.at[cnpj, "pl"])
        conf = lines.loc[lines["block"].str.endswith("CONFID"), "value"].sum() / pl * 100 if pl else 0.0
        if first is None:
            first = (month, lines, pl, conf)
        if conf <= max_confidential_pct:
            chosen = (month, lines, pl, conf)
            break
        notes.append(f"{ym}: {conf:.1f}% of net assets confidential")
    else:
        chosen = first
    if chosen is None:
        raise LookupError(f"CNPJ {cnpj} has no CDA filing in the last {lookback} months")
    month, lines, pl, conf = chosen
    if month is not first[0]:
        notes.append(f"used {month.ym}, the newest month with at most {max_confidential_pct}% confidential")
    try:
        instruments = b3_instruments()
    except Exception as e:
        logger.warning("B3 instruments file unavailable: %s", e)
        instruments = None
    df = build_holdings(lines, pl, month, instruments, _fund_isins(ctx.cache_dir, instruments))
    as_of = _clean(month.funds.at[cnpj, "as_of"])
    if conf:
        notes.append(f"{conf:.2f}% of net assets in confidential positions")
    total = df["weight_pct"].sum()
    if abs(total - 100) > 5:
        notes.append(f"lines add up to {total:.1f}% of the net assets the fund reported")
    return IssuerHoldings(as_of, df, month.url, notes)


register(
    IssuerAdapter(
        name="cvm",
        title="CVM fund portfolios (CDA), Brazil",
        homepage=DATASET_PAGE,
        credit="Source: CVM - Comissão de Valores Mobiliários, Dados Abertos (CDA); fund ISINs from B3",
        licence=(
            "CVM open data under the Open Data Commons Open Database License (ODbL), as stated on the "
            "dataset page; credit the CVM and share derived databases alike. Fund CNPJs and ISINs from "
            "B3's public listed-funds pages and instruments file, whose terms have not been reviewed."
        ),
        regions=("BR",),
        products=products,
        holdings=holdings,
        frequency="monthly",
    )
)
