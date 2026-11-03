# Deployment

The public site, its JSON API and the monthly data release are all built by
one GitHub Actions workflow, `.github/workflows/publish.yml`, in
`omaxim/openbasket`. Nothing runs on a server of its own.

openbasket is an aggregator for quick lookups. Every fund file, every row and
every page names its source, with a link, an as-of date and the credit line
the source asks for. It is not investment advice and is not affiliated with
any fund issuer or data provider; the site footer and the release notes say
so.

## What a build does

```text
ingest ─► security_master ─► consolidate ─► universe ─► publish
                                                          │
                     docs/build/html ─┐                   ├─► build/site/api ─┐
                                      └─► assemble_site ◄─┘                   │
                     kedro viz build ─────► build/pages/pipeline              │
                                            build/pages ─► check ─► GitHub Pages
                                          build/release ─► check ─► GitHub Release
```

1. `kedro run --pipeline=ingest`, `security_master` and `consolidate`, as
   for a local run (see {doc}`plan`).
2. `kedro run --pipeline=universe`, with `--params=universe.sample=N` on a
   sample run.
3. `kedro run --pipeline=publish --params=publish.release_tag=YYYY-MM`, the
   tag being the current UTC month. It writes the static API to
   `build/site/api/v1` (`publish.site_dir`) and the release files to
   `build/release` (`publish.release_dir`), see {doc}`publishing`.
4. The Sphinx docs are built with warnings as errors.
5. `scripts/assemble_site.py` puts the landing page, the docs and the API
   together in `build/pages`.
6. `scripts/build_pipeline_viz.py` builds the static
   [Kedro-Viz](https://docs.kedro.org/projects/kedro-viz/) diagram of the
   pipelines into `build/pages/pipeline/`, served at
   `https://openbasket.leithresearch.org/pipeline/` (see
   [The pipeline diagram](#the-pipeline-diagram)).
7. **Check build** (`scripts/check_build.py`). The run fails here, and
   nothing is released or deployed, if:
   - fewer funds were published (`meta.json` `n_funds`) than the minimum;
   - `meta.sources` is empty, or a source in it has no name, link, licence or
     credit line;
   - a fund, a constituent row or an `unresolved` entry names a source that
     `meta.sources` does not list, or a fund or row has no source or no
     as-of date;
   - TickerInside is credited without a link to the CC BY 4.0 licence;
   - a fund in `index.json` has no fund file;
   - `build/pages` is over GitHub Pages' 1 GB limit, or a page carries an
     analytics script.

   It warns, without failing, when the site is over 900 MB, when a dated
   source has no as-of range, and when N-PORT looks stale (see
   [Quarterly maintenance](#quarterly-maintenance)). The job summary lists
   the errors and warnings, the site size, every source with its link, fund
   count, as-of range, licence and credit line, and every figure in
   `build/release/quality-report.json`. The same step writes the release
   notes.
8. The release job creates the GitHub Release `YYYY-MM`, or replaces its
   files if it already exists (`gh release upload --clobber`). The notes list
   each source with its link, as-of range, licence and credit line.
9. The deploy job publishes `build/pages` to GitHub Pages. It waits for the
   release, so the site never links to files that are not there yet.

Steps 8 and 9 run only for a full build, or for a sample run with
`deploy_sample` ticked.

`.github/workflows/test.yml` runs the tests (`pytest -m 'not network'`), the
docs build and the Kedro-Viz build on every push and pull request, in a few
minutes.

Both workflows set `KEDRO_DISABLE_TELEMETRY=1` and `DO_NOT_TRACK=1`, so
neither Kedro nor Kedro-Viz sends usage data, and Kedro-Viz does not put its
analytics script into the published diagram. Set both when running `kedro`
locally too.

## Schedule and manual runs

The build runs at 06:00 UTC on the 3rd of each month (`cron: "0 6 3 * *"`),
once issuers' month-end holdings files are out.

To run it by hand: Actions > publish > Run workflow. The inputs are:

`sample`
: Build only this many funds from each universe list (`universe.sample`:
  iShares, SPDR and N-PORT, so `sample: 20` builds up to 60 funds). Empty
  builds the full universe. Use a small number to check a change end to end
  in a few minutes. **A sample run is not released or deployed**: the build
  and the check run as usual, and the site (artifact `github-pages`) and the
  release files (artifact `release`) are attached to the run to download and
  inspect. The production release and site are left as they are.

`deploy_sample`
: Release and deploy a sample run anyway (default off). This replaces the
  current month's release files and the live site with the sample-sized
  build, so tick it only on purpose, and run a full build afterwards.

`release`
: Create or update the GitHub Release (default on). Turn it off to redeploy
  the site only. The site still links to the `YYYY-MM` release, which may then
  be last month's files or missing.

`min_funds`
: The fewest published funds that count as a good build. Empty means 1 for
  a sample run, otherwise the repository variable `OPENBASKET_MIN_FUNDS`,
  otherwise 10,000. A full build covers about 15,000 funds, so a run far
  below that points to a source that failed.

Runs share the concurrency group `pages`: a second run waits for the first
instead of cancelling it.

## Secrets and variables

Set under Settings > Secrets and variables > Actions. The workflow writes
them to `.env` with permissions 600 and never prints them.

| Name | Kind | Needed | Use |
|---|---|---|---|
| `SEC_USER_AGENT` | secret | yes | Name and email sent to SEC EDGAR, which refuses requests without one (e.g. `openbasket research you@example.org`). |
| `OPENFIGI_API_KEY` | secret | recommended | OpenFIGI's rate limit is much higher with a key; without one the run warns and is slower. |
| `OPENBASKET_MIN_FUNDS` | variable | no | Default minimum fund count for full runs. |

The workflow also uses the built-in `GITHUB_TOKEN`, with `contents: write`
for the release job only and `pages: write` and `id-token: write` for the
deploy job only.

## GitHub Pages

In `omaxim/openbasket`, Settings > Pages:

- Source: **GitHub Actions**.
- Custom domain: `openbasket.leithresearch.org`.
- **Enforce HTTPS**, once GitHub has issued the certificate.

The deploy job uses the `github-pages` environment, which GitHub creates on
the first deployment. If its protection rules limit deployments to certain
branches, `main` must be one of them.

A published Pages site may be at most 1 GB. The JSON API is kept under
`publish.site_budget_mb` (800 MB) by cutting the longest fund files (see
{doc}`publishing`); the docs and the pipeline diagram add about 15 MB. The
check step fails the run above 1 GB and warns above 900 MB, before anything
is uploaded, and the job summary gives the size of every build.

DNS is already in place, at Namecheap:

| Host | Type | Value |
|---|---|---|
| `openbasket.leithresearch.org` | CNAME | `omaxim.github.io` |
| `openbasket.leith.org.uk` | CNAME | `omaxim.github.io` |

Both domains are verified for the `omaxim` GitHub account, so no other
repository can claim them.

## The redirect site

`openbasket.leith.org.uk` is served by a second repository,
`omaxim/openbasket-redirect`, whose files are kept in `redirect-site/`
here. Its `index.html` and `404.html` send a browser on to the same path,
query and hash on `openbasket.leithresearch.org`. It has its own `CNAME` and
is published from its default branch (Settings > Pages > Deploy from a
branch), with HTTPS enforced. Copy `redirect-site/` over when it changes; it
needs no build.

The redirect works in browsers only. GitHub Pages cannot send a 301 or 302:
every path but the root is answered with `404.html` and HTTP status 404, and
JavaScript on that page does the redirect. `curl`, `fetch` and other API
clients get the 404 and an HTML page, never the JSON. **API clients must use
`https://openbasket.leithresearch.org/api/v1/` directly**; the redirect page
says so too.

## Release assets

Each release is tagged `YYYY-MM` and carries:

| File | Contents |
|---|---|
| `openbasket-lookthrough.parquet` | Every constituent row of every fund, never truncated. |
| `openbasket-funds.parquet` | One row per fund: source, as-of date, resolved and unresolved weight. |
| `quality-report.json` | Coverage figures for the build. |

Every row keeps its `source`, `source_ref` and `as_of`. See {doc}`data-api`
for the fields. A second run in the same month replaces the files of that
month's release; earlier releases are left as they are.

## Quarterly maintenance

The monthly build does not move on to a newer N-PORT quarter by itself.
`nport.quarters` in `conf/base/parameters.yml` is a fixed list, and `ingest`
downloads exactly those quarters. SEC posts each quarter's
[Form N-PORT data sets](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets)
some weeks after the quarter ends. Once a quarter is up:

1. Add it to the end of `nport.quarters` (e.g. `[2026q3, 2026q4]`). Older
   quarters can be dropped once the new one is in; each is about 450 MB.
2. Push to `main`. The N-PORT cache key changes with the list, so the next
   run downloads only the new quarter on top of the restored cache.
3. Optionally run the workflow with a small `sample` (not deployed) to check
   that the new quarter reads cleanly before the scheduled full build.

The check step warns in the job summary when this is due: when the newest
N-PORT holdings in `meta.json` are more than 150 days old, or when
`nport.quarters` lacks the latest quarter that ended more than 60 days
before the run. The warning does not fail the run; a build on last
quarter's N-PORT is still worth publishing.

## The pipeline diagram

`https://openbasket.leithresearch.org/pipeline/` is a static
[Kedro-Viz](https://docs.kedro.org/projects/kedro-viz/) build of every
pipeline: nodes, datasets and parameters, with a pipeline selector. It holds
no data, only the structure and the `conf/base` parameters, and is rebuilt
with every deploy. It needs the `viz` extra:

```bash
pip install -e '.[viz]'
KEDRO_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 python scripts/build_pipeline_viz.py --out build/viz
```

`kedro viz build` always writes to `./build` of the project it runs in,
where it would mix with `build/site`, `build/release` and `build/pages`. The
script runs it in a temporary copy of the project (`pyproject.toml`, `src`
and `conf/base`, so no local config is read) and copies the result to
`--out`, without its development-only files. The build is about 10 MB and
takes under half a minute. `kedro viz` (without `build`) serves the same
diagram locally.

## Caches

| Path | Key | Why |
|---|---|---|
| `data/01_raw/nport` | `nport-` plus the quarter list in `nport.quarters` | Each N-PORT quarter is about 450 MB zipped. The key changes when a quarter is added, and the previous cache is restored as a starting point so only the new quarter is downloaded. |
| `data/03_primary/openfigi_cache.parquet` | `openfigi-<run id>-<attempt>`, restored from the newest `openfigi-*` | OpenFIGI answers are reused across runs. It is saved even when a later step fails, so a rerun does not repeat the lookups. |

GitHub drops caches that are not used for 7 days, and keeps up to 10 GB per
repository. A monthly schedule is longer than that, so expect most scheduled
runs to download N-PORT again; manual runs within a week reuse it. To force a
fresh download, delete the cache under Actions > Caches.

## Expected runtime

A full build should take one to three hours on a standard runner: the
N-PORT download and conversion and the issuer files dominate, and OpenFIGI
lookups add more on a cold cache. The run's own timings, per step, are on
its page under Actions. The build job times out after 330 minutes, under
GitHub's six-hour limit. A sample run takes a few minutes plus the N-PORT
download.

## Recovering from a failed run

Nothing is deployed or released unless the build job succeeds, so the site
and the last release keep serving the previous build.

1. Open the run and find the failed step. The job summary shows the check's
   errors and warnings, the quality figures and per-source counts when the
   build got that far.
2. **A source failed** (a timeout, a changed issuer endpoint, a low fund
   count): check {doc}`sources/index`, fix the source, and rerun with a small
   `sample` before a full run.
3. **A transient error** (SEC or an issuer refusing requests): use "Re-run
   failed jobs". The OpenFIGI cache was saved, and the N-PORT cache too once
   `ingest` finished.
4. **Too few funds** but the build looks right (the universe really
   shrank): rerun with a lower `min_funds`, or lower the
   `OPENBASKET_MIN_FUNDS` variable.
5. **A source is not stated** (a source without a credit line, or a row
   whose source is not in `meta.sources`): add or fix its entry under
   `publish.sources` in `conf/base/parameters.yml`. Every source id a row can
   carry, issuer adapters included, needs one. Do not work around the check;
   it is there so that nothing is published without its source and credit.
6. **The site is over 1 GB**: lower `publish.site_budget_mb` or
   `publish.max_constituents`. The full lists stay in the release files.
7. **The release failed but the build passed**: "Re-run failed jobs" reruns
   the release and then the deploy, from the files the build uploaded (kept
   14 days).
8. **The deploy failed**: check Settings > Pages (source must be GitHub
   Actions) and the `github-pages` environment rules, then rerun the failed
   job.

A bad release can be fixed by running the workflow again in the same month,
which replaces its files, or removed by hand under Releases.
