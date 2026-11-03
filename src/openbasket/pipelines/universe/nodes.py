"""Look through every fund openbasket knows, in one batch.

The universe is every fund in the issuer lists (iShares, SPDR) plus every
N-PORT series with a latest filing. A US ETF that is both is one fund, keyed
by the issuer's ISIN, with the series kept so N-PORT can still serve as a
fallback. Share classes of one portfolio are aliases of one fund.

Funds are expanded one at a time with the same engine and providers as the
``lookthrough`` pipeline. Unlike its output, a constituent row here is one
ISIN from one source through one fund, so every row's source is exact. A fund that raises is recorded with its error and
the batch moves on. Constituent rows go to Parquet parts on disk as they are
made, so memory stays flat however many funds there are.
"""

from __future__ import annotations

import logging
import shutil
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ... import throttle
from ...datasets import connection
from ...identifiers import normalise_ticker
from ...lookthrough import FundRef, look_through
from ...providers import edgar_url, issuer_id
from ...sources.openfigi import OpenFIGI, best_listing
from ...sources.tickerinside_source import issuer_tickers
from ..lookthrough.nodes import aggregate, make_providers

logger = logging.getLogger(__name__)

UNIVERSE_SOURCES = ("ishares", "spdr", "nport")

UNIVERSE_COLUMNS = [
    "id", "isin", "isins", "series_id", "issuer_id", "name", "ticker", "issuer", "universe_source",
    "series_match", "product_page_url", "cik",
]

_STR = pa.string()
#: Columns of the constituent parts and of ``universe_constituents`` before OpenFIGI fields.
PART_SCHEMA = pa.schema(
    [
        ("fund_id", _STR), ("constituent_isin", _STR), ("name", _STR), ("weight_pct", pa.float64()),
        ("asset_class", _STR), ("source", _STR), ("source_ref", _STR), ("source_url", _STR), ("as_of", _STR),
        ("issuer_name", _STR), ("issuer_lei", _STR), ("title", _STR), ("ticker", _STR), ("cusip", _STR),
        ("isin_source", _STR), ("depth", pa.int32()), ("n_paths", pa.int32()), ("via_fund", _STR), ("note", _STR),
    ]
)

FUND_COLUMNS = [
    "id", "isin", "isins", "series_id", "issuer_id", "name", "ticker", "issuer", "universe_source", "series_match",
    "source", "source_url", "source_ref", "credit", "as_of", "matched_via", "note", "sources_used", "sources_tried",
    "reported_total_pct", "resolved_pct", "unresolved_pct", "unidentified_lines_pct", "n_constituents",
    "error", "traceback", "warnings", "seconds",
]


# -- the universe -----------------------------------------------------------


def _str(v: object) -> str | None:
    return v if isinstance(v, str) and v.strip() else None


def _issuer_funds(issuer_products: pd.DataFrame, issuer_names: dict) -> list[dict]:
    """One fund per issuer portfolio; its share-class ISINs, largest first, are the aliases."""
    ip = issuer_products.copy()
    if "issuer" not in ip:
        ip["issuer"] = "ishares"
    ip["issuer"] = ip["issuer"].fillna("ishares")
    for col in ("total_net_assets", "product_page_url", "ticker"):
        if col not in ip:
            ip[col] = None
    ip["issuer_id"] = [issuer_id(i, s, p) for i, s, p in zip(ip["issuer"], ip["site"], ip["portfolio_id"])]
    ip = ip.dropna(subset=["isin"]).sort_values(["total_net_assets", "isin"], ascending=[False, True], na_position="last")
    funds = []
    for iid, g in ip.groupby("issuer_id", sort=False):
        first = g.iloc[0]
        isins = list(dict.fromkeys(g["isin"]))
        funds.append(
            {
                "id": isins[0], "isin": isins[0], "isins": isins, "series_id": None, "issuer_id": iid,
                "name": _str(first["fund_name"]), "ticker": _str(first["ticker"]),
                "issuer": issuer_names.get(first["issuer"], first["issuer"]), "universe_source": first["issuer"],
                "series_match": None, "product_page_url": _str(first["product_page_url"]), "cik": None,
                "_site": first["site"],
            }
        )
    return funds


def _series_isins(security_master: duckdb.DuckDBPyRelation, leis: list[str]) -> pd.DataFrame:
    """ISINs other funds hold whose issuer LEI is a series' LEI: the series' share classes.

    Only registered-fund issuers count, so a closed-end fund's notes are not
    taken for its shares.
    """
    con = connection()
    con.register("_universe_leis", pd.DataFrame({"lei": leis}, dtype=object))
    con.register("_universe_sm", security_master)
    try:
        return con.execute(
            "select isin, issuer_lei, n_positions from _universe_sm "
            "where issuer_type = 'RF' and issuer_lei in (select lei from _universe_leis) "
            "order by n_positions desc, isin"
        ).df()
    finally:
        con.unregister("_universe_sm")
        con.unregister("_universe_leis")


def _series_funds(fund_master: pd.DataFrame, security_master: duckdb.DuckDBPyRelation) -> list[dict]:
    fm = fund_master[fund_master["has_nport"].fillna(False).astype(bool)] if "has_nport" in fund_master else fund_master
    fm = fm.dropna(subset=["series_id"])
    leis = fm["series_lei"].dropna().unique().tolist()
    sm = _series_isins(security_master, leis)
    isins_by_lei = sm.groupby("issuer_lei", sort=False)["isin"].apply(list).to_dict()
    funds = []
    for sid, g in fm.groupby("series_id", sort=True):
        first = g.iloc[0]
        tickers = [t for t in dict.fromkeys(g["ticker"].dropna()) if _str(t)]
        lei = _str(first.get("series_lei"))
        funds.append(
            {
                "id": sid, "isin": None, "isins": list(isins_by_lei.get(lei, [])) if lei else [], "series_id": sid,
                "issuer_id": None, "name": _str(first.get("series_name")), "ticker": tickers[0] if tickers else None,
                "issuer": None, "universe_source": "nport", "series_match": None, "product_page_url": None,
                "cik": _str(first.get("cik")), "_tickers": tickers,
            }
        )
    return funds


def build_universe(
    issuer_products: pd.DataFrame,
    fund_master: pd.DataFrame,
    security_master: duckdb.DuckDBPyRelation,
    params: dict,
) -> pd.DataFrame:
    """Every known fund, one row each, with every ISIN that names it in ``isins``.

    A series joins an issuer fund when one of its share-class ISINs is the
    issuer fund's ISIN, or, for US issuer sites, when one of its tickers is
    the issuer fund's ticker. An ISIN belongs to one fund only: issuer funds
    claim theirs first.
    """
    issuer = _issuer_funds(issuer_products, params.get("issuers") or {})
    series = _series_funds(fund_master, security_master)
    by_isin = {i: f for f in issuer for i in f["isins"]}
    us_by_ticker = {}
    for f in issuer:
        if f["_site"] == "us" and f["ticker"]:
            us_by_ticker.setdefault(normalise_ticker(f["ticker"]), f)

    rest = []
    for s in series:
        target = next((by_isin[i] for i in s["isins"] if i in by_isin and by_isin[i]["series_id"] is None), None)
        how = "isin"
        if target is None:
            how = "ticker"
            target = next(
                (us_by_ticker[t] for t in map(normalise_ticker, s["_tickers"])
                 if t in us_by_ticker and us_by_ticker[t]["series_id"] is None),
                None,
            )
        if target is None:
            rest.append(s)
            continue
        target.update(series_id=s["series_id"], series_match=how, cik=s["cik"])
        target["_series_isins"] = s["isins"]

    claimed = set(by_isin)
    for f in issuer:
        extra = [i for i in f.pop("_series_isins", []) if i not in claimed]
        claimed.update(extra)
        f["isins"] = f["isins"] + extra
    for s in rest:
        s["isins"] = [i for i in s["isins"] if i not in claimed]
        claimed.update(s["isins"])
        if s["isins"]:
            s["id"] = s["isin"] = s["isins"][0]

    out = pd.DataFrame(issuer + rest)
    if out.empty:
        return pd.DataFrame(columns=UNIVERSE_COLUMNS)
    out = out[UNIVERSE_COLUMNS]
    dupes = out["id"].duplicated()
    if dupes.any():
        logger.warning("Dropping %d funds whose id repeats another's: %s", dupes.sum(), out.loc[dupes, "id"].tolist()[:10])
        out = out[~dupes]
    logger.info(
        "Universe: %d funds (%s); %d issuer funds matched to an N-PORT series",
        len(out), out["universe_source"].value_counts().to_dict(), out["series_match"].notna().sum(),
    )
    return out.reset_index(drop=True)


def select_universe(universe: pd.DataFrame, params: dict) -> pd.DataFrame:
    """The funds to run: ``sources``, then ``ids``, then ``sample`` funds per source, then ``limit``."""
    sources = params.get("sources") or list(UNIVERSE_SOURCES)
    unknown = set(sources) - set(UNIVERSE_SOURCES)
    if unknown:
        raise ValueError(f"universe.sources: unknown {sorted(unknown)}; choose from {UNIVERSE_SOURCES}")
    out = universe[universe["universe_source"].isin(sources)]
    if params.get("ids"):
        out = out[out["id"].isin(params["ids"]) | out["series_id"].isin(params["ids"])]
    if params.get("sample"):
        n = int(params["sample"])
        seed = params.get("seed", 0)
        picks = [g.sample(min(n, len(g)), random_state=seed) for _, g in out.groupby("universe_source")]
        out = pd.concat(picks).sort_values(["universe_source", "id"]) if picks else out
    if params.get("limit"):
        out = out.head(int(params["limit"]))
    logger.info("Selected %d of %d funds: %s", len(out), len(universe), out["universe_source"].value_counts().to_dict())
    return out.reset_index(drop=True)


# -- the batch --------------------------------------------------------------


class _Warnings(logging.Handler):
    """Collects warnings logged while one fund is expanded, so they land in its summary row."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def fund_ref(fund: dict) -> FundRef:
    """The engine's ref for a universe row; issuer funds keep their series for the N-PORT fallback."""
    return FundRef(
        series_id=_str(fund.get("series_id")), ticker=_str(fund.get("ticker")), name=_str(fund.get("name")),
        via=fund["universe_source"], issuer_id=_str(fund.get("issuer_id")),
    )


def _short_traceback(exc: BaseException, frames: int = 4) -> str:
    tb = traceback.extract_tb(exc.__traceback__)[-frames:]
    return " | ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in tb)


def expand_fund(fund: dict, providers, params: dict) -> tuple[dict, pd.DataFrame]:
    """Look through one universe fund. Never raises: an error is returned in the summary."""
    t0 = time.perf_counter()
    catcher = _Warnings()
    pkg = logging.getLogger("openbasket")
    pkg.addHandler(catcher)
    summary: dict = {}
    rows = pd.DataFrame()
    error = tb = None
    try:
        res = look_through(
            fund["id"], providers, max_depth=params["max_depth"], min_resolved_pct=params["min_resolved_pct"],
            ref=fund_ref(fund),
        )
        summary = res.summary
        rows = _fund_rows(fund["id"], res.leaves, summary)
    except Exception as exc:  # one broken fund must not stop the batch
        error = f"{type(exc).__name__}: {exc}"[:500]
        tb = _short_traceback(exc)
        logger.warning("%s failed: %s", fund["id"], error)
    finally:
        pkg.removeHandler(catcher)
    return _summary_row(fund, summary, error, tb, catcher.messages, time.perf_counter() - t0), rows


#: A constituent row never mixes sources: one ISIN reached directly from N-PORT
#: and through a sub-fund's issuer file is two rows, each with its own source,
#: as-of date and ``via_fund``.
ROW_KEY = ("input_isin", "constituent_isin", "source", "as_of", "via_fund")


def _fund_rows(fid: str, leaves: list[dict], summary: dict) -> pd.DataFrame:
    """Constituents of one fund, routes summed per ``ROW_KEY``, plus its unresolved-weight row."""
    agg = aggregate(pd.DataFrame(leaves), ROW_KEY) if leaves else pd.DataFrame(columns=PART_SCHEMA.names)
    agg = agg.rename(columns={"input_isin": "fund_id"})
    unresolved = {
        "fund_id": fid, "constituent_isin": None, "name": "Unresolved weight", "weight_pct": summary["unresolved_pct"],
        "asset_class": "unresolved", "source": summary["source"], "source_ref": summary.get("source_ref"),
        "source_url": summary.get("source_url"), "as_of": summary["as_of"], "depth": 0, "n_paths": 0,
        "note": summary["note"],
    }
    out = pd.concat([agg, pd.DataFrame([unresolved])], ignore_index=True)
    out["fund_id"] = fid
    return out


def _summary_row(fund: dict, s: dict, error: str | None, tb: str | None, warnings: list[str], seconds: float) -> dict:
    src = s.get("source")
    note = s.get("note")
    if error is None and src is None:
        # Say why, when a source said so (e.g. an issuer that lists only the ten largest lines).
        note = "; ".join([note or "no holdings found from any source", *list(dict.fromkeys(warnings))[:2]])
    fallback_url = _str(fund.get("product_page_url")) or (
        edgar_url(fund["series_id"], None, None) if _str(fund.get("series_id")) else None
    )
    return {
        **{k: fund.get(k) for k in ("id", "isin", "isins", "series_id", "issuer_id", "ticker", "issuer",
                                    "universe_source", "series_match")},
        "name": _str(fund.get("name")) or s.get("fund_name"),
        # A fund with no holdings is still credited to the list it came from.
        "source": src or fund["universe_source"],
        "source_url": s.get("source_url") or fallback_url,
        "source_ref": s.get("source_ref"),
        "credit": None,
        "as_of": s.get("as_of"),
        "matched_via": s.get("matched_via") or fund["universe_source"],
        "note": note,
        "sources_used": s.get("sources_used"),
        "sources_tried": s.get("sources_tried"),
        "reported_total_pct": s.get("reported_total_pct"),
        "resolved_pct": s.get("resolved_pct"),
        "unresolved_pct": s.get("unresolved_pct"),
        "unidentified_lines_pct": s.get("unidentified_lines_pct"),
        "n_constituents": s.get("n_constituents"),
        "error": error,
        "traceback": tb,
        "warnings": "; ".join(dict.fromkeys(warnings))[:2000] or None,
        "seconds": round(seconds, 4),
    }


class _PartWriter:
    """Writes constituent rows to numbered Parquet files of about ``rows_per_part`` rows."""

    def __init__(self, directory: Path, rows_per_part: int):
        self.dir = directory
        self.rows_per_part = rows_per_part
        self.pending: list[pd.DataFrame] = []
        self.n_pending = 0
        self.n_parts = 0
        self.n_rows = 0
        if self.dir.exists():
            shutil.rmtree(self.dir)
        self.dir.mkdir(parents=True)

    def add(self, df: pd.DataFrame) -> None:
        if len(df):
            self.pending.append(df)
            self.n_pending += len(df)
        if self.n_pending >= self.rows_per_part:
            self.flush()

    def flush(self, force: bool = False) -> None:
        if not self.pending and not (force and self.n_parts == 0):
            return
        df = pd.concat(self.pending, ignore_index=True) if self.pending else pd.DataFrame(columns=PART_SCHEMA.names)
        pq.write_table(_to_arrow(df), self.dir / f"part-{self.n_parts:05d}.parquet", compression="zstd")
        self.n_parts += 1
        self.n_rows += len(df)
        self.pending, self.n_pending = [], 0

    @property
    def glob(self) -> str:
        return str(self.dir / "part-*.parquet")


def _to_arrow(df: pd.DataFrame) -> pa.Table:
    cols = {}
    for field in PART_SCHEMA:
        v = df[field.name] if field.name in df else pd.Series([None] * len(df), dtype=object)
        if field.type == _STR:
            values = [None if x is None or (isinstance(x, float) and pd.isna(x)) else str(x) for x in v.tolist()]
        elif field.type == pa.int32():
            values = pd.to_numeric(v, errors="coerce").fillna(0).astype("int32").tolist()
        else:
            values = pd.to_numeric(v, errors="coerce").tolist()
        cols[field.name] = pa.array(values, type=field.type, from_pandas=True)
    return pa.table(cols, schema=PART_SCHEMA)


def run_universe(
    universe: pd.DataFrame,
    nport_holdings: duckdb.DuckDBPyRelation,
    security_master: duckdb.DuckDBPyRelation,
    fund_master: pd.DataFrame,
    ticker_isin_map: pd.DataFrame,
    issuer_products: pd.DataFrame,
    cusip_isin_map: duckdb.DuckDBPyRelation,
    sedol_isin_map: pd.DataFrame,
    lookthrough_params: dict,
    params: dict,
    sources: dict,
) -> tuple[pd.DataFrame, duckdb.DuckDBPyRelation, dict]:
    """Expand every selected fund. Returns the fund table, the constituent rows and a run record."""
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    http = params.get("http") or {}
    throttle.configure(http.get("default_interval_s"), http.get("per_host_interval_s"))
    use_figi = lookthrough_params.get("use_openfigi", True)
    figi = OpenFIGI(lookthrough_params["openfigi_cache"], autosave=False) if use_figi else None
    ti = issuer_tickers() if "tickerinside" in lookthrough_params["source_order"] and params.get("tickerinside_prefilter", True) else None
    providers = make_providers(
        nport_holdings, security_master, fund_master, ticker_isin_map, issuer_products, cusip_isin_map,
        sedol_isin_map, lookthrough_params, figi,
        stop_at_pct=lookthrough_params["min_resolved_pct"] if params.get("stop_early", True) else None,
        tickerinside_tickers=ti,
    )
    writer = _PartWriter(Path(params["work_dir"]), int(params.get("rows_per_part", 500_000)))
    every = int(params.get("log_every", 100))
    # The OpenFIGI cache is rewritten whole, so not after every fund.
    save_every = int(params.get("openfigi_save_every", 500))
    records = []
    funds = universe.to_dict("records")
    for i, fund in enumerate(funds, 1):
        rec, rows = expand_fund(fund, providers, lookthrough_params)
        records.append(rec)
        writer.add(rows)
        if i % every == 0 or i == len(funds):
            _progress(records, len(funds), time.perf_counter() - started)
        if figi is not None and i % save_every == 0:
            figi.save()
    writer.flush(force=True)

    enrich_started = time.perf_counter()
    figi_table = _figi_table(writer.glob, figi) if use_figi and lookthrough_params.get("enrich_openfigi", True) else None
    enrich_s = time.perf_counter() - enrich_started
    constituents = _constituents(writer.glob, figi_table)
    funds_df = _with_credit(pd.DataFrame(records, columns=FUND_COLUMNS), constituents, sources)
    ok = funds_df["error"].isna()
    run = {
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.perf_counter() - started, 1),
        "openfigi_enrich_seconds": round(enrich_s, 1),
        "n_attempted": len(funds_df),
        "n_ok": int(ok.sum()),
        "n_failed": int((~ok).sum()),
        "n_no_holdings": int((ok & funds_df["sources_tried"].isna()).sum()),
        "n_constituent_rows": writer.n_rows,
        "http_requests": throttle.THROTTLE.requests,
        "http_wait_seconds": round(throttle.THROTTLE.waited_s, 1),
        "selection": {k: params.get(k) for k in ("sources", "ids", "sample", "seed", "limit")},
    }
    logger.info("Universe run: %s", run)
    return funds_df, constituents, run


def _progress(records: list[dict], total: int, elapsed: float) -> None:
    done = len(records)
    failed = sum(r["error"] is not None for r in records)
    rate = elapsed / done if done else 0.0
    logger.info(
        "Universe: %d/%d funds, %d failed, %.2f s/fund, about %.0f min to go",
        done, total, failed, rate, rate * (total - done) / 60,
    )


def _figi_table(glob: str, figi: OpenFIGI) -> pd.DataFrame:
    """OpenFIGI name and type of every constituent ISIN, mapped in one batch with the shared cache."""
    isins = [r[0] for r in connection().execute(
        f"select distinct constituent_isin from read_parquet('{glob}') where constituent_isin is not null"
    ).fetchall()]
    found = figi.map("ID_ISIN", isins)
    figi.save()
    rows = []
    for isin, listings in found.items():
        b = best_listing(listings)
        if b:
            rows.append({"isin": isin, "figi_name": b.get("name"), "figi_ticker": b.get("ticker"),
                         "security_type": b.get("securityType"), "market_sector": b.get("marketSector"),
                         "composite_figi": b.get("compositeFIGI")})
    logger.info("OpenFIGI: %d of %d constituent ISINs known", len(rows), len(isins))
    return pd.DataFrame(rows, columns=["isin", "figi_name", "figi_ticker", "security_type", "market_sector", "composite_figi"])


def _constituents(glob: str, figi_table: pd.DataFrame | None) -> duckdb.DuckDBPyRelation:
    """Constituent rows with OpenFIGI fields; ``name_source`` says who supplied each name."""
    con = connection()
    ft = figi_table if figi_table is not None else pd.DataFrame(
        columns=["isin", "figi_name", "figi_ticker", "security_type", "market_sector", "composite_figi"], dtype=object
    )
    con.register("_universe_figi", ft)
    cols = ", ".join(f"c.{n}" for n in PART_SCHEMA.names if n != "name")
    return con.sql(
        f"""
        select {cols},
               coalesce(c.name, f.figi_name) as name,
               case when c.name is not null then c.source when f.figi_name is not null then 'openfigi' end as name_source,
               f.figi_name, f.figi_ticker, f.security_type, f.market_sector, f.composite_figi
        from read_parquet('{glob}') c
        left join _universe_figi f on c.constituent_isin = f.isin
        """
    )


def _with_credit(funds: pd.DataFrame, constituents: duckdb.DuckDBPyRelation, sources: dict) -> pd.DataFrame:
    """Credit every source a fund's rows came from, its own source first, then by weight."""
    rel = constituents  # noqa: F841 - read by DuckDB below
    used = connection().sql(
        """
        select fund_id, source as src, sum(abs(weight_pct)) w from rel
        where constituent_isin is not null group by all
        union all
        select fund_id, 'openfigi', 0 from rel where name_source = 'openfigi' or composite_figi is not null group by 1
        """
    ).df()
    by_fund = {
        fid: g.sort_values("w", ascending=False)["src"].tolist() for fid, g in used.groupby("fund_id", sort=False)
    }
    credits = []
    for fid, top in zip(funds["id"], funds["source"]):
        ids = list(dict.fromkeys([top, *by_fund.get(fid, [])]))
        credits.append("; ".join((sources.get(s) or {}).get("credit") or s for s in ids if s))
    funds = funds.copy()
    funds["credit"] = credits
    return funds
