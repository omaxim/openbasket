# Running it yourself

openbasket is a tool, not a database. The public site shows what open data
allows anyone to republish; run it on your own computer and it reads every
source you have switched on, issuer websites included, and shows you
everything it found, with the same site and the same JSON API.

## Three commands

You need git, [uv](https://docs.astral.sh/uv/) and a few GB of free disk.

```bash
git clone https://github.com/omaxim/openbasket && cd openbasket
uv venv --python 3.12 .venv && source .venv/bin/activate && uv pip install -e '.[docs]' && cp .env.example .env
kedro run --pipeline=full && python scripts/serve.py
```

Before the first run, open `.env` and put your own name and email address in
`SEC_USER_AGENT` (see [](#env)). Kedro itself sends anonymous usage data
unless told not to; `export DO_NOT_TRACK=1` before running stops it. When
the run is done, `serve.py` prints

```text
Serving openbasket at http://localhost:8000/  (API: http://localhost:8000/api/v1/meta.json; Ctrl+C to stop)
```

Open that address. Press Ctrl+C in the terminal to stop the server; run
`python scripts/serve.py` again at any time to serve the last build.

### A quick sample first

A full run takes hours (see [](#time-and-disk)). To see the whole chain
work, look through only a few funds from each fund list:

```bash
kedro run --pipeline=full --params=universe.sample=3 && python scripts/serve.py
```

`universe.sample=3` takes 3 funds from each fund list (iShares, SPDR,
N-PORT and each adapter's list), chosen with `universe.seed`. Everything
before the universe step still runs in full, so the downloads happen once
and later runs, sampled or full, reuse them.

## What `full` runs

`kedro run --pipeline=full` is every pipeline except the one-fund
`lookthrough`, in the order the data needs:

1. `ingest`: SEC N-PORT (about 450 MB a quarter), SEC fund tickers, the
   iShares and SPDR fund lists and every enabled adapter's fund list.
2. `reg_brazil` and `reg_spain`: CVM's monthly portfolio files and CNMV's
   quarterly ones. These and `ingest` need nothing from each other, so Kedro
   may run them in either order; the CVM and CNMV adapters fetch the files
   themselves when `ingest` asks for their fund lists first, and the
   pipelines then find them up to date.
3. `security_master`: ISIN-keyed securities from every N-PORT position.
4. `consolidate`: each fund's latest N-PORT filing and its holdings.
5. `universe`: looks through every fund in every list, one at a time.
6. `publish`: writes the static JSON API to `build/site/api` and the
   release files to `build/release`.

Each pipeline saves its outputs under `data/`, so any of them can be rerun
on its own, for example `kedro run --pipeline=universe` after changing a
look-through parameter, then `kedro run --pipeline=publish`. A plain
`kedro run` runs everything up to the universe and the look-through of
`isin`, without `universe` and `publish` ([](usage.md)).

## Your own site and API

`python scripts/serve.py` puts the landing page (`site/`), these docs
(built into `docs/build/html` the first time) and your API together in
`build/pages-local`, the same layout the public site has, and serves it with
Python's built-in web server on <http://localhost:8000>, for this computer
only. JSON is served as `application/json`, and nothing is cached, so a
rebuild shows on the next reload.

The API is a set of static files described in [](data-api.md). Everything
that reads the public API reads yours:

```bash
curl -s http://localhost:8000/api/v1/meta.json
curl -s http://localhost:8000/api/v1/index.json
curl -s http://localhost:8000/api/v1/funds/IE00B5BMR087.json
```

If another program already uses port 8000, `serve.py` takes the next free
one (8001, 8002, ...) and prints it; use that address in the commands
above. Options: `--port 8080` for a fixed port, `--rebuild-docs` after changing
the docs, `--no-assemble` to serve `build/pages-local` as it is,
`--api` to serve another API folder.

## Public data and local data

Every source has a redistribution class:

`public`
: Its licence allows republication: SEC N-PORT and N-MFP (US public
  domain), TickerInside (CC BY 4.0), Spain's CNMV, Brazil's CVM (ODbL 1.0),
  ESMA FIRDS, GLEIF (CC0) and OpenFIGI names and types. The public build at
  <https://openbasket.leithresearch.org> publishes only these.

`local_only`
: An issuer's own website whose terms restrict republication: iShares,
  SPDR and the other issuer adapters, and anything built from them.
  openbasket reads these only when you run it yourself.

On the public site, a fund that only a `local_only` source covers is
listed with its name, ISIN and issuer from open registries and the status
`local_only`, with no constituents, and a note that its holdings are
available from the issuer when you run openbasket yourself. Your local
build has the holdings themselves.

To see every source, its class and whether your runs use it:

```bash
python scripts/list_sources.py
```

### Optional sources

Some sources forbid automated access even for private use. Their adapters
exist but are off by default. A few sites ask not to be crawled at all
(their `robots.txt` says so); openbasket has no adapter for them.

To choose the adapters, create `conf/local/parameters.yml` (gitignored); it
is read on top of `conf/base/parameters.yml` for every local run:

```yaml
issuers:
  # Replaces the default list: name every adapter you want, the ones that
  # are on by default included.
  enabled: [cnmv_spain, cvm_brazil]
  # Or keep the default list and drop some:
  # disabled: [cvm_brazil]
  # iShares and SPDR are chosen by site and region:
  # ishares_sites: [uk]
  # spdr_regions: [emea]
```

`python scripts/list_sources.py` shows the result. Switching on an
adapter that is off by default is your decision: read that source's terms
first.

## Public and full builds

| | Full (`kedro run`) | Public (`kedro run --env public`) |
|---|---|---|
| `publish.mode` | `full` | `public` |
| Issuer sites (`local_only`) | read | never asked, not even for their fund lists; funds listed by name and ISIN, status `local_only` |
| Public sources | read | read |
| Fund list | `data/01_raw/issuers/issuer_products.parquet` | `data/01_raw/issuers/issuer_products_public.parquet` |
| `publish` | writes everything | refuses to write anything if a row or fund comes from a `local_only` source |

The GitHub Action runs the public build. Its output goes through
`scripts/check_build.py`, which fails the run if any published row, fund or
`meta.sources` entry is `local_only` (see {doc}`deployment`).

## Whose terms apply

In plain words: when you run openbasket, your computer fetches each fund
list and holdings file from its source, for your own use. openbasket does
not send that data anywhere and does not publish it; it stays in `data/`
and `build/` on your computer, both gitignored. You are responsible for
respecting each source's terms of use, the same as if you downloaded the
files yourself in a browser: a source's terms may, for example, allow
personal use but not sharing its holdings with others. Do not publish or
share a local build that contains `local_only` sources.

openbasket is polite by design: it waits at least a second between two
requests to the same website, caches each issuer file for the day, gives
the SEC the contact you set in `SEC_USER_AGENT`, and does not read sites
whose `robots.txt` asks it not to.

(env)=
## `.env`

`cp .env.example .env` creates it; it is gitignored and nothing in it is
written to the output.

`SEC_USER_AGENT`
: Required. Sent as the User-Agent of every request to the SEC, which asks
  automated clients to give a name and a contact email and blocks those that
  do not, e.g. `Jane Doe jane@example.org`.

`OPENFIGI_API_KEY`
: Optional, free from <https://www.openfigi.com/api>. OpenFIGI supplies
  constituents' names and security types and maps tickers to ISINs. Without
  a key it works at the lower anonymous rate limit: the same results, more
  slowly on a large run. Lookups are cached in
  `data/03_primary/openfigi_cache.parquet`, so each ISIN is asked once.

(time-and-disk)=
## Time and disk

Measured on 3 November 2026 on a laptop (Apple silicon, home broadband),
from a fresh clone, with `universe.sample=3`:

| Step | Time | Disk |
|---|---|---|
| Install | seconds with uv's cache warm, a minute or two cold | 0.33 GB (`.venv`) |
| Downloads: SEC fund tickers, iShares and SPDR lists, CNMV (one quarter), CVM (three months) | about 2 minutes | 0.15 GB in `data/01_raw` |
| N-PORT download | depends on your line: 450 MB | 0.45 GB zip, 1.2 GB unpacked in `data/01_raw/nport` |
| `security_master`, `consolidate` | about 20 seconds | 0.76 GB in `data/02_intermediate` and `data/03_primary` |
| `universe`, 15 funds (3 from each of 5 lists) | 2 minutes, 1.5 of them OpenFIGI | 1.3 MB |
| `publish` | under a second | 1.4 MB API in `build/site/api` |
| `serve.py`, first time | 10 seconds (it builds the docs) | 2 MB in `build/pages-local` |

That run took 4.5 minutes in all (the N-PORT zip was already on disk) and
about 2.8 GB of disk, most of it N-PORT. Plan for about 4 GB free for a
full build.

A full universe run (about 42,000 funds) takes several hours: it is mostly
waiting, since each issuer website is asked at most once a second, and the
first run looks up a few hundred thousand ISINs on OpenFIGI (faster with
`OPENFIGI_API_KEY`). The API it writes is kept under
`publish.site_budget_mb` (800 MB). Later runs on the same day reuse the
cached issuer files, and the OpenFIGI cache is kept between runs.
[](publishing.md) has per-fund timings.


## When a site is down

A full run reads dozens of sites and registers, and any of them can be down
for an hour. The optional downloads (the ESMA, FCA and GLEIF registers, the
regulator stores, FinanceDatabase, the Swiss sources; `SOFT_NODES` in
`openbasket/pipeline_registry.py`) do not stop the run when they fail: the
run goes on with the copy the last successful run saved, and the log says
so. On a first run with no saved copy, the two ESMA registers are left out
and the others still stop the run.

## Publishing coarse splits of local-only funds

The full run also writes `public_aggregates/aggregates.json`: for every fund
whose holdings came from a `local_only` source, openbasket's own sector,
region, country and asset-class splits in whole percent, and nothing else.
The public site shows these as blocks for such funds, labelled as computed
by openbasket, with no companies behind them.

Each run merges into the file already there. A fund the run has no holdings
for, for example because its issuer's site failed or changed, keeps its
earlier splits and their own date (`computed_at`) until they are older than
`aggregates.keep_days` (180 days); the site shows that date. Rows whose
issuer gives no sector take their company's GICS sector from FinanceDatabase
by ISIN.

To publish them (maintainers only), the file is committed and pushed, and
the next public build picks it up. The run can do that itself: set

```yaml
# conf/local/parameters.yml (not committed)
aggregates:
  share:
    enabled: true
    push: true
```

and after writing the file the `aggregates` pipeline validates it and
commits that one file on top of `origin/main` as it is then, with a private
git index: your working tree, checked-out branch and anything you have
staged are left alone, no other path changes, and nothing is committed when
the file is unchanged. A refused push (someone pushed first) is retried once.
The site's "last updated" date is the time of that commit.
`openbasket.aggregates.validate` refuses a file that holds anything but
coarse splits, and so does the build's CI gate.
