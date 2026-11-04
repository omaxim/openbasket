"""Rebuild the fixture API in ``site/fixtures/api/v1`` from a public-only build.

The fixtures stand in for the real API (``docs/source/data-api.md``, v1.1)
while the landing page is developed and tested. The repository is public, so
they hold nothing from a ``local_only`` source (see
``openbasket.sources.redistribution``):

- Fund files with holdings are copied from a ``publish`` output that was
  built with public sources only (``site/fixtures/conf/parameters.yml``:
  SEC N-PORT and TickerInside). Every source named in a copied file, its
  rows, its breakdowns or its credit must be ``public``; anything else stops
  the script.
- Funds that only a ``local_only`` issuer covers are written as the public
  build shows them: identity only (name, ISIN, ticker, issuer and the
  issuer's fund page, from the issuer's public fund list), status
  ``local_only``, no constituents, and a note that points to running
  openbasket yourself.
- One made-up fund (``SYNTHETIC``) has sector labels, a sub-fund that is
  looked through, one that is not, and a short position, so the page's
  sector view can be tried without any issuer's data. Its rows are written
  here, not read from any source.

``index.json`` gains a ``status`` field after the contract's fields, so the
search can mark ``local_only`` funds.

Usage (from the repository root)::

    mkdir -p conf/fixtures && cp site/fixtures/conf/parameters.yml conf/fixtures/
    kedro run --env fixtures --pipeline=universe
    kedro run --env fixtures --pipeline=publish
    .venv/bin/python site/fixtures/make_fixtures.py --api build/fixture-site/api [--data data]
    rm -r conf/fixtures

This script is a development tool. It is not deployed (``assemble_site.py``
leaves ``site/fixtures/`` out) and the real files come from the ``publish``
pipeline.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openbasket.identifiers import is_valid_isin, isin_check_digit  # noqa: E402
from openbasket.pipelines.publish.nodes import FUND_FILE_KEYS, INDEX_FIELDS, fund_breakdowns, fund_document  # noqa: E402
from openbasket.sources import redistribution  # noqa: E402

API = HERE / "api" / "v1"
BUILT_AT = "2026-11-03T12:00:00Z"
PRODUCT_LIST_DATE = "2026-11-03"
RUN_LOCALLY_URL = "https://openbasket.leithresearch.org/docs/running-locally.html"
#: The made-up fund's source id; the only id allowed here that is not ``public``.
SYNTHETIC = "synthetic"

#: Funds only a local_only issuer covers: (ISIN, issuer source id, other share-class ISINs).
LOCAL_ONLY = [
    ("IE00B5BMR087", "ishares", []),
    ("IE00B4L5Y983", "ishares", []),
    ("IE000OY7BDY9", "ishares", []),
    ("IE000TOBHQ26", "ishares", []),
    ("IE00B6YX5C33", "spdr", ["IE000XZSV718"]),
    ("US78467X1090", "spdr", []),
]
ISSUER = {
    "ishares": {
        "issuer": "iShares", "listed_by": "iShares by BlackRock",
        "name": "iShares (BlackRock)", "url": "https://www.ishares.com/",
    },
    "spdr": {
        "issuer": "SPDR", "listed_by": "SPDR ETFs by State Street Global Advisors",
        "name": "SPDR (State Street)", "url": "https://www.ssga.com/",
    },
}
LOCAL_ONLY_LICENCE = "Issuer terms do not allow republication; read only when you run openbasket yourself"

#: (ISIN, name, weight, asset class, country, sector, via or None for a direct holding).
_SYNTHETIC_ROWS = [
    ("US0378331005", "APPLE INC", 6.1, "equity", "US", "Information Technology", None),
    ("US5949181045", "MICROSOFT CORP", 5.4, "equity", "US", "Information Technology", None),
    ("US67066G1040", "NVIDIA CORP", 4.9, "equity", "US", "Information Technology", None),
    ("NL0010273215", "ASML HOLDING NV", 2.2, "equity", "NL", "Information Technology", None),
    ("TW0002330008", "TAIWAN SEMICONDUCTOR MANUFACTURING", 2.0, "equity", "TW", "Information Technology", None),
    ("KR7005930003", "SAMSUNG ELECTRONICS CO LTD", 1.4, "equity", "KR", "Information Technology", None),
    ("DE0007164600", "SAP SE", 1.1, "equity", "DE", "Information Technology", None),
    ("US46625H1005", "JPMORGAN CHASE & CO", 3.2, "equity", "US", "Financials", None),
    ("US92826C8394", "VISA INC CLASS A", 2.1, "equity", "US", "Financials", None),
    ("GB0005405286", "HSBC HOLDINGS PLC", 1.3, "equity", "GB", "Financials", None),
    ("ES0113900J37", "BANCO SANTANDER SA", 0.9, "equity", "ES", "Financials", None),
    ("US6174464486", "MORGAN STANLEY", 0.8, "equity", "US", "Financials", None),
    ("US4781601046", "JOHNSON & JOHNSON", 2.0, "equity", "US", "Health Care", None),
    ("DK0062498333", "NOVO NORDISK CLASS B", 1.6, "equity", "DK", "Health Care", None),
    ("CH0012032048", "ROCHE HOLDING PAR AG", 1.4, "equity", "CH", "Health Care", None),
    ("GB0009895292", "ASTRAZENECA PLC", 1.3, "equity", "GB", "Health Care", None),
    ("US0231351067", "AMAZON COM INC", 3.6, "equity", "US", "Consumer Discretionary", None),
    ("US88160R1014", "TESLA INC", 1.7, "equity", "US", "Consumer Discretionary", None),
    ("FR0000121014", "LVMH", 1.2, "equity", "FR", "Consumer Discretionary", None),
    ("JP3633400001", "TOYOTA MOTOR CORP", 1.1, "equity", "JP", "Consumer Discretionary", None),
    ("US02079K3059", "ALPHABET INC CLASS A", 3.3, "equity", "US", "Communication", None),
    ("US30231G1022", "EXXON MOBIL CORP", 1.5, "equity", "US", "Energy", None),
    ("US1667641005", "CHEVRON CORP", 1.0, "equity", "US", "Energy", None),
    ("FR0000120271", "TOTALENERGIES", 0.9, "equity", "FR", "Energy", None),
    ("CH0038863350", "NESTLE SA", 1.5, "equity", "CH", "Consumer Staples", None),
    ("US7427181091", "PROCTER & GAMBLE", 1.4, "equity", "US", "Consumer Staples", None),
    ("US1491231015", "CATERPILLAR INC", 1.1, "equity", "US", "Industrials", None),
    ("DE0007236101", "SIEMENS N AG", 1.0, "equity", "DE", "Industrials", None),
    ("IT0003128367", "ENEL", 0.7, "equity", "IT", "Utilities", None),
    ("US4581401001", "INTEL CORP (SHORT)", -0.6, "equity", "US", "Information Technology", None),
    # Through a sub-fund that is looked through.
    ("US0378331005", "APPLE INC", 1.9, "equity", "US", "Information Technology", "Synthetic Global Tech Sub-fund"),
    ("US67066G1040", "NVIDIA CORP", 1.7, "equity", "US", "Information Technology", "Synthetic Global Tech Sub-fund"),
    ("NL0010273215", "ASML HOLDING NV", 1.2, "equity", "NL", "Information Technology", "Synthetic Global Tech Sub-fund"),
    ("JP3633400001", "TOYOTA MOTOR CORP", 0.8, "equity", "JP", "Consumer Discretionary", "Synthetic Global Tech Sub-fund"),
    ("US912810TM09", "UNITED STATES TREASURY 4.25% 2053", 7.5, "debt", "US", "Government", "Synthetic Bond Sub-fund"),
    ("DE0001102614", "BUNDESREPUBLIK DEUTSCHLAND 1.8% 2053", 4.2, "debt", "DE", "Government", "Synthetic Bond Sub-fund"),
    ("GB00BMBL1D50", "UNITED KINGDOM GILT 1.5% 2053", 3.1, "debt", "GB", "Government", "Synthetic Bond Sub-fund"),
]
#: A sub-fund no source has holdings for: listed, not looked through.
_SYNTHETIC_UNEXPANDED = ("ZZ0000000016", "SYNTHETIC EM SUB-FUND, NOT LOOKED THROUGH", 4.5, "fund", "IE", None, None)


def _synthetic_id() -> str:
    body = "ZZ000000000"
    return body + str(isin_check_digit(body))


def _fail(msg: str) -> None:
    raise SystemExit(f"make_fixtures: {msg}")


def _sources_named(doc: dict) -> set[str]:
    """Every source id a fund file names: its own, its rows', its breakdown's and its unresolved entry's."""
    ix = {n: i for i, n in enumerate(doc.get("fields") or [])}
    named = {doc.get("source"), (doc.get("unresolved") or {}).get("source")}
    named |= {r[ix["source"]] for r in doc.get("constituents") or []}
    named |= {k for k, _ in (doc.get("breakdowns") or {}).get("source", []) if k not in ("Unresolved", "Unknown")}
    return {s for s in named if s}


def check_public(doc: dict, allow: frozenset[str] = frozenset()) -> None:
    """Stop unless every source the file names may be republished."""
    bad = sorted(s for s in _sources_named(doc) if s not in allow and redistribution(s) != "public")
    if bad:
        _fail(f"{doc.get('id')} names local_only sources {bad}; the fixtures are public and may not hold them")


def _registrants(data: Path) -> dict[str, str]:
    """Accession number -> registrant name, from the cached N-PORT quarter's REGISTRANT.tsv."""
    out: dict[str, str] = {}
    for zp in sorted((data / "01_raw" / "nport").glob("*_nport.zip")):
        with zipfile.ZipFile(zp) as z:
            if "REGISTRANT.tsv" not in z.namelist():
                continue
            with z.open("REGISTRANT.tsv") as f:
                for r in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"), delimiter="\t"):
                    out[r["ACCESSION_NUMBER"]] = r["REGISTRANT_NAME"].strip()
    return out


def _accession(url: str | None) -> str | None:
    m = re.search(r"/Archives/edgar/data/\d+/(\d{10})(\d{2})(\d{6})/", url or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def local_only_document(fund: dict, full_data_url: str) -> dict:
    """A fund only a local_only source covers, as the public build lists it: identity, no holdings."""
    issuer = ISSUER[fund["source"]]
    note = (f"Holdings for this fund are available from {issuer['issuer']} when you run openbasket yourself: "
            f"{RUN_LOCALLY_URL}")
    meta = {
        **fund, "issuer": issuer["issuer"], "credit": f"Fund listed by {issuer['listed_by']}; its holdings are not "
        "republished here", "as_of": PRODUCT_LIST_DATE, "matched_via": fund["source"], "note": note,
        "status": "local_only", "reported_total_pct": None, "resolved_pct": 0.0, "unexpanded_fund_pct": 0.0,
        "unresolved_pct": 100.0, "unidentified_lines_pct": None, "n_constituents": 0,
    }
    return fund_document(meta, [], 0, full_data_url)


def synthetic_document(full_data_url: str) -> dict:
    fid = _synthetic_id()
    name = "Synthetic Example Fund of Funds"
    rows = []
    for isin, nm, w, ac, country, sector, via in [*_SYNTHETIC_ROWS, _SYNTHETIC_UNEXPANDED]:
        if not is_valid_isin(isin):
            _fail(f"synthetic row {nm} has an invalid ISIN {isin}")
        rows.append((isin, nm, w, ac, SYNTHETIC, PRODUCT_LIST_DATE, 1 if via else 0, via or name, country, sector))
    rows.sort(key=lambda r: (-abs(r[2]), r[0], r[7]))
    unexpanded = _SYNTHETIC_UNEXPANDED[2]
    resolved = round(sum(r[2] for r in rows) - unexpanded, 5)
    unresolved = round(100.0 - resolved - unexpanded, 5)
    meta = {
        "id": fid, "isin": fid, "isins": [fid], "name": name, "ticker": None, "issuer": "openbasket fixtures",
        "source": SYNTHETIC, "source_url": "https://openbasket.leithresearch.org/docs/data-api.html",
        "credit": "Made-up rows for testing the page; not a real fund", "as_of": PRODUCT_LIST_DATE,
        "matched_via": "directory", "status": "ok",
        "note": ("Not a real fund: made-up weights over real company ISINs, to show sector labels, a sub-fund that "
                 "is looked through, one that is not, and a short position."),
        "reported_total_pct": round(sum(r[2] for r in _SYNTHETIC_ROWS) + unexpanded + 1.6, 5),
        "resolved_pct": resolved, "unexpanded_fund_pct": unexpanded, "unresolved_pct": unresolved,
        "unidentified_lines_pct": 1.6, "n_constituents": len({r[0] for r in rows}),
    }
    bd = fund_breakdowns([(r[2], {"asset_class": r[3], "country": r[8], "sector": r[9], "source": r[4]}, r[0])
                          for r in rows], unresolved)
    return fund_document(meta, rows, len(rows), full_data_url, bd)


def build(api_in: Path, data: Path) -> None:
    src_api = api_in / "v1"
    meta_in = json.loads((src_api / "meta.json").read_text())
    full_data_url = meta_in["full_data_url"]
    registrants = _registrants(data)
    products = pd.read_parquet(data / "01_raw" / "issuers" / "issuer_products.parquet")
    by_isin = {r["isin"]: r for r in products.to_dict("records")}

    docs: dict[str, dict] = {}
    aliases: dict[str, str] = {}
    for p in sorted((src_api / "funds").glob("*.json")):
        doc = json.loads(p.read_text())
        if "alias_of" in doc:
            aliases[p.stem] = doc["alias_of"]
            continue
        check_public(doc)
        if not doc.get("issuer") and doc.get("source") == "nport":
            doc["issuer"] = registrants.get(_accession(doc.get("source_url")))
        if list(doc) != FUND_FILE_KEYS:
            _fail(f"{p.name} does not have the contract's keys in order")
        docs[doc["id"]] = doc

    for isin, source, share_classes in LOCAL_ONLY:
        if redistribution(source) == "public":
            _fail(f"{source} is public; list {isin} with its holdings instead")
        prod = by_isin.get(isin)
        if prod is None:
            _fail(f"{isin} is not in the cached {source} fund list")
        ticker = prod.get("ticker")
        fund = {
            "id": isin, "isin": isin, "isins": [isin, *share_classes], "name": prod["fund_name"].replace("\xa0", " "),
            "ticker": ticker.split(" ")[0] if isinstance(ticker, str) and ticker else None,
            "source": source, "source_url": prod["product_page_url"],
        }
        docs[isin] = local_only_document(fund, full_data_url)
        for sc in share_classes:
            aliases[sc] = isin

    syn = synthetic_document(full_data_url)
    check_public(syn, allow=frozenset({SYNTHETIC}))
    docs[syn["id"]] = syn

    out = API / "funds"
    out.mkdir(parents=True, exist_ok=True)
    for p in out.glob("*.json"):
        p.unlink()
    for fid, doc in docs.items():
        _write(out / f"{fid}.json", doc)
    for isin, target in aliases.items():
        _write(out / f"{isin}.json", {"alias_of": target})

    all_aliases = dict(aliases)
    for fid, doc in docs.items():
        for x in [doc["isin"], *doc["isins"]]:
            if x:
                all_aliases[x] = fid
    status_rank = {"local_only": 2}
    order = sorted(docs.values(), key=lambda d: (status_rank.get(d["status"], d["source"] == SYNTHETIC), d["id"]))
    index = {
        "fields": INDEX_FIELDS,
        "rows": [[d["id"], d["isin"], d["name"], d["ticker"], d["issuer"], "fund", d["source"], d["as_of"],
                  None if d["status"] == "local_only" else round(d["resolved_pct"], 2),
                  None if d["status"] == "local_only" else d["n_constituents"], d["status"]] for d in order],
        "aliases": dict(sorted(all_aliases.items())),
    }
    _write(API / "index.json", index)

    sources = []
    for s in meta_in["sources"]:
        if redistribution(s["id"]) != "public":
            _fail(f"meta.json lists local_only source {s['id']}")
        sources.append(s)
    for sid in dict.fromkeys(src for _, src, _ in LOCAL_ONLY):
        listed = [d for d in docs.values() if d["source"] == sid]
        sources.append({"id": sid, "name": ISSUER[sid]["name"], "url": ISSUER[sid]["url"], "licence": LOCAL_ONLY_LICENCE,
                        "credit": f"Funds listed by {ISSUER[sid]['listed_by']}; holdings not republished",
                        "n_funds": len(listed), "as_of_min": PRODUCT_LIST_DATE, "as_of_max": PRODUCT_LIST_DATE})
    sources.append({"id": SYNTHETIC, "name": "Synthetic fixture rows", "url": "https://openbasket.leithresearch.org/docs/",
                    "licence": "Made-up test data", "credit": "Made-up rows for testing the page; not a real fund",
                    "n_funds": 1, "as_of_min": PRODUCT_LIST_DATE, "as_of_max": PRODUCT_LIST_DATE})
    meta = {**meta_in, "built_at": BUILT_AT, "n_funds": len(docs), "n_isins": len(all_aliases), "sources": sources}
    _write(API / "meta.json", meta)
    print(f"Wrote {len(docs)} fund files, {len(aliases)} alias files and {len(sources)} sources to {API}")


def _write(path: Path, doc: object) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--api", type=Path, required=True, help="a public-only publish output: the folder holding v1/")
    parser.add_argument("--data", type=Path, default=ROOT / "data", help="the project's data/ folder (fund lists, N-PORT)")
    args = parser.parse_args(argv)
    build(args.api, args.data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
