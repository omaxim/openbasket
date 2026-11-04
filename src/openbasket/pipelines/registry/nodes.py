"""Nodes for the ``registry`` pipeline: a fund registry from GLEIF, ESMA and regulator open data."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from ...sources import gleif_fund_registry as reg

logger = logging.getLogger(__name__)


def _with_meta(df: pd.DataFrame, as_of: str | None, url: str) -> pd.DataFrame:
    return df.assign(as_of=as_of, source_url=url)


def fetch_firds(params: dict) -> pd.DataFrame:
    """ESMA FIRDS ``FULINS_C`` (fund units on EU venues), one row per ISIN."""
    if not params.get("sources", {}).get("esma_firds", True):
        return _with_meta(pd.DataFrame(columns=reg.FIRDS_COLUMNS), None, "")
    files = reg.latest_firds_c()
    if not files:
        raise RuntimeError("no FULINS_C file listed by the ESMA FIRDS index")
    folder = Path(params["root"]) / "esma_firds"
    paths, frames = [], []
    for f in files:
        p = reg.download(f["download_link"], folder / f["file_name"])
        paths.append(p)
        frames.append(reg.parse_firds(p))
    reg.prune(folder, paths, "FULINS_C_*.zip")
    df = pd.concat(frames, ignore_index=True).drop_duplicates("isin")
    logger.info("FIRDS FULINS_C: %d fund ISINs", len(df))
    return _with_meta(df, files[0]["publication_date"][:10], " ".join(f["download_link"] for f in files))


def empty_firds() -> pd.DataFrame:
    """What :func:`fetch_firds` returns with the source turned off."""
    return _with_meta(pd.DataFrame(columns=reg.FIRDS_COLUMNS), None, "")


def empty_esma_funds() -> pd.DataFrame:
    """What :func:`fetch_esma_funds` returns with the source turned off."""
    return _with_meta(pd.DataFrame(columns=reg.ESMA_COLUMNS), None, "")


def fetch_relationships(params: dict) -> pd.DataFrame:
    """GLEIF Level 2 relationships: fund manager, umbrella, master, parents."""
    gc = reg.latest_golden_copy()
    folder = Path(params["root"]) / "gleif"
    p = reg.download(gc["rr"], folder / f"{gc['publish_date']}-rr.csv.zip")
    reg.prune(folder, [p], "*-rr.csv.zip")
    df = reg.parse_relationships(p)
    logger.info("GLEIF relationships kept: %s", df["relation"].value_counts().to_dict())
    return _with_meta(df, gc["publish_date"], gc["rr"])


def fetch_esma_funds(params: dict) -> pd.DataFrame:
    """ESMA's cross-border UCITS/AIF database and AIF register, one row per fund record."""
    cores = params.get("esma_cores") or list(reg.ESMA_FUND_CORES)
    if not params.get("sources", {}).get("esma_funds", True):
        return _with_meta(pd.DataFrame(columns=reg.ESMA_COLUMNS), None, "")
    frames = [reg.fetch_esma_register(c, max_pages=params.get("esma_max_pages")) for c in cores]
    df = pd.concat(frames, ignore_index=True)
    logger.info("ESMA fund registers: %s", df["register"].value_counts().to_dict())
    as_of = df["last_update"].dropna().max() if len(df) else None
    return _with_meta(df, as_of, " ".join(reg.ESMA_REGISTER_HOME.format(core=c) for c in cores))


def fetch_entities(relationships: pd.DataFrame, firds: pd.DataFrame, esma: pd.DataFrame, params: dict) -> pd.DataFrame:
    """GLEIF Level 1 records of every fund and of every manager, umbrella and parent they name."""
    gc = reg.latest_golden_copy()
    folder = Path(params["root"]) / "gleif"
    p = reg.download(gc["lei2"], folder / f"{gc['publish_date']}-lei2.csv.zip", timeout=1800)
    reg.prune(folder, [p], "*-lei2.csv.zip")
    keep = set(relationships["end_lei"]) | set(firds["issuer_lei"].dropna())
    keep |= set(esma["lei"].dropna()) | set(esma["manager_lei"].dropna())
    df = reg.parse_entities(p, keep)
    logger.info("GLEIF records kept: %s", df["category"].value_counts().to_dict())
    return _with_meta(df, gc["publish_date"], gc["lei2"])


def fetch_isin_lei(entities: pd.DataFrame, firds: pd.DataFrame, params: dict) -> pd.DataFrame:
    """GLEIF ISIN-LEI pairs whose LEI is a fund (GLEIF category FUND or a FIRDS fund issuer)."""
    meta = reg.latest_isin_lei()
    folder = Path(params["root"]) / "gleif_isin_lei"
    p = reg.download(meta["url"], folder / meta["file_name"], timeout=1800)
    reg.prune(folder, [p], "isin-lei-*.zip")
    leis = set(entities.loc[entities["category"] == "FUND", "lei"]) | set(firds["issuer_lei"].dropna())
    df = reg.parse_isin_lei(p, leis)
    logger.info("GLEIF ISIN-LEI: %d fund ISINs", len(df))
    return _with_meta(df, meta["as_of"], meta["url"])


def load_regulators(params: dict) -> pd.DataFrame:
    """Fund ISINs from the regulator adapters (CNMV Spain, CVM Brazil) whose local store is built."""
    df = reg.regulator_products(params.get("regulator_adapters") or [], refresh=bool(params.get("refresh_regulators")))
    logger.info("Regulator fund ISINs: %s", df["adapter"].value_counts().to_dict() if len(df) else {})
    return df


def _as_of(frame: pd.DataFrame) -> str | None:
    if "as_of" not in frame or not len(frame):
        return None
    v = frame["as_of"].dropna()
    return str(v.max()) if len(v) else None


def build(
    entities: pd.DataFrame,
    relationships: pd.DataFrame,
    firds: pd.DataFrame,
    esma: pd.DataFrame,
    isin_lei: pd.DataFrame,
    regulators: pd.DataFrame,
    params: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``fund_entities`` (one row per fund) and ``fund_registry`` (one row per fund ISIN)."""
    sec = reg.read_optional_parquet(params.get("sec_fund_master"))
    as_of = {
        "gleif": _as_of(entities),
        "gleif_isin_lei": _as_of(isin_lei),
        "esma_firds": _as_of(firds),
        "esma_funds": _as_of(esma),
        "sec": None if sec is None or "report_date" not in sec else str(pd.to_datetime(sec["report_date"]).max().date()),
    }
    if len(regulators):
        for a, g in regulators.groupby("adapter"):
            as_of[a] = _as_of(g)
    ents = reg.build_entities(entities, relationships, firds, esma, isin_lei, sec, regulators, as_of)
    registry = reg.build_registry(ents, firds, isin_lei, regulators, as_of)
    logger.info("Registry: %s", reg.summarise(ents, registry))
    return ents, registry


def report(fund_entities: pd.DataFrame, fund_registry: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Coverage by manager group, by bank group and by domicile, and headline counts."""
    return (
        reg.coverage(fund_entities, fund_registry),
        reg.bank_coverage(fund_entities, fund_registry),
        reg.domicile_coverage(fund_entities, fund_registry),
        reg.summarise(fund_entities, fund_registry),
    )
