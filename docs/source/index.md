# openbasket

Give it the ISIN of an ETF, fund or bond, and it returns the securities behind
it, each with its ISIN and its weight as a percent of the input's net assets.
Weight that cannot be tied to an ISIN is reported as unresolved and is never
spread over the rest.

```bash
kedro run --pipeline=lookthrough --params=isin=IE00B5BMR087
```

```{toctree}
:maxdepth: 2

usage
output
sources/index
plan
data-api
publishing
deployment
api
```
