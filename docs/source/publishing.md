# Publishing

openbasket is an aggregator and a quick-lookup tool. It holds no data of its
own: every fund, every row and every page says which public source it came
from, with a link, an as-of date and the source's credit line. It is not
investment advice and is not affiliated with any fund issuer. The terms
under which the published data may be reused, including the Open Database
License that applies where CVM data is included, are on [](data-licence.md).

The scheduled build runs two pipelines after the usual ones:

```bash
kedro run --pipeline=universe
kedro run --pipeline=publish --params=publish.release_tag=2026-11
```

`universe` looks through every fund openbasket knows in one batch;
`publish` turns the result into the static JSON API described in
[](data-api.md) and the release files. Neither runs as part of a plain
`kedro run`.

## Public and full builds

openbasket is a tool, not a database. Every source has a redistribution
class (`openbasket.sources.redistribution`): `public` when its licence lets
anyone republish its holdings (SEC N-PORT, TickerInside, CVM, CNMV, the
open registries, OpenFIGI names), `local_only` when its terms restrict that
(issuer websites such as iShares and SPDR, and any source nobody has
classified). `publish.mode` says which build is running:

`full` (`conf/base`, a run on your own machine)
: Every enabled source is read and every row is written. The local API and
  site show everything. Nothing here is published.

`public` (`conf/public`, the GitHub Action: `kedro run --env public`)
: No `local_only` source is asked for anything, and none of its rows can
  be written:
: `ingest` does not read the iShares or SPDR lists or call a `local_only`
  adapter's `products()`. Their funds are added as identity rows (ISIN,
  name, issuer) from the open registries when they are built
  (`publish.identity.registries`: the fund directory, the GLEIF and ESMA
  fund registry, FIRDS) or, failing that, from the fund list an earlier full
  run cached (`publish.identity.issuer_lists`), of which only those three
  columns are read. A US fund's ticker comes from OpenFIGI, not the issuer
  (`publish.identity.openfigi_cache`). The public fund list is its own file,
  `data/01_raw/issuers/issuer_products_public.parquet`, so a public run
  never overwrites a local one.
: The providers never call a `local_only` issuer's `holdings()` or the
  iShares and SPDR fetchers: that source answers `local_only` without a
  request (`sources_status` `issuer=local_only`), and TickerInside and
  N-PORT are still read.
: The universe turns a `local_only` issuer fund that is also an N-PORT
  series into that series, named from SEC, so its public holdings are used.
  A fund only `local_only` sources cover is listed with status
  `local_only`, no rows, `source` set to where its identity came from
  (`issuer_list`, `fund_directory`, ...), and the note "Holdings for this
  fund are available from *issuer* when you run openbasket yourself (see
  <publish.run_locally_url>)". A public fund that holds such a fund keeps it
  as an unexpanded `fund` row with the same note.
: `publish` checks every row and fund before writing anything and stops
  with `LocalOnlyInPublicBuild` if one comes from a `local_only` source (a
  full run's universe outputs, for example); each fund file is checked again
  as it is written, and `meta.sources` lists public sources only.
  `scripts/check_build.py` checks the written files once more (see
  {doc}`deployment`).

A source whose robots.txt disallows the pages it would need has no adapter.
One whose terms forbid automated access even for private use has an
adapter with `enabled_by_default = False`, which runs only when named in
`issuers.enabled` in `conf/local` (see {doc}`running-locally`).

## The universe

Every fund in the issuer lists (`issuer_products`: iShares UK and US, SPDR US
and UCITS, and every registered issuer adapter) and every N-PORT series with
a latest filing (`fund_master`).

Issuer adapters
: `ingest` reads the fund list of every adapter registered in
  `openbasket.sources.issuers` (see [](sources/adding-a-source.md)) and adds
  it to `issuer_products` with `issuer` set to the adapter's name and
  `listed_on` set to the day the list was read. `issuers.enabled` (null for
  all) and `issuers.disabled` choose which. An adapter whose list fails is
  logged and skipped; rows that break the product contract are logged and
  rows with an invalid ISIN dropped. An ISIN two lists share stays with the
  first (iShares, SPDR, then adapters by name). The look-through reads each
  adapter fund's holdings with its own adapter and turns CUSIPs and SEDOLs
  into ISINs exactly as for iShares and SPDR: through N-PORT's CUSIP and
  SEDOL maps, and, for an ISIN derived from a CUSIP, only if OpenFIGI knows
  it.

One fund, many ISINs
: Share classes of one portfolio are one fund. The largest share class by
  net assets gives the fund its `id`; the others are aliases.
: For an adapter, share classes are the rows that share a `portfolio_id`
  (the adapter contract). iShares and SPDR give every share class its own
  portfolio id, so their classes are grouped by issuer, site, fund name
  (exactly, ignoring case and spacing), domicile and asset class. On the
  iShares UK site every share class carries its fund's name unchanged,
  hedged classes included, and the issuer's holdings files for those
  classes are the same file: checked on 3 November 2026 for seven pairs
  (LQDA/LQDE, IB01/IBTU, SDIA/SDIG, TIP5/TI5A, ITPS/ITPD, and the hedged
  CSPX/GSPX and ITPS/ITPG), every line and weight matched. A name that
  differs keeps funds apart: "iShares Core S&P 500 UCITS ETF USD (Dist)"
  (IUSA) is a separate fund from CSPX, so suffixes are never stripped. SPDR
  names end in "(Acc)" or "(Dist)" and the fund finder gives no portfolio
  link between them, so each SPDR share class stays its own fund. Grouping
  turns the 1,913 iShares share classes into 1,015 funds (307 with more
  than one ISIN, up to 20) and saves one holdings request per alias.
: An N-PORT series' share-class ISINs are those other funds hold whose
  issuer LEI is the series' LEI and whose issuer type is a registered fund
  (`security_master`). A series with no known ISIN is keyed by its series ID.
: The `issuer` of an N-PORT series is the registrant of its N-PORT filing
  (SEC's `REGISTRANT` table): the trust or company it belongs to.

One fund, two sources
: A US fund that is both an issuer fund and an N-PORT series is one fund,
  keyed by the issuer's ISIN. The series is matched by ISIN first, then, for
  US issuer funds only (US site or US domicile), by ticker (UCITS tickers
  are local codes), then by name: a US issuer fund still unmatched whose
  name, ignoring case and punctuation, is that of exactly one remaining
  series, and of no other unmatched issuer fund. The series ID stays on the
  fund, so its N-PORT holdings remain a fallback, and `series_match` says
  how it was found (`isin`, `ticker` or `name`).

Every ISIN names one fund; issuer funds claim theirs first. On 2026 Q3
N-PORT and the 3 November 2026 issuer lists the universe is 13,936 funds:
1,015 iShares, 362 SPDR and 12,559 N-PORT series, with 641 US issuer ETFs
matched to their series (269 by ISIN, 336 by ticker, 36 by name, among them
the BlackRock ETF Trust active ETFs whose series have no ticker).

## Running it

Each fund is expanded with the same engine and providers as the
`lookthrough` pipeline, named by its fund ref rather than matched from an
ISIN.

- A fund that raises is recorded with its error and a short traceback, and
  the batch moves on. A source that fails (an issuer file that cannot be
  downloaded or read) is not a fund failure: the next source is tried, the
  outcome of each source is kept in `sources_status` (`ok`, `empty`,
  `top10_only`, `http_403`, `network_error`, `parse_error` ...), warnings go
  to `warnings`, and a fund with no holdings says why in plain language in
  its `note`.
- `status` flags each result: `ok`, `partial`, `over_100`, `leveraged`,
  `no_holdings` or `error` (see [](data-api.md)). Sub-funds that could not
  be looked through, including those reached at `lookthrough.max_depth`,
  are kept as rows with `asset_class` `fund` and counted in
  `unexpanded_fund_pct`, not in `resolved_pct`; `max_depth_reached` says
  when the depth limit cut one.
- A proxy in `lookthrough.proxies` for any of a fund's ISINs replaces the
  fund's own holdings, issuer funds included, so a swap ETF can be shown
  through a physical stand-in.
- TickerInside is asked only for US funds: never for a fund listed on a
  non-US issuer site, whose local ticker can be a different US fund's.
- Issuer responses are cached per fund per day only once they parse, so a
  block page or a truncated download is fetched again on the next run.
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
  `universe.openfigi_save_every` funds, every 50 requests within a long
  call, and at the end. Requests are paced to OpenFIGI's limit and retried
  with backoff on 429, 5xx, timeouts and refused connections. OpenFIGI never
  stops a run: values in a request that still fails are left unmapped (and
  not cached, so the next run asks again), after three failures in a row it
  is not asked again in that run, and the counts are in `universe_run`
  under `openfigi`.
- An ISIN or CUSIP that N-PORT reports under more than
  `lookthrough.max_issuer_leis_per_isin` (3) fund series LEIs is not used
  to match a fund (holdings filed under a trust's LEI or a mistyped one do
  not count, so VOO, VWO or EEM still match their series), and placeholder CUSIPs (`000000000`, nine equal characters,
  `N/A`) are never mapped to an ISIN. On 2026 Q3 the placeholder alone had
  spread one ISIN (IE000S9YS762) to 163,328 holdings in 4,451 series; it
  now sits on the 1,238 holdings that report it themselves.
- Constituent rows are written to Parquet parts under `universe.work_dir`
  as they are made, so memory does not grow with the universe.

### Quick runs

| Parameter | Effect |
|---|---|
| `universe.sources` | Fund lists to include: `ishares`, `spdr`, `nport` and any adapter's name; null for all. A fund belongs to the list it is keyed by. |
| `universe.ids` | Only these fund ids or series IDs. |
| `universe.sample` | This many funds per list, chosen with `universe.seed`. |
| `universe.limit` | Then only the first this many. |

```bash
kedro run --pipeline=universe --params=universe.sample=10   # 30 funds
```

### Outputs

`universe` (`data/03_primary/universe.parquet`)
: The whole universe before selection: `id`, `isin`, `isins`, `series_id`,
  `issuer_id`, `share_class_ids`, `name`, `ticker`, `issuer`,
  `universe_source`, `series_match`, `product_page_url`, `cik`,
  `listed_on`, `list_source` (the list or registry that named the fund) and
  `list_url`.

`universe_funds` (`data/07_model_output/universe_funds.parquet`)
: One row per fund run: the columns above plus `source`, `source_url`,
  `source_ref`, `credit`, `as_of`, `matched_via`, `note`, `status`,
  `sources_used`, `sources_tried`, `sources_status`, `reported_total_pct`,
  `resolved_pct`, `unexpanded_fund_pct`, `unresolved_pct`,
  `unidentified_lines_pct`, `n_constituents`, `max_depth_reached`, `error`,
  `traceback`, `warnings` and `seconds`. `source` is the source of the
  fund's own holdings, or the list the fund came from when no source had
  any; `as_of` is then the day that list was read. `credit` joins the
  credit lines of every source its rows came from, its own first, or says
  the fund was listed by its source with no holdings available.

`universe_constituents` (`data/07_model_output/universe_constituents.parquet`)
: Every constituent row of every fund, with `fund_id`, and one
  unresolved-weight row per fund, which carries the fund's own source, link,
  as-of date and note. Columns as in [](output.md), plus `source_url` and
  `name_source` (who supplied the name: the holdings source, or
  `openfigi`). Unlike `lookthrough.csv`, a row never mixes
  sources: an ISIN held directly according to N-PORT and through a sub-fund
  according to that sub-fund's issuer file is two rows, each with its own
  `source`, `as_of` and `via_fund`.

`universe_run` (`data/08_reporting/universe_run.json`)
: Start and end, seconds, `mode`, funds attempted, succeeded, failed,
  without holdings and `local_only`, funds by `status`, rows written, HTTP requests and time spent
  waiting, the issuer adapters used, and OpenFIGI's requests, failures and
  whether it gave up.

## Publishing

`publish` writes, under `publish.site_dir` (default `build/site`, not
committed), `api/v1/meta.json`, `api/v1/index.json` (contract 1.1, see [](data-api.md)), one
`api/v1/funds/{id}.json` per fund and an `{"alias_of": id}` file for every
other share-class ISIN; and under `publish.release_dir` (default
`build/release`) the three release files. Funds whose run failed are left
out of the API but kept, with their error, in `openbasket-funds.parquet`.

Sources
: `publish.sources` in `conf/base/parameters.yml` holds each source's name,
  link, licence and credit line; each issuer adapter brings its own (title,
  homepage, licence, credit). `meta.sources` lists every source that
  contributed a row, a name or a fund, with the number of funds and the
  range of as-of dates; a source missing from both is still listed, by its
  id, with a warning. Each fund file's `credit` names every source its
  rows came from, and each constituent row carries its own `source` and
  `as_of`.

Release links
: `publish.release_tag` (default: the current UTC year and month) and
  `publish.repo` (`omaxim/openbasket`) give `release_url` and
  `full_data_url`, the download link of `openbasket-lookthrough.parquet`.

Size
: Fund files keep their `publish.max_constituents` (1,000) heaviest rows and
  say `truncated: true` when rows were cut; `n_rows`, `breakdowns` and the
  counts are always over the full list. If `api/v1` still comes out
  larger than `publish.site_budget_mb` (800), the cut is lowered, never
  below `publish.min_constituents` (50), and the large funds are rewritten.
  Every cut is logged and recorded in the quality report; no row is dropped
  from the release file.

`quality-report.json`
: Funds listed, attempted, succeeded, failed, without holdings and published
  per list, and by `status` (all funds run, and `published_by_status`);
  under `publishing`, the build `mode`, the sources published, the
  `local_only` sources withheld, rows per source and the `local_only` funds
  per issuer; funds by holdings source, by how they were
  matched and by how their series was found; the resolved-weight
  distribution; funds with unexpanded sub-funds; seconds per fund by list;
  the most common errors and warnings, with example funds; every fund whose
  source's lines add up to more than 5 points away from 100%; constituent
  ISINs that rows attribute to more than `publish.shared_isin_issuers` (20)
  issuer names, a sign of an ISIN spread to lines it does not belong to;
  API size and cuts; release file sizes; and the run record. It is also
  saved as `data/08_reporting/quality_report.json`.

## Sample run

### Public and full, side by side

Run on 3 November 2026 with `universe.sample=5`, `universe.seed=0`: 5 funds
from each list, on 2026 Q3 N-PORT, that day's iShares and SPDR lists (read
by the full run only) and the CVM and CNMV stores.

| | Full (`kedro run`) | Public (`--env public`) |
|---|---|---|
| Universe | 42,148 funds | 42,148 funds |
| US issuer ETFs tied to their N-PORT series | 641 (as iShares or SPDR funds) | 641, published as N-PORT series (268 by ISIN, 337 by OpenFIGI ticker, 36 by name) |
| iShares and SPDR funds listed by identity only | none | 736 (547 iShares, 189 SPDR) |
| Sample funds by status | 18 `ok`, 5 `partial`, 2 `leveraged` | 10 `local_only`, 8 `ok`, 6 `partial`, 1 `leveraged` |
| Release rows by source | spdr 10,617, cvm_brazil 6,289, ishares 5,071, nport 1,700, cnmv_spain 564 | cvm_brazil 6,289, nport 1,700, tickerinside 1,409, cnmv_spain 582, issuer_list 10 (the unresolved rows of `local_only` funds) |
| Requests to issuer sites | 32 HTTP requests, iShares and SPDR included | none (6 requests in all: TickerInside and OpenFIGI) |
| `meta.sources` | ishares, spdr, nport, cnmv_spain, cvm_brazil | tickerinside, nport, issuer_list, openfigi, cnmv_spain, cvm_brazil |
| `scripts/check_build.py` | fails: 7 errors, all `local_only` sources and a `full` build | passes, 0 warnings |

The public sample picks different funds from the iShares and SPDR lists:
there the lists hold only the funds no public source covers. Spanish funds
of funds resolve less in the public build, because the iShares and SPDR
UCITS ETFs they hold are kept as unexpanded `fund` rows.

### Full build in detail

Run on 3 November 2026 with `universe.sample=10`, `universe.seed=3` (30
funds, 10 per list), 2026 Q3 N-PORT rebuilt without placeholder CUSIPs, and
that day's issuer files and lists:

| | iShares | SPDR | N-PORT |
|---|---|---|---|
| Funds attempted | 10 | 10 | 10 |
| Succeeded | 10 | 10 | 10 |
| `ok` | 9 | 10 | 6 |
| `partial` / `over_100` / `leveraged` | 0 / 1 / 0 | 0 / 0 / 0 | 2 / 1 / 1 |

- No fund failed and none was without holdings. The five flags are right:
  ProShares Ultra Consumer Staples (`leveraged`, by name); Jacob Small Cap
  Growth (lines total 108.2%) and iShares Total Return Active ETF, whose
  lines total 100% but whose bond sub-funds, looked through, bring it to
  111.5% (`over_100`); ProShares Long Online/Short Stores (lines total
  90.8%) and an FT Vest buffer ETF whose 93.9% sits in one SPY line with no
  ISIN (`partial`).
- Two of the iShares US funds found their N-PORT series by name, one of the
  match steps added here.
- Median resolved weight 99.76%; 19 funds resolve 99 to 101%, 4 more than
  101%, 4 from 90 to 99%, 2 from 50 to 90% and 1 below 50.
- The first run took 230 s: 44 s expanding funds (1.47 s a fund) and 185 s
  mapping 19,278 new constituent ISINs through OpenFIGI (196 requests, no
  failures). A rerun the same day, with every issuer file and ISIN cached,
  took 7 s. `publish` took 0.2 s and wrote 2.0 MB of API (30 fund files, 10
  cut to 1,000 rows, 4 alias files), a 3.4 MB look-through file and a 27 kB
  fund file. Every fund file's breakdowns add up to 100, every row's source
  is in `meta.sources`, and the quality report found no constituent ISIN
  attributed to more than 20 issuers.

Funds checked by hand in a run of their own: AdvisorShares Vice ETF
(S000059263) now has 23 constituents instead of 505 S&P 500 rows
expanded from a money market line; iShares Global Aggregate 1-5 Year Bond
Index Fund (IE) is `no_holdings`, dated the day its list was read, with the
note "iShares publishes only the ten largest holdings for this fund; see
the source link"; iShares MSCI North America Swap UCITS ETF is `partial`
with 99.97% in one swap line.

The 300-fund sample before these changes (`universe.sample=100`,
`universe.seed=1`) gave steadier timings: 1.61 s per iShares fund (median
1.00, slowest 23 s for a large bond fund), 0.95 s per SPDR fund and 0.36 s
per N-PORT series (median 0.03).

### The full universe

From the 300-fund timings:

| Step | Estimate |
|---|---|
| 1,015 iShares funds | about 30 min (requests to one host are spaced 1 s apart; share classes are fetched once) |
| 362 SPDR funds | about 6 min |
| 12,559 N-PORT series | 30 to 75 min (most take 0.03 s; large funds and the 223 with a TickerInside ticker take longer) |
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
