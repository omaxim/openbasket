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
                                      └─► assemble_site ◄─┘                    │
                                            build/pages ─► GitHub Pages        │
                                          build/release ─► GitHub Release ◄────┘
```

1. `kedro run --pipeline=ingest`, `security_master` and `consolidate`, as
   for a local run (see {doc}`plan`).
2. `kedro run --pipeline=universe`, with `--params=universe.sample=N` on a
   sample run.
3. `kedro run --pipeline=publish --params=publish.release_tag=YYYY-MM`, the
   tag being the current UTC month. It writes the static API to
   `build/site/api` and the release files to `build/release`.
4. The Sphinx docs are built with warnings as errors.
5. `scripts/assemble_site.py` puts the landing page, the docs and the API
   together in `build/pages`.
6. **Quality check.** The job summary lists the figures in
   `build/release/quality-report.json` and every source in `meta.json` with
   its fund count, as-of range and credit line. If fewer funds were built
   than the minimum, the run fails here and nothing is deployed or released.
7. The release job creates the GitHub Release `YYYY-MM`, or replaces its
   files if it already exists (`gh release upload --clobber`). The notes list
   each source with its link, as-of range, licence and credit line.
8. The deploy job publishes `build/pages` to GitHub Pages. It waits for the
   release, so the site never links to files that are not there yet.

`.github/workflows/test.yml` runs the tests (`pytest -m 'not network'`) and
the docs build on every push and pull request, in a few minutes.

## Schedule and manual runs

The build runs at 06:00 UTC on the 3rd of each month (`cron: "0 6 3 * *"`),
once issuers' month-end holdings files are out.

To run it by hand: Actions > publish > Run workflow. The inputs are:

`sample`
: Build only this many funds. Empty builds the full universe. Use a small
  number (e.g. 50) to check a change end to end in a few minutes; the site and
  release are then sample-sized, so run a full build afterwards.

`release`
: Create or update the GitHub Release (default on). Turn it off to redeploy
  the site only. The site still links to the `YYYY-MM` release, which may then
  be last month's files or missing.

`min_funds`
: The fewest funds that count as a good build. Empty means 1 for a sample
  run, otherwise the repository variable `OPENBASKET_MIN_FUNDS`, otherwise
  10,000. A full build covers about 15,000 funds, so a run far below that
  points to a source that failed.

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
here. Its `index.html` and `404.html` send every request to the same path,
query and hash on `openbasket.leithresearch.org`, so links of either form
work. It has its own `CNAME` and is published from its default branch
(Settings > Pages > Deploy from a branch), with HTTPS enforced. Copy
`redirect-site/` over when it changes; it needs no build.

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

1. Open the run and find the failed step. The job summary shows the quality
   figures and per-source counts when the build got that far.
2. **A source failed** (a timeout, a changed issuer endpoint, a low fund
   count): check {doc}`sources`, fix the source, and rerun with a small
   `sample` before a full run.
3. **A transient error** (SEC or an issuer refusing requests): use "Re-run
   failed jobs". The OpenFIGI cache was saved, and the N-PORT cache too once
   `ingest` finished.
4. **Too few funds** but the build looks right (the universe really
   shrank): rerun with a lower `min_funds`, or lower the
   `OPENBASKET_MIN_FUNDS` variable.
5. **The release failed but the build passed**: "Re-run failed jobs" reruns
   the release and then the deploy, from the files the build uploaded (kept
   14 days).
6. **The deploy failed**: check Settings > Pages (source must be GitHub
   Actions) and the `github-pages` environment rules, then rerun the failed
   job.

A bad release can be fixed by running the workflow again in the same month,
which replaces its files, or removed by hand under Releases.
