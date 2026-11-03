# Fixture API

`api/v1/` follows `docs/source/data-api.md` (v1.1) so the landing page can be
built and tested without running the pipelines. It is never deployed:
`scripts/assemble_site.py` leaves `site/fixtures/` out.

The files are cut from the project's cached source data by
`make_fixtures.py`, so names, ISINs, weights, countries, sectors, dates and
source links are real but not maintained:

- iShares UK and US, and SPDR US and UCITS, from the cached issuer holdings
  files. Countries are the issuer's country of risk or of issue, mapped to
  ISO 3166 codes where the name is known; sectors are the issuer's labels.
  AOR (`US4642898674`) is expanded one level through its iShares sub-funds,
  so its rows carry `depth` 1 and `via`, and an ISIN held through two
  sub-funds has a row for each (`n_rows` > `n_constituents`).
- SEC N-PORT for seven Vanguard, Fidelity and American Funds series; four of
  them have no ISIN and use their series ID as `id`. `issuer` is the
  registrant named in the filing; N-PORT gives a country but no sector.
- TickerInside for QQQ, with its CC BY 4.0 credit and what was changed.
- `IE000TOBHQ26`, an iShares index fund whose page lists only its ten
  largest holdings, as a fund with no holdings.
- `IE000OY7BDY9`, a swap-based fund whose note says so.

Fund files are cut at 500 rows (`truncated: true` above that); `breakdowns`
are computed over every row, so they are right for the cut files too.
`funds/IE000XZSV718.json` is an alias file for the accumulating share class of
the SPDR S&P 500 UCITS ETF.

To rebuild them (reads `data/` read-only; pass `--data` for another copy):

```bash
.venv/bin/python site/fixtures/make_fixtures.py --data /path/to/openbasket/data
```

To preview the page against these files:

```bash
.venv/bin/sphinx-build -b html -q docs/source docs/build/html
python scripts/assemble_site.py --docs docs/build/html --api site/fixtures/api --out build/pages-fixture
python3 -m http.server 8000 --directory build/pages-fixture
```
