# openbasket

Look through an ETF, fund or bond by ISIN to the securities behind it, using
free sources: iShares and SPDR issuer files, SEC Form N-PORT, TickerInside
and OpenFIGI. Built with Kedro.

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv -e '.[dev,docs]'
cp .env.example .env          # OPENFIGI_API_KEY, SEC_USER_AGENT
kedro run                     # download and build everything (~450 MB per N-PORT quarter)
kedro run --pipeline=lookthrough --params=isin=IE00B5BMR087
```

The result is `data/08_reporting/lookthrough.csv`: one row per constituent
ISIN with its weight in percent of the fund's net assets, its source and
date, and one row of unresolved weight. Weights are never rescaled to 100%.

Full documentation: `make -C docs html`, then open
`docs/build/html/index.html`. It covers usage, the output columns, each
source's coverage and gaps, and the plan.

TickerInside data is CC BY 4.0: rows from it carry the required credit in
`source_ref`.
