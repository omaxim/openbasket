# Data API

openbasket is an aggregator. It republishes nothing of its own: every
holding comes from a named public source, and every file and every row says
which one, with a link and an as-of date.

The site at `https://openbasket.leithresearch.org/` is static. The "API" is a
set of JSON files rebuilt by the scheduled build and served by GitHub Pages,
so there is no server to overload. All paths are relative to the site root.

This page is contract **1.1**. The public site and release are built with
`publish.mode: public`: they carry only sources whose licence allows
republication. A run on your own machine (`publish.mode: full`, see
[](running-locally.md)) writes the same files with every source it read,
for your own use. The files still live under `api/v1` and
`meta.json` still says `"api_version": 1`; `meta.json` says which contract
the files follow in `contract`. 1.1 adds fields, and changes what two 1.0
fields count (see [Changes from 1.0](#changes-from-10)).

## Changes from 1.0

1. `resolved_pct` no longer counts sub-funds that could not be looked
   through (no source had their holdings, a cycle, the depth limit). That
   weight is in the new `unexpanded_fund_pct`, and those rows are listed
   with `asset_class` `fund`. So `resolved_pct + unexpanded_fund_pct` is the
   1.0 `resolved_pct`.
2. `unresolved_pct` is `100 - resolved_pct - unexpanded_fund_pct`. It is the
   same number as in 1.0, because 1.0 counted the unexpanded weight as
   resolved; it now sits beside `unexpanded_fund_pct` instead of inside
   `resolved_pct`. A consumer that compares `resolved_pct` across builds
   sees it fall for funds of funds whose sub-funds have no holdings.
3. Fund files gain `status`, `unexpanded_fund_pct`, `n_rows`, `listed_pct`
   and `breakdowns`; constituent rows gain `country` and `sector`.
4. Breakdowns sum rows that have no label under `"Unknown"`, and always end
   with `"Unresolved"`.
5. `unresolved` gains `source`, `source_url` and `as_of`, so a fund with no
   holdings still says where its list came from.
6. `matched_via` takes the values listed below (`series_id`, `issuer_id`
   and `proxy` are new).
7. `meta.json` gains `contract` and `mode`, and lists every source that
   appears anywhere, including `openfigi`, each with its `redistribution`
   class.
8. `status` gains `local_only`: in the public build, a fund whose holdings
   only a source that may not be republished has (an issuer's website). Such
   a fund is listed by name and ISIN with no rows; its `source` is where that
   identity came from (`issuer_list`, `fund_directory`, `fund_registry`,
   `firds`).

## `api/v1/meta.json`

```json
{
  "api_version": 1,
  "contract": "1.1",
  "mode": "public",
  "built_at": "2026-11-03T12:00:00Z",
  "release_tag": "2026-11",
  "release_url": "https://github.com/omaxim/openbasket/releases/tag/2026-11",
  "full_data_url": "https://github.com/omaxim/openbasket/releases/download/2026-11/openbasket-lookthrough.parquet",
  "n_funds": 15234,
  "n_isins": 31877,
  "sources": [
    {
      "id": "nport",
      "name": "SEC Form N-PORT",
      "url": "https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets",
      "licence": "Public US government data",
      "credit": "Holdings from SEC Form N-PORT filings, U.S. Securities and Exchange Commission",
      "redistribution": "public",
      "n_funds": 12559,
      "as_of_min": "2026-04-30",
      "as_of_max": "2026-07-31"
    }
  ]
}
```

`sources[].id` is one of:

| id | What it supplies |
|---|---|
| `ishares`, `spdr` | The issuer's own holdings files. `local_only`: in local (full) builds only. |
| any issuer adapter's name (`cvm_brazil`, `cnmv_spain`, `amundi`, ...) | That source's holdings, read by its adapter in `openbasket.sources.issuers`; in the public build only if the adapter is `public` (CVM, CNMV). |
| `issuer_list`, `fund_directory`, `fund_registry`, `firds` | A fund's name and ISIN only, for a `local_only` fund in the public build: from the issuer's public fund list, or from an open registry. |
| `nport` | SEC Form N-PORT filings. |
| `tickerinside` | TickerInside's issuer-layer holdings of US ETFs. |
| `security_master` | A single stock or bond, described from N-PORT. |
| `openfigi` | Names and security types, where a row's own source gave no name. |

Every id that appears anywhere in the files (a row's `source`, a fund's
`source`, a name taken from OpenFIGI) is listed, with `name`, `url`,
`licence`, `credit` and `redistribution` (`public`, or `local_only` for a
source whose terms restrict republication; the public build lists none of
those). `mode` is `public` for the public build and `full` for a local run. `as_of_min` and `as_of_max` are null for `openfigi`,
which has no holdings dates. TickerInside's credit says what openbasket
changed: tickers mapped to ISINs and names taken from N-PORT, under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## `api/v1/index.json`

Every fund the build covers, as a table, so the search box can load it once
(gzip on the wire, about 1 MB). In 1.1 each row ends with the fund's `status` (`ok`, `partial`, `over_100`, `leveraged`, `no_holdings`, `local_only`, `stand_in`, `error`), so search can mark funds whose holdings need a local run:

```json
{
  "fields": ["id", "isin", "name", "ticker", "issuer", "kind", "source", "as_of", "resolved_pct", "n_constituents", "status"],
  "rows": [
    ["US4642872000", "US4642872000", "iShares Core S&P 500 ETF", "IVV", "iShares Trust", "fund", "nport", "2026-06-30", 99.8, 503, "ok"]
  ],
  "aliases": {"US4642872000": "US4642872000"}
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

This example is a US ETF whose holdings come from its SEC N-PORT filing, a
public source, so the public build writes it like this. Numbers in these
examples are illustrative, not taken from a real file.

```json
{
  "id": "US4642872000",
  "isin": "US4642872000",
  "isins": ["US4642872000"],
  "name": "iShares Core S&P 500 ETF",
  "ticker": "IVV",
  "issuer": "iShares Trust",
  "source": "nport",
  "source_url": "https://www.sec.gov/Archives/edgar/data/1100663/...",
  "credit": "Holdings from SEC Form N-PORT filings, U.S. Securities and Exchange Commission",
  "as_of": "2026-06-30",
  "matched_via": "nport_lei",
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
    "source": [["nport", 99.86422], ["Unresolved", 0.13578]],
    "top10_pct": 38.9
  },
  "fields": ["isin", "name", "weight_pct", "asset_class", "source", "as_of", "depth", "via", "country", "sector"],
  "constituents": [
    ["US0000000001", "EXAMPLE CORP", 6.5, "equity", "nport", "2026-06-30", 0, "iShares Core S&P 500 ETF", "US", "Information Technology"]
  ],
  "unresolved": {
    "weight_pct": 0.13578,
    "note": "0.15% is in lines with no ISIN (cash, futures, FX, swaps or pools).",
    "source": "nport",
    "source_url": "https://www.sec.gov/Archives/edgar/data/1100663/...",
    "as_of": "2026-06-30"
  },
  "full_data_url": "https://github.com/omaxim/openbasket/releases/download/2026-11/openbasket-lookthrough.parquet"
}
```

Keys
: The keys are written in the order shown. `fields` names the columns of
  each row of `constituents`.

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
: Options, swaps and futures that name what they reference (N-PORT's
  `DESC_REF_OTHER`) are looked through to it: one on a single company is a
  row for that company, one on a fund is that fund's companies, weighted by
  the derivative's market value as reported (payoffs such as buffers, caps
  or leverage are not modelled). Their `via` ends in `(via derivatives)`,
  and the fund's `note` says how much weight is held this way. A derivative
  on a fund that cannot be looked through is an unexpanded `fund` row.

Counts
: `n_constituents` is the number of **distinct constituent ISINs**.
: `n_rows` is the number of rows in the **full** list, which can be larger
  (see Rows). `truncated` is `n_rows > len(constituents)`: large funds are
  cut to the build's `max_constituents` rows and the full list is in
  `full_data_url`.
: `listed_pct` is the sum of `weight_pct` over the rows listed in this file.

Weight
: `resolved_pct` is the weight in securities with an ISIN, including
  those reached through derivatives (see Rows).
: `unexpanded_fund_pct` is the weight in funds that could not be looked
  through: no source had their holdings, they sit in a cycle, or they were
  reached at the depth limit. Their rows are listed with `asset_class`
  `fund` and do **not** count as resolved.
: `unresolved_pct` is `100 - resolved_pct - unexpanded_fund_pct`: lines with
  no ISIN (cash, derivatives whose reference is not identified) and whatever
  the source's lines do not add up to. It is negative when the lines total
  more than 100%.
: `unresolved` repeats `unresolved_pct` as `weight_pct`, with the fund's
  `source`, `source_url` and `as_of`. Its `note` is the fund's note when
  there are no rows, otherwise a sentence on the weight in lines without an
  ISIN, or `null` when there is none.
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
: `sector_from`, when present, is a list of `[source id, pct]`: the weight of
  rows whose holdings source gives no sector (SEC Form N-PORT gives none) and
  whose sector openbasket took from that source instead, matched by the
  company's ISIN. Today the only such source is `financedatabase`, the GICS
  sector of each company in FinanceDatabase's equities file (MIT). A row's
  own sector always wins, and the source is listed in `meta.sources`. In the
  release Parquet file these rows say `sector_source = "financedatabase"`.

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
  `proxy` (a configured stand-in, said in `note`); `stand_in` (a US fund
  named after the same index, see {doc}`sources/stand-ins`); `security_master` (not a
  fund: a single security, or a fund ISIN no source has holdings for);
  `issuer_list`, `fund_directory`, `fund_registry` or `firds` (a
  `local_only` fund in the public build, named from that list or registry).

Status
: `status` is a plausibility flag: `ok`; `partial` (the source's lines are
  more than 5 points below 100, or less than 90% resolves to securities);
  `over_100` (the lines, or the looked-through constituents, are more than 5
  points above 100); `leveraged` (the name says 2x, Ultra, Inverse, Bear and
  the like, or the lines are more than 50 points from 100: weights are market
  values, not exposure); `no_holdings`; `local_only` (public build only:
  the only source of the fund's holdings may not be republished, see
  below); `stand_in` (the fund's own holdings are not available, so the rows
  are a US fund's N-PORT holdings on the same index, see below). `note` says
  why in words.

Funds with no holdings
: `as_of` is the date the source's fund list was read (for an N-PORT
  series, its latest report date); `note` says in plain language why there
  are no rows ("iShares publishes only the ten largest holdings for this
  fund; see the source link"); `credit` says the fund was listed by that
  source but no holdings were available; `constituents` is empty, `n_rows`
  is 0, and the unresolved weight is 100. `unresolved` carries the same
  `source`, `source_url`, `as_of` and `note`.
: A `local_only` fund is one of these, in the public build, whose holdings
  only an issuer's own site publishes. Its `source` is where its name and
  ISIN came from, `source_url` the issuer's home page (or the registry's
  record), and `note` points to running openbasket yourself:

```json
{
  "id": "IE0001JK8FV4", "name": "iShares Broad Global Govt Bond UCITS ETF", "issuer": "iShares Trust",
  "source": "issuer_list", "source_url": "https://www.ishares.com/",
  "credit": "Fund listed by the issuer's public fund list; its holdings are available from iShares when you run openbasket yourself",
  "as_of": "2026-11-03", "matched_via": "issuer_list", "status": "local_only",
  "note": "Holdings for this fund are available from iShares when you run openbasket yourself (see https://openbasket.leithresearch.org/docs/running-locally.html)",
  "resolved_pct": 0.0, "unresolved_pct": 100.0, "n_constituents": 0, "n_rows": 0, "constituents": [],
  "unresolved": {"weight_pct": 100.0, "note": "Holdings for this fund are available from iShares ...",
                 "source": "issuer_list", "source_url": "https://www.ishares.com/", "as_of": "2026-11-03"}
}
```

  (Keys abridged.) A fund in the public build that holds a `local_only`
  fund keeps it as an unexpanded `fund` row with the same note.

## Stand-in funds

A fund with status `stand_in` has rows, but they are not its own: they are
the SEC Form N-PORT holdings of a US fund whose name names the same index
(see {doc}`sources/stand-ins`). `source` is `nport`, `as_of` the date of
that filing, `matched_via` `stand_in`, every row's `via` the US fund, and
`note` names the US fund, its SEC series, the index and why the fund's own
holdings are missing. Weights are percent of the US fund's net assets.
`credit` begins "Stand-in, not this fund's own holdings".

Where a local run has computed the fund's own coarse splits (below),
`breakdowns.own` carries them in the same shape as a `local_only` fund's
`breakdowns`; the other `breakdowns` keys describe the stand-in's rows.

## Coarse splits for `local_only` funds

In the public build, a `local_only` fund has no constituents, but its
`breakdowns` may carry openbasket's own coarse splits, computed in a local
full run from the issuer's holdings and published without them:

```json
"breakdowns": {
  "basis": "openbasket_aggregate",
  "note": "Computed by openbasket from iShares's published holdings as of 2026-11-02; whole percent, holdings not shown. Run openbasket yourself to see the companies.",
  "as_of": "2026-11-02",
  "computed_at": "2026-11-04T09:00:00Z",
  "updated_at": "2026-11-04T10:12:00Z",
  "sector": [["Information Technology", 30], ["Financials", 14], ["Other", 56], ["Unresolved", 0]],
  "region": [["North America", 99], ["Other", 1], ["Unresolved", 0]],
  "country": [["US", 99], ["Other", 1], ["Unresolved", 0]],
  "asset_class": [["Equity", 100], ["Unresolved", 0]],
  "top10_pct": null
}
```

Values are whole percent of net assets. Labels come only from fixed lists:
openbasket's sector list (the eleven GICS sector names plus Government Bonds
and Corporate Bonds), ISO country codes, five regions, six asset classes, and
`Other`, `Unknown`, `Unresolved`. There is no `source` split and no
`top10_pct`, because both would describe the holdings. Without such splits
(`basis` absent), `breakdowns` is computed from the fund's own rows as above.

`computed_at` is when a local run computed this fund's splits; `updated_at`
is when `public_aggregates/aggregates.json` was last committed to the
repository (the public build reads it from git; outside git it repeats
`computed_at`). Splits kept from an earlier run, because the latest run had
no holdings for the fund, have `updated_at` equal to their own `computed_at`. Rows
whose issuer file gives no sector take their company's GICS sector from
FinanceDatabase by ISIN, as in the public build.

## `api/v1/funds/{isin}.json` for other share classes

A share-class ISIN that is not a fund file's `id` gets a small alias file:

```json
{"alias_of": "US4642872000"}
```

## Release assets

Each scheduled build publishes a GitHub Release tagged `YYYY-MM` with
`openbasket-lookthrough.parquet` (every constituent row of every fund, no
truncation, and one unresolved-weight row per fund that carries the fund's
own source, link, as-of date and note), `openbasket-funds.parquet` (one row
per fund, the summary fields above plus `sources_status`, `error` and
timings) and `quality-report.json`.
