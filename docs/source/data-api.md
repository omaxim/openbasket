# Data API

openbasket is an aggregator. It republishes nothing of its own: every
holding comes from a named public source, and every file and every row says
which one, with a link and an as-of date.

The site at `https://openbasket.leithresearch.org/` is static. The "API" is a
set of JSON files rebuilt by the scheduled build and served by GitHub Pages,
so there is no server to overload. All paths are relative to the site root.

This page is contract **1.1**. The files still live under `api/v1` and
`meta.json` still says `"api_version": 1`: 1.1 only adds fields, and every
field of 1.0 keeps its meaning. `meta.json` says which contract the files
follow in `contract`.

## `api/v1/meta.json`

```json
{
  "api_version": 1,
  "contract": "1.1",
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
      "n_funds": 1015,
      "as_of_min": "2026-10-30",
      "as_of_max": "2026-11-03"
    }
  ]
}
```

`sources[].id` is one of:

| id | What it supplies |
|---|---|
| `ishares`, `spdr` | The issuer's own holdings files. |
| any issuer adapter's name (`xtrackers`, `amundi`, `vanguard`, ...) | That issuer's holdings files, read by its adapter in `openbasket.sources.issuers`. |
| `nport` | SEC Form N-PORT filings. |
| `tickerinside` | TickerInside's issuer-layer holdings of US ETFs. |
| `security_master` | A single stock or bond, described from N-PORT. |
| `openfigi` | Names and security types, where a row's own source gave no name. |

Every id that appears anywhere in the files (a row's `source`, a fund's
`source`, a name taken from OpenFIGI) is listed, with `name`, `url`,
`licence` and `credit`. `as_of_min` and `as_of_max` are null for `openfigi`,
which has no holdings dates. TickerInside's credit says what openbasket
changed: tickers mapped to ISINs and names taken from N-PORT, under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## `api/v1/index.json`

Every fund the build covers, as a table, so the search box can load it once
(gzip on the wire, about 1 MB). Unchanged in 1.1:

```json
{
  "fields": ["id", "isin", "name", "ticker", "issuer", "kind", "source", "as_of", "resolved_pct", "n_constituents"],
  "rows": [
    ["IE00B5BMR087", "IE00B5BMR087", "iShares Core S&P 500 UCITS ETF", "CSPX", "iShares", "fund", "ishares", "2026-11-02", 99.86, 503]
  ],
  "aliases": {"IE00B5BMR087": "IE00B5BMR087", "IE00BD8KRH84": "IE00B5BMR087"}
}
```

- `id` names the fund file. It is the fund's ISIN where one is known,
  otherwise its SEC series ID (`S000004310`).
- `isin` may be `null` (N-PORT series whose share-class ISINs are unknown).
- `aliases` maps every known ISIN, including other share classes of the
  same portfolio, to an `id`. How share classes are grouped is described in
  [](publishing.md).
- `issuer` is the issuer's name for issuer funds (`iShares`, `SPDR`, an
  adapter's title) and, for N-PORT series, the **registrant** that filed the
  N-PORT report (`REGISTRANT_NAME` in SEC's data sets): the trust or company
  the series belongs to, such as `Goldman Sachs ETF Trust`. It can be `null`
  when the filing does not name one.
- `kind` is `fund` for everything in the index; single bonds and stocks are
  not listed (they are their own constituent).

## `api/v1/funds/{id}.json`

```json
{
  "id": "IE00B5BMR087",
  "isin": "IE00B5BMR087",
  "isins": ["IE00B5BMR087", "IE00BD8KRH84"],
  "name": "iShares Core S&P 500 UCITS ETF",
  "ticker": "CSPX",
  "issuer": "iShares",
  "source": "ishares",
  "source_url": "https://www.ishares.com/uk/individual/en/products/253743/...",
  "credit": "Holdings data from iShares by BlackRock",
  "as_of": "2026-11-02",
  "matched_via": "ishares",
  "note": null,
  "status": "ok",
  "reported_total_pct": 100.00011,
  "resolved_pct": 99.86422,
  "unexpanded_fund_pct": 0.0,
  "unresolved_pct": 0.13578,
  "unidentified_lines_pct": 0.14602,
  "n_constituents": 503,
  "n_rows": 503,
  "truncated": false,
  "listed_pct": 99.86422,
  "breakdowns": {
    "asset_class": [["equity", 99.86422], ["Unresolved", 0.13578]],
    "country": [["US", 99.1], ["IE", 0.42], ["Unknown", 0.34422], ["Unresolved", 0.13578]],
    "sector": [["Information Technology", 34.2], ["Financials", 13.1], ["Unresolved", 0.13578]],
    "source": [["ishares", 99.86422], ["Unresolved", 0.13578]],
    "top10_pct": 38.9
  },
  "fields": ["isin", "name", "weight_pct", "asset_class", "source", "as_of", "depth", "via", "country", "sector"],
  "constituents": [
    ["US0378331005", "APPLE", 7.59332, "equity", "ishares", "2026-11-02", 0, "iShares Core S&P 500 UCITS ETF", "US", "Information Technology"]
  ],
  "unresolved": {"weight_pct": 0.13578, "note": null},
  "full_data_url": "https://github.com/omaxim/openbasket/releases/download/2026-11/openbasket-lookthrough.parquet"
}
```

Rows
: One row per **(ISIN, source, as-of date, via)**. An ISIN can therefore
  appear more than once in one fund file: Apple held directly according to
  the fund's N-PORT filing and through a sub-fund according to that
  sub-fund's issuer file is two rows, each with its own `source`, `as_of`
  and `via` (the fund whose holdings list the row came from). Routes that
  share all four are summed into one row.
: `constituents` are sorted by absolute weight, largest first. Weights are
  percent of the fund's net assets and are never rescaled.
: `country` is the ISO 3166 alpha-2 code where the source's label is a known
  country (N-PORT gives the code; issuer files give names such as "Korea
  (South)", which are converted), otherwise the source's own label (for
  example `Supranational`), otherwise `null`.
: `sector` is the source's own sector label, or `null`. N-PORT has none.
: `asset_class` is `fund` for a sub-fund that could not be looked through;
  see `unexpanded_fund_pct`.

Counts
: `n_constituents` is the number of **distinct constituent ISINs**.
: `n_rows` is the number of rows in the **full** list, which can be larger
  (see Rows). `truncated` is `n_rows > len(constituents)`: large funds are
  cut to the build's `max_constituents` rows and the full list is in
  `full_data_url`.
: `listed_pct` is the sum of `weight_pct` over the rows listed in this file.

Weight
: `resolved_pct` is the weight in securities with an ISIN.
: `unexpanded_fund_pct` is the weight in funds that could not be looked
  through: no source had their holdings, they sit in a cycle, or they were
  reached at the depth limit. Their rows are listed with `asset_class`
  `fund` and do **not** count as resolved.
: `unresolved_pct` is `100 - resolved_pct - unexpanded_fund_pct`: lines with
  no ISIN (cash, derivatives) and whatever the source's lines do not add up
  to. It is negative when the lines total more than 100%.
: `reported_total_pct` is what the fund's own source's lines add up to;
  `unidentified_lines_pct` the weight of its lines with no ISIN.

Breakdowns
: Each of `asset_class`, `country`, `sector` and `source` is a list of
  `[label, pct]` over the fund's **full** list of rows (not only the rows in
  the file), largest first. Rows without a label are summed under
  `"Unknown"`. The unresolved weight is always the **last** entry, labelled
  `"Unresolved"`, so each breakdown adds up to 100.
: `top10_pct` is the weight of the ten largest constituent ISINs, each summed
  over its rows.

Source and matching
: `source`, `source_url`, `as_of` and `credit` describe the fund's own
  holdings. Each row's `source` and `as_of` can differ from the fund's (a
  fund of funds mixes sources). `credit` names every source the rows came
  from, the fund's own first.
: `matched_via` says how the fund was found: `ishares`, `spdr` or an issuer
  adapter's name (the issuer's own fund list); `series_id` (an N-PORT series
  in the universe); `issuer_id` (named by its issuer fund ID);
  `nport_lei` (an N-PORT holding with this ISIN whose issuer LEI is a
  series' LEI); `openfigi_ticker` (the ISIN's US ticker from OpenFIGI, in
  SEC's fund list); `sec_ticker` (a US ticker in SEC's fund list);
  `proxy` (a configured stand-in, said in `note`); `security_master` (not a
  fund: a single security, or a fund ISIN no source has holdings for);
  `directory` (a fund directory source, where one is configured).

Status
: `status` is a plausibility flag: `ok`; `partial` (the source's lines are
  more than 5 points below 100, or less than 90% resolves to securities);
  `over_100` (the lines, or the looked-through constituents, are more than 5
  points above 100); `leveraged` (the name says 2x, Ultra, Inverse, Bear and
  the like, or the lines are more than 50 points from 100: weights are market
  values, not exposure); `no_holdings`. `note` says why in words.

Funds with no holdings
: `as_of` is the date the source's fund list was read (for an N-PORT
  series, its latest report date); `note` says in plain language why there
  are no rows ("iShares publishes only the ten largest holdings for this
  fund; see the source link"); `credit` says the fund was listed by that
  source but no holdings were available; `constituents` is empty, `n_rows`
  is 0, and the unresolved weight is 100.

## `api/v1/funds/{isin}.json` for other share classes

A share-class ISIN that is not a fund file's `id` gets a small alias file:

```json
{"alias_of": "IE00B5BMR087"}
```

## Release assets

Each scheduled build publishes a GitHub Release tagged `YYYY-MM` with
`openbasket-lookthrough.parquet` (every constituent row of every fund, no
truncation, and one unresolved-weight row per fund that carries the fund's
own source, link, as-of date and note), `openbasket-funds.parquet` (one row
per fund, the summary fields above plus `sources_status`, `error` and
timings) and `quality-report.json`.
