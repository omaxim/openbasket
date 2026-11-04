"""A worldwide fund registry from open data: every fund we can name, with or without holdings.

The registry holds no holdings. It lets the site say *known fund, no open
holdings source* for an ISIN nobody has a portfolio for, and route the others
to the regulator or issuer source that has one. Every field says which source
it came from.

Sources (all open for reuse and republication; see ``LICENCES``):

GLEIF Level 1 golden copy (``lei2`` CSV)
    Every LEI record. Records with ``Entity.EntityCategory = FUND`` are the
    funds; the managers, umbrellas and parent companies they point to are kept
    too, for their names. ``https://goldencopy.gleif.org/api/v2/golden-copies/publishes/latest``
    lists the daily files. CC0 1.0.
GLEIF Level 2 relationship records (``rr`` CSV)
    ``IS_FUND-MANAGED_BY`` (fund -> management company), ``IS_SUBFUND_OF``
    (sub-fund -> umbrella), ``IS_FEEDER_TO`` (feeder -> master) and
    ``IS_ULTIMATELY_CONSOLIDATED_BY`` / ``IS_DIRECTLY_CONSOLIDATED_BY``
    (management company -> parent group, which is how a bank's funds are
    found). Same index. CC0 1.0.
GLEIF ISIN-LEI mapping
    ``https://mapping.gleif.org/api/v2/isin-lei/latest``: every ISIN that a
    national numbering agency has linked to an LEI. Pairs whose LEI is a fund
    (or a FIRDS fund issuer) give the fund's share-class ISINs. CC0 1.0.
ESMA FIRDS ``FULINS_C``
    Weekly full file of every collective investment vehicle unit admitted to
    trading on an EU venue: ISIN, full name, CFI code, currency, the issuing
    fund's LEI and the venues. ESMA legal notice: reproduction authorised with
    acknowledgement.
ESMA fund registers (Solr, documented by ESMA for application-to-application use)
    ``esma_registers_funds_cbdif``: the central database of UCITS and AIFs
    marketed cross-border (Regulation (EU) 2019/1156), and
    ``esma_registers_funds``: the register of AIFs, EuVECA, EuSEF and ELTIF.
    Fund name, LEI (where given), legal framework, domicile, manager name and
    LEI. Same legal notice.
SEC (optional)
    The project's own ``fund_master`` (SEC ``company_tickers_mf.json`` joined
    to N-PORT series LEIs), read if present, to tie US series to their LEIs and
    mark them as covered by N-PORT. US government work, public.

Outputs: ``fund_entities`` (one row per fund: an LEI, else an ESMA register id,
else an SEC series id) and ``fund_registry`` (one row per fund ISIN).
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd

from .. import http
from ..identifiers import is_valid_isin

logger = logging.getLogger(__name__)

# -- endpoints --------------------------------------------------------------

GLEIF_GOLDEN_COPY_INDEX = "https://goldencopy.gleif.org/api/v2/golden-copies/publishes/latest"
GLEIF_ISIN_LEI_INDEX = "https://mapping.gleif.org/api/v2/isin-lei/latest"
GLEIF_LEI_PAGE = "https://search.gleif.org/#/record/{lei}"
GLEIF_HOME = "https://www.gleif.org/en/lei-data/gleif-golden-copy-and-delta-files"
ESMA_FIRDS_INDEX = "https://registers.esma.europa.eu/solr/esma_registers_firds_files/select"
ESMA_FIRDS_HOME = "https://registers.esma.europa.eu/publication/searchRegister?core=esma_registers_firds"
ESMA_SOLR = "https://registers.esma.europa.eu/solr/{core}/select"
ESMA_REGISTER_HOME = "https://registers.esma.europa.eu/publication/searchRegister?core={core}"
SEC_HOME = "https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets"

#: ESMA fund registers read, as ``{core: label}``.
ESMA_FUND_CORES = {
    "esma_registers_funds_cbdif": "ESMA central database of UCITS and AIFs marketed cross-border",
    "esma_registers_funds": "ESMA register of AIFs, EuVECA, EuSEF and ELTIF",
}
ESMA_FIELDS = [
    "id", "funds_national_name", "funds_lei", "funds_legal_framework_name", "funds_domicile_cou_code",
    "funds_manager_nat_name", "funds_manager_lei", "funds_manager_cou_code", "funds_status_code_name", "funds_ca_cou_code",
    "funds_last_update_date", "timestamp",
]

# -- terms ------------------------------------------------------------------

#: Source key -> (credit line, licence text quoted from the source's terms, link to those terms).
LICENCES = {
    "gleif": (
        "LEI data: Global Legal Entity Identifier Foundation (GLEIF)",
        'CC0 1.0. GLEIF terms of use: "The data available through the Access Service are provided '
        'under the CC0 licence, see CC0 1.0 Universal (CC0 1.0)."',
        "https://www.gleif.org/en/meta/lei-data-terms-of-use",
    ),
    "gleif_isin_lei": (
        "ISIN-LEI mapping: GLEIF and the Association of National Numbering Agencies (ANNA)",
        "CC0 1.0 (GLEIF terms of use, as for all GLEIF Access Service data).",
        "https://www.gleif.org/en/meta/lei-data-terms-of-use",
    ),
    "esma_firds": (
        "Source: ESMA Financial Instruments Reference Data System (FIRDS)",
        'ESMA legal notice: "Reproduction of all information on this site (ESMA Library) is authorised '
        'except as otherwise stated, provided the source is acknowledged". Merged with other sources here.',
        "https://www.esma.europa.eu/legal-notice",
    ),
    "esma_funds": (
        "Source: ESMA registers (cross-border marketing of UCITS and AIFs; AIF register)",
        'ESMA registers legal notice: "Reproduction of all information on this site (REGISTERS information) '
        'is authorised except as otherwise stated, provided the source is acknowledged". The register rows are '
        "transformed (merged with GLEIF and FIRDS) before republication, as that notice asks to state.",
        "https://registers.esma.europa.eu/publication/legalNoticePage",
    ),
    "sec": (
        "U.S. Securities and Exchange Commission: company_tickers_mf.json and Form N-PORT",
        'sec.gov: "Information presented on sec.gov is considered public information and may be copied or '
        'further distributed by users of the web site without the SEC\'s permission."',
        "https://www.sec.gov/about/privacy-information",
    ),
}

#: Relationship types kept from the GLEIF Level 2 file.
RELATIONSHIP_TYPES = {
    "IS_FUND-MANAGED_BY": "manager",
    "IS_SUBFUND_OF": "umbrella",
    "IS_FEEDER_TO": "master",
    "IS_ULTIMATELY_CONSOLIDATED_BY": "ultimate_parent",
    "IS_DIRECTLY_CONSOLIDATED_BY": "direct_parent",
}

LEI2_COLUMNS = {
    "LEI": "lei",
    "Entity.LegalName": "name",
    "Entity.LegalJurisdiction": "jurisdiction",
    "Entity.LegalAddress.Country": "legal_country",
    "Entity.EntityCategory": "category",
    "Entity.LegalForm.EntityLegalFormCode": "legal_form_code",
    "Entity.LegalForm.OtherLegalForm": "legal_form_other",
    "Entity.AssociatedEntity.type": "associated_type",
    "Entity.AssociatedEntity.AssociatedLEI": "associated_lei",
    "Entity.AssociatedEntity.AssociatedEntityName": "associated_name",
    "Entity.EntityStatus": "entity_status",
    "Registration.RegistrationStatus": "registration_status",
    "Registration.LastUpdateDate": "last_update",
}

ENTITY_COLUMNS = list(LEI2_COLUMNS.values())

FIRDS_COLUMNS = ["isin", "name", "cfi", "currency", "issuer_lei", "venues", "n_venues"]

ESMA_COLUMNS = [
    "register", "register_id", "name", "lei", "legal_framework", "domicile", "authority_country", "manager_name",
    "manager_lei", "manager_country", "status", "last_update",
]

FUND_ENTITY_COLUMNS = [
    "fund_id", "lei", "name", "name_source", "category", "legal_form", "legal_framework",
    "legal_framework_source", "jurisdiction", "domicile", "domicile_source", "entity_status",
    "manager_lei", "manager_name", "manager_source", "manager_group_lei", "manager_group_name",
    "manager_group_source", "bank_group", "bank_group_rule", "umbrella_lei", "umbrella_name",
    "umbrella_source", "master_lei", "master_name", "esma_register_id", "sec_series_id", "sec_tickers",
    "n_isins", "open_holdings_source", "listed_in", "source", "source_url", "credit", "licence", "as_of",
]

#: Columns of ``fund_registry``, one row per fund ISIN. The assignment's
#: fields (isin, lei, name, manager_lei, manager_name, umbrella, domicile,
#: cfi) each have a ``<field>_source``.
REGISTRY_COLUMNS = [
    "isin", "lei", "lei_source", "name", "name_source", "manager_lei", "manager_name", "manager_source",
    "manager_group_lei", "manager_group_name", "bank_group", "bank_group_rule", "umbrella_lei",
    "umbrella_name", "umbrella_source", "master_lei", "domicile", "domicile_source", "legal_form",
    "legal_framework", "legal_framework_source", "cfi", "cfi_source", "currency", "currency_source",
    "venues", "fund_status", "sec_series_id", "open_holdings_source", "listed_in", "source", "source_url",
    "credit", "licence", "as_of",
]

# -- identifiers ------------------------------------------------------------


def is_valid_lei(value: object) -> bool:
    """ISO 17442: 18 alphanumerics and two check digits (ISO 7064 MOD 97-10); all-zero placeholders fail."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Z]{18}[0-9]{2}", value) or set(value) == {"0"}:
        return False
    return int("".join(str(int(c, 36)) for c in value)) % 97 == 1


def _lei(value: object) -> str | None:
    v = value.strip().upper() if isinstance(value, str) else None
    return v if is_valid_lei(v) else None


# -- banks ------------------------------------------------------------------

#: Bank groups named in the fund-manager hierarchy. Matched on the name of the
#: manager's ultimate (else direct) parent, then on the manager's own name.
#: The first match wins; the canonical name is what the reports show.
NAMED_BANKS: list[tuple[str, str]] = [
    ("UBS", r"\bUBS\b|credit suisse"),
    ("Zürcher Kantonalbank", r"z(ü|ue|u)rcher kantonalbank|swisscanto"),
    ("Raiffeisen", r"raiffeisen"),
    ("Deka (Sparkassen)", r"\bdeka|sparkassen"),
    ("DZ Bank (Union Investment)", r"\bdz bank|union investment|genossenschaftliche zentralbank"),
    ("Santander", r"santander"),
    ("CaixaBank", r"caixabank|criteria caixa|\bla caixa\b"),
    ("BBVA", r"\bbbva\b|banco bilbao vizcaya"),
    ("Nordea", r"\bnordea"),
    ("Danske Bank", r"danske bank|danske invest"),
    ("SEB", r"skandinaviska enskilda|\bSEB\b"),
    ("Swedbank", r"swedbank"),
    ("Handelsbanken", r"handelsbanken"),
    ("KBC", r"\bKBC\b"),
    ("ING", r"\bING (groep|bank|belgi|-diba)"),
    ("Erste Group", r"\berste (group|bank|asset)"),
    ("Itaú Unibanco", r"ita[uú] unibanco|\bita[uú]\b"),
    ("Bradesco", r"bradesco"),
    ("Banco do Brasil", r"banco do brasil|\bBB (gest|dtvm|asset)"),
    ("Caixa Econômica Federal", r"caixa econ[oô]mica"),
    ("HDFC", r"\bhdfc\b"),
    ("ICICI", r"\bicici\b"),
    ("State Bank of India", r"state bank of india|\bSBI\b"),
    ("Kotak Mahindra", r"kotak mahindra"),
    ("Axis Bank", r"axis bank|axis asset"),
    ("JPMorgan Chase", r"jp\s?morgan|j\.\s?p\.\s?morgan"),
    ("Bank of America", r"bank of america"),
    ("Wells Fargo", r"wells fargo"),
    ("Citigroup", r"citigroup|citibank"),
    ("Goldman Sachs", r"goldman sachs"),
    ("Morgan Stanley", r"morgan stanley"),
    ("BNY", r"bank of new york mellon|\bBNY\b"),
    ("State Street", r"state street corp"),
    ("Northern Trust", r"northern trust corp"),
    ("BNP Paribas", r"bnp paribas"),
    ("Société Générale", r"soci[eé]t[eé] g[eé]n[eé]rale"),
    ("Crédit Agricole (Amundi)", r"cr[eé]dit agricole|\bamundi|\blyxor|cpr asset|indosuez"),
    ("Groupe BPCE (Natixis)", r"\bbpce\b|natixis"),
    ("Crédit Mutuel", r"cr[eé]dit mutuel|la fran[cç]aise"),
    ("La Banque Postale", r"banque postale"),
    ("Deutsche Bank (DWS)", r"deutsche bank|\bDWS\b"),
    ("Commerzbank", r"commerzbank"),
    ("LBBW", r"landesbank baden|\blbbw"),
    ("BayernLB", r"bayerische landesbank"),
    ("Helaba", r"landesbank hessen|\bhelaba"),
    ("HSBC", r"\bhsbc\b"),
    ("Barclays", r"barclays"),
    ("Lloyds Banking Group", r"lloyds banking"),
    ("NatWest", r"natwest|royal bank of scotland"),
    ("Standard Chartered", r"standard chartered"),
    ("Intesa Sanpaolo (Eurizon)", r"intesa sanpaolo|eurizon|fideuram"),
    ("UniCredit", r"unicredit"),
    ("Mediobanca", r"mediobanca"),
    ("Banco BPM", r"banco bpm"),
    ("Rabobank", r"rabobank|co[oö]peratieve rabobank"),
    ("ABN AMRO", r"abn amro"),
    ("Belfius", r"belfius"),
    ("Julius Bär", r"julius b(ä|ae|a)r"),
    ("Pictet", r"\bpictet"),
    ("Lombard Odier", r"lombard odier"),
    ("Vontobel", r"vontobel"),
    ("LGT", r"\bLGT\b"),
    ("EFG", r"\bEFG\b"),
    ("Banque Cantonale Vaudoise", r"banque cantonale vaudoise|\bBCV\b|gerifonds"),
    ("Mitsubishi UFJ", r"mitsubishi ufj|\bmufg\b"),
    ("Mizuho", r"mizuho"),
    ("Sumitomo Mitsui", r"sumitomo mitsui"),
    ("Royal Bank of Canada", r"royal bank of canada|\bRBC\b"),
    ("TD Bank", r"toronto-dominion"),
    ("BMO", r"bank of montreal|\bBMO\b"),
    ("Scotiabank", r"bank of nova scotia|scotiabank"),
    ("CIBC", r"canadian imperial bank"),
    ("Desjardins", r"desjardins"),
    ("National Bank of Canada", r"national bank of canada|banque nationale du canada"),
    ("DBS", r"\bDBS (group|bank)"),
    ("OCBC", r"\bOCBC\b|oversea-chinese banking"),
    ("UOB", r"united overseas bank|\bUOB\b"),
    ("ANZ", r"australia and new zealand banking"),
    ("Westpac", r"westpac"),
    ("Commonwealth Bank of Australia", r"commonwealth bank of australia"),
    ("National Australia Bank", r"national australia bank"),
    ("Bank of China", r"bank of china"),
    ("ICBC", r"industrial and commercial bank of china"),
    ("China Construction Bank", r"china construction bank"),
    ("Agricultural Bank of China", r"agricultural bank of china"),
    ("KB Financial", r"\bKB financial|kookmin"),
    ("Shinhan", r"shinhan"),
    ("Hana Financial", r"hana financial|\bhana bank"),
    ("Woori", r"woori"),
    ("Sabadell", r"sabadell"),
    ("Bankinter", r"bankinter"),
    ("Ibercaja", r"ibercaja"),
    ("Kutxabank", r"kutxabank"),
    ("Unicaja", r"unicaja"),
    ("Abanca", r"abanca"),
    ("Cajamar", r"cajamar"),
    ("Caja Rural", r"caja rural|grupo caja rural"),
    ("OTP", r"\bOTP bank"),
    ("PKO", r"\bPKO\b|powszechna kasa"),
    ("Pekao", r"pekao"),
    ("Bank Millennium / BCP", r"millennium bcp|banco comercial portugu[eê]s"),
    ("Caixa Geral de Depósitos", r"caixa geral"),
    ("Liechtensteinische Landesbank", r"liechtensteinische landesbank|\bLLB\b"),
    ("Investec", r"investec"),
    ("Macquarie", r"macquarie group|macquarie bank"),
    ("Andbank", r"andbank"),
    ("Banca Mediolanum", r"mediolanum"),
    ("Van Lanschot Kempen", r"van lanschot|\bkempen"),
    ("Berenberg", r"berenberg"),
    ("Metzler", r"\bmetzler"),
    ("Hauck Aufhäuser Lampe", r"hauck (&|und) aufh|hauck aufh"),
    ("M.M. Warburg", r"\bwarburg"),
    ("Nykredit", r"nykredit"),
    ("Jyske Bank", r"\bjyske"),
    ("DNB", r"\bDNB\b"),
]

#: Any other parent whose name says it is a bank. Labelled ``generic_name``.
GENERIC_BANK = (
    r"\b(bank|banco|banque|banca|bancorp|bancshares|bankshares|banken|bankas|bankia|sparkasse|sparebank|"
    r"sparbank|kantonalbank|landesbank|caixa|caja|kasse|kassa|volksbank|cr[eé]dit coop[eé]ratif)\b"
)

_NAMED = [(name, re.compile(rx, re.I)) for name, rx in NAMED_BANKS]
_GENERIC = re.compile(GENERIC_BANK, re.I)


def bank_group(*names: object) -> tuple[str | None, str | None]:
    """``(bank group, rule)`` for the first of ``names`` that names a bank, else ``(None, None)``.

    ``rule`` is ``named`` for a group in :data:`NAMED_BANKS` and
    ``generic_name`` when the name only contains a bank word (the group is then
    that name).
    """
    for n in names:
        if not isinstance(n, str) or not n.strip():
            continue
        for canonical, rx in _NAMED:
            if rx.search(n):
                return canonical, "named"
    for n in names:
        if isinstance(n, str) and _GENERIC.search(n):
            return n.strip(), "generic_name"
    return None, None


# -- downloads --------------------------------------------------------------


def download(url: str, dest: Path, timeout: int = 600) -> Path:
    """Stream ``url`` to ``dest`` (once; an existing file is reused)."""
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        logger.info("Using cached %s", dest)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    logger.info("Downloading %s", url)
    r = http.get(url, stream=True, timeout=timeout)
    r.raise_for_status()
    with open(tmp, "wb") as fh:
        for chunk in r.iter_content(chunk_size=1 << 20):
            fh.write(chunk)
    r.close()
    if not zipfile.is_zipfile(tmp):
        tmp.unlink(missing_ok=True)
        raise ValueError(f"{url}: expected a ZIP file")
    tmp.replace(dest)
    return dest


def prune(folder: Path, keep: Iterable[Path], pattern: str) -> None:
    """Delete older downloads matching ``pattern`` in ``folder`` (raw files stay local and few)."""
    keep = {Path(k).resolve() for k in keep}
    for p in Path(folder).glob(pattern):
        if p.resolve() not in keep:
            logger.info("Removing old download %s", p)
            p.unlink(missing_ok=True)


def golden_copy_files(index: dict) -> dict:
    """``{"publish_date", "lei2", "rr"}`` (CSV full-file URLs) from the golden copy index response."""
    data = index["data"]
    return {
        "publish_date": data["publish_date"][:10],
        "lei2": data["lei2"]["full_file"]["csv"]["url"],
        "rr": data["rr"]["full_file"]["csv"]["url"],
    }


def latest_golden_copy() -> dict:
    r = http.get(GLEIF_GOLDEN_COPY_INDEX)
    r.raise_for_status()
    return golden_copy_files(r.json())


def isin_lei_file(index: dict) -> dict:
    """``{"as_of", "url", "file_name"}`` from the ISIN-LEI ``latest`` response."""
    a = index["data"]["attributes"]
    return {"as_of": a["uploadedAt"][:10], "url": a["downloadLink"], "file_name": a["fileName"]}


def latest_isin_lei() -> dict:
    r = http.get(GLEIF_ISIN_LEI_INDEX)
    r.raise_for_status()
    return isin_lei_file(r.json())


def _csv_member(path: Path) -> tuple[zipfile.ZipFile, str]:
    z = zipfile.ZipFile(path)
    names = [n for n in z.namelist() if n.lower().endswith(".csv")]
    if not names:
        raise ValueError(f"{path}: no CSV inside")
    return z, names[0]


# -- GLEIF parsing ----------------------------------------------------------


def parse_relationships(path: Path) -> pd.DataFrame:
    """Active fund and consolidation relationships from the Level 2 ``rr`` CSV ZIP.

    Columns: ``start_lei``, ``end_lei``, ``relation`` (``manager``,
    ``umbrella``, ``master``, ``ultimate_parent``, ``direct_parent``).
    """
    z, name = _csv_member(path)
    cols = {
        "Relationship.StartNode.NodeID": "start_lei",
        "Relationship.EndNode.NodeID": "end_lei",
        "Relationship.EndNode.NodeIDType": "end_type",
        "Relationship.RelationshipType": "type",
        "Relationship.RelationshipStatus": "status",
    }
    with z, z.open(name) as fh:
        df = pd.read_csv(fh, usecols=list(cols), dtype=str).rename(columns=cols)
    df = df[(df["status"] == "ACTIVE") & df["type"].isin(RELATIONSHIP_TYPES) & (df["end_type"].fillna("LEI") == "LEI")]
    df = df.assign(relation=df["type"].map(RELATIONSHIP_TYPES))
    return df[["start_lei", "end_lei", "relation"]].drop_duplicates().reset_index(drop=True)


def parse_entities(path: Path, keep_leis: set[str] | None = None, chunksize: int = 250_000) -> pd.DataFrame:
    """Fund LEI records, plus records whose LEI is in ``keep_leis``, from the ``lei2`` CSV ZIP.

    Streams the 3.5 million records in chunks and keeps a dozen columns.
    Addresses are not kept. Sole proprietors are natural persons, so their
    names are dropped.
    """
    keep_leis = keep_leis or set()
    z, name = _csv_member(path)
    parts = []
    with z, z.open(name) as fh:
        header = pd.read_csv(fh, nrows=0).columns
    usecols = [c for c in LEI2_COLUMNS if c in header]
    z, name = _csv_member(path)
    with z, z.open(name) as fh:
        for chunk in pd.read_csv(fh, usecols=usecols, dtype=str, chunksize=chunksize, keep_default_na=False, na_values=[""]):
            sel = (chunk["Entity.EntityCategory"] == "FUND") | chunk["LEI"].isin(keep_leis)
            parts.append(chunk[sel])
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=usecols)
    df = df.rename(columns=LEI2_COLUMNS)
    for c in ENTITY_COLUMNS:
        if c not in df:
            df[c] = None
    df.loc[df["category"] == "SOLE_PROPRIETOR", ["name", "associated_name"]] = None
    df["last_update"] = df["last_update"].str[:10]
    return df[ENTITY_COLUMNS].drop_duplicates("lei").reset_index(drop=True)


def parse_isin_lei(path: Path, leis: set[str], chunksize: int = 1_000_000) -> pd.DataFrame:
    """``isin``, ``lei`` pairs from the GLEIF ISIN-LEI ZIP whose LEI is in ``leis`` and ISIN is valid."""
    z, name = _csv_member(path)
    parts = []
    with z, z.open(name) as fh:
        for chunk in pd.read_csv(fh, dtype=str, chunksize=chunksize):
            chunk.columns = [c.strip().upper() for c in chunk.columns]
            parts.append(chunk.loc[chunk["LEI"].isin(leis), ["ISIN", "LEI"]])
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["ISIN", "LEI"])
    df = df.rename(columns={"ISIN": "isin", "LEI": "lei"})
    df["isin"] = df["isin"].str.strip().str.upper()
    df = df[df["isin"].map(is_valid_isin)]
    return df.drop_duplicates().reset_index(drop=True)


# -- FIRDS ------------------------------------------------------------------


def firds_files(docs: list[dict], prefix: str = "C") -> list[dict]:
    """The newest publication's ``FULINS_<prefix>`` files from a FIRDS index response's docs."""
    files = [d for d in docs if d.get("file_type") == "FULINS" and d.get("file_name", "").startswith(f"FULINS_{prefix}_")]
    if not files:
        return []
    newest = max(d["publication_date"][:10] for d in files)
    return sorted((d for d in files if d["publication_date"][:10] == newest), key=lambda d: d["file_name"])


def latest_firds_c(today: date | None = None, days: int = 21) -> list[dict]:
    today = today or date.today()
    since = (today - timedelta(days=days)).isoformat()
    r = http.get(
        ESMA_FIRDS_INDEX,
        params={
            "q": "*",
            "fq": ["file_type:FULINS", f"publication_date:[{since}T00:00:00Z TO *]", "file_name:FULINS_C*"],
            "wt": "json",
            "rows": 100,
        },
    )
    r.raise_for_status()
    return firds_files(r.json()["response"]["docs"], "C")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def iter_firds_records(stream) -> Iterator[dict]:
    """One dict per ``RefData`` element of a FIRDS XML stream (ISIN, name, CFI, currency, issuer, venue)."""
    for _, el in ET.iterparse(stream, events=("end",)):
        if _local(el.tag) != "RefData":
            continue
        rec: dict = {}
        for child in el:
            t = _local(child.tag)
            if t == "FinInstrmGnlAttrbts":
                for g in child:
                    gt = _local(g.tag)
                    if gt in ("Id", "FullNm", "ClssfctnTp", "NtnlCcy"):
                        rec[gt] = (g.text or "").strip()
            elif t == "Issr":
                rec["Issr"] = (child.text or "").strip()
            elif t == "TradgVnRltdAttrbts":
                for g in child:
                    if _local(g.tag) == "Id":
                        rec["Venue"] = (g.text or "").strip()
        el.clear()
        if rec.get("Id"):
            yield rec


def parse_firds(path: Path) -> pd.DataFrame:
    """One row per ISIN from a ``FULINS_C`` ZIP (FIRDS lists one record per venue)."""
    by_isin: dict[str, dict] = {}
    with zipfile.ZipFile(path) as z:
        for member in z.namelist():
            if not member.lower().endswith(".xml"):
                continue
            with z.open(member) as fh:
                for rec in iter_firds_records(fh):
                    row = by_isin.get(rec["Id"])
                    if row is None:
                        row = by_isin[rec["Id"]] = {
                            "isin": rec["Id"],
                            "name": rec.get("FullNm") or None,
                            "cfi": rec.get("ClssfctnTp") or None,
                            "currency": rec.get("NtnlCcy") or None,
                            "issuer_lei": rec.get("Issr") or None,
                            "venues": set(),
                        }
                    if rec.get("Venue"):
                        row["venues"].add(rec["Venue"])
    rows = list(by_isin.values())
    for r in rows:
        r["n_venues"] = len(r["venues"])
        r["venues"] = ",".join(sorted(r["venues"])) or None
    df = pd.DataFrame(rows, columns=FIRDS_COLUMNS)
    return df[df["isin"].map(is_valid_isin)].reset_index(drop=True)


# -- ESMA fund registers ----------------------------------------------------


def parse_esma_docs(docs: list[dict], core: str) -> pd.DataFrame:
    rows = []
    for d in docs:
        rows.append(
            {
                "register": core,
                "register_id": str(d.get("id")),
                "name": d.get("funds_national_name"),
                "lei": _lei(d.get("funds_lei")),
                "legal_framework": d.get("funds_legal_framework_name"),
                "domicile": d.get("funds_domicile_cou_code"),
                "authority_country": d.get("funds_ca_cou_code"),
                "manager_name": d.get("funds_manager_nat_name"),
                "manager_lei": _lei(d.get("funds_manager_lei")),
                "manager_country": d.get("funds_manager_cou_code"),
                "status": d.get("funds_status_code_name"),
                "last_update": (d.get("funds_last_update_date") or d.get("timestamp") or "")[:10] or None,
            }
        )
    return pd.DataFrame(rows, columns=ESMA_COLUMNS)


def fetch_esma_register(core: str, rows: int = 1000, max_pages: int | None = None) -> pd.DataFrame:
    """Every fund (Solr parent document) in one ESMA fund register."""
    parts, start, page = [], 0, 0
    while True:
        r = http.get(
            ESMA_SOLR.format(core=core),
            params={"q": "type_s:parent", "fl": ",".join(ESMA_FIELDS), "sort": "id asc", "start": start, "rows": rows, "wt": "json"},
        )
        r.raise_for_status()
        resp = r.json()["response"]
        parts.append(parse_esma_docs(resp["docs"], core))
        start += rows
        page += 1
        if start >= resp["numFound"] or not resp["docs"] or (max_pages and page >= max_pages):
            break
    return pd.concat(parts, ignore_index=True)



# -- regulator fund lists ---------------------------------------------------

#: Columns of the frame :func:`regulator_products` returns.
REGULATOR_COLUMNS = [
    "adapter", "portfolio_id", "isin", "name", "manager_name", "domicile", "currency", "as_of", "credit",
    "licence", "homepage",
]


def _month_end(period: str | None) -> str | None:
    """``"202606"`` -> ``"2026-06-30"``."""
    if not period or not re.fullmatch(r"\d{6}", str(period)):
        return None
    return (pd.Timestamp(f"{period[:4]}-{period[4:]}-01") + pd.offsets.MonthEnd(0)).date().isoformat()


def regulator_products(adapters: Iterable[str], refresh: bool = False) -> pd.DataFrame:
    """Fund ISINs from the regulator adapters already in this project (CNMV Spain, CVM Brazil).

    These are open regulator datasets with full portfolios, so an ISIN found
    here gets ``open_holdings_source`` = the adapter. A store that has not
    been built is skipped unless ``refresh`` is true (then the adapter
    downloads it, as its own pipeline would).
    """
    import importlib

    from .issuers import discover

    registry = discover()
    parts = []
    for name in adapters:
        adapter = registry.get(name)
        if adapter is None:
            logger.warning("Adapter %s is not registered; skipped", name)
            continue
        module = importlib.import_module(f"openbasket.sources.issuers.{name}")
        store = module.store() if hasattr(module, "store") else None
        if store is not None and not store.is_built() and not refresh:
            logger.info("Adapter %s has no local store; skipped (set registry.refresh_regulators)", name)
            continue
        prods = adapter.products()
        if prods is None or not len(prods):
            continue
        period = None
        if store is not None:
            period = store.latest() if hasattr(store, "latest") else (store.available_months() or [None])[-1]
        parts.append(
            pd.DataFrame(
                {
                    "adapter": name,
                    "portfolio_id": prods["portfolio_id"].astype(str),
                    "isin": prods["isin"],
                    "name": prods["fund_name"].astype("string").str.strip(),
                    "manager_name": prods["manager"] if "manager" in prods else None,
                    "domicile": prods["domicile"],
                    "currency": prods["currency"],
                    "as_of": _month_end(period),
                    "credit": adapter.credit,
                    "licence": adapter.licence,
                    "homepage": adapter.homepage,
                }
            )
        )
    if not parts:
        return pd.DataFrame(columns=REGULATOR_COLUMNS)
    out = pd.concat(parts, ignore_index=True)
    out = out[out["isin"].map(is_valid_isin)]
    return out.drop_duplicates("isin")[REGULATOR_COLUMNS].reset_index(drop=True)


# -- merge ------------------------------------------------------------------


def _join(*vals: object) -> str | None:
    out: list[str] = []
    for v in vals:
        if isinstance(v, str):
            for p in v.split(","):
                p = p.strip()
                if p and p not in out:
                    out.append(p)
    return ",".join(out) or None


def _relation(rel: pd.DataFrame, relation: str) -> dict[str, str]:
    """``start_lei -> end_lei`` for one relation (the first end node in sort order when several)."""
    sub = rel[rel["relation"] == relation].sort_values(["start_lei", "end_lei"]).drop_duplicates("start_lei")
    return dict(zip(sub["start_lei"], sub["end_lei"]))


def _str(s: pd.Series) -> pd.Series:
    """Object series with empty strings and NaN as ``None``."""
    s = s.astype(object)
    return s.where(s.map(lambda v: isinstance(v, str) and v.strip() != ""), None)


def _coalesce(frame: pd.DataFrame, options: list[tuple[str, str]]) -> tuple[pd.Series, pd.Series]:
    """First non-empty of ``frame[col]`` over ``options`` (``(col, source)``) and the source it came from."""
    val = pd.Series([None] * len(frame), index=frame.index, dtype=object)
    src = pd.Series([None] * len(frame), index=frame.index, dtype=object)
    for col, source in options:
        if col not in frame:
            continue
        v = _str(frame[col])
        take = val.isna() & v.notna()
        val[take] = v[take]
        src[take] = source
    return val, src


def _banks(frame: pd.DataFrame) -> pd.DataFrame:
    """``bank_group`` and ``bank_group_rule`` from the manager group, the manager and (named banks only) the fund name."""
    cache: dict = {}

    def one(group, manager, fund):
        group = group if isinstance(group, str) else None
        manager = manager if isinstance(manager, str) else None
        if group is None and manager is None:
            if isinstance(fund, str):
                for canonical, rx in _NAMED:
                    if rx.search(fund):
                        return canonical, "fund_name"
            return None, None
        key = (group, manager)
        if key not in cache:
            cache[key] = bank_group(group, manager)
        return cache[key]

    got = [one(g, m, f) for g, m, f in zip(frame["manager_group_name"], frame["manager_name"], frame["name"])]
    return pd.DataFrame(got, index=frame.index, columns=["bank_group", "bank_group_rule"])


def _manager_group(frame: pd.DataFrame, names: dict, ultimate: dict, direct: dict) -> None:
    """Add ``manager_group_*`` from the manager's GLEIF parents (in place)."""
    mgr = frame["manager_lei"]
    up = mgr.map(lambda m: ultimate.get(m) if isinstance(m, str) else None)
    dp = mgr.map(lambda m: direct.get(m) if isinstance(m, str) else None)
    grp = up.where(up.notna(), dp)
    grp = grp.where(grp.notna(), mgr)
    src = pd.Series(None, index=frame.index, dtype=object)
    src[mgr.notna()] = "manager"
    src[dp.notna()] = "gleif_rr_direct_parent"
    src[up.notna()] = "gleif_rr_ultimate_parent"
    gname = grp.map(lambda g: names.get(g) if isinstance(g, str) else None)
    no_grp_name = gname.isna() & (grp == mgr)
    gname[no_grp_name] = frame.loc[no_grp_name, "manager_name"]
    only_name = mgr.isna() & frame["manager_name"].notna()
    src[only_name] = "manager"
    gname[only_name] = frame.loc[only_name, "manager_name"]
    frame["manager_group_lei"] = grp
    frame["manager_group_name"] = gname
    frame["manager_group_source"] = src


def _isin_lei_lookup(firds: pd.DataFrame, isin_lei: pd.DataFrame) -> dict[str, str]:
    """ISIN -> fund LEI: FIRDS issuer first, then GLEIF's ISIN-LEI map."""
    m = dict(zip(isin_lei.sort_values(["isin", "lei"], ascending=[True, False])["isin"], isin_lei.sort_values(["isin", "lei"], ascending=[True, False])["lei"]))
    m.update({i: l for i, l in zip(firds["isin"], firds["issuer_lei"]) if isinstance(l, str)})
    return m


def build_entities(
    entities: pd.DataFrame,
    relationships: pd.DataFrame,
    firds: pd.DataFrame,
    esma: pd.DataFrame,
    isin_lei: pd.DataFrame,
    sec: pd.DataFrame | None,
    regulators: pd.DataFrame | None,
    as_of: dict[str, str | None],
) -> pd.DataFrame:
    """One row per fund: an LEI (GLEIF fund, FIRDS issuer, ESMA or SEC), else an ESMA, SEC or regulator id.

    ``as_of`` maps a source key (``gleif``, ``gleif_isin_lei``, ``esma_firds``,
    ``esma_funds``, ``sec`` and each regulator adapter) to its date.
    """
    regulators = regulators if regulators is not None else pd.DataFrame(columns=REGULATOR_COLUMNS)
    names = dict(zip(entities["lei"], entities["name"]))
    manager = _relation(relationships, "manager")
    umbrella = _relation(relationships, "umbrella")
    master = _relation(relationships, "master")
    ultimate = _relation(relationships, "ultimate_parent")
    direct = _relation(relationships, "direct_parent")

    esma = esma.copy()
    esma["_rank"] = esma["register"].map({"esma_registers_funds_cbdif": 0, "esma_registers_funds": 1}).fillna(2)
    esma = esma.sort_values(["_rank", "register_id"])
    esma_lei = esma[esma["lei"].notna()].drop_duplicates("lei").set_index("lei")

    sec_by_lei = pd.DataFrame(columns=["series_id", "series_name", "tickers", "has_nport"])
    if sec is not None and len(sec):
        sec = sec.assign(series_lei=sec["series_lei"].map(_lei))
        s = sec[sec["series_lei"].notna()]
        sec_by_lei = s.groupby("series_lei").agg(
            series_id=("series_id", lambda v: _join(*sorted(set(v.dropna())))),
            series_name=("series_name", "first"),
            tickers=("ticker", lambda v: _join(*sorted(set(v.dropna())))),
            has_nport=("has_nport", lambda v: bool(v.fillna(False).astype(bool).any())),
        )

    lookup = _isin_lei_lookup(firds, isin_lei)
    regs = regulators.copy()
    regs["lei"] = regs["isin"].map(lookup)
    reg_by_lei = regs[regs["lei"].notna()].sort_values(["adapter", "isin"]).drop_duplicates("lei").set_index("lei")

    ent = entities.set_index("lei")
    leis = set(ent.index[ent["category"] == "FUND"]) | set(firds["issuer_lei"].dropna()) | set(esma_lei.index)
    leis |= set(sec_by_lei.index) | set(reg_by_lei.index)
    base = pd.DataFrame(index=pd.Index(sorted(leis), name="lei"))
    base = base.join(ent).join(esma_lei.add_prefix("esma_")).join(sec_by_lei.add_prefix("sec_")).join(reg_by_lei[["adapter", "manager_name", "name"]].add_prefix("reg_"))
    firds_name = firds.dropna(subset=["issuer_lei"]).drop_duplicates("issuer_lei").set_index("issuer_lei")["name"]
    base["firds_name"] = firds_name.reindex(base.index)
    base = base.reset_index()

    out = pd.DataFrame({"fund_id": base["lei"], "lei": base["lei"]})
    out["name"], out["name_source"] = _coalesce(
        base, [("name", "gleif"), ("esma_name", "esma_funds"), ("sec_series_name", "sec"), ("firds_name", "esma_firds"), ("reg_name", "regulator")]
    )
    out.loc[out["name_source"] == "regulator", "name_source"] = base["reg_adapter"]
    out["category"] = base["category"]
    lf = base["legal_form_code"].where(~base["legal_form_code"].isin(["8888", "9999"]), None)
    out["legal_form"] = [_join(a, b) for a, b in zip(lf, base["legal_form_other"])]
    out["legal_framework"] = base["esma_legal_framework"]
    out["legal_framework_source"] = base["esma_legal_framework"].notna().map({True: "esma_funds", False: None})
    out["jurisdiction"] = base["jurisdiction"]
    jur2 = _str(base["jurisdiction"]).str[:2]
    out["domicile"], out["domicile_source"] = _coalesce(
        base.assign(_jur=jur2, _sec=base["sec_series_id"].notna().map({True: "US", False: None})),
        [
            ("_jur", "gleif_jurisdiction"),
            ("esma_domicile", "esma_funds"),
            ("legal_country", "gleif_legal_address"),
            ("esma_authority_country", "esma_funds_authority"),
            ("_sec", "sec"),
        ],
    )
    out["entity_status"] = base["entity_status"]

    lei = base["lei"]
    umb = lei.map(umbrella)
    m_rr = lei.map(manager)
    m_umb = umb.map(lambda u: manager.get(u) if isinstance(u, str) else None)
    fam = base["associated_lei"].where(base["associated_type"] == "FUND_FAMILY", None)
    tmp = pd.DataFrame({"a": m_rr, "b": m_umb, "c": base["esma_manager_lei"], "d": fam})
    out["manager_lei"], out["manager_source"] = _coalesce(
        tmp, [("a", "gleif_rr"), ("b", "gleif_rr_umbrella"), ("c", "esma_funds"), ("d", "gleif_associated_entity")]
    )
    mname = out["manager_lei"].map(lambda m: names.get(m) if isinstance(m, str) else None)
    esma_name_ok = out["manager_lei"].isna() | (out["manager_lei"] == base["esma_manager_lei"])
    fb = pd.DataFrame({"g": mname, "e": base["esma_manager_name"].where(esma_name_ok, None), "r": base["reg_manager_name"].where(out["manager_lei"].isna(), None)})
    out["manager_name"], name_src = _coalesce(fb, [("g", "gleif"), ("e", "esma_funds"), ("r", "regulator")])
    no_lei_name = out["manager_lei"].isna() & out["manager_name"].notna()
    out.loc[no_lei_name, "manager_source"] = name_src[no_lei_name]
    out.loc[out["manager_source"] == "regulator", "manager_source"] = base["reg_adapter"]
    _manager_group(out, names, ultimate, direct)
    out["umbrella_lei"] = umb
    out["umbrella_name"] = umb.map(lambda u: names.get(u) if isinstance(u, str) else None)
    out["umbrella_source"] = umb.notna().map({True: "gleif_rr", False: None})
    out["master_lei"] = lei.map(master)
    out["master_name"] = out["master_lei"].map(lambda u: names.get(u) if isinstance(u, str) else None)
    out["esma_register_id"] = [f"{r}:{i}" if isinstance(r, str) else None for r, i in zip(base["esma_register"], base["esma_register_id"])]
    out["sec_series_id"] = base["sec_series_id"]
    out["sec_tickers"] = base["sec_tickers"]
    n_isins = pd.concat([isin_lei[["isin", "lei"]], firds[["isin", "issuer_lei"]].rename(columns={"issuer_lei": "lei"}), regs[["isin", "lei"]]]).dropna().drop_duplicates()
    out["n_isins"] = lei.map(n_isins.groupby("lei")["isin"].nunique()).fillna(0).astype(int)
    ohs = base["reg_adapter"].astype(object)
    ohs = ohs.where(ohs.notna(), base["sec_has_nport"].map(lambda v: "sec_nport" if v is True else None))
    out["open_holdings_source"] = ohs
    firds_leis, map_leis = set(firds["issuer_lei"].dropna()), set(isin_lei["lei"])
    flags = [
        ("gleif", base["category"].notna() | base["name"].notna()),
        ("esma_firds", lei.isin(firds_leis)),
        ("esma_funds", base["esma_register"].notna()),
        ("sec", base["sec_series_id"].notna()),
        ("gleif_isin_lei", lei.isin(map_leis)),
    ]
    cols = [f.to_numpy(dtype=bool) for _, f in flags]
    keys = [k for k, _ in flags]
    out["listed_in"] = [
        ",".join([k for k, v in zip(keys, row) if v] + ([a] if isinstance(a, str) else []))
        for row, a in zip(zip(*cols), base["reg_adapter"])
    ] if len(base) else []

    extra = [out]

    # ESMA register funds without an LEI.
    e = esma[esma["lei"].isna()]
    # A fund without an LEI can sit in both registers: keep one record per name, domicile and manager.
    key = pd.DataFrame(
        {
            "n": e["name"].fillna("").str.casefold().str.replace(r"\W+", " ", regex=True).str.strip(),
            "d": e["domicile"].fillna(e["authority_country"]).fillna(""),
            "m": e["manager_lei"].fillna(e["manager_name"].fillna("").str.casefold()),
        }
    )
    e = e[~key.duplicated()]
    if len(e):
        x = pd.DataFrame(
            {
                "fund_id": e["register"] + ":" + e["register_id"],
                "name": e["name"],
                "name_source": "esma_funds",
                "legal_framework": e["legal_framework"],
                "legal_framework_source": "esma_funds",
                "domicile": e["domicile"].where(e["domicile"].notna(), e["authority_country"]),
                "domicile_source": e["domicile"].notna().map({True: "esma_funds", False: "esma_funds_authority"}).where(
                    e["domicile"].notna() | e["authority_country"].notna(), None
                ),
                "manager_lei": e["manager_lei"],
                "esma_register_id": e["register"] + ":" + e["register_id"],
                "n_isins": 0,
                "listed_in": "esma_funds",
            }
        ).reset_index(drop=True)
        mn = x["manager_lei"].map(lambda m: names.get(m) if isinstance(m, str) else None)
        x["manager_name"] = mn.where(mn.notna(), e["manager_name"].reset_index(drop=True))
        x["manager_source"] = (x["manager_lei"].notna() | x["manager_name"].notna()).map({True: "esma_funds", False: None})
        _manager_group(x, names, ultimate, direct)
        extra.append(x)

    # SEC series without an LEI.
    if sec is not None and len(sec):
        s = sec[sec["series_lei"].isna() & sec["series_id"].notna()]
        if len(s):
            g = s.groupby("series_id").agg(
                name=("series_name", "first"),
                sec_tickers=("ticker", lambda v: _join(*sorted(set(v.dropna())))),
                has_nport=("has_nport", lambda v: bool(v.fillna(False).astype(bool).any())),
            ).reset_index()
            extra.append(
                pd.DataFrame(
                    {
                        "fund_id": "sec:" + g["series_id"],
                        "name": g["name"],
                        "name_source": g["name"].notna().map({True: "sec", False: None}),
                        "domicile": "US",
                        "domicile_source": "sec",
                        "sec_series_id": g["series_id"],
                        "sec_tickers": g["sec_tickers"],
                        "n_isins": 0,
                        "open_holdings_source": g["has_nport"].map({True: "sec_nport", False: None}),
                        "listed_in": "sec",
                    }
                )
            )

    # Regulator funds none of whose ISINs has an LEI.
    r = regs[regs["lei"].isna()]
    if len(r):
        linked = set(regs.loc[regs["lei"].notna(), "adapter"] + ":" + regs.loc[regs["lei"].notna(), "portfolio_id"])
        r = r.assign(fund_id=r["adapter"] + ":" + r["portfolio_id"])
        r = r[~r["fund_id"].isin(linked)]
        g = r.sort_values("isin").groupby("fund_id").agg(
            adapter=("adapter", "first"), name=("name", "first"), manager_name=("manager_name", "first"),
            domicile=("domicile", "first"), n_isins=("isin", "nunique"),
        ).reset_index()
        x = pd.DataFrame(
            {
                "fund_id": g["fund_id"],
                "name": g["name"],
                "name_source": g["adapter"],
                "domicile": g["domicile"],
                "domicile_source": g["adapter"],
                "manager_lei": None,
                "manager_name": _str(g["manager_name"]),
                "n_isins": g["n_isins"],
                "open_holdings_source": g["adapter"],
                "listed_in": g["adapter"],
            }
        )
        x["manager_source"] = g["adapter"].where(x["manager_name"].notna(), None)
        _manager_group(x, names, ultimate, direct)
        extra.append(x)

    allf = pd.concat(extra, ignore_index=True)
    for c in FUND_ENTITY_COLUMNS:
        if c not in allf:
            allf[c] = None
    banks = _banks(allf)
    allf["bank_group"], allf["bank_group_rule"] = banks["bank_group"], banks["bank_group_rule"]
    _cite(allf, as_of, _regulator_terms(regulators))
    return allf[FUND_ENTITY_COLUMNS].reset_index(drop=True)


def _regulator_terms(regulators: pd.DataFrame | None) -> dict[str, tuple[str, str, str]]:
    if regulators is None or not len(regulators):
        return {}
    first = regulators.drop_duplicates("adapter").set_index("adapter")
    return {a: (row["credit"], row["licence"], row["homepage"]) for a, row in first.iterrows()}


_HOMES = {
    "gleif": GLEIF_HOME,
    "gleif_isin_lei": "https://www.gleif.org/en/lei-data/lei-mapping/download-isin-to-lei-relationship-files",
    "esma_firds": ESMA_FIRDS_HOME,
    "esma_funds": ESMA_REGISTER_HOME.format(core="esma_registers_funds_cbdif"),
    "sec": SEC_HOME,
}


def _cite(df: pd.DataFrame, as_of: dict[str, str | None], extra: dict[str, tuple[str, str, str]] | None = None) -> None:
    """Fill ``source``, ``source_url``, ``credit``, ``licence`` and ``as_of`` from ``listed_in`` (in place).

    ``licence`` holds the link to each source's terms; the quoted terms are in
    :data:`LICENCES` and on the docs page.
    """
    extra = extra or {}
    cache: dict = {}

    def cite(listed: object) -> tuple:
        if listed not in cache:
            srcs = [s for s in str(listed or "").split(",") if s]
            dates = [as_of.get(s) for s in srcs if as_of.get(s)]
            credits, terms, homes = [], [], []
            for s in srcs:
                if s in LICENCES:
                    credits.append(LICENCES[s][0])
                    terms.append(LICENCES[s][2])
                    homes.append(_HOMES[s])
                elif s in extra:
                    credits.append(extra[s][0])
                    terms.append(extra[s][1])
                    homes.append(extra[s][2])
            cache[listed] = (
                ",".join(srcs) or None,
                " ".join(dict.fromkeys(homes)) or None,
                "; ".join(dict.fromkeys(credits)) or None,
                " | ".join(dict.fromkeys(terms)) or None,
                max(dates) if dates else None,
            )
        return cache[listed]

    cites = [cite(v) for v in df["listed_in"]]
    for i, col in enumerate(["source", "source_url", "credit", "licence", "as_of"]):
        df[col] = [c[i] for c in cites]


def build_registry(
    fund_entities: pd.DataFrame,
    firds: pd.DataFrame,
    isin_lei: pd.DataFrame,
    regulators: pd.DataFrame | None,
    as_of: dict[str, str | None],
) -> pd.DataFrame:
    """One row per fund ISIN: FIRDS ``FULINS_C``, GLEIF ISIN-LEI pairs of fund LEIs and regulator fund lists."""
    regulators = regulators if regulators is not None else pd.DataFrame(columns=REGULATOR_COLUMNS)
    fund_leis = set(fund_entities["lei"].dropna())
    f = firds.rename(columns={"issuer_lei": "firds_lei", "name": "firds_name"})
    g = isin_lei[isin_lei["lei"].isin(fund_leis)].sort_values(["isin", "lei"], ascending=[True, False]).drop_duplicates("isin")
    g = g.rename(columns={"lei": "map_lei"})
    r = regulators.rename(columns={"name": "reg_name", "manager_name": "reg_manager", "domicile": "reg_domicile", "currency": "reg_currency", "as_of": "reg_as_of"})
    r = r[["isin", "adapter", "portfolio_id", "reg_name", "reg_currency"]]
    df = f.merge(g, on="isin", how="outer").merge(r, on="isin", how="outer")
    df["lei"] = df["firds_lei"].where(df["firds_lei"].notna(), df["map_lei"])
    df["lei_source"] = None
    df.loc[df["map_lei"].notna(), "lei_source"] = "gleif_isin_lei"
    df.loc[df["firds_lei"].notna(), "lei_source"] = "esma_firds"
    df["fund_id"] = df["lei"]
    no_lei = df["lei"].isna() & df["adapter"].notna()
    df.loc[no_lei, "fund_id"] = df.loc[no_lei, "adapter"] + ":" + df.loc[no_lei, "portfolio_id"]

    ent = fund_entities.drop_duplicates("fund_id").set_index("fund_id")
    keep = [c for c in ent.columns if c not in ("lei", "n_isins", "source", "source_url", "credit", "licence", "as_of", "listed_in", "name", "name_source")]
    df = df.join(ent[keep + ["listed_in", "name", "name_source"]].rename(columns={"listed_in": "ent_listed", "name": "ent_name", "name_source": "ent_name_source"}), on="fund_id")

    df["name"], df["name_source"] = _coalesce(df, [("firds_name", "esma_firds"), ("reg_name", "regulator"), ("ent_name", "entity")])
    df.loc[df["name_source"] == "regulator", "name_source"] = df["adapter"]
    df.loc[df["name_source"] == "entity", "name_source"] = df["ent_name_source"]
    df["currency"], df["currency_source"] = _coalesce(df, [("currency", "esma_firds"), ("reg_currency", "regulator")])
    df.loc[df["currency_source"] == "regulator", "currency_source"] = df["adapter"]
    df["cfi_source"] = df["cfi"].notna().map({True: "esma_firds", False: None})

    prefix = df["isin"].str[:2]
    need = df["domicile"].isna() & ~prefix.isin(["XS", "EU", "XD", "XA", "XB", "XC", "QS"])
    df.loc[need, "domicile"] = prefix[need]
    df.loc[need, "domicile_source"] = "isin_prefix"
    df["open_holdings_source"] = df["adapter"].where(df["adapter"].notna(), df["open_holdings_source"])

    listed = []
    for el, fl, ml, ad in zip(df["ent_listed"], df["firds_lei"].notna(), df["map_lei"].notna(), df["adapter"]):
        parts = [p for p in str(el or "").split(",") if p and p not in ("esma_firds", "gleif_isin_lei", "nan")]
        if isinstance(ad, str):
            parts = [p for p in parts if p != ad]
        if fl:
            parts.append("esma_firds")
        if ml:
            parts.append("gleif_isin_lei")
        if isinstance(ad, str):
            parts.append(ad)
        listed.append(",".join(dict.fromkeys(parts)))
    df["listed_in"] = listed
    df["fund_status"] = df["entity_status"]
    for c in REGISTRY_COLUMNS:
        if c not in df:
            df[c] = None
    _cite(df, as_of, _regulator_terms(regulators))
    return df[REGISTRY_COLUMNS].sort_values("isin").reset_index(drop=True)


def route(isin: str, fund_registry: pd.DataFrame) -> dict:
    """What the registry knows about one ISIN, with a plain status line for the site."""
    row = fund_registry[fund_registry["isin"] == isin]
    if row.empty:
        return {"isin": isin, "status": "unknown"}
    out = row.iloc[0].to_dict()
    src = out.get("open_holdings_source")
    out["status"] = f"open holdings source: {src}" if isinstance(src, str) and src else "known fund, no open holdings source"
    return out


def coverage(fund_entities: pd.DataFrame, fund_registry: pd.DataFrame, top: int = 500) -> pd.DataFrame:
    """Funds and ISINs per manager group (the management company's GLEIF parent), with bank flag and domiciles."""
    ent = fund_entities.assign(group=fund_entities["manager_group_name"].fillna("(manager unknown)"))
    reg = fund_registry.assign(group=fund_registry["manager_group_name"].fillna("(manager unknown)"))
    funds = ent.groupby("group").agg(
        funds=("fund_id", "nunique"),
        funds_with_lei=("lei", "nunique"),
        funds_with_open_holdings=("open_holdings_source", lambda v: int(v.notna().sum())),
        group_lei=("manager_group_lei", "first"),
    )
    # The group's own name decides whether it is a bank group here.
    flags = [bank_group(g) if g != "(manager unknown)" else (None, None) for g in funds.index]
    funds["bank_group"] = [f[0] for f in flags]
    funds["bank_group_rule"] = [f[1] for f in flags]
    isins = reg.groupby("group").agg(isins=("isin", "nunique"))
    d = ent.dropna(subset=["domicile"]).groupby(["group", "domicile"]).size().reset_index(name="n")
    d = d.sort_values(["group", "n"], ascending=[True, False])
    dom = d.groupby("group").apply(lambda x: ", ".join(f"{a} {b}" for a, b in zip(x["domicile"].head(8), x["n"].head(8))), include_groups=False).rename("domiciles")
    out = funds.join(isins).join(dom).reset_index()
    out["isins"] = out["isins"].fillna(0).astype(int)
    return out.sort_values("funds", ascending=False).head(top).reset_index(drop=True)


def bank_coverage(fund_entities: pd.DataFrame, fund_registry: pd.DataFrame) -> pd.DataFrame:
    """Funds and ISINs per bank group, with how many have an open holdings source."""
    ent = fund_entities[fund_entities["bank_group"].notna()]
    reg = fund_registry[fund_registry["bank_group"].notna()]
    a = ent.groupby("bank_group").agg(
        rule=("bank_group_rule", lambda v: v.mode().iloc[0] if v.notna().any() else None),
        funds=("fund_id", "nunique"),
        funds_with_open_holdings=("open_holdings_source", lambda v: int(v.notna().sum())),
        open_holdings_sources=("open_holdings_source", lambda v: _join(*sorted(set(v.dropna())))),
    )
    b = reg.groupby("bank_group").agg(isins=("isin", "nunique"))
    d = ent.dropna(subset=["domicile"]).groupby(["bank_group", "domicile"]).size().reset_index(name="n")
    d = d.sort_values(["bank_group", "n"], ascending=[True, False])
    dom = d.groupby("bank_group").apply(lambda x: ", ".join(f"{p} {q}" for p, q in zip(x["domicile"].head(8), x["n"].head(8))), include_groups=False).rename("domiciles")
    out = a.join(b).join(dom).reset_index()
    out["isins"] = out["isins"].fillna(0).astype(int)
    return out.sort_values("funds", ascending=False).reset_index(drop=True)


def domicile_coverage(fund_entities: pd.DataFrame, fund_registry: pd.DataFrame) -> pd.DataFrame:
    """Funds and ISINs per domicile."""
    a = fund_entities.groupby(fund_entities["domicile"].fillna("(unknown)")).agg(
        funds=("fund_id", "nunique"),
        bank_run_funds=("bank_group", lambda v: int(v.notna().sum())),
        funds_with_open_holdings=("open_holdings_source", lambda v: int(v.notna().sum())),
    )
    b = fund_registry.groupby(fund_registry["domicile"].fillna("(unknown)")).agg(isins=("isin", "nunique"))
    out = a.join(b, how="outer").fillna(0).astype(int).reset_index(names="domicile")
    return out.sort_values("funds", ascending=False).reset_index(drop=True)


def summarise(fund_entities: pd.DataFrame, fund_registry: pd.DataFrame) -> dict:
    """Headline counts for the docs page and logs."""
    ent, reg = fund_entities, fund_registry
    return {
        "fund_entities": int(len(ent)),
        "fund_entities_with_lei": int(ent["lei"].notna().sum()),
        "gleif_fund_category": int((ent["category"] == "FUND").sum()),
        "active_gleif_funds": int(((ent["category"] == "FUND") & (ent["entity_status"] == "ACTIVE")).sum()),
        "with_manager": int((ent["manager_lei"].notna() | ent["manager_name"].notna()).sum()),
        "with_manager_lei": int(ent["manager_lei"].notna().sum()),
        "with_umbrella": int(ent["umbrella_lei"].notna().sum()),
        "bank_run_funds": int(ent["bank_group"].notna().sum()),
        "funds_with_open_holdings": int(ent["open_holdings_source"].notna().sum()),
        "fund_isins": int(len(reg)),
        "isins_with_lei": int(reg["lei"].notna().sum()),
        "isins_with_manager": int(reg["manager_name"].notna().sum()),
        "isins_bank_run": int(reg["bank_group"].notna().sum()),
        "isins_with_cfi": int(reg["cfi"].notna().sum()),
        "isins_with_open_holdings": int(reg["open_holdings_source"].notna().sum()),
    }


def read_optional_parquet(path: str | None) -> pd.DataFrame | None:
    """Read ``path`` if it exists (the SEC ``fund_master`` is built by another pipeline)."""
    if not path or not Path(path).exists():
        return None
    return pd.read_parquet(path)
