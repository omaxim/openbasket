# Data API

openbasket is an aggregator. It republishes nothing of its own: every
holding comes from a named public source, and every file and every row says
which one, with a link and an as-of date.

The site at `https://openbasket.leithresearch.org/` is static. The "API" is a
set of JSON files rebuilt by the scheduled build and served by GitHub Pages,
so there is no server to overload. All paths are relative to the site root.

## `api/v1/meta.json`

```json
{
  "api_version": 1,
  "built_at": "2026-11-03T12:00:00Z",
  "release_tag": "2026-11",
  "release_url": "https://github.com/omaxim/openbasket/releases/tag/2026-11",
  "full_data_url": "https://github.com/omaxim/openbasket/releases/download/2026-11/openbasket-lookthrough.parquet",
  "n_funds": 15234,
  "n_isins": 31877,
  "sources": [
    {
      "id": "ishares",
      "name": "iShares (BlackRock) fund holdings",
      "url": "https://www.ishares.com/",
      "licence": "Public website data; terms not reviewed",
      "credit": "Holdings data from iShares by BlackRock",
      "n_funds": 1913,
      "as_of_min": "2026-10-30",
      "as_of_max": "2026-11-02"
    }
  ]
}
```

`sources[].id` is one of `ishares`, `spdr`, `tickerinside`, `nport`,
`security_master`. Every source that contributes a row anywhere must be
listed here, with its credit line.

## `api/v1/index.json`

Every fund the build covers, as a table, so the search box can load it once
(gzip on the wire, about 1 MB):

```json
{
  "fields": ["id", "isin", "name", "ticker", "issuer", "kind", "source", "as_of", "resolved_pct", "n_constituents"],
  "rows": [
    ["IE00B5BMR087", "IE00B5BMR087", "iShares Core S&P 500 UCITS ETF", "CSPX", "iShares", "fund", "ishares", "2026-11-02", 99.86, 503]
  ],
  "aliases": {"IE00B5BMR087": "IE00B5BMR087"}
}
```

- `id` names the fund file. It is the fund's ISIN where one is known,
  otherwise its SEC series ID (`S000004310`).
- `isin` may be `null` (N-PORT series whose share-class ISINs are unknown).
- `aliases` maps every known ISIN, including other share classes of the
  same portfolio, to an `id`.
- `kind` is `fund` for everything in the index; single bonds and stocks are
  not listed (they are their own constituent).

## `api/v1/funds/{id}.json`

```json
{
  "id": "IE00B5BMR087",
  "isin": "IE00B5BMR087",
  "isins": ["IE00B5BMR087"],
  "name": "iShares Core S&P 500 UCITS ETF",
  "ticker": "CSPX",
  "issuer": "iShares",
  "source": "ishares",
  "source_url": "https://www.ishares.com/uk/individual/en/products/253743/...",
  "credit": "Holdings data from iShares by BlackRock",
  "as_of": "2026-11-02",
  "matched_via": "ishares",
  "note": null,
  "reported_total_pct": 100.00011,
  "resolved_pct": 99.86422,
  "unresolved_pct": 0.13578,
  "unidentified_lines_pct": 0.14602,
  "n_constituents": 503,
  "truncated": false,
  "fields": ["isin", "name", "weight_pct", "asset_class", "source", "as_of", "depth", "via"],
  "constituents": [
    ["US0378331005", "APPLE", 7.59332, "equity", "ishares", "2026-11-02", 0, "iShares Core S&P 500 UCITS ETF"]
  ],
  "unresolved": {"weight_pct": 0.13578, "note": null},
  "full_data_url": "https://github.com/omaxim/openbasket/releases/download/2026-11/openbasket-lookthrough.parquet"
}
```

- `constituents` are sorted by absolute weight, largest first. Weights are
  percent of the fund's net assets and are never rescaled.
- Each row's `source` and `as_of` can differ from the fund's (a fund of
  funds mixes sources); `via` names the fund the row came from.
- Large funds are cut to the build's `max_constituents` rows; `truncated`
  is then `true` and the full list is in `full_data_url`.

## `api/v1/funds/{isin}.json` for other share classes

A share-class ISIN that is not a fund file's `id` gets a small alias file:

```json
{"alias_of": "IE00B5BMR087"}
```

## Release assets

Each scheduled build publishes a GitHub Release tagged `YYYY-MM` with
`openbasket-lookthrough.parquet` (every constituent row of every fund, no
truncation), `openbasket-funds.parquet` (one row per fund, the summary
fields above) and `quality-report.json`.
