"""Coarse splits for funds whose holdings may not be republished.

A local (full) run reads issuer files whose terms do not allow openbasket to
republish their holdings. For those funds this module computes openbasket's
own coarse aggregates over the full holdings: the share of the fund in each
sector, country, region and asset class, rounded to whole percent. They are
written to ``public_aggregates/aggregates.json`` so the public site can show a
local_only fund's shape without any of what is behind it.

What the file may contain is fixed by :func:`validate`: fund ids, dates,
source ids, and lists of ``[label, whole percent]`` whose labels come from
fixed vocabularies (our sector list, ISO country codes, regions, asset
classes, and Other / Unknown / Unresolved). No names, ISINs, row weights or
issuer labels. The public build checks the file with the same function.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from .countries import country_code
from .datasets import connection
from .sources import redistribution

logger = logging.getLogger(__name__)

VERSION = 1
DIMENSIONS = ("sector", "country", "region", "asset_class")
OTHER, UNKNOWN, UNRESOLVED = "Other", "Unknown", "Unresolved"
#: A label below this share of the fund is folded into "Other".
MIN_PCT = 1

#: openbasket's sector list: the eleven GICS sector names, plus two for bonds.
SECTORS = (
    "Information Technology", "Health Care", "Financials", "Consumer Discretionary", "Consumer Staples",
    "Communication Services", "Industrials", "Energy", "Materials", "Utilities", "Real Estate",
    "Government Bonds", "Corporate Bonds",
)
#: Keywords in an issuer's sector label, mapped to openbasket's sector list. Checked in order.
_SECTOR_RULES = (
    (r"real estate|reit|immobil", "Real Estate"),
    (r"information tech|technolog|software|semiconduct|\bit\b|tech hardware", "Information Technology"),
    (r"health|pharma|biotech|medical|life science", "Health Care"),
    (r"communicat|telecom|media|interactive", "Communication Services"),
    (r"consumer (disc|cycl)|discretionary|cyclical|retail|automob|leisure", "Consumer Discretionary"),
    (r"consumer stap|staples|non.?cyclical|defensive|food|beverage|household", "Consumer Staples"),
    (r"financ|bank|insur|capital market", "Financials"),
    (r"industr|aerospace|defen[cs]e|transport|machinery|capital goods", "Industrials"),
    (r"energy|oil|gas", "Energy"),
    (r"material|chemical|metal|mining|basic", "Materials"),
    (r"utilit", "Utilities"),
    (r"treasur|government|sovereign|agency|municipal|supranational", "Government Bonds"),
    (r"corporate|credit", "Corporate Bonds"),
)
REGIONS = ("North America", "Europe", "Asia-Pacific", "Latin America", "Middle East & Africa")
_REGION_OF = {}
for _region, _codes in {
    "North America": "US CA BM",
    "Europe": "GB IE FR DE NL BE LU CH AT IT ES PT SE NO DK FI IS PL CZ HU SK SI HR RO BG GR CY MT EE LV LT JE GG IM LI MC",
    "Asia-Pacific": "JP CN HK TW KR SG IN ID TH MY PH VN AU NZ PK BD LK MO KY",
    "Latin America": "BR MX CL CO PE AR UY PA VG BS",
    "Middle East & Africa": "IL SA AE QA KW BH OM TR EG ZA NG KE MA",
}.items():
    for _c in _codes.split():
        _REGION_OF[_c] = _region
ASSET_CLASSES = ("Equity", "Bonds", "Cash", "Derivatives", "Funds", "Other assets")
_ISIN_LIKE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b")


def sector_of(label: object, asset_class: object = None) -> str:
    """openbasket's sector for an issuer's sector label (and the row's asset class)."""
    text = str(label or "").strip().lower()
    for pattern, sector in _SECTOR_RULES:
        if text and re.search(pattern, text):
            return sector
    ac = asset_class_of(asset_class)
    if ac == "Bonds":
        return "Corporate Bonds" if "corp" in text else UNKNOWN
    return OTHER if text else UNKNOWN


def asset_class_of(label: object) -> str:
    text = str(label or "").lower()
    if not text:
        return UNKNOWN
    if "derivative" in text or "future" in text or "swap" in text or "forward" in text:
        return "Derivatives"
    if text.startswith(("equity", "preferred")) or "stock" in text or "share" in text:
        return "Equity"
    if any(k in text for k in ("debt", "bond", "loan", "asset-backed", "mortgage", "fixed income", "note")):
        return "Bonds"
    if "cash" in text or "money market" in text or "short-term" in text or "repurchase" in text:
        return "Cash"
    if "fund" in text:
        return "Funds"
    return "Other assets"


def region_of(code: str | None) -> str:
    return _REGION_OF.get(code or "", UNKNOWN if not code else OTHER)


def _split(sums: dict[str, float], unresolved_pct: float) -> list[list]:
    """Whole-percent ``[label, pct]`` list, largest first; small labels folded into Other, Unresolved last."""
    kept, other = {}, 0.0
    for label, pct in sums.items():
        if abs(pct) >= MIN_PCT - 0.5 and label not in (OTHER,):
            kept[label] = pct
        else:
            other += pct
    out = [[k, int(round(v))] for k, v in sorted(kept.items(), key=lambda kv: -kv[1]) if int(round(v)) != 0]
    if int(round(other)):
        out.append([OTHER, int(round(other))])
    out.append([UNRESOLVED, int(round(unresolved_pct or 0.0))])
    return out


def fund_aggregates(rows: pd.DataFrame, unresolved_pct: float | None) -> dict[str, list[list]]:
    """Splits for one fund from its full constituent rows (weight_pct, sector, country, asset_class)."""
    sums: dict[str, dict[str, float]] = {d: {} for d in DIMENSIONS}
    for r in rows.itertuples(index=False):
        w = float(r.weight_pct or 0.0)
        code = country_code(r.country) or None
        labels = {
            "sector": sector_of(r.sector, r.asset_class),
            "country": code or UNKNOWN,
            "region": region_of(code),
            "asset_class": asset_class_of(r.asset_class),
        }
        for d, label in labels.items():
            sums[d][label] = sums[d].get(label, 0.0) + w
    return {d: _split(sums[d], unresolved_pct or 0.0) for d in DIMENSIONS}


def build(universe_funds: pd.DataFrame, universe_constituents: duckdb.DuckDBPyRelation, params: dict,
          sector_map: pd.DataFrame | None = None, publish_params: dict | None = None) -> dict:
    """The aggregates document for every fund whose holdings come from a ``local_only`` source.

    A row whose issuer file gives no sector takes its company's sector from
    ``sector_map`` (company ISIN -> GICS sector, see
    :func:`openbasket.sources.financedatabase.sector_map`), as the public build does.

    The file already at ``publish_params.aggregates_path`` is merged in (see
    :func:`merge`): a fund this run has no holdings for, for example because
    its issuer's site failed, keeps its earlier splits and their date.
    """
    funds = universe_funds[universe_funds["source"].map(lambda s: bool(s) and redistribution(str(s)) == "local_only")]
    funds = funds[funds["n_constituents"].fillna(0) > 0]
    con = connection()
    con.register("_agg_rel", universe_constituents)
    has_map = sector_map is not None and len(sector_map) > 0
    if has_map:
        con.register("_agg_sectors", sector_map[["isin", "sector"]])
    sector = "coalesce(nullif(trim(r.sector), ''), m.sector)" if has_map else "r.sector"
    join = "left join _agg_sectors m on r.constituent_isin = m.isin" if has_map else ""
    try:
        rows = con.execute(
            f"select r.fund_id, r.weight_pct, {sector} as sector, r.country, r.asset_class from _agg_rel r {join} "
            "where r.fund_id in (select unnest(?))", [funds["id"].tolist()]
        ).df()
    finally:
        con.unregister("_agg_rel")
        if has_map:
            con.unregister("_agg_sectors")
    by_fund = dict(tuple(rows.groupby("fund_id"))) if len(rows) else {}
    computed_at = params.get("computed_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {}
    for f in funds.to_dict("records"):
        r = by_fund.get(f["id"])
        if r is None or r.empty:
            continue
        out[f["id"]] = {
            "isins": [i for i in (f.get("isins") if f.get("isins") is not None else []) if i],
            "as_of": f.get("as_of"),
            "holdings_source": f.get("source"),
            "computed_at": computed_at,
            **fund_aggregates(r, f.get("unresolved_pct")),
        }
    previous = _read_previous((publish_params or {}).get("aggregates_path")) if params.get("keep_previous", True) else None
    out, kept = merge(out, previous, computed_at, params.get("keep_days", 180))
    if kept:
        logger.info("Kept the earlier splits of %d funds this run has no holdings for", kept)
    doc = {
        "version": VERSION,
        "computed_at": computed_at,
        "method": ("openbasket's own aggregates over the fund's full holdings: shares of net assets by "
                   "openbasket's sector list, ISO country, region and asset class, rounded to whole percent; "
                   "labels under 1% are folded into Other. Rows whose issuer gives no sector take their "
                   "company's GICS sector from FinanceDatabase (MIT) by ISIN. No holdings are included."),
        "n_funds": len(out),
        "n_kept": kept,
        "funds": dict(sorted(out.items())),
    }
    validate(doc)
    return doc


def _read_previous(path: str | None) -> dict | None:
    if not path or not Path(path).is_file():
        return None
    try:
        doc = json.loads(Path(path).read_text())
        validate(doc)
    except (ValueError, OSError) as exc:  # a broken earlier file is not kept, and not a reason to stop
        logger.warning("Not keeping the earlier splits in %s: %s", path, exc)
        return None
    return doc


def merge(new: dict, previous: dict | None, computed_at: str, keep_days: int | None) -> tuple[dict, int]:
    """``new`` plus each fund of ``previous`` that ``new`` lacks, if computed within ``keep_days``; and how many were kept.

    A kept entry keeps its own ``computed_at`` (the file's, for files written
    before entries had one), so the site shows how old it is.
    """
    out = dict(new)
    if not previous:
        return out, 0
    now = datetime.fromisoformat(computed_at.replace("Z", "+00:00"))
    taken = {i for e in new.values() for i in e.get("isins") or []} | set(new)
    kept = 0
    for fid, entry in (previous.get("funds") or {}).items():
        if fid in taken or any(i in taken for i in entry.get("isins") or []):
            continue
        when = entry.get("computed_at") or previous.get("computed_at")
        try:
            age = (now - datetime.fromisoformat(str(when).replace("Z", "+00:00"))).days
        except ValueError:
            continue
        if keep_days is not None and age > keep_days:
            continue
        out[fid] = {**entry, "computed_at": when}
        kept += 1
    return out, kept


class AggregatesError(ValueError):
    """The aggregates file holds something other than coarse splits."""


def validate(doc: dict) -> None:
    """Raise :class:`AggregatesError` unless ``doc`` holds only coarse splits from fixed vocabularies."""
    top = {"version", "computed_at", "method", "n_funds", "n_kept", "funds"}
    if set(doc) - top:
        raise AggregatesError(f"unexpected top-level keys {sorted(set(doc) - top)}")
    allowed = {
        "sector": set(SECTORS),
        "region": set(REGIONS),
        "asset_class": set(ASSET_CLASSES),
    }
    specials = {OTHER, UNKNOWN, UNRESOLVED}
    for fid, entry in (doc.get("funds") or {}).items():
        keys = {"isins", "as_of", "holdings_source", "computed_at", *DIMENSIONS}
        if set(entry) - keys:
            raise AggregatesError(f"{fid}: unexpected keys {sorted(set(entry) - keys)}")
        if entry.get("computed_at") is not None and not re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:]{8}Z", str(entry["computed_at"])):
            raise AggregatesError(f"{fid}: computed_at must be a UTC timestamp")
        for d in DIMENSIONS:
            for item in entry.get(d) or []:
                if not (isinstance(item, list) and len(item) == 2 and isinstance(item[1], int)):
                    raise AggregatesError(f"{fid}.{d}: {item!r} is not [label, whole percent]")
                label = item[0]
                if label in specials:
                    continue
                ok = re.fullmatch(r"[A-Z]{2}", label) is not None if d == "country" else label in allowed[d]
                if not ok:
                    raise AggregatesError(f"{fid}.{d}: label {label!r} is not in openbasket's vocabulary")
        if not isinstance(entry.get("holdings_source"), (str, type(None))):
            raise AggregatesError(f"{fid}: holdings_source must be a source id")
    # Fund ids and their own ISINs name the fund and may appear; nothing else may look like an ISIN.
    text = " ".join(str(doc.get(k) or "") for k in ("computed_at", "method")) + " " + " ".join(
        str(item[0]) for e in (doc.get("funds") or {}).values() for d in DIMENSIONS for item in (e.get(d) or []))
    if _ISIN_LIKE.search(text):
        raise AggregatesError("an ISIN-like string appears in the splits")


def _git(args: list[str], cwd: Path, env: dict | None = None, input_text: str | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, env=env, input=input_text, capture_output=True, text=True,
                          check=True, timeout=300).stdout.strip()


def share(doc: dict, params: dict, path: str) -> dict:
    """Commit the aggregates file at ``path``, and only that file, to ``params.share.branch`` of the repository.

    Off unless ``params.share.enabled``. The file is checked with
    :func:`validate` first, so only coarse splits can be committed. The
    commit is made on top of the remote branch as it is now, with a private
    index: the working tree, the checked-out branch and anything staged are
    left alone, and no other path changes. Nothing is committed when the
    file is what the branch already holds. With ``share.push`` the commit is
    pushed; a refused push (someone else pushed first) is retried once.
    """
    cfg = params.get("share") or {}
    if not cfg.get("enabled"):
        return {"shared": False, "reason": "aggregates.share.enabled is false"}
    validate(doc)
    file = Path(path).resolve()
    repo = Path(_git(["rev-parse", "--show-toplevel"], file.parent))
    rel_path = file.relative_to(repo).as_posix()
    remote, branch = cfg.get("remote", "origin"), cfg.get("branch", "main")
    for attempt in (1, 2):
        _git(["fetch", "-q", remote, branch], repo)
        base = _git(["rev-parse", "FETCH_HEAD"], repo)
        blob = _git(["hash-object", "-w", "--", str(file)], repo)
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
            _git(["read-tree", base], repo, env)
            _git(["update-index", "--add", "--cacheinfo", f"100644,{blob},{rel_path}"], repo, env)
            tree = _git(["write-tree"], repo, env)
        if tree == _git(["rev-parse", f"{base}^{{tree}}"], repo):
            return {"shared": False, "reason": f"{rel_path} is unchanged on {remote}/{branch}", "base": base}
        message = cfg.get("message", "Update coarse splits of local_only funds ({n_funds} funds)").format(
            n_funds=doc.get("n_funds"), computed_at=doc.get("computed_at"))
        commit = _git(["commit-tree", tree, "-p", base, "-F", "-"], repo, input_text=message + "\n")
        changed = _git(["diff-tree", "--no-commit-id", "--name-only", "-r", base, commit], repo).splitlines()
        if changed != [rel_path]:  # cannot happen with one update-index above; refuse rather than push anything else
            raise AggregatesError(f"the commit would change {changed}, not only {rel_path}")
        if not cfg.get("push", False):
            return {"shared": False, "reason": "aggregates.share.push is false", "commit": commit, "base": base}
        try:
            _git(["push", "-q", remote, f"{commit}:refs/heads/{branch}"], repo)
        except subprocess.CalledProcessError as exc:
            if attempt == 2:
                raise
            logger.warning("Push of %s refused (%s); fetching %s/%s again", rel_path, exc.stderr.strip(), remote, branch)
            continue
        logger.info("Committed %s (%d funds) to %s/%s as %s", rel_path, doc.get("n_funds") or 0, remote, branch, commit[:7])
        return {"shared": True, "commit": commit, "base": base, "branch": branch, "path": rel_path}
    return {"shared": False, "reason": "push refused twice"}
