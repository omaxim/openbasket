"""iShares (BlackRock) holdings from the issuer's own websites.

Every regional iShares or BlackRock site is backed by the same two services,
so one module reads them all. A site is a key of :data:`SITES`.

Fund list (one of four shapes, by site)
    ``varnish``: ``/varnish-api/blk-product-screener-server/api/v1/product-screener/product-data``
    with ``country``, ``language`` and ``siteName``; returns a dict keyed by
    portfolio ID (UK, CH, DE, NL, IT, the Nordic sites, Hong Kong, Latin
    America offshore).
    ``v3``: ``.../product-screener/product-screener-v3.jsn?dcrPath=...`` returns
    ``data.tableData`` as columns plus rows (US, Spain, Singapore, Japan,
    Canada, Mexico, Brazil, Colombia).
    ``legacy``: the older ``<page>.ajax?tab=all&fileType=json`` screener with
    the same columns-plus-rows table at the top level (France).
    ``sitemap``: the site's public ``product-sitemap.xml`` gives the portfolio
    IDs, and each fund's ``keyFundFacts`` its ISIN (Australia, whose
    product-list pages answer 403 to scripts).
    Each row carries the share class ``isin`` (Canada's screener has none, so
    it comes from the CUSIP in ``keyFundFacts``), ticker, asset class and
    ``totalNetAssets``. This is how an ISIN is matched to a fund.
Product data, holdings component
    ``https://www.blackrock.com/varnish-api/<data_site>/product-data/api/v2/get-product-data``
    with ``component=holdings,keyFundFacts`` returns the full holdings table
    as columns (``isin``, ``ticker``, ``issueName``, the weight,
    ``assetClass`` ...) with ``asOfDate``, and the fund's key facts
    (product structure and replication method on EMEA sites). EMEA sites use
    the ``uk-retail01-product-data`` service, the rest ``blk-one01-product-data``.
    The weight is ``holdingPercent`` (percent, five decimals); US equity
    tables give ``marketWeight`` (two decimals) instead, which is renamed to
    ``holdingPercent`` here. Mutual and index funds have no ``all`` table,
    only ``ten-largest`` (names and ``fundPercentage``, no identifiers,
    monthly); those are returned with ``scope == "top10"``.

Chile is not a site here: its screener works, but product data has no
``cl-one`` configuration (HTTP 500) and every share class it lists is also on
the US or UK site.

The older ``.ajax`` holdings CSVs are not used: on the UK and US sites they
return the HTML product page, and where they still work they carry no ISIN
and round weights to two decimals.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from .. import http
from ..identifiers import cusip_to_isin, is_valid_isin

logger = logging.getLogger(__name__)

ISHARES = "https://www.ishares.com"
BLACKROCK = "https://www.blackrock.com"
HOST = ISHARES
SCREENER_PATH = "/varnish-api/blk-product-screener-server/api/v1/product-screener/product-data"
SCREENER_URL = HOST + SCREENER_PATH
PRODUCT_DATA_URL = BLACKROCK + "/varnish-api/{data_site}/product-data/api/v2/get-product-data"
USER_AGENT = http.USER_AGENT
EMEA_DATA = "uk-retail01-product-data"
ONE_DATA = "blk-one01-product-data"
HOLDINGS_COMPONENTS = "holdings,keyFundFacts"
#: The site never returned more lines than this; a fund at the limit may be cut.
LINE_CAP = 20000
_V3_DCR = "/templatedata/config/product-screener-v3/data/{}/product-screener-backend-config"


def _varnish(host: str, country: str, language: str, site_name: str, data_site: str, locale: str, sub: str = "ONE") -> dict:
    return {"screener": "varnish", "host": host, "country": country, "language": language, "siteName": site_name,
            "data_site": data_site, "targetSite": site_name, "locale": locale, "appSubType": sub}


def _v3(path: str, dcr: str, target: str, locale: str, data_site: str = ONE_DATA, sub: str = "ONE", **extra) -> dict:
    return {"screener": "v3", "host": BLACKROCK, "screener_url": BLACKROCK + path,
            "screener_params": {"dcrPath": _V3_DCR.format(dcr), "siteEntryPassthrough": "true"},
            "data_site": data_site, "targetSite": target, "locale": locale, "appSubType": sub, **extra}


#: Regional sites, in the order the ``ishares`` adapter prefers them when the
#: same share class is listed on several (English-language EMEA sites first).
#: ``data_site``, ``targetSite``, ``locale`` and ``appSubType`` are the
#: product-data parameters found in each site's product pages.
SITES: dict[str, dict] = {
    "uk": _varnish(ISHARES, "gb", "en", "ishares-uk", EMEA_DATA, "en_GB", "ISHARES"),
    "ch": _varnish(ISHARES, "ch", "en", "ishares-ch", EMEA_DATA, "en_CH", "ISHARES"),
    "us": {
        "screener": "v3",
        "host": ISHARES,
        "screener_url": HOST + "/us/product-screener/product-screener-v3.jsn",
        "screener_params": {
            "dcrPath": "/templatedata/config/product-screener-v3/data/en/us-ishares/ishares-product-screener-backend-config",
            "siteEntryPassthrough": "true",
        },
        # BlackRock mutual funds share the US list but their holdings carry no
        # ISINs; N-PORT covers them better.
        "product_types": ["ISHARES_FUND_DATA"],
        "targetSite": "us-ishares",
        "locale": "en_US",
        "appSubType": "ISHARES",
        "data_site": ONE_DATA,
    },
    "de": _varnish(ISHARES, "de", "de", "de-ishares-v2", EMEA_DATA, "de_DE", "ISHARES"),
    "nl": _varnish(ISHARES, "nl", "nl", "ishares-nl", EMEA_DATA, "nl_NL", "ISHARES"),
    "it": _varnish(ISHARES, "it", "it", "ishares-it", EMEA_DATA, "it_IT", "ISHARES"),
    "se": _varnish(BLACKROCK, "se", "en", "one-se", EMEA_DATA, "en_SE"),
    "dk": _varnish(BLACKROCK, "dk", "en", "one-dk", EMEA_DATA, "en_DK"),
    "no": _varnish(BLACKROCK, "no", "en", "one-no", EMEA_DATA, "en_NO"),
    "fi": _varnish(BLACKROCK, "fi", "en", "one-fi", EMEA_DATA, "en_FI"),
    "fr": {
        "screener": "legacy",
        "host": BLACKROCK,
        "screener_url": BLACKROCK + "/fr/intermediaries/products/product-list/1524727817804.ajax",
        "screener_params": {"tab": "all", "fileType": "json"},
        "data_site": EMEA_DATA, "targetSite": "one-fr", "locale": "fr_FR", "appSubType": "ONE",
    },
    "es": _v3("/es/particulares/product-screener/product-screener-v3.jsn", "es/one-es", "one-es", "es_ES", EMEA_DATA),
    "latam": _varnish(BLACKROCK, "lm", "en", "latam", ONE_DATA, "en_LM"),
    "hk": _varnish(BLACKROCK, "hk", "en", "hk-one", ONE_DATA, "en_HK"),
    "sg": _v3("/sg/en/product-screener/product-screener-v3.jsn", "en/sg-one", "sg-one", "en_SG"),
    "jp": _v3("/jp/individual-en/en/product-screener/product-screener-v3.jsn", "ja/jp-one", "jp-one", "en_JP"),
    "au": {
        "screener": "sitemap",
        "host": BLACKROCK,
        "sitemap_url": BLACKROCK + "/au/product-sitemap.xml",
        "sitemap_pattern": r"/au/products/(\d+)/",
        "data_site": ONE_DATA, "targetSite": "blk-au", "locale": "en_AU", "appSubType": "ONE",
    },
    "ca": _v3("/ca/investors/en/product-screener/product-screener-v3.jsn", "en/ca-one", "ca-one", "en_CA",
              isin_from_key_facts="CA"),
    "mx": _v3("/mx/intermediarios/lista-de-producto/product-screener-v3.jsn", "es/latam/mexico", "mx-one", "es_MX"),
    "br": _v3("/br/product-list/product-screener-v3.jsn", "pt/br-one", "br-one", "pt_BR"),
    "co": _v3("/co/lista-de-producto/product-screener-v3.jsn", "es/latam/columbia", "co-one", "es_CO"),
}

#: iShares asset-class labels mapped to the labels used elsewhere in the
#: output. Local-language sites return their own labels; those seen in a run
#: are listed too.
ASSET_CLASS = {
    "Equity": "equity",
    "Fixed Income": "debt",
    "Cash": "cash",
    "Money Market": "money market",
    "Futures": "derivative (futures)",
    "FX": "derivative (foreign exchange)",
    "Cash Collateral and Margins": "cash",
    "Forwards": "derivative (forwards)",
    "Aktien": "equity",
    "Anleihen": "debt",
    "Barmittel": "cash",
    "Geldmarkt": "money market",
    "Devisen": "derivative (foreign exchange)",
    "Actions": "equity",
    "Obligations": "debt",
    "Liquidités": "cash",
    "Monétaire": "money market",
    "Marchés monétaires (tous)": "money market",
    "Azionario": "equity",
    "Reddito Fisso": "debt",
    "Liquidità": "cash",
    "Monetario": "money market",
    "Aandelen": "equity",
    "Obligaties": "debt",
    "Renta variable": "equity",
    "Renta fija": "debt",
    "Efectivo": "cash",
    "Renta Variable": "equity",
    "Renda Variável": "equity",
    "Renda Fixa": "debt",
    "Caixa": "cash",
}

#: Fund-level asset class by Aladdin code, so local-language sites get the
#: same English label as the UK site.
FUND_ASSET_CLASS = {
    "43511": "Equity", "43515": "Fixed Income", "43530": "Real Estate", "43525": "Commodity",
    "43521": "Multi Asset", "43518": "Cash", "62183": "Digital Assets", "OTHR": "Others",
}
_FUND_ASSET_LABELS = {
    "Aktien": "Equity", "Anleihen": "Fixed Income", "Immobilien": "Real Estate", "Rohstoffe": "Commodity",
    "Azionario": "Equity", "Reddito Fisso": "Fixed Income", "Immobiliare": "Real Estate", "Materie prime": "Commodity",
    "Aandelen": "Equity", "Obligaties": "Fixed Income", "Vastgoed": "Real Estate", "Grondstoffen": "Commodity",
    "Renta variable": "Equity", "Renta fija": "Fixed Income", "Multiactivo": "Multi Asset", "Efectivo": "Cash",
    "Materias primas": "Commodity", "Bienes raíces": "Real Estate", "Sector inmobiliario": "Real Estate",
    "Monetario": "Cash", "Marchés privés": "Private Markets", "Sector privado": "Private Markets", "Renda Variável": "Equity", "Renda Fixa": "Fixed Income", "Actions": "Equity",
    "Obligations": "Fixed Income", "Barmittel": "Cash", "Multi-Asset": "Multi Asset", "Multi-asset": "Multi Asset",
}

#: Country names as the sites spell them (several languages) to ISO codes.
_COUNTRY = {
    "IE": ("Ireland", "Irland", "Irlande", "Irlanda", "Ierland"),
    "LU": ("Luxembourg", "Luxemburg", "Luxemburgo", "Lussemburgo"),
    "DE": ("Germany", "Deutschland", "Allemagne", "Germania", "Duitsland", "Alemania", "Alemanha"),
    "CH": ("Switzerland", "Schweiz", "Suisse", "Svizzera", "Zwitserland", "Suiza", "Suíça"),
    "GB": ("United Kingdom", "Vereinigtes Königreich", "Royaume-Uni", "Regno Unito", "Verenigd Koninkrijk", "Reino Unido"),
    "US": ("United States", "Vereinigte Staaten", "Etats-Unis", "États-Unis", "Stati Uniti", "Verenigde Staten", "Estados Unidos"),
    "NL": ("Netherlands", "Niederlande", "Pays-Bas", "Paesi Bassi", "Nederland", "Países Bajos", "Holanda"),
    "FR": ("France", "Frankreich", "Francia", "Frankrijk", "França"),
    "HK": ("Hong Kong",),
    "JP": ("Japan", "Japon", "Giappone", "Japón", "Japão"),
    "AU": ("Australia", "Australien", "Australie"),
    "CA": ("Canada", "Kanada", "Canadá"),
    "MX": ("Mexico", "México", "Mexiko", "Mexique", "Messico"),
    "BR": ("Brazil", "Brasil", "Brasilien", "Brésil", "Brasile"),
    "CO": ("Colombia", "Kolumbien", "Colombie"),
    "CL": ("Chile", "Chili", "Cile"),
    "SG": ("Singapore", "Singapur", "Singapour"),
}
COUNTRY_CODES = {name.casefold(): code for code, names in _COUNTRY.items() for name in names}

_SWAP = re.compile(r"\bswap\b|synthetic", re.IGNORECASE)


@dataclass
class IssuerHoldings:
    as_of: str | None
    holdings: pd.DataFrame
    url: str
    notes: list[str] = field(default_factory=list)
    scope: str = "full"          # "full", or "top10" for the ten-largest table
    replication: str | None = None
    key_facts: dict = field(default_factory=dict)


def _get(url: str, params: dict | None) -> requests.Response:
    r = http.get(url, params=params, timeout=90)
    r.raise_for_status()
    return r


def _json(r) -> object:
    """Response JSON, tolerating the byte-order mark some screeners send."""
    try:
        return r.json()
    except ValueError:
        return json.loads(r.content.decode("utf-8-sig"))


def _table(payload: dict) -> list[dict]:
    table = payload["data"]["tableData"] if isinstance(payload.get("data"), dict) else payload
    names = [c["name"] for c in table["columns"]]
    return [dict(zip(names, r)) for r in table["data"]]


def _product_data_params(portfolio_id: object, site: str, component: str) -> dict:
    cfg = SITES[site]
    return {
        "appType": "PRODUCT_PAGE",
        "appSubType": cfg.get("appSubType", "ISHARES"),
        "targetSite": cfg["targetSite"],
        "locale": cfg["locale"],
        "userType": "individual",
        "portfolioId": str(portfolio_id),
        "component": component,
    }


def holdings_url(portfolio_id: object, site: str, component: str = HOLDINGS_COMPONENTS) -> str:
    """The product-data URL that :func:`holdings` reads for one share class."""
    url = PRODUCT_DATA_URL.format(data_site=SITES[site]["data_site"])
    return requests.Request("GET", url, params=_product_data_params(portfolio_id, site, component)).prepare().url


def _product_data(portfolio_id: object, site: str, component: str) -> dict:
    url = PRODUCT_DATA_URL.format(data_site=SITES[site]["data_site"])
    return _json(_get(url, _product_data_params(portfolio_id, site, component)))


def _column(dp: dict) -> object:
    value = dp.get("value")
    if isinstance(value, str) and value.startswith("["):
        # Values are Python-style list literals ('...' quotes, None).
        return ast.literal_eval(value)
    return value


def parse_key_facts(payload: dict) -> dict:
    """Scalar key facts of a product-data response (empty if not requested)."""
    comp = (payload.get("componentsByNameMap") or {}).get("keyFundFacts") or {}
    out: dict = {}
    for container in (comp.get("containersByNameMap") or {}).values():
        for k, v in (container.get("dataPointsByNameMap") or {}).items():
            if isinstance(v, dict):
                out[k] = v.get("value")
    for k in ("portfolioType", "fundName", "currencyCode", "aladdinFundTicker"):
        if k in payload:
            out.setdefault(k, payload[k])
    return out


def replication_from_key_facts(facts: dict) -> str | None:
    """``swap``, ``physical`` or ``sampled`` from ``productStructure`` and
    ``fundMethodologyTypeCode`` (EMEA sites only; other sites omit them)."""
    structure = str(facts.get("productStructure") or "").casefold()
    method = str(facts.get("fundMethodologyTypeCode") or "").casefold()
    if structure.startswith("synth") or "swap" in structure:
        return "swap"
    if method.startswith(("optim", "ottim", "sampl")):
        return "sampled"
    if structure.startswith("phys") or method.startswith(("replic", "repli")):
        return "physical"
    return None


def parse_holdings(payload: dict) -> tuple[str | None, pd.DataFrame]:
    """Turn a ``component=holdings`` response into ``(as_of, DataFrame)``.

    A fund with only a ten-largest table (mutual and index funds) gives those
    ten lines, with ``df.attrs["scope"] == "top10"``.
    """
    containers = payload["componentsByNameMap"]["holdings"]["containersByNameMap"]
    scope = "full"
    if "all" in containers:
        container = containers["all"]
    elif "ten-largest" in containers:
        container, scope = containers["ten-largest"], "top10"
    else:
        raise KeyError(f"iShares holdings have no 'all' or 'ten-largest' table: {sorted(containers)}")
    cols = {k: _column(v) for k, v in container["dataPointsByNameMap"].items()}
    as_of = cols.pop("asOfDate", None)
    cols.pop("dateList", None)
    lists = {k: v for k, v in cols.items() if isinstance(v, list)}
    lengths = {len(v) for v in lists.values()}
    if len(lengths) > 1:
        raise ValueError(f"iShares holdings columns differ in length: { {k: len(v) for k, v in lists.items()} }")
    df = pd.DataFrame(lists)
    if scope == "top10":
        df = df.rename(columns={"fundPercentage": "holdingPercent", "name": "issueName"})
    if "holdingPercent" not in df and "marketWeight" in df:
        df = df.rename(columns={"marketWeight": "holdingPercent"})
    if "isin" in df:
        isins = [None if _blank(x) else x.strip().upper() for x in df["isin"]]
        sources = ["reported" if x else None for x in isins]
        # Lines with only a CUSIP are left for the providers, which look the
        # CUSIP up in N-PORT and check any derived ISIN against OpenFIGI.
        df["isin"] = pd.Series(isins, index=df.index, dtype=object)
        df["isin_source"] = pd.Series(sources, index=df.index, dtype=object)
    elif len(df):
        df["isin"] = pd.Series([None] * len(df), index=df.index, dtype=object)
        df["isin_source"] = pd.Series([None] * len(df), index=df.index, dtype=object)
    if as_of:
        s = str(as_of)
        as_of = f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s
    df.attrs["scope"] = scope
    return as_of, df


def _blank(value: object) -> bool:
    return not isinstance(value, str) or value.strip() in ("", "-")


def _key_facts_row(portfolio_id: object, site: str, base: dict | None = None) -> dict:
    """A screener-shaped row for one fund from its ``keyFundFacts``."""
    payload = _product_data(portfolio_id, site, "keyFundFacts")
    facts = parse_key_facts(payload)
    row = dict(base or {})
    row["portfolioId"] = portfolio_id
    isin = facts.get("isin")
    if _blank(isin) and SITES[site].get("isin_from_key_facts") and not _blank(facts.get("cusip")):
        isin = cusip_to_isin(facts["cusip"], SITES[site]["isin_from_key_facts"])
    row["isin"] = None if _blank(isin) else isin
    row.setdefault("fundName", facts.get("fundName"))
    row["productType"] = row.get("productType") or facts.get("portfolioType")
    row["domicile"] = row.get("domicile") or facts.get("domicile")
    row["seriesBaseCurrencyCode"] = row.get("seriesBaseCurrencyCode") or facts.get("baseCurrencyCode") or facts.get("currencyCode")
    row["aladdinFundTicker"] = row.get("aladdinFundTicker") or facts.get("aladdinFundTicker")
    if _blank(row.get("localExchangeTicker")):
        row["localExchangeTicker"] = facts.get("exchangeTicker") or facts.get("bbeqtick")
    row.setdefault("aladdinAssetClass", facts.get("assetClass"))
    row["totalNetAssets"] = row.get("totalNetAssets") or facts.get("totalNetAssetsFundLevel") or facts.get("totalNetAssets")
    return row


def _screener_rows(site: str, max_lookups: int | None = None) -> list[dict]:
    """Raw fund-list rows for a site. ``max_lookups`` caps the per-fund
    ``keyFundFacts`` calls that Australia and Canada need (for testing)."""
    cfg = SITES[site]
    kind = cfg.get("screener", "varnish")
    if kind in ("v3", "legacy"):
        rows = _table(_json(_get(cfg["screener_url"], cfg["screener_params"])))
    elif kind == "sitemap":
        text = _get(cfg["sitemap_url"], None).text
        ids = list(dict.fromkeys(re.findall(cfg["sitemap_pattern"], text)))
        rows = [{"portfolioId": int(i)} for i in ids]
    else:
        params = {k: cfg[k] for k in ("country", "language", "siteName")} | {"userType": "individual"}
        host = cfg.get("host", HOST)
        rows = [p | {"portfolioId": p.get("portfolioId") or pid}
                for pid, p in _json(_get(host + SCREENER_PATH, params)).items() if isinstance(p, dict)]
    if kind == "sitemap" or cfg.get("isin_from_key_facts"):
        out, done = [], 0
        for r in rows:
            if not _blank(r.get("isin")):
                out.append(r)
                continue
            if max_lookups is not None and done >= max_lookups:
                continue
            done += 1
            try:
                out.append(_key_facts_row(r["portfolioId"], site, r))
            except Exception as exc:  # one broken fund must not stop the list
                logger.warning("iShares %s key facts for %s: %s", site, r.get("portfolioId"), exc)
        rows = out
    if "product_types" in cfg:
        rows = [r for r in rows if r.get("productType") in cfg["product_types"]]
    return rows


def _country(value: object) -> str | None:
    if _blank(value):
        return None
    v = str(value).strip()
    if len(v) == 2 and v.isalpha():
        return v.upper()
    return COUNTRY_CODES.get(v.casefold(), v)


def _scope(p: dict) -> str:
    """Which holdings table the site has for this product: ``full`` for ETFs,
    ``top10`` for mutual and index funds, ``etp`` for exchange-traded products."""
    kind = str(p.get("productType") or "")
    if kind == "ETP":
        return "etp"
    if kind == "BLK_MUTUAL_FUND_DATA":
        return "top10"
    if kind == "ISHARES_FUND_DATA":
        return "full"
    view = " ".join(map(str, p.get("productView") or [])) if isinstance(p.get("productView"), list) else str(p.get("productView") or "")
    if re.search(r"etf|ishares|tselisted", view, re.IGNORECASE):
        return "full"
    if re.search(r"mutual|index", view, re.IGNORECASE):
        return "top10"
    name = str(p.get("fundName") or "")
    return "full" if re.search(r"\bETF\b|Fundo de Índice|TRAC", name) else "top10"


def _fund_asset_class(p: dict) -> str | None:
    code = str(p.get("aladdinAssetClassCode") or "")
    if code in FUND_ASSET_CLASS:
        return FUND_ASSET_CLASS[code]
    label = p.get("aladdinAssetClass")
    return _FUND_ASSET_LABELS.get(label, label) if not _blank(label) else None


def _page_url(p: dict, cfg: dict) -> str | None:
    url = p.get("productPageUrl")
    if _blank(url):
        return None
    return url if url.startswith("http") else cfg.get("host", HOST) + ("" if url.startswith("/") else "/") + url


def parse_products(rows: list[dict], site: str) -> pd.DataFrame:
    """One row per share-class ISIN from screener rows. A missing ISIN is
    derived from the CUSIP where that is safe."""
    cfg = SITES[site]
    out = []
    for p in rows:
        tna = p.get("totalNetAssets")
        isin = None if _blank(p.get("isin")) else p["isin"].strip().upper()
        if isin is None and not _blank(p.get("cusip")):
            isin = cusip_to_isin(p["cusip"])
        name = p.get("fundName")
        page = _page_url(p, cfg)
        domicile = _country(p.get("domicileCode")) or _country(p.get("domicile"))
        if domicile is None and isin and isin[:2] not in ("XS", "EU"):
            domicile = isin[:2]  # fund ISINs carry the country of domicile
        pid = str(p.get("portfolioId"))
        fund_id = p.get("aladdinFundTicker")
        out.append(
            {
                "site": site,
                "portfolio_id": pid,
                "fund_id": None if _blank(fund_id) else fund_id,
                "isin": isin,
                "ticker": None if _blank(p.get("localExchangeTicker")) else p["localExchangeTicker"],
                "fund_name": name,
                "asset_class": _fund_asset_class(p),
                "sub_asset_class": p.get("aladdinSubAssetClass"),
                "domicile": domicile,
                "currency": None if _blank(p.get("seriesBaseCurrencyCode")) else p["seriesBaseCurrencyCode"],
                # Only the name: some page slugs still say "synthetic" for funds
                # that now hold the shares (Hong Kong's FTSE China A50 ETF).
                "replication": "swap" if _SWAP.search(str(name or "")) else None,
                "holdings_scope": _scope(p),
                "product_page_url": page,
                "holdings_url": holdings_url(pid, site) if pid not in ("None", "") else None,
                "total_net_assets": pd.to_numeric(tna.get("r") if isinstance(tna, dict) else tna, errors="coerce"),
            }
        )
    df = pd.DataFrame(out, columns=list(out[0]) if out else ["site", "portfolio_id", "isin"])
    df = df[df["isin"].map(is_valid_isin)] if len(df) else df
    return df.drop_duplicates("isin").reset_index(drop=True)


def products(site: str = "uk", max_lookups: int | None = None) -> pd.DataFrame:
    """Every fund on a regional iShares site, one row per share class ISIN.

    ``holdings_scope`` says what :func:`holdings` can return for it: ``full``
    (ETFs), ``top10`` (mutual and index funds: ten names, no identifiers) or
    ``etp``.
    """
    return parse_products(_screener_rows(site, max_lookups=max_lookups), site)


def _cacheable(payload: dict) -> dict:
    # The ``content`` block is about 350 KB of page disclaimers per response.
    return {k: v for k, v in payload.items() if k != "content"}


def holdings(portfolio_id: str, site: str = "uk", cache_dir: str | Path | None = None) -> IssuerHoldings:
    """Holdings of one share class. Responses are cached per fund per day,
    only once they have parsed."""
    url = holdings_url(portfolio_id, site)
    cache = Path(cache_dir) / f"{site}_{portfolio_id}_{date.today().isoformat()}.json" if cache_dir else None
    fresh = not (cache is not None and cache.exists())
    if not fresh:
        payload = json.loads(cache.read_text())
    else:
        logger.info("Fetching iShares %s holdings for portfolio %s", site, portfolio_id)
        payload = _product_data(portfolio_id, site, HOLDINGS_COMPONENTS)
    as_of, df = parse_holdings(payload)
    if fresh and cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(_cacheable(payload)))
    facts = parse_key_facts(payload)
    scope = df.attrs.get("scope", "full")
    notes = []
    if scope == "top10":
        notes.append("Top 10 holdings only (names, no identifiers); the issuer publishes no full list for this fund.")
    if len(df) >= LINE_CAP:
        notes.append(f"{len(df)} lines: the site appears to cap holdings at {LINE_CAP}, so a tail may be missing.")
    replication = replication_from_key_facts(facts)
    if replication == "swap":
        notes.append("Swap-based fund: lines are the substitute basket or the swap, not the index.")
    return IssuerHoldings(as_of, df, url, notes=notes, scope=scope, replication=replication, key_facts=facts)
