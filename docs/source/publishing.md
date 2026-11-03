# Publishing

openbasket is an aggregator and a quick-lookup tool. It holds no data of its
own: every fund, every row and every page says which public source it came
from, with a link, an as-of date and the source's credit line. It is not
investment advice and is not affiliated with any fund issuer.

The scheduled build runs two pipelines after the usual ones:

```bash
kedro run --pipeline=universe
kedro run --pipeline=publish --params=publish.release_tag=2026-11
```

`universe` looks through every fund openbasket knows in one batch;
`publish` turns the result into the static JSON API described in
[](data-api.md) and the release files. Neither runs as part of a plain
`kedro run`.

## The universe

Every fund in the issuer lists (`issuer_products`: iShares UK and US, SPDR US
and UCITS) and every N-PORT series with a latest filing (`fund_master`).

One fund, many ISINs
: Share classes of one issuer portfolio are one fund. The largest share
  class by net assets gives the fund its `id`; the others are aliases.
  An N-PORT series' share-class ISINs are those other funds hold whose
  issuer LEI is the series' LEI and whose issuer type is a registered fund
  (`security_master`). A series with no known ISIN is keyed by its series ID.

One fund, two sources
: A US ETF that is both an issuer fund and an N-PORT series is one fund,
  keyed by the issuer's ISIN. The series is matched by ISIN first, then, for
  US issuer sites only, by ticker (UCITS tickers are local codes). The
  series ID stays on the fund, so its N-PORT holdings remain a fallback, and
  `series_match` says how it was found.

Every ISIN names one fund; issuer funds claim theirs first. On 2026 Q3
N-PORT and the 3 November 2026 issuer lists the universe is 14,870 funds:
1,913 iShares, 362 SPDR and 12,595 N-PORT series, with 605 US issuer ETFs
matched to their series (261 by ISIN, 344 by ticker).

## Running it

Each fund is expanded with the same engine and providers as the
`lookthrough` pipeline, named by its fund ref rather than matched from an
ISIN.

- A fund that raises is recorded with its error and a short traceback, and
  the batch moves on. Warnings logged while a fund is expanded (an issuer
  file that could not be read, say) are kept in its `warnings` column, and a
  fund with no holdings says why in its `note`.
- Each fund's time is recorded in `seconds`; progress is logged every
  `universe.log_every` funds with an estimate of the time left.
- Requests to one host are spaced at least `universe.http.default_interval_s`
  apart (1 s), whichever client makes them; `per_host_interval_s` overrides
  it per host. Issuer files are cached per fund per day, so a rerun on the
  same day makes no requests.
- Once a source resolves `lookthrough.min_resolved_pct`, later sources are
  not read (`universe.stop_early`). TickerInside is asked only for tickers
  its fund list puts in the issuer layer (`universe.tickerinside_prefilter`).
- Which N-PORT series issues an ISIN is looked up in an index built with one
  scan of N-PORT; a series' own lines are read by series ID from
  `nport_holdings`, which is sorted by series ID.
- OpenFIGI names and types are added once, for every constituent ISIN of the
  run, through the shared cache, which is saved every
  `universe.openfigi_save_every` funds and at the end.
- Constituent rows are written to Parquet parts under `universe.work_dir`
  as they are made, so memory does not grow with the universe.

### Quick runs

| Parameter | Effect |
|---|---|
| `universe.sources` | Fund lists to include: `ishares`, `spdr`, `nport`. A fund belongs to the list it is keyed by. |
| `universe.ids` | Only these fund ids or series IDs. |
| `universe.sample` | This many funds per list, chosen with `universe.seed`. |
| `universe.limit` | Then only the first this many. |

```bash
kedro run --pipeline=universe --params=universe.sample=14   # 42 funds
```

### Outputs

`universe` (`data/03_primary/universe.parquet`)
: The whole universe before selection: `id`, `isin`, `isins`, `series_id`,
  `issuer_id`, `name`, `ticker`, `issuer`, `universe_source`,
  `series_match`.

`universe_funds` (`data/07_model_output/universe_funds.parquet`)
: One row per fund run: the columns above plus `source`, `source_url`,
  `source_ref`, `credit`, `as_of`, `matched_via`, `note`, `sources_used`,
  `sources_tried`, `reported_total_pct`, `resolved_pct`, `unresolved_pct`,
  `unidentified_lines_pct`, `n_constituents`, `error`, `traceback`,
  `warnings` and `seconds`. `source` is the source of the fund's own
  holdings, or the list the fund came from when no source had any.
  `credit` joins the credit lines of every source its rows came from, its
  own first.

`universe_constituents` (`data/07_model_output/universe_constituents.parquet`)
: Every constituent row of every fund, with `fund_id`, and one
  unresolved-weight row per fund. Columns as in [](output.md), plus
  `source_url` and `name_source` (who supplied the name: the holdings
  source, or `openfigi`). Unlike `lookthrough.csv`, a row never mixes
  sources: an ISIN held directly according to N-PORT and through a sub-fund
  according to that sub-fund's issuer file is two rows, each with its own
  `source`, `as_of` and `via_fund`.

`universe_run` (`data/08_reporting/universe_run.json`)
: Start and end, seconds, funds attempted, succeeded, failed and without
  holdings, rows written, HTTP requests and time spent waiting.

## Publishing

`publish` writes, under `publish.site_dir` (default `build/site`, not
committed), `api/v1/meta.json`, `api/v1/index.json`, one
`api/v1/funds/{id}.json` per fund and an `{"alias_of": id}` file for every
other share-class ISIN; and under `publish.release_dir` (default
`build/release`) the three release files. Funds whose run failed are left
out of the API but kept, with their error, in `openbasket-funds.parquet`.

Sources
: `publish.sources` in `conf/base/parameters.yml` holds each source's name,
  link, licence and credit line. `meta.sources` lists every source that
  contributed a row or a fund, with the number of funds and the range of
  as-of dates; a source missing from the configuration is still listed, by
  its id, with a warning. Each fund file's `credit` names every source its
  rows came from, and each constituent row carries its own `source` and
  `as_of`.

Release links
: `publish.release_tag` (default: the current UTC year and month) and
  `publish.repo` (`omaxim/openbasket`) give `release_url` and
  `full_data_url`, the download link of `openbasket-lookthrough.parquet`.

Size
: Fund files keep their `publish.max_constituents` (1,000) heaviest rows and
  say `truncated: true` when rows were cut. If `api/v1` still comes out
  larger than `publish.site_budget_mb` (800), the cut is lowered, never
  below `publish.min_constituents` (50), and the large funds are rewritten.
  Every cut is logged and recorded in the quality report; no row is dropped
  from the release file.

`quality-report.json`
: Funds listed, attempted, succeeded, failed, without holdings and published
  per list; funds by holdings source, by how they were matched and by how
  their series was found; the resolved-weight distribution; seconds per fund
  by list; the most common errors and warnings, with example funds; every
  fund whose source's lines add up to more than 5 points away from 100%;
  API size and cuts; release file sizes; and the run record. It is also
  saved as `data/08_reporting/quality_report.json`.

## Sample run

Run on 3 November 2026 with `universe.sample=14` (42 funds, 14 per list),
2026 Q3 N-PORT and that day's issuer files, starting with no issuer files
cached:

| | iShares | SPDR | N-PORT |
|---|---|---|---|
| Funds attempted | 14 | 14 | 14 |
| Succeeded | 14 | 14 | 14 |
| No holdings | 2 | 0 | 0 |
| Seconds per fund, mean | 0.98 | 0.93 | 0.25 |
| Seconds per fund, median | 0.96 | 0.94 | 0.03 |

- No fund failed. The two iShares funds without holdings are index mutual
  funds whose page lists only the ten largest lines.
- Of the 40 funds with holdings, 29 resolve 99 to 101% of their weight to
  ISINs, 6 resolve 90 to 99%, 1 resolves 50 to 90% (ProShares Short FTSE
  China 50, whose N-PORT lines add up to 74.77%) and 4 more than 101%, whose
  reported lines add up to more than their net assets (two core bond ETFs
  at 121% and 120%). The median is 99.89%.
- The run took 57 s: 30 s expanding funds, of which 12 s waiting on the
  throttle, and 24 s mapping 4,609 new constituent ISINs through OpenFIGI.
  `publish` took under a second and wrote 1.9 MB of API (42 fund files,
  5 cut to 1,000 rows), a 0.8 MB look-through file and a 27 kB fund file.
- A rerun the same day, with every issuer file cached, took 5 s.

A larger sample of 300 funds (`universe.sample=100`, `universe.seed=1`) gives
steadier timings: 1.61 s per iShares fund (median 1.00, slowest 23 s for a
large bond fund), 0.95 s per SPDR fund and 0.36 s per N-PORT series (median
0.03). 28 of the 100 iShares funds had no full holdings table. 271 funds had
holdings; their median resolved weight was 99.82% and 252 resolved 90% or
more. The API came to 12.8 MB, about 43 kB a fund. OpenFIGI mapped 31,220
new ISINs in 152 s, about 205 a second.

### The full universe

From the 300-fund timings:

| Step | Estimate |
|---|---|
| 1,913 iShares funds | about 50 min (requests to one host are spaced 1 s apart) |
| 362 SPDR funds | about 6 min |
| 12,595 N-PORT series | 30 to 75 min (most take 0.03 s; large funds and the 223 with a TickerInside ticker take longer) |
| OpenFIGI, first run | about 27 min for the 330,000 N-PORT ISINs not yet in the cache; minutes once the cache is kept between runs |
| `publish` | a few minutes |

That is two to three hours for a first run and under two and a half once the
OpenFIGI cache (`data/03_primary/openfigi_cache.parquet`) is kept between
builds, well inside a six-hour job. The API should come to about 630 MB,
under the 800 MB budget, so fund files would be cut only at 1,000
constituents.

## Reference

```{eval-rst}
.. automodule:: openbasket.pipelines.universe.nodes
   :members: build_universe, select_universe, run_universe, expand_fund, fund_ref

.. automodule:: openbasket.pipelines.publish.nodes
   :members: publish, fund_document, quality_report, release_urls

.. automodule:: openbasket.throttle
   :members:
```
