# Usage

## Setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv -e '.[dev,docs]'
cp .env.example .env   # then put your own contact in SEC_USER_AGENT
```

`.env` is gitignored and holds:

`OPENFIGI_API_KEY`
: Raises the OpenFIGI limits to 100 identifiers a request and 25 requests per
  6 seconds. Without it the pipeline still runs, more slowly.

`SEC_USER_AGENT`
: The SEC asks automated clients to name themselves and give an email
  address, e.g. `openbasket research you@example.org`.

## Running

The pipelines run in order; each saves its outputs under `data/`, so later
ones can be rerun on their own.

```bash
kedro run --pipeline=ingest           # N-PORT (~450 MB a quarter), SEC fund tickers, iShares and SPDR fund lists
kedro run --pipeline=security_master  # ISIN-keyed securities from every N-PORT position
kedro run --pipeline=consolidate      # latest filing per fund, holdings with ISINs
kedro run --pipeline=lookthrough --params=isin=US4642872000
```

`kedro run` with no pipeline runs all four (and the `reg_brazil` and
`reg_spain` downloads). `kedro run --pipeline=full` instead builds the whole
site: everything but the one-fund look-through, then `universe` and
`publish`; `python scripts/serve.py` then serves it on
<http://localhost:8000> ([](running-locally.md)). To look through several ISINs at
once, separate them with semicolons (Kedro splits `--params` on commas):

```bash
kedro run --pipeline=lookthrough --params='isin=US4642872000;IE00B4L5Y983;US037833EE62'
```

Each N-PORT quarter takes about 10 seconds to convert and 20 to process on a
laptop. The first look-through of a large bond fund spends about a minute
mapping its ISINs through OpenFIGI; results are cached in
`data/03_primary/openfigi_cache.parquet`.

## Configuration

`conf/base/parameters.yml`:

`nport.quarters`
: Which quarterly SEC files to load, e.g. `[2026q2, 2026q3]`. Loading more
  than one fills gaps for funds whose latest filing fell in an earlier
  quarter; each fund still uses only its latest filing.

`issuers.ishares_sites`
: iShares regional sites the `ishares` adapter reads, in order of preference
  for a share class several sites list (default: all 21; `[]` reads none).
  A site that fails is skipped. See [iShares regional sites](sources/ishares-regions.md).

`issuers.options`
: Keyword arguments for an adapter's `products()`, by adapter name, e.g.
  `first_trust: {cache_dir: data/01_raw/issuers}`.

`issuers.spdr_regions`
: SPDR fund finders: `us` (US ETFs, including SPY and DIA) and `emea`
  (UCITS ETFs).

`lookthrough.source_order`
: Holdings sources in priority order: `issuer` (SPDR and every enabled issuer adapter, iShares included), `tickerinside`,
  `nport`, and `top_holdings` (not built yet, skipped).

`lookthrough.min_resolved_pct`
: A source is used if at least this much weight resolves to ISINs;
  otherwise the source resolving the most is used.

`lookthrough.max_depth`
: How many levels of funds holding funds to expand.

`lookthrough.proxies`
: Funds with no free holdings mapped to a fund on the same index. Rows from
  a proxy are marked in `matched_via` and `note`. Empty by default.

`lookthrough.issuer_cache_dir`
: Where issuer responses are cached, one file per fund per day.

`publish.mode`
: `full` (the default here): every enabled source is read, issuer websites
  included, for your own use. `conf/public` sets `public`, which the GitHub
  Action uses: sources whose terms restrict republication are never read
  and their funds are listed without holdings (see {doc}`running-locally`).
  Run the public build with `--env public` on every pipeline.

## Tests and docs

```bash
pytest
make -C docs html     # docs/build/html/index.html
```
