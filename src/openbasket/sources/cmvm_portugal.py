"""Portugal: CMVM quarterly security-by-security portfolios of every securities fund.

The Comissão do Mercado de Valores Mobiliários publishes, in its *Sistema de
Difusão de Informação*, the *composição discriminada das carteiras* that each
Portuguese securities fund (FIM: UCITS, PPR and alternative securities funds)
files at quarter end (Regulamento da CMVM 5/2013): every position, its value
and the fund's net asset value. Real-estate funds (FII) are not read.

The site is an OutSystems app with no bulk file. Its pages read JSON from
"screen services", one POST per list or fund, under
``https://www.cmvm.pt/PInstitucional/screenservices/``:

``CMVM_SDI_Fun_GestAtivos_CW/ComposicaoDescriminadaCarteiras/FimList/DataActionFetchData``
    The funds with a portfolio for a quarter (``Date`` ``1900-01-01`` is the
    newest), with CMVM's fund number ``num_fun``, compartment ``num_cpa``
    (``0`` for a fund without compartments) and name, plus ``DateOut``, the
    quarter end.
``CMVM_SDI_Fun_GestAtivos_CW/ComposicaoDescriminadaCarteiras/FIMDetail/DataActionDataAction1``
    One fund's or compartment's positions (``FIM_QA``): section code and name
    (``num_rub``, ``DSC_RUB``), the security description (``des_tit``,
    usually a Bloomberg-style short name), quantity, price, currency, accrued
    interest, value in euros (``val_tot``), and on every row the fund's net
    asset value (``vlg_fun``) and units in issue.
``CMVM_SDI_CapRiscoEmp_CW/UIFlow1/CapRiscoEmp_FundoDetail_Wb/DataActionGetFundoDetail``
    The fund's register entry: ISIN (``cod_isi``), management company, type,
    and each active compartment with its own ISIN, and share-class ISINs
    where the fund has classes.

Each request body names the app's module version (``moduleservices/
moduleversioninfo``) and the action's API version, which is read from the
page script that calls it, so a redeploy of the site does not break the
client. Requests carry OutSystems' anonymous CSRF token, which is public and
the same for every visitor. ``https://www.cmvm.pt/robots.txt`` disallows
nothing these calls use.

Positions carry no ISINs. Equity lines get one by name from openbasket's
security master when the match is clear (``isin_source = name_match``, see
:mod:`openbasket.sources.name_isin`); everything else keeps its name and
weight with no ISIN, so it counts as unresolved.

Weights are ``val_tot / vlg_fun * 100`` for the same fund and quarter: never
rescaled. CMVM's sections for other assets and liabilities (``CC07``,
``CC08``) are kept as lines, so a fund's lines add up to its net assets.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pandas as pd

from .. import http
from ..identifiers import is_valid_isin

logger = logging.getLogger(__name__)

#: Redistribution class (see :func:`openbasket.sources.redistribution`).
#: CMVM is a public authority publishing these portfolios under its legal
#: duty to disclose them; the site states no licence. Portuguese law on the
#: reuse of public sector information (Lei 26/2016, which transposes the EU
#: Open Data Directive) makes documents public bodies publish reusable,
#: including commercially, with the source cited and the content not
#: distorted. openbasket is non-commercial and cites CMVM on every row.
REDISTRIBUTION = "public"

HOST = "https://www.cmvm.pt"
BASE = HOST + "/PInstitucional"
#: The *Composição discriminada das carteiras* page, cited as the source.
PAGE_URL = BASE + "/ContentSDI?Input=6DC02E106BBDBD62731D4921AEB7F06C2D9035AB73EDCF4AC46723111A21CDE3"
VERSION_URL = BASE + "/moduleservices/moduleversioninfo"
SCRIPT_URL = BASE + "/scripts/{script}.mvc.js"
SERVICE_URL = BASE + "/screenservices/{path}"
#: OutSystems' anonymous CSRF token (``AnonymousCSRFToken`` in its OutSystems.js).
ANONYMOUS_CSRF = "T6C+9iB49TLra4jEsMeSckDMNhQ="
#: Newest quarter, in the site's own convention.
LATEST = "1900-01-01"

CREDIT = (
    "Comissão do Mercado de Valores Mobiliários (CMVM), Sistema de Difusão de Informação: "
    "composição discriminada das carteiras dos fundos de investimento mobiliário"
)
LICENCE = (
    "Published by CMVM, Portugal's securities regulator, as required by law; no licence is stated on the site. "
    "Reused as public sector information under Lei 26/2016 (reuse of administrative documents), with CMVM cited. "
    "CMVM says the information reproduces what the funds filed and is the funds' sole responsibility. Weights here "
    "are calculated from the reported values; names are CMVM's."
)

#: action -> (page script, screen-service path)
ACTIONS = {
    "list": (
        "CMVM_SDI_Fun_GestAtivos_CW.ComposicaoDescriminadaCarteiras.FimList",
        "CMVM_SDI_Fun_GestAtivos_CW/ComposicaoDescriminadaCarteiras/FimList/DataActionFetchData",
    ),
    "portfolio": (
        "CMVM_SDI_Fun_GestAtivos_CW.ComposicaoDescriminadaCarteiras.FIMDetail",
        "CMVM_SDI_Fun_GestAtivos_CW/ComposicaoDescriminadaCarteiras/FIMDetail/DataActionDataAction1",
    ),
    "fund": (
        "CMVM_SDI_CapRiscoEmp_CW.UIFlow1.CapRiscoEmp_FundoDetail_Wb",
        "CMVM_SDI_CapRiscoEmp_CW/UIFlow1/CapRiscoEmp_FundoDetail_Wb/DataActionGetFundoDetail",
    ),
}
VIEW = "ContentSplit.ContentSDI"

#: Version of the Parquet layout under ``<root>/parquet/<as_of>``.
STORE_VERSION = 1

FUND_COLUMNS = [
    "portfolio_id", "num_fun", "num_cpa", "fund_name", "compartment_name", "isin", "share_class_isins",
    "manager", "fund_type", "status", "as_of", "net_assets_eur", "units", "n_lines",
]
POSITION_COLUMNS = [
    "portfolio_id", "line", "section", "section_name", "name", "quantity", "price", "currency",
    "accrued_interest", "value_eur",
]


# -- the screen services ------------------------------------------------------


class Client:
    """The three screen services, with the module and API versions read from the site."""

    def __init__(self):
        self._module: str | None = None
        self._api: dict[str, str] = {}

    def module_version(self) -> str:
        if self._module is None:
            r = http.get(VERSION_URL, timeout=60)
            r.raise_for_status()
            self._module = r.json()["versionToken"]
        return self._module

    def api_version(self, action: str) -> str:
        if action not in self._api:
            script, path = ACTIONS[action]
            r = http.get(SCRIPT_URL.format(script=script), timeout=60)
            r.raise_for_status()
            self._api[action] = parse_api_version(r.text, path)
        return self._api[action]

    def call(self, action: str, variables: dict) -> dict:
        body = {
            "versionInfo": {"moduleVersion": self.module_version(), "apiVersion": self.api_version(action)},
            "viewName": VIEW,
            "screenData": {"variables": variables},
        }
        r = http.post(
            SERVICE_URL.format(path=ACTIONS[action][1]),
            data=json.dumps(body),
            headers={"Content-Type": "application/json; charset=UTF-8", "Accept": "application/json",
                     "X-CSRFToken": ANONYMOUS_CSRF},
            timeout=90,
        )
        r.raise_for_status()
        out = r.json()
        info = out.get("versionInfo") or {}
        if info.get("hasModuleVersionChanged") or info.get("hasApiVersionChanged"):
            # CMVM redeployed between our version reads and this call: read them again once.
            logger.info("CMVM site was redeployed; reading its versions again")
            self._module, self._api = None, {}
            return self.call(action, variables)
        if "data" not in out:
            raise ValueError(f"CMVM {action} returned no data: {str(out)[:200]}")
        return out["data"]

    def funds(self, date: str = LATEST) -> tuple[str, list[dict]]:
        """``(quarter end, [{num_fun, num_cpa, descom_fun, nom_cpa}])`` for one quarter."""
        data = self.call("list", {"StartIndex": 0, "MaxRecords": 5000, "Date": date, "_dateInDataFetchStatus": 1,
                                  "Search": "", "_searchInDataFetchStatus": 1})
        return data["DateOut"], data["Fim_List"]["List"]

    def portfolio(self, num_fun: str, num_cpa: str, name: str, date: str = LATEST) -> list[dict]:
        data = self.call("portfolio", {
            "num_cpa": str(num_cpa), "_num_cpaInDataFetchStatus": 1, "num_fun": str(num_fun),
            "_num_funInDataFetchStatus": 1, "Date": date, "_dateInDataFetchStatus": 1, "nom_fun": name,
            "_nom_funInDataFetchStatus": 1,
        })
        return data["FIM_QA"]["List"]

    def fund(self, num_fun: str) -> dict:
        """The register entry: ``Out_FundoDetail`` and ``Fundo_Compartimentos_Ativo``."""
        return self.call("fund", {"Fun_Num": str(num_fun), "_fun_NumInDataFetchStatus": 1})


def parse_api_version(script: str, path: str) -> str:
    """The API version the page script sends for ``path``."""
    m = re.search(r'callDataAction\("[^"]+",\s*"screenservices/' + re.escape(path) + r'",\s*"([^"]+)"', script)
    if not m:
        raise ValueError(f"No API version for {path} in CMVM's page script")
    return m.group(1)


# -- parsing ------------------------------------------------------------------

#: CMVM's section names (``DSC_RUB``) to the normalised asset class. The section
#: codes (``num_rub``) mean different things for different fund types, so the
#: name is what counts.
SECTIONS = {
    "títulos de dívida pública": "debt",
    "outros fundos públicos e equiparados": "debt",
    "obrigações diversas": "debt",
    "papel comercial": "debt",
    "depósitos à ordem": "cash",
    "depósitos a prazo": "cash",
    "aplicações nos mercados monetários": "cash",
    "ações": "equity",
    "ações emitidas por sociedades imobiliárias": "equity",
    "direitos": "equity",
    "unidades de participação/ações de oic que não oii (etfs)": "fund",
    "unidades de participação/ações de oic que não oii": "fund",
    "unidades de participação/ações de oii (etfs)": "fund",
    "unidades de participação/ações de oii": "fund",
    "opções": "derivative",
    "valores ativos": "other",
    "valores passivos": "other",
    "empréstimos obtidos": "other",
    "ativos não financeiros": "other",
    "outros instrumentos financeiros": "other",
}
#: Sections whose lines are a fund's other assets and liabilities, not securities.
BALANCE_SECTIONS = {"valores ativos", "valores passivos", "empréstimos obtidos"}

_ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")
#: Prefixes and suffixes CMVM's descriptions put around a company name:
#: "AC.NOVARTIS AG-REG", "Acao Mota Engil SGPS", "EDP-Nom.", "Sonae -Po".
_EQ_PREFIX = re.compile(r"^\s*(?:a[cç]\.|a[cç][aã]o\s+|a[cç][oõ]es\s+)\s*", re.I)
_EQ_SUFFIX = re.compile(
    r"(?:\s*[-–/]?\s*\b(?:nom(?:inativas?)?|ord|port(?:ador)?|po|no|reg|new|r|b shrs)\b\.?\s*/?)+$|\(\d+\)|\(nom\)",
    re.I,
)


def section_class(section: str | None) -> str:
    return SECTIONS.get(" ".join(str(section or "").lower().split()), "other")


def embedded_isin(text: str | None) -> str | None:
    """An ISIN written into a description ("GALP Energia SGPS SA-B Shrs-PTGAL0AM0009"), if valid."""
    return next((m for m in _ISIN_RE.findall(str(text or "")) if is_valid_isin(m)), None)


def company_name(text: str | None) -> str:
    """A share line's description without CMVM's prefixes, share tags or embedded ISIN."""
    t = _ISIN_RE.sub(" ", str(text or ""))
    t = _EQ_PREFIX.sub("", t)
    t = _EQ_SUFFIX.sub("", t.strip())
    return re.sub(r"[\s.\-–,/]+$", "", t).strip()


def _num(v) -> float | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date(v: str | None) -> str | None:
    """``30-09-2026 00:00:00`` -> ``2026-09-30``."""
    m = re.match(r"(\d{2})-(\d{2})-(\d{4})", str(v or ""))
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def parse_portfolio(portfolio_id: str, rows: list[dict]) -> tuple[dict, pd.DataFrame]:
    """``(fund totals, positions)`` from one ``FIM_QA`` list."""
    lines = []
    for i, r in enumerate(rows):
        lines.append({
            "portfolio_id": portfolio_id,
            "line": i,
            "section": (r.get("num_rub") or "").strip() or None,
            "section_name": " ".join(str(r.get("DSC_RUB") or "").split()) or None,
            "name": " ".join(str(r.get("des_tit") or "").split()) or None,
            "quantity": _num(r.get("qtd_tit")),
            "price": _num(r.get("cot_tit")),
            "currency": (r.get("cod_moe") or "").strip() or None,
            "accrued_interest": _num(r.get("jur_cor")),
            "value_eur": _num(r.get("val_tot")),
        })
    first = rows[0] if rows else {}
    totals = {
        "as_of": _date(first.get("dat_mov")),
        "net_assets_eur": _num(first.get("vlg_fun")),
        "units": _num(first.get("num_ups")),
        "n_lines": len(lines),
    }
    return totals, pd.DataFrame(lines, columns=POSITION_COLUMNS)


def parse_fund(detail: dict) -> dict:
    """``{"fund": {...}, "compartments": {num_cpa: {...}}}`` from a register entry."""
    out = detail.get("Out_FundoDetail") or {}
    s = out.get("Fundo_SimpleStructure") or {}

    def isins(block) -> list[str]:
        got = [(x.get("COD_ISIN") or "").strip() for x in (block or {}).get("List", [])]
        return [i for i in got if is_valid_isin(i)]

    comps = {}
    for c in (detail.get("Fundo_Compartimentos_Ativo") or {}).get("List", []):
        comps[str(c.get("num_cpa"))] = {
            "name": " ".join(str(c.get("nom_cpa") or "").split()) or None,
            "isin": (c.get("cod_isi") or "").strip() or None,
            "share_class_isins": isins(c.get("FundUP_ISINCategoryList")),
            "fund_type": c.get("t_gr_tab_dsc_plus") or None,
            "status": c.get("s_gr_tab_dsc_plus") or None,
        }
    return {
        "fund": {
            "name": " ".join(str(s.get("descom_fun") or "").split()) or None,
            "isin": (s.get("cod_isi") or "").strip() or None,
            "share_class_isins": isins(out.get("FundUP_ISINCategoryList")),
            "manager": s.get("ent_gestora") or None,
            "fund_type": s.get("tipo") or None,
            "status": s.get("situacao") or None,
        },
        "compartments": comps,
    }


def fund_row(item: dict, detail: dict, totals: dict) -> dict:
    """One row of the ``funds`` frame for a list item, its fund's register entry and its totals."""
    num_fun, num_cpa = str(item["num_fun"]), str(item["num_cpa"])
    reg = parse_fund(detail) if detail else {"fund": {}, "compartments": {}}
    fund, comp = reg["fund"], reg["compartments"].get(num_cpa) if num_cpa != "0" else None
    src = comp or fund
    isin = src.get("isin")
    return {
        "portfolio_id": f"{num_fun}:{num_cpa}",
        "num_fun": num_fun,
        "num_cpa": num_cpa,
        "fund_name": fund.get("name") or " ".join(str(item.get("descom_fun") or "").split()),
        "compartment_name": (comp or {}).get("name") or (item.get("nom_cpa") or None),
        "isin": isin if isin and is_valid_isin(isin) else None,
        "share_class_isins": "; ".join(src.get("share_class_isins") or []) or None,
        "manager": fund.get("manager"),
        "fund_type": src.get("fund_type") or fund.get("fund_type"),
        "status": src.get("status") or fund.get("status"),
        **totals,
    }


# -- ISINs for share lines ----------------------------------------------------


def findb_index(path: str | Path | None) -> dict[str, str] | None:
    """Normalised company name -> ISIN from FinanceDatabase, for names with exactly one ISIN."""
    if not path or not Path(path).exists():
        return None
    from .name_isin import normalise

    df = pd.read_parquet(path, columns=["name", "isin"]).dropna()
    df = df[df["isin"].map(is_valid_isin)]
    df["key"] = df["name"].map(normalise)
    groups = df[df["key"] != ""].groupby("key")["isin"].agg(lambda s: sorted(set(s)))
    return {k: v[0] for k, v in groups.items() if len(v) == 1}


def resolve_isins(lines: pd.DataFrame, master: dict | None, findb: dict | None) -> tuple[pd.DataFrame, dict]:
    """Fill ``isin`` on lines that have none: an ISIN in the description, then share names.

    Share lines are matched by company name, first against the N-PORT
    security master (:func:`openbasket.sources.name_isin.resolve`), then
    against FinanceDatabase names that have exactly one ISIN. Only exact
    normalised names count: a near match is never taken.
    """
    from . import name_isin

    out = lines.copy()
    out["isin_source"] = None
    found = out["isin"].isna() & out["name"].map(embedded_isin).notna()
    out.loc[found, "isin"] = out.loc[found, "name"].map(embedded_isin)
    out.loc[found, "isin_source"] = "reported"
    eq = out["isin"].isna() & (out["asset_class"] == "equity")
    stats = {"embedded": int(found.sum()), "equity_lines": int(eq.sum()), "master": 0, "findb": 0}
    if not eq.any():
        return out, stats
    probe = pd.DataFrame({"isin": None, "name": out.loc[eq, "name"].map(company_name), "asset_class": "equity",
                          "weight_pct": out.loc[eq, "weight_pct"]}, index=out.index[eq])
    if master:
        probe, _ = name_isin.resolve(probe, master)
        hit = probe["isin"].notna()
        stats["master"] = int(hit.sum())
    if findb:
        left = probe["isin"].isna()
        probe.loc[left, "isin"] = probe.loc[left, "name"].map(lambda n: findb.get(name_isin.normalise(n)))
        stats["findb"] = int((left & probe["isin"].notna()).sum())
    hit = probe["isin"].notna()
    out.loc[probe.index[hit], "isin"] = probe.loc[hit, "isin"]
    out.loc[probe.index[hit], "isin_source"] = "name_match"
    return out, stats


def fund_lines(positions: pd.DataFrame, net_assets: float) -> pd.DataFrame:
    """One fund's positions in :data:`~openbasket.sources.issuers.HOLDING_COLUMNS` plus extras."""
    p = positions.sort_values("line").reset_index(drop=True)
    cls = p["section_name"].map(section_class)
    balance = p["section_name"].map(lambda s: " ".join(str(s or "").lower().split()) in BALANCE_SECTIONS)
    names = p["name"].where(~balance | p["name"].notna(), p["section_name"])
    out = pd.DataFrame({
        "isin": None,
        "cusip": None,
        "sedol": None,
        "ticker": None,
        "name": names.fillna(p["section_name"]),
        "weight_pct": (p["value_eur"].astype(float) / float(net_assets) * 100.0).round(6),
        "asset_class": cls,
        "currency": p["currency"],
        "country": None,
        "section": p["section_name"],
        "quantity": p["quantity"],
        "price": p["price"],
        "value_eur": p["value_eur"],
    })
    return out


def classify(lines: pd.DataFrame) -> str:
    """The fund's asset class from its own lines (65% rule, as for Sweden)."""
    w = lines.groupby("asset_class")["weight_pct"].sum()
    if w.get("equity", 0) >= 65:
        return "Equity"
    if w.get("debt", 0) >= 65:
        return "Fixed Income"
    if w.get("fund", 0) >= 65:
        return "Fund of funds"
    if w.get("cash", 0) >= 65:
        return "Money Market"
    return "Mixed"


def _nulls_to_none(df: pd.DataFrame) -> pd.DataFrame:
    """Missing values as ``None`` in text columns; numeric columns stay numeric."""
    out = df.copy()
    for c in out.columns:
        if not pd.api.types.is_numeric_dtype(out[c]):
            out[c] = out[c].astype(object).where(out[c].notna(), None)
    return out


# -- store ----------------------------------------------------------------------


class CmvmStore:
    """CMVM quarterly portfolios as Parquet under ``root/parquet/<as_of>/``."""

    def __init__(self, root: str | Path, client: Client | None = None):
        self.root = Path(root)
        self.client = client or Client()
        self._cache: tuple[str, dict[str, pd.DataFrame]] | None = None

    def refresh(self, date: str = LATEST) -> dict:
        """Read the newest quarter (or ``date``) from CMVM unless it is already stored."""
        as_of, items = self.client.funds(date)
        out = self.root / "parquet" / as_of
        stamp = out / "source.txt"
        if not (stamp.exists() and stamp.read_text().strip() == f"v{STORE_VERSION} {len(items)}"
                and (out / "positions.parquet").exists()):
            details: dict[str, dict] = {}
            funds, positions = [], []
            for item in items:
                num_fun = str(item["num_fun"])
                if num_fun not in details:
                    try:
                        details[num_fun] = self.client.fund(num_fun)
                    except Exception as exc:  # noqa: BLE001 - the register entry only adds ISINs
                        logger.warning("CMVM register entry for fund %s failed: %s", num_fun, exc)
                        details[num_fun] = {}
                pid = f"{num_fun}:{item['num_cpa']}"
                rows = self.client.portfolio(num_fun, str(item["num_cpa"]), str(item.get("descom_fun") or ""), date)
                totals, lines = parse_portfolio(pid, rows)
                funds.append(fund_row(item, details[num_fun], totals))
                positions.append(lines)
            out.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(funds, columns=FUND_COLUMNS).to_parquet(out / "funds.parquet", index=False)
            pos = pd.concat(positions, ignore_index=True) if positions else pd.DataFrame(columns=POSITION_COLUMNS)
            pos.to_parquet(out / "positions.parquet", index=False)
            stamp.write_text(f"v{STORE_VERSION} {len(items)}")
        self._cache = None
        funds = pd.read_parquet(out / "funds.parquet")
        return {"as_of": as_of, "url": PAGE_URL, "parquet_dir": str(out), "portfolios": len(funds),
                "with_isin": int(funds["isin"].notna().sum())}

    def quarters(self) -> list[str]:
        d = self.root / "parquet"
        return sorted(p.name for p in d.iterdir() if (p / "positions.parquet").exists()) if d.exists() else []

    def is_built(self) -> bool:
        return bool(self.quarters())

    def latest(self) -> str | None:
        """The newest stored quarter end, ``YYYY-MM-DD``."""
        got = self.quarters()
        return got[-1] if got else None

    def frames(self, as_of: str | None = None) -> tuple[str, dict[str, pd.DataFrame]]:
        got = self.quarters()
        if not got:
            raise FileNotFoundError(f"No CMVM portfolios under {self.root}: run CmvmStore.refresh()")
        as_of = as_of or got[-1]
        if self._cache is None or self._cache[0] != as_of:
            d = self.root / "parquet" / as_of
            self._cache = (as_of, {k: pd.read_parquet(d / f"{k}.parquet") for k in ("funds", "positions")})
        return self._cache

    def products(self, as_of: str | None = None) -> pd.DataFrame:
        """One row per ISIN: the fund's or compartment's, and each share class's."""
        as_of, f = self.frames(as_of)
        funds = f["funds"]
        funds = funds[funds["net_assets_eur"].fillna(0) > 0]
        pos = f["positions"]
        rows = []
        for r in funds.itertuples(index=False):
            lines = fund_lines(pos[pos["portfolio_id"] == r.portfolio_id], r.net_assets_eur)
            name = r.compartment_name if isinstance(r.compartment_name, str) and r.compartment_name else r.fund_name
            isins = [r.isin] if isinstance(r.isin, str) else []
            classes = r.share_class_isins if isinstance(r.share_class_isins, str) else ""
            isins += [i for i in classes.split("; ") if i and i not in isins]
            for isin in isins:
                rows.append({
                    "issuer": "cmvm_portugal",
                    "site": "pt",
                    "portfolio_id": r.portfolio_id,
                    "isin": isin,
                    "ticker": None,
                    "fund_name": name,
                    "domicile": "PT",
                    "currency": "EUR",
                    "asset_class": classify(lines),
                    "replication": None,
                    "product_page_url": PAGE_URL,
                    "holdings_url": PAGE_URL,
                    "manager": r.manager,
                    "fund_type": r.fund_type,
                    "net_assets_eur": r.net_assets_eur,
                    "as_of": r.as_of,
                })
        cols = ["issuer", "site", "portfolio_id", "isin", "ticker", "fund_name", "domicile", "currency",
                "asset_class", "replication", "product_page_url", "holdings_url", "manager", "fund_type",
                "net_assets_eur", "as_of"]
        df = pd.DataFrame(rows, columns=cols)
        return _nulls_to_none(df.drop_duplicates("isin").reset_index(drop=True))

    def holdings(self, portfolio_id: str, master: dict | None = None, findb: dict | None = None,
                 as_of: str | None = None) -> tuple[str, pd.DataFrame, list[str]]:
        """``(as_of, lines, notes)`` for one fund or compartment (``num_fun:num_cpa``)."""
        as_of, f = self.frames(as_of)
        row = f["funds"][f["funds"]["portfolio_id"] == str(portfolio_id)]
        if row.empty:
            raise LookupError(f"CMVM has no portfolio {portfolio_id} for {as_of}")
        fund = row.iloc[0].to_dict()
        total = fund.get("net_assets_eur")
        if not total or pd.isna(total) or total <= 0:
            raise LookupError(f"CMVM portfolio {portfolio_id} has no net asset value for {as_of}")
        lines = fund_lines(f["positions"][f["positions"]["portfolio_id"] == str(portfolio_id)], total)
        lines, stats = resolve_isins(lines, master, findb)
        positions_as_of = fund.get("as_of") or as_of
        matched = lines.loc[lines["isin_source"] == "name_match", "weight_pct"].sum()
        notes = [
            f"regulatory filing: CMVM composição discriminada da carteira, positions as of {positions_as_of}",
            "weights are each line's value in euros / the fund's net asset value (valor líquido global) in the same "
            "filing, not rescaled; CMVM's lines for other assets and liabilities are kept, so the lines add up to "
            "net assets",
            "CMVM gives no ISINs for positions: ISINs written in a description are used, share lines are matched by "
            f"exact company name to openbasket's SEC N-PORT security master and to FinanceDatabase "
            f"({stats['master'] + stats['findb']} of {stats['equity_lines']} share lines, {matched:.2f}% of net "
            "assets, isin_source = name_match); bonds, funds and other lines keep their name with no ISIN",
            f"management company: {fund.get('manager')}",
        ]
        if fund.get("share_class_isins"):
            notes.append(f"one portfolio for all share classes: {fund['share_class_isins']}")
        return positions_as_of, _nulls_to_none(lines), notes
