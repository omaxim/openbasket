"""Turn a universe run into the static JSON API and the release files.

The shapes are those in ``docs/source/data-api.md``. Every fund file names
its source, a link, the as-of date and a credit line; every constituent row
names its own source and as-of date; ``meta.json`` lists every source that
contributed a row, with its licence and credit, from ``publish.sources``.

Fund files are cut to ``max_constituents`` rows, and if the API still comes
out larger than ``site_budget_mb`` the cut is lowered and the large funds are
rewritten. Each cut is logged and recorded in the quality report; the full
lists are always in the release file.
"""

from __future__ import annotations

import json
import logging
import math
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from ...datasets import connection

logger = logging.getLogger(__name__)

API_VERSION = 1
LOOKTHROUGH_FILE = "openbasket-lookthrough.parquet"
FUNDS_FILE = "openbasket-funds.parquet"
QUALITY_FILE = "quality-report.json"

CONSTITUENT_FIELDS = ["isin", "name", "weight_pct", "asset_class", "source", "as_of", "depth", "via"]
INDEX_FIELDS = ["id", "isin", "name", "ticker", "issuer", "kind", "source", "as_of", "resolved_pct", "n_constituents"]
#: Fund-file keys in contract order.
FUND_KEYS = [
    "id", "isin", "isins", "name", "ticker", "issuer", "source", "source_url", "credit", "as_of", "matched_via",
    "note", "reported_total_pct", "resolved_pct", "unresolved_pct", "unidentified_lines_pct", "n_constituents",
]
_ID_RE = re.compile(r"^[A-Z0-9]{9,12}$")


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


def fund_document(fund: dict, rows: list[tuple], n_rows: int, full_data_url: str) -> dict:
    """One ``funds/{id}.json``. ``rows`` are the kept constituent rows, ``n_rows`` how many there are in all."""
    doc = {k: _clean(fund.get(k)) for k in FUND_KEYS}
    doc["isins"] = _isins(fund.get("isins"))
    for k in ("reported_total_pct", "resolved_pct", "unresolved_pct", "unidentified_lines_pct"):
        doc[k] = _num(fund.get(k))
    doc["n_constituents"] = int(_clean(fund.get("n_constituents")) or 0)
    doc["truncated"] = n_rows > len(rows)
    doc["fields"] = CONSTITUENT_FIELDS
    doc["constituents"] = [
        [isin, name, _num(w), ac, src, as_of, int(depth) if depth is not None else None, via]
        for isin, name, w, ac, src, as_of, depth, via in rows
    ]
    doc["unresolved"] = {"weight_pct": doc["unresolved_pct"], "note": doc["note"]}
    doc["full_data_url"] = full_data_url
    return doc


def _stream_constituents(rel: duckdb.DuckDBPyRelation, cap: int, ids: set[str], chunk: int = 500):
    """Yield ``(fund_id, n_rows, rows)`` with each fund's heaviest ``cap`` rows, largest absolute weight first.

    Funds are read a few hundred at a time by id range, which the Parquet
    file's sort order lets DuckDB prune, so no query result stays open while
    files are written.
    """
    con = connection()
    ordered = sorted(ids)
    for start in range(0, len(ordered), chunk):
        part = ordered[start : start + chunk]
        con.register("_publish_rel", rel)
        try:
            found = con.execute(
                f"""
                select fund_id, n, constituent_isin, name, weight_pct, asset_class, source, as_of, depth, via_fund
                from (
                    select *, row_number() over (partition by fund_id order by abs(weight_pct) desc, constituent_isin) rn,
                           count(*) over (partition by fund_id) n
                    from _publish_rel where constituent_isin is not null and fund_id between ? and ?
                )
                where rn <= {int(cap)}
                order by fund_id, rn
                """,
                [part[0], part[-1]],
            ).fetchall()
        finally:
            con.unregister("_publish_rel")
        wanted = set(part)
        fid, n, rows = None, 0, []
        for f, count, *row in found:
            if f != fid:
                if fid in wanted:
                    yield fid, n, rows
                fid, n, rows = f, count, []
            rows.append(tuple(row))
        if fid in wanted:
            yield fid, n, rows


def _write_fund_files(funds: dict[str, dict], rel, cap: int, out: Path, full_data_url: str,
                      only: set[str] | None = None) -> tuple[dict[str, int], dict[str, int]]:
    """Write fund files; returns bytes per file and constituent rows per fund."""
    sizes, n_rows = {}, {}
    for fid, n, rows in _stream_constituents(rel, cap, only if only is not None else set(funds)):
        if fid in funds:
            data = _dumps(fund_document(funds[fid], rows, n, full_data_url))
            (out / f"{fid}.json").write_bytes(data)
            sizes[fid], n_rows[fid] = len(data), n
    for fid in (only if only is not None else funds.keys()) - sizes.keys():
        data = _dumps(fund_document(funds[fid], [], 0, full_data_url))
        (out / f"{fid}.json").write_bytes(data)
        sizes[fid], n_rows[fid] = len(data), 0
    return sizes, n_rows


def _source_entries(sources_cfg: dict, rel, funds: pd.DataFrame) -> list[dict]:
    """``meta.sources``: every source that contributed a row or a fund, with its credit and dates."""
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
            where name_source = 'openfigi' or composite_figi is not null
            having count(*) > 0
            """
        ).fetchall()
    finally:
        con.unregister("_publish_rel")
        con.unregister("_publish_ids")
    found = {s: {"n_funds": n, "as_of_min": lo, "as_of_max": hi} for s, n, lo, hi in stats}
    # Funds with no holdings are still credited to the list they came from.
    for src, g in funds[~funds["id"].isin(_ids_with_sources(rel))].groupby("source"):
        e = found.setdefault(src, {"n_funds": 0, "as_of_min": None, "as_of_max": None})
        e["n_funds"] += len(g)
    entries = []
    for sid in list(sources_cfg) + sorted(set(found) - set(sources_cfg)):
        if sid not in found:
            continue
        cfg = sources_cfg.get(sid)
        if cfg is None:
            logger.warning("Source %r contributed rows but has no entry in publish.sources", sid)
            cfg = {"name": sid, "url": None, "licence": None, "credit": sid}
        entries.append({"id": sid, **{k: cfg.get(k) for k in ("name", "url", "licence", "credit")}, **found[sid]})
    return entries


def _ids_with_sources(rel) -> set[str]:
    r = rel  # noqa: F841 - read by DuckDB below
    return {x[0] for x in connection().sql("select distinct fund_id from r where source is not null").fetchall()}


def _dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def publish(
    universe: pd.DataFrame,
    universe_funds: pd.DataFrame,
    universe_constituents: duckdb.DuckDBPyRelation,
    universe_run: dict,
    params: dict,
) -> dict:
    """Write ``api/v1`` under ``site_dir`` and the release files under ``release_dir``; return the quality report."""
    t0 = time.perf_counter()
    now = datetime.now(timezone.utc)
    built_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    tag = str(params.get("release_tag") or now.strftime("%Y-%m"))
    release_url, full_data_url = release_urls(params["repo"], tag)
    sources_cfg = params["sources"]

    release = Path(params["release_dir"])
    release.mkdir(parents=True, exist_ok=True)
    rel = universe_constituents
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
    sizes, n_rows = _write_fund_files(funds, rel, cap, api / "funds", full_data_url)

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
             int(_clean(f.get("n_constituents")) or 0)]
            for fid, f in sorted(funds.items())
        ],
        "aliases": dict(sorted(aliases.items())),
    }
    (api / "index.json").write_bytes(_dumps(index))
    meta = {
        "api_version": API_VERSION,
        "built_at": built_at,
        "release_tag": tag,
        "release_url": release_url,
        "full_data_url": full_data_url,
        "n_funds": len(funds),
        "n_isins": len(aliases),
        "sources": _source_entries(sources_cfg, rel, ok),
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
        new_sizes, _ = _write_fund_files(funds, rel, cap, api / "funds", full_data_url, only=affected)
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
    f["no_holdings"] = f["ok"] & f["sources_tried"].isna()
    by_source = {}
    listed = universe["universe_source"].value_counts().to_dict() if len(universe) else {}
    for src in sorted(set(listed) | set(f["universe_source"].dropna())):
        g = f[f["universe_source"] == src]
        by_source[src] = {
            "listed": int(listed.get(src, 0)), "attempted": len(g), "ok": int(g["ok"].sum()),
            "failed": int((~g["ok"]).sum()), "no_holdings": int(g["no_holdings"].sum()),
            "published": int(g["published"].sum()),
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
