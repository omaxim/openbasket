# Fixture API

`api/v1/` follows `docs/source/data-api.md` (v1.1) so the landing page can be
built and tested without running the pipelines. It is never deployed:
`scripts/assemble_site.py` leaves `site/fixtures/` out.

The repository is public, so the fixtures hold nothing from a source whose
terms do not allow republication (a `local_only` source; see
`openbasket.sources.redistribution`). `make_fixtures.py` stops if a file it
copies names one, and a test checks every row, breakdown and fund.

What is in them:

- Funds with holdings, cut by the `publish` pipeline from a run on public
  sources only (`conf/parameters.yml`: SEC Form N-PORT and TickerInside).
  Names, ISINs, weights, countries, dates and links are real but not
  maintained. Among them:
  - `US4642898674` (AOR), a fund of funds expanded through its sub-funds'
    filings, cut at 500 rows (`truncated`), whose lines add up to more than
    100% (negative unresolved weight);
  - `S000083284` (PGIM S&P 500 Buffer 12 ETF - January), looked through its
    SPY options to the S&P 500 companies, `via` "... (via derivatives)";
  - `S000074838` (Convergence Long/Short Equity ETF), with short positions,
    some of them through ETFs it sells short;
  - `US02072L5654` (BOXX) and `S000084489` (BTCU), whose lines have no ISIN,
    so they have no rows.
- Funds only a `local_only` issuer covers (iShares and SPDR UCITS funds,
  DIA), as the public build lists them: identity from the issuer's public
  fund list, status `local_only`, no constituents, and a note that points
  to running openbasket yourself. `IE000XZSV718` is an alias of
  `IE00B6YX5C33`.
- `ZZ0000000008`, a made-up "Synthetic Example Fund of Funds" (source
  `synthetic`) with sector labels, a sub-fund looked through, one that is
  not (`unexpanded_fund_pct`), and a short position. Its weights are
  invented; its constituent ISINs are real companies'.

`index.json` has one field more than the contract, `status`, after
`n_constituents`, so the search can mark `local_only` funds.

To rebuild them (the two runs write their outputs under `data/` and `build/`
like any run, and make a few TickerInside requests):

```bash
mkdir -p conf/fixtures && cp site/fixtures/conf/parameters.yml conf/fixtures/
kedro run --env fixtures --pipeline=universe
kedro run --env fixtures --pipeline=publish
.venv/bin/python site/fixtures/make_fixtures.py --api build/fixture-site/api
rm -r conf/fixtures
```

To preview the page against these files:

```bash
.venv/bin/sphinx-build -b html -q docs/source docs/build/html
python scripts/assemble_site.py --docs docs/build/html --api site/fixtures/api --out build/pages-fixture
python3 -m http.server 8000 --directory build/pages-fixture
```
