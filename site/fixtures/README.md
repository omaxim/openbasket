# Fixture API

`api/v1/` follows `docs/source/data-api.md` so the landing page can be built
and tested without running the pipelines. It is never deployed:
`scripts/assemble_site.py` leaves `site/fixtures/` out.

The files were cut on 2026-11-03 from the project's cached source data, so
names, ISINs, weights, dates and source links are real but not maintained:

- iShares UK and US, and SPDR US and UCITS, from the cached issuer holdings
  files. AOR (`US4642898674`) is expanded one level through its iShares
  sub-funds, so its rows carry `depth` 1 and `via`.
- SEC N-PORT for seven Vanguard, Fidelity and American Funds series; four of
  them have no ISIN and use their series ID as `id`.
- TickerInside for QQQ, with its `cite_as` credit.

Fund files are cut at 500 rows (`truncated: true` above that).
`funds/IE000XZSV718.json` is an alias file for the accumulating share class of
the SPDR S&P 500 UCITS ETF.

To preview the page against these files:

```bash
.venv/bin/sphinx-build -b html -q docs/source docs/build/html
python scripts/assemble_site.py --docs docs/build/html --api site/fixtures/api --out build/pages-fixture
python3 -m http.server 8000 --directory build/pages-fixture
```
