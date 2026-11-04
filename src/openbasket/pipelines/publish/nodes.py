"""Turn a universe run into the static JSON API and the release files.

The shapes are those in ``docs/source/data-api.md``. Every fund file names
its source, a link, the as-of date and a credit line; every constituent row
names its own source and as-of date; ``meta.json`` lists every source that
contributed a row, with its licence and credit, from ``publish.sources``.

Fund files are cut to ``max_constituents`` rows, and if the API still comes
out larger than ``site_budget_mb`` the cut is lowered and the large funds are
rewritten. Each cut is logged and recorded in the quality report; the full
lists are always in the release file, and each fund file's ``n_rows``,
``listed_pct`` and ``breakdowns`` are computed over the full list.

In the public build (``publish.mode: public``) only sources whose licence
allows republication may appear (see :func:`openbasket.sources.redistribution`).
Before anything is written, every constituent row's source and every fund's
source are checked, and a ``local_only`` one stops the build with
:class:`LocalOnlyInPublicBuild`; each fund file is checked again as it is
written, and ``meta.sources`` lists public sources only.
"""

from __future__ import annotations

import json
import logging
import math
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from ... import aggregates
from ...datasets import connection
from ...sources import financedatabase as findb
from ...sources import FULL_MODE, PUBLIC_MODE, is_public, publish_mode, redistribution
from ..lookthrough.nodes import issuer_adapters
from ..universe.nodes import source_catalogue

logger = logging.getLogger(__name__)

API_VERSION = 1
#: The version of ``docs/source/data-api.md`` the files follow.
CONTRACT = "1.1"
LOOKTHROUGH_FILE = "openbasket-lookthrough.parquet"
FUNDS_FILE = "openbasket-funds.parquet"
QUALITY_FILE = "quality-report.json"

CONSTITUENT_FIELDS = ["isin", "name", "weight_pct", "asset_class", "source", "as_of", "depth", "via", "country", "sector"]
#: Breakdowns in fund files, each over the fund's full list of rows.
BREAKDOWNS = ("asset_class", "country", "sector", "source")
UNRESOLVED_LABEL = "Unresolved"
UNKNOWN_LABEL = "Unknown"
INDEX_FIELDS = ["id", "isin", "name", "ticker", "issuer", "kind", "source", "as_of", "resolved_pct", "n_constituents", "status"]
#: Fund-file keys taken from the funds table, in contract order.
FUND_KEYS = [
    "id", "isin", "isins", "name", "ticker", "issuer", "source", "source_url", "credit", "as_of", "matched_via",
    "note", "status", "reported_total_pct", "resolved_pct", "unexpanded_fund_pct", "unresolved_pct",
    "unidentified_lines_pct", "n_constituents",
]
#: Every fund-file key, in the order the file is written (docs/source/data-api.md).
FUND_FILE_KEYS = FUND_KEYS + [
    "n_rows", "truncated", "listed_pct", "breakdowns", "fields", "constituents", "unresolved", "full_data_url",
]
_PCT_KEYS = ("reported_total_pct", "resolved_pct", "unexpanded_fund_pct", "unresolved_pct", "unidentified_lines_pct")
_ID_RE = re.compile(r"^[A-Z0-9]{9,12}$")


class LocalOnlyInPublicBuild(RuntimeError):
    """The public build was handed rows or funds from a source it may not republish."""


def check_public(funds: pd.DataFrame, rel: duckdb.DuckDBPyRelation) -> dict[str, int]:
    """Raise :class:`LocalOnlyInPublicBuild` if any row or fund comes from a ``local_only`` source.

    Returns the number of rows per source. A local run's universe outputs
    (``kedro run`` without ``--env public``) hold issuer rows; publishing them
    in the public build would republish them, so it fails here instead.
    """
    r = rel  # noqa: F841 - read by DuckDB below
    rows = dict(connection().sql("select coalesce(source, ''), count(*) from r group by 1").fetchall())
    fund_sources = funds["source"].fillna("").value_counts().to_dict() if "source" in funds else {}
    bad_rows = {s: int(n) for s, n in rows.items() if s and not is_public(s)}
    bad_funds = {s: int(n) for s, n in fund_sources.items() if s and not is_public(s)}
    if bad_rows or bad_funds:
        raise LocalOnlyInPublicBuild(
            "The public build may only publish sources whose licence allows it, but these local_only sources are "
            f"in its input: rows {bad_rows or 'none'}, funds {bad_funds or 'none'}. Run the universe with "
            "`kedro run --env public --pipeline=universe` (publish.mode: public) before publishing."
        )
    return {s: int(n) for s, n in rows.items()}


def _check_document(doc: dict) -> None:
    """A fund file in the public build: its source, its unresolved entry's and every row's must be public."""
    srcs = {doc.get("source"), (doc.get("unresolved") or {}).get("source")}
    i = CONSTITUENT_FIELDS.index("source")
    srcs |= {row[i] for row in doc.get("constituents") or []}
    bad = sorted(s for s in srcs if s and not is_public(s))
    if bad:
        raise LocalOnlyInPublicBuild(f"funds/{doc.get('id')}.json would publish local_only sources {bad}")


def release_urls(repo: str, tag: str) -> tuple[str, str]:
    """The release page and the full look-through file's download link for ``tag``."""
    return (
        f"https://github.com/{repo}/releases/tag/{tag}",
        f"https://github.com/{repo}/releases/download/{tag}/{LOOKTHROUGH_FILE}",
    )


def _clean(v: object) -> object:
    """JSON-safe scalar: numpy types unwrapped, NaN to ``None``."""
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _num(v: object, digits: int = 5) -> float | None:
    v = _clean(v)
    return None if v is None else round(float(v), digits)


def _isins(v: object) -> list[str]:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return []
    return [str(x) for x in v]


def _dumps(doc: object) -> bytes:
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def fund_document(fund: dict, rows: list[tuple], n_rows: int, full_data_url: str, breakdowns: dict | None = None) -> dict:
    """One ``funds/{id}.json``.

    ``rows`` are the kept constituent rows in :data:`CONSTITUENT_FIELDS`
    order, ``n_rows`` how many there are in the full list, and
    ``breakdowns`` the fund's breakdowns over that full list (see
    :func:`fund_breakdowns`); without them they are built from ``rows``.
    """
    doc = {k: _clean(fund.get(k)) for k in FUND_KEYS}
    doc["isins"] = _isins(fund.get("isins"))
    for k in _PCT_KEYS:
        doc[k] = _num(fund.get(k))
    doc["n_constituents"] = int(_clean(fund.get("n_constituents")) or 0)
    constituents = [
        [isin, name, _num(w), ac, src, as_of, int(depth) if depth is not None else None, via, country, sector]
        for isin, name, w, ac, src, as_of, depth, via, country, sector in rows
    ]
    if breakdowns is None:
        breakdowns = fund_breakdowns(
            [(r[2], {"asset_class": r[3], "country": r[8], "sector": r[9], "source": r[4]}, r[0]) for r in constituents],
            doc["unresolved_pct"],
        )
    doc["n_rows"] = int(n_rows)
    doc["truncated"] = n_rows > len(rows)
    doc["listed_pct"] = _num(sum(r[2] or 0.0 for r in constituents))
    doc["breakdowns"] = breakdowns
    doc["fields"] = CONSTITUENT_FIELDS
    doc["constituents"] = constituents
    doc["unresolved"] = unresolved_entry(doc, n_rows)
    doc["full_data_url"] = full_data_url
    return doc


def unresolved_entry(doc: dict, n_rows: int) -> dict:
    """The fund file's ``unresolved`` entry: the weight, why, and where it was read.

    It carries the fund's own source, link and as-of date, so even a fund
    with no holdings says where its (empty) list came from. ``note`` is the
    fund's note when there are no rows, otherwise a sentence on the lines
    the source gave without an ISIN, or ``None``.
    """
    unidentified = doc.get("unidentified_lines_pct")
    if n_rows == 0:
        note = doc.get("note")
    elif unidentified is not None and abs(unidentified) >= 0.005:
        note = f"{unidentified:.2f}% is in lines with no ISIN (cash, futures, FX, swaps or pools)."
    else:
        note = None
    return {
        "weight_pct": doc.get("unresolved_pct"),
        "note": note,
        "source": doc.get("source"),
        "source_url": doc.get("source_url"),
        "as_of": doc.get("as_of"),
    }


def fund_breakdowns(rows: list[tuple[float, dict, str | None]], unresolved_pct: float | None) -> dict:
    """``{"asset_class": [[label, pct], ...], ..., "top10_pct": x}`` from ``(weight, labels, isin)`` rows.

    Each breakdown sums the rows' weights by label, largest first, with rows
    that have no label under "Unknown", and ends with the fund's unresolved
    weight labelled "Unresolved". ``top10_pct`` is the weight of the ten
    largest constituent ISINs, each summed over its rows.
    """
    out: dict = {}
    for dim in BREAKDOWNS:
        sums: dict[str, float] = {}
        for w, labels, _ in rows:
            label = labels.get(dim) or UNKNOWN_LABEL
            sums[label] = sums.get(label, 0.0) + float(w or 0.0)
        out[dim] = _ordered(sums, unresolved_pct)
    by_isin: dict[str, float] = {}
    for w, _, isin in rows:
        if isin:
            by_isin[isin] = by_isin.get(isin, 0.0) + float(w or 0.0)
    out["top10_pct"] = _num(sum(sorted(by_isin.values(), key=abs, reverse=True)[:10]))
    return out


def _ordered(sums: dict[str, float], unresolved_pct: float | None) -> list[list]:
    items = sorted(sums.items(), key=lambda kv: (-kv[1], kv[0]))
    return [[k, _num(v)] for k, v in items] + [[UNRESOLVED_LABEL, _num(unresolved_pct) if unresolved_pct is not None else 0.0]]


def _stream_constituents(rel: duckdb.DuckDBPyRelation, cap: int, ids: set[str], chunk: int = 500):
    """Yield ``(fund_id, n_rows, rows, sums)`` with each fund's heaviest ``cap`` rows, largest absolute weight first.

    ``sums`` holds the fund's breakdown sums over its full list:
    ``{dimension: {label: pct}, "top10_pct": pct}``.

    Funds are read a few hundred at a time by id range, which the Parquet
    file's sort order lets DuckDB prune, so no query result stays open while
    files are written.
    """
    con = connection()
    ordered = sorted(ids)
    for start in range(0, len(ordered), chunk):
        part = ordered[start : start + chunk]
        rng = [part[0], part[-1]]
        con.register("_publish_rel", rel)
        try:
            found = con.execute(
                f"""
                select fund_id, n, constituent_isin, name, weight_pct, asset_class, source, as_of, depth, via_fund,
                       country, sector
                from (
                    select *, row_number() over (partition by fund_id order by abs(weight_pct) desc, constituent_isin) rn,
                           count(*) over (partition by fund_id) n
                    from _publish_rel where constituent_isin is not null and fund_id between ? and ?
                )
                where rn <= {int(cap)}
                order by fund_id, rn
                """,
                rng,
            ).fetchall()
            dims = " union all ".join(
                f"select fund_id, '{d}' dim, {d} label, sum(weight_pct) w from r group by all" for d in BREAKDOWNS
            )
            sums_rows = con.execute(
                f"""
                with r as (select * from _publish_rel where constituent_isin is not null and fund_id between ? and ?)
                {dims}
                union all
                select fund_id, 'sector_filled_pct', null, sum(weight_pct) from r
                where sector_source is not null group by 1
                union all
                select fund_id, 'top10_pct', null, sum(w) from (
                    select fund_id, constituent_isin, sum(weight_pct) w,
                           row_number() over (partition by fund_id order by abs(sum(weight_pct)) desc, constituent_isin) rn
                    from r group by 1, 2
                ) where rn <= 10 group by 1
                """,
                rng,
            ).fetchall()
        finally:
            con.unregister("_publish_rel")
        sums: dict[str, dict] = {}
        for f, dim, label, w in sums_rows:
            d = sums.setdefault(f, {})
            if dim in ("top10_pct", "sector_filled_pct"):
                d[dim] = w
            else:
                bucket = d.setdefault(dim, {})
                key = label or UNKNOWN_LABEL
                bucket[key] = bucket.get(key, 0.0) + float(w or 0.0)
        wanted = set(part)
        fid, n, rows = None, 0, []
        for f, count, *row in found:
            if f != fid:
                if fid in wanted:
                    yield fid, n, rows, sums.get(fid, {})
                fid, n, rows = f, count, []
            rows.append(tuple(row))
        if fid in wanted:
            yield fid, n, rows, sums.get(fid, {})


def _finish_breakdowns(sums: dict, unresolved_pct: object) -> dict:
    """Breakdown sums of one fund in the published shape, its unresolved weight last."""
    u = _num(unresolved_pct)
    out = {d: _ordered(sums.get(d, {}), u) for d in BREAKDOWNS}
    out["top10_pct"] = _num(sums.get("top10_pct")) or 0.0
    filled = _num(sums.get("sector_filled_pct"))
    if filled:
        # Rows whose holdings source gave no sector, labelled from FinanceDatabase by company ISIN.
        out["sector_from"] = [[SECTOR_SOURCE, filled]]
    return out


def _write_fund_files(funds: dict[str, dict], rel, cap: int, out: Path, full_data_url: str,
                      only: set[str] | None = None, public: bool = False,
                      aggregates: dict | None = None) -> tuple[dict[str, int], dict[str, int]]:
    """Write fund files; returns bytes per file and constituent rows per fund.

    With ``public`` each file is checked to name public sources only before it is written.
    A fund with no rows that has an entry in ``aggregates`` (see
    :mod:`openbasket.aggregates`) gets those coarse splits as its breakdowns.
    """
    sizes, n_rows = {}, {}
    for fid, n, rows, sums in _stream_constituents(rel, cap, only if only is not None else set(funds)):
        if fid in funds:
            bd = _finish_breakdowns(sums, funds[fid].get("unresolved_pct"))
            doc = fund_document(funds[fid], rows, n, full_data_url, bd)
            if public:
                _check_document(doc)
            data = _dumps(doc)
            (out / f"{fid}.json").write_bytes(data)
            sizes[fid], n_rows[fid] = len(data), n
    for fid in (only if only is not None else funds.keys()) - sizes.keys():
        bd = _aggregate_breakdowns(funds[fid], aggregates) or _finish_breakdowns({}, funds[fid].get("unresolved_pct"))
        doc = fund_document(funds[fid], [], 0, full_data_url, bd)
        if public:
            _check_document(doc)
        data = _dumps(doc)
        (out / f"{fid}.json").write_bytes(data)
        sizes[fid], n_rows[fid] = len(data), 0
    return sizes, n_rows


def committed_at(path: str | Path) -> str | None:
    """When the last git commit that changed ``path`` was made (UTC, ISO 8601), or ``None`` outside git.

    A shallow clone knows only its newest commit, so the publish workflow checks out the full history.
    """
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%cI", "--", Path(path).name], cwd=Path(path).parent,
                             capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out:
        return None
    return datetime.fromisoformat(out).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_aggregates(path: str | None) -> dict | None:
    """The validated aggregates file, indexed by fund id and every ISIN it lists; ``None`` if absent.

    Each entry carries ``updated_at``: when the file was last committed, else when it was computed.
    """
    if not path or not Path(path).is_file():
        return None
    doc = json.loads(Path(path).read_text())
    aggregates.validate(doc)
    updated_at = committed_at(path) or doc.get("computed_at")
    index = {}
    for fid, entry in doc["funds"].items():
        # An entry kept from an earlier run shows when it was computed, not when the file was last committed.
        when = entry.get("computed_at") or doc.get("computed_at")
        e = {**entry, "computed_at": when, "updated_at": updated_at if when == doc.get("computed_at") else when}
        for key in [fid, *entry.get("isins", [])]:
            index.setdefault(key, e)
    return index


def _aggregate_breakdowns(fund: dict, index: dict | None) -> dict | None:
    """Breakdowns for a fund without rows, from openbasket's own aggregates of its holdings."""
    if not index or fund.get("status") != "local_only":
        return None
    entry = next((index[k] for k in [fund.get("id"), *_isins(fund.get("isins"))] if k in index), None)
    if entry is None:
        return None
    issuer = _clean(fund.get("issuer")) or "the issuer"
    return {
        "basis": "openbasket_aggregate",
        "note": (f"Computed by openbasket from {issuer}'s published holdings as of {entry.get('as_of')}; "
                 "whole percent, holdings not shown. Run openbasket yourself to see the companies."),
        "as_of": entry.get("as_of"),
        "computed_at": entry.get("computed_at"),
        "updated_at": entry.get("updated_at"),
        **{d: entry[d] for d in aggregates.DIMENSIONS if d in entry},
        "top10_pct": None,
    }


def _source_entries(sources_cfg: dict, rel, funds: pd.DataFrame) -> list[dict]:
    """``meta.sources``: every source that contributed a row, a name or a fund, with its credit and dates.

    Row sources include each fund's unresolved row, which carries the fund's
    own source, so a fund with no holdings still lists the source that
    listed it. OpenFIGI is listed when it supplied a name or matched a fund.
    """
    con = connection()
    con.register("_publish_rel", rel)
    con.register("_publish_ids", pd.DataFrame({"fund_id": funds["id"].astype(object)}))
    try:
        stats = con.execute(
            """
            with r as (select * from _publish_rel where fund_id in (select fund_id from _publish_ids))
            select source, count(distinct fund_id), min(as_of), max(as_of) from r where source is not null group by 1
            union all
            select 'openfigi', count(distinct fund_id), null, null from r
            where name_source = 'openfigi'
            having count(*) > 0
            union all
            select sector_source, count(distinct fund_id), null, null from r
            where sector_source is not null group by 1
            """
        ).fetchall()
    finally:
        con.unregister("_publish_rel")
        con.unregister("_publish_ids")
    found = {s: {"n_funds": n, "as_of_min": lo, "as_of_max": hi} for s, n, lo, hi in stats}
    # Funds without rows are still credited to their source.
    for src, g in funds[~funds["id"].isin(_ids_with_sources(rel))].groupby("source"):
        e = found.setdefault(src, {"n_funds": 0, "as_of_min": None, "as_of_max": None})
        e["n_funds"] += len(g)
    figi_matched = funds[funds["matched_via"] == "openfigi_ticker"] if "matched_via" in funds else funds.iloc[:0]
    if len(figi_matched):
        e = found.setdefault("openfigi", {"n_funds": 0, "as_of_min": None, "as_of_max": None})
        e["n_funds"] += len(figi_matched)
    entries = []
    for sid in list(sources_cfg) + sorted(set(found) - set(sources_cfg)):
        if sid not in found:
            continue
        cfg = sources_cfg.get(sid)
        if cfg is None:
            logger.warning("Source %r contributed rows but has no entry in publish.sources or the issuer adapters", sid)
            cfg = {"name": sid, "url": None, "licence": None, "credit": sid}
        entries.append({"id": sid, **{k: cfg.get(k) for k in ("name", "url", "licence", "credit")},
                        "redistribution": redistribution(sid), **found[sid]})
    return entries


def _ids_with_sources(rel) -> set[str]:
    r = rel  # noqa: F841 - read by DuckDB below
    return {x[0] for x in connection().sql("select distinct fund_id from r where source is not null").fetchall()}


def _dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


#: Where a row's sector came from when its holdings source gave none.
SECTOR_SOURCE = findb.SOURCE_ID


def build_sector_map(params: dict) -> pd.DataFrame:
    """Company ISIN -> GICS sector from FinanceDatabase's equities file (MIT), with the file's date.

    Empty when ``params.enabled`` is false or the file cannot be fetched: the
    build then goes on with the sectors the holdings sources give.
    """
    empty = pd.DataFrame({c: pd.Series(dtype=object) for c in [*findb.SECTOR_MAP_COLUMNS, "as_of"]})
    if not params.get("enabled", True):
        return empty
    try:
        got = findb.fetch_equities(params["root"], overwrite=params.get("overwrite", False))
        out, stats = findb.sector_map(pd.read_parquet(got["path"], columns=["isin", "sector"]))
    except Exception as exc:  # noqa: BLE001 - sector labels are an extra, not a reason to stop the build
        logger.warning("FinanceDatabase sectors unavailable (%s); rows keep their source's sectors", exc)
        return empty
    logger.info("FinanceDatabase sectors: %d company ISINs (%d with conflicting sectors dropped)",
                stats["isins"], stats["conflicting_isins"])
    out["as_of"] = got["as_of"]
    return out


def fill_sectors(rel: duckdb.DuckDBPyRelation, sectors: pd.DataFrame | None) -> duckdb.DuckDBPyRelation:
    """``rel`` with each row that has no sector given its company's sector from ``sectors``.

    A row's own sector always wins. Rows filled this way say
    ``sector_source = 'financedatabase'``; every other row has it null.
    """
    con = connection()
    con.register("_sector_base", rel)
    if sectors is None or not len(sectors):
        return con.sql("select *, cast(null as varchar) as sector_source from _sector_base")
    con.register("_sector_map", sectors[findb.SECTOR_MAP_COLUMNS])
    return con.sql(
        f"""
        select b.* replace (coalesce(nullif(trim(b.sector), ''), m.sector) as sector),
               case when nullif(trim(b.sector), '') is null and m.sector is not null then '{SECTOR_SOURCE}' end
                   as sector_source
        from _sector_base b left join _sector_map m on b.constituent_isin = m.isin
        """
    )


def publish(
    universe: pd.DataFrame,
    universe_funds: pd.DataFrame,
    universe_constituents: duckdb.DuckDBPyRelation,
    universe_run: dict,
    params: dict,
    issuers_params: dict | None = None,
    sector_map: pd.DataFrame | None = None,
) -> dict:
    """Write ``api/v1`` under ``site_dir`` and the release files under ``release_dir``; return the quality report.

    In the public build (``mode: public``) nothing is written unless every
    row and fund comes from a public source (:func:`check_public`).
    """
    t0 = time.perf_counter()
    now = datetime.now(timezone.utc)
    built_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    tag = str(params.get("release_tag") or now.strftime("%Y-%m"))
    release_url, full_data_url = release_urls(params["repo"], tag)
    mode = publish_mode(params)
    public = mode == PUBLIC_MODE
    sources_cfg = source_catalogue(params["sources"], issuer_adapters(issuers_params))
    withheld = sorted(sid for sid in sources_cfg if not is_public(sid))
    if public:
        rows_by_source = check_public(universe_funds, universe_constituents)
        sources_cfg = {sid: cfg for sid, cfg in sources_cfg.items() if is_public(sid)}
    else:
        rows_by_source = None

    release = Path(params["release_dir"])
    release.mkdir(parents=True, exist_ok=True)
    rel = fill_sectors(universe_constituents, sector_map)
    rel.order("fund_id, constituent_isin is null, abs(weight_pct) desc").write_parquet(
        str(release / LOOKTHROUGH_FILE), compression="zstd"
    )
    funds_out = universe_funds.copy()
    funds_out["isins"] = funds_out["isins"].map(_isins)
    funds_out.to_parquet(release / FUNDS_FILE, index=False)

    api = Path(params["site_dir"]) / "api" / f"v{API_VERSION}"
    if api.exists():
        shutil.rmtree(api)
    (api / "funds").mkdir(parents=True)

    ok = universe_funds[universe_funds["error"].isna()]
    bad_ids = ~ok["id"].astype(str).str.match(_ID_RE)
    if bad_ids.any():
        logger.warning("Not publishing %d funds whose id cannot be a file name: %s", bad_ids.sum(), ok.loc[bad_ids, "id"].tolist()[:10])
        ok = ok[~bad_ids]
    funds = {r["id"]: r for r in ok.to_dict("records")}

    cap = int(params.get("max_constituents", 1000))
    requested_cap = cap
    agg = load_aggregates(params.get("aggregates_path")) if public else None
    sizes, n_rows = _write_fund_files(funds, rel, cap, api / "funds", full_data_url, public=public, aggregates=agg)

    aliases: dict[str, str] = {}
    for fid, f in funds.items():
        for isin in _isins(f.get("isins")):
            if isin in aliases or (isin in funds and isin != fid):
                logger.warning("ISIN %s names more than one fund (%s, %s); keeping the first", isin, aliases.get(isin, isin), fid)
                continue
            aliases[isin] = fid
            if isin != fid:
                (api / "funds" / f"{isin}.json").write_bytes(_dumps({"alias_of": fid}))

    index = {
        "fields": INDEX_FIELDS,
        "rows": [
            [fid, _clean(f.get("isin")), _clean(f.get("name")), _clean(f.get("ticker")), _clean(f.get("issuer")), "fund",
             _clean(f.get("source")), _clean(f.get("as_of")), _num(f.get("resolved_pct"), 2),
             int(_clean(f.get("n_constituents")) or 0), _clean(f.get("status"))]
            for fid, f in sorted(funds.items())
        ],
        "aliases": dict(sorted(aliases.items())),
    }
    (api / "index.json").write_bytes(_dumps(index))
    meta_sources = _source_entries(sources_cfg, rel, ok)
    if public:
        bad = [e["id"] for e in meta_sources if e["redistribution"] != "public"]
        if bad:
            raise LocalOnlyInPublicBuild(f"meta.sources would list local_only sources {bad}")
    meta = {
        "api_version": API_VERSION,
        "contract": CONTRACT,
        "mode": mode,
        "built_at": built_at,
        "release_tag": tag,
        "release_url": release_url,
        "full_data_url": full_data_url,
        "n_funds": len(funds),
        "n_isins": len(aliases),
        "sources": meta_sources,
    }
    (api / "meta.json").write_bytes(_dumps(meta))

    budget = float(params.get("site_budget_mb", 800)) * 1e6
    floor = int(params.get("min_constituents", 50))
    cuts = []
    total = _dir_bytes(api)
    while total > budget and cap > floor:
        new_cap = max(floor, int(cap * min(0.9, budget / total)))
        affected = {fid for fid, n in n_rows.items() if n > new_cap}
        logger.warning(
            "API is %.1f MB, over the %.0f MB budget: cutting fund files from %d to %d constituents (%d funds)",
            total / 1e6, budget / 1e6, cap, new_cap, len(affected),
        )
        cap = new_cap
        new_sizes, _ = _write_fund_files(funds, rel, cap, api / "funds", full_data_url, only=affected, public=public,
                                         aggregates=agg)
        sizes.update(new_sizes)
        total = _dir_bytes(api)
        cuts.append({"max_constituents": cap, "funds_rewritten": len(affected), "api_mb": round(total / 1e6, 1)})
    if total > budget:
        logger.error("API is %.1f MB, still over the %.0f MB budget at %d constituents per fund", total / 1e6, budget / 1e6, cap)

    truncated = {fid: n - cap for fid, n in n_rows.items() if n > cap}
    if truncated:
        logger.info(
            "%d fund files cut to %d constituents; %d rows are only in %s",
            len(truncated), cap, sum(truncated.values()), LOOKTHROUGH_FILE,
        )
    report = quality_report(universe, universe_funds, universe_run, funds)
    report["shared_constituent_isins"] = shared_isins(rel, int(params.get("shared_isin_issuers", 20)))
    report["publishing"] = publishing_report(mode, universe_funds, funds, meta_sources, withheld, rows_by_source)
    report.update(
        release_tag=tag,
        built_at=built_at,
        api={
            "mb": round(total / 1e6, 2),
            "budget_mb": budget / 1e6,
            "over_budget": total > budget,
            "n_fund_files": len(sizes),
            "n_alias_files": len(aliases) - sum(1 for k, v in aliases.items() if k == v),
            "max_constituents_requested": requested_cap,
            "max_constituents_used": cap,
            "budget_cuts": cuts,
            "n_funds_truncated": len(truncated),
            "rows_only_in_release": int(sum(truncated.values())),
            "largest_truncated": sorted(truncated, key=truncated.get, reverse=True)[:20],
        },
    )
    report["release"] = {"files": {p.name: p.stat().st_size for p in (release / LOOKTHROUGH_FILE, release / FUNDS_FILE)}}
    report["publish_seconds"] = round(time.perf_counter() - t0, 1)
    (release / QUALITY_FILE).write_text(json.dumps(report, indent=2, ensure_ascii=False, default=_clean))
    logger.info("Published %d funds, %d ISINs, %.1f MB of API to %s", len(funds), len(aliases), total / 1e6, api)
    return report


def publishing_report(mode: str, funds: pd.DataFrame, published: dict, meta_sources: list[dict], withheld: list[str],
                      rows_by_source: dict[str, int] | None) -> dict:
    """The build mode, the sources published and withheld, and the funds listed as ``local_only``.

    ``local_only_by_issuer`` counts the published funds listed without
    holdings because only a ``local_only`` source has them, by issuer.
    """
    pub = funds[funds["id"].isin(list(published))]
    lo = pub[pub["status"] == "local_only"] if "status" in pub else pub.iloc[:0]
    return {
        "mode": mode,
        "sources_published": [e["id"] for e in meta_sources],
        # In a full build nothing is withheld; the list says what a public build would leave out.
        "sources_local_only": withheld,
        "withheld": withheld if mode != FULL_MODE else [],
        "rows_by_source": rows_by_source,
        "n_local_only": len(lo),
        "local_only_by_issuer": {k: int(v) for k, v in lo["issuer"].fillna("unknown").value_counts().items()},
    }


def shared_isins(rel: duckdb.DuckDBPyRelation, min_issuers: int = 20, top: int = 20) -> dict:
    """Constituent ISINs that rows attribute to more than ``min_issuers`` different issuer names.

    One security has one issuer, so such an ISIN is almost always a
    placeholder or an ISIN spread to lines it does not belong to.
    """
    r = rel  # noqa: F841 - read by DuckDB below
    rows = connection().sql(
        f"""
        select constituent_isin, count(distinct issuer_name) n_issuers, count(distinct fund_id) n_funds,
               sum(weight_pct) / nullif(count(distinct fund_id), 0) mean_weight
        from r where constituent_isin is not null and issuer_name is not null
        group by 1 having count(distinct issuer_name) > {int(min_issuers)}
        order by n_issuers desc
        """
    ).fetchall()
    return {
        "min_issuers": min_issuers,
        "n_isins": len(rows),
        "isins": [{"isin": i, "n_issuer_names": int(n), "n_funds": int(f)} for i, n, f, _ in rows[:top]],
    }


def message_kind(message: str) -> str:
    """A message with its numbers blanked, so the same failure on many funds counts as one kind."""
    return re.sub(r"\d+", "#", str(message))[:160]


def resolution_bucket(pct: float) -> str:
    """Resolved weight band; above 101 means the source's lines add up to more than the fund."""
    for upper, label in ((0, "below 0"), (50, "0 to 50"), (90, "50 to 90"), (99, "90 to 99")):
        if pct < upper:
            return label
    return "99 to 101" if pct <= 101 else "above 101"


def quality_report(universe: pd.DataFrame, funds: pd.DataFrame, run: dict, published: dict) -> dict:
    """Counts per source, resolution, timing, errors and funds whose lines do not add up to about 100%."""
    f = funds.copy()
    f["ok"] = f["error"].isna()
    f["published"] = f["id"].isin(list(published))
    if "status" in f:
        f["no_holdings"] = f["ok"] & (f["status"] == "no_holdings")
    else:
        f["no_holdings"] = f["ok"] & f["sources_tried"].isna()
    if "status" not in f:
        f["status"] = None
    by_source = {}
    listed = universe["universe_source"].value_counts().to_dict() if len(universe) else {}
    for src in sorted(set(listed) | set(f["universe_source"].dropna())):
        g = f[f["universe_source"] == src]
        by_source[src] = {
            "listed": int(listed.get(src, 0)), "attempted": len(g), "ok": int(g["ok"].sum()),
            "failed": int((~g["ok"]).sum()), "no_holdings": int(g["no_holdings"].sum()),
            "published": int(g["published"].sum()),
            "status": {k: int(v) for k, v in g["status"].value_counts().items()},
        }
    done = f[f["ok"] & ~f["no_holdings"]]
    resolved = done["resolved_pct"].astype(float)
    buckets = {k: int(v) for k, v in resolved.map(resolution_bucket).value_counts().items()}
    seconds = {
        src: {
            "n": len(g), "total": round(float(g.sum()), 1), "mean": round(float(g.mean()), 3),
            "median": round(float(g.median()), 3), "p95": round(float(g.quantile(0.95)), 3), "max": round(float(g.max()), 3),
        }
        for src, g in f.groupby("universe_source")["seconds"]
    }
    errors = (
        f.dropna(subset=["error"]).assign(key=lambda d: d["error"].map(message_kind))
        .groupby("key").agg(n=("id", "size"), examples=("id", lambda s: s.head(5).tolist()))
        .sort_values("n", ascending=False).head(20).reset_index()
    )
    off = done[(done["reported_total_pct"].astype(float) - 100).abs() > 5]
    off = off.assign(dev=(off["reported_total_pct"].astype(float) - 100).abs()).sort_values("dev", ascending=False)
    warn = (
        f.dropna(subset=["warnings"])["warnings"].str.split("; ").explode().map(message_kind)
        .value_counts().head(20)
    )
    return {
        "counts": {
            "by_universe_source": by_source,
            "by_holdings_source": {k: int(v) for k, v in done["source"].value_counts().items()},
            "matched_via": {k: int(v) for k, v in f["matched_via"].value_counts().items()},
            "series_match": {k: int(v) for k, v in f["series_match"].value_counts().items()},
            "status": {k: int(v) for k, v in f["status"].value_counts().items()},
            "published_by_status": {k: int(v) for k, v in f.loc[f["published"], "status"].value_counts().items()},
        },
        "unexpanded_funds": {
            "n_funds": int((done.get("unexpanded_fund_pct", pd.Series(dtype=float)).astype(float) > 0).sum()),
            "n_depth_capped": int(done.get("max_depth_reached", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()),
            "largest": [
                {"id": r.id, "name": r.name, "unexpanded_fund_pct": round(float(r.unexpanded_fund_pct), 3)}
                for r in done.sort_values("unexpanded_fund_pct", ascending=False).head(20).itertuples()
                if float(r.unexpanded_fund_pct or 0) > 0
            ] if "unexpanded_fund_pct" in done else [],
        },
        "resolution": {
            "n_funds": len(done),
            "resolved_pct_buckets": buckets,
            "resolved_pct_quantiles": {q: round(float(resolved.quantile(float(q))), 2) for q in ("0.1", "0.25", "0.5", "0.75", "0.9")}
            if len(resolved) else {},
        },
        "seconds_per_fund": seconds,
        "run": run,
        "top_errors": [{"error": r.key, "count": int(r.n), "examples": r.examples} for r in errors.itertuples()],
        "top_warnings": [{"warning": k, "count": int(v)} for k, v in warn.items()],
        "reported_total_off_by_more_than_5": [
            {"id": r.id, "name": r.name, "source": r.source, "reported_total_pct": round(float(r.reported_total_pct), 3)}
            for r in off.itertuples()
        ],
    }
