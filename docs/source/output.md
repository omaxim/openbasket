# Output

## `data/08_reporting/lookthrough.csv`

One row per constituent ISIN per input ISIN, plus one **unresolved weight**
row per input.

| Column | Meaning |
|---|---|
| `input_isin` | The ISIN asked for. |
| `constituent_isin` | The security held. Empty on the unresolved row. |
| `name` | Security name: the issuer for a stock, the issue title for a bond. |
| `weight_pct` | Percent of the input's net assets, multiplied through sub-funds. |
| `asset_class` | From N-PORT's asset category, e.g. `equity`, `debt`, `asset-backed (mortgage)`, or the issuer's class. `fund` for a sub-fund that could not be looked through (its `note` says why). |
| `country` | ISO 3166 alpha-2 code where the source's label is a known country, else the source's label. |
| `sector` | The source's sector label; N-PORT has none. |
| `source` | Where the holdings came from: `ishares`, `spdr`, an issuer adapter's name, `tickerinside`, `nport`, `security_master`. |
| `as_of` | Holdings date of that source. |
| `issuer_name`, `issuer_lei` | The issuer. For a bond this is who owes the money. |
| `isin_source` | `reported` by the fund or issuer; `reported_for_cusip` in another N-PORT filing; for issuer files, `nport_cusip` or `nport_sedol` (looked up in N-PORT); `derived_cusip`; `ticker_match`; or `name_match`, found by the holding's name (statutory reports name holdings only; see [Matching names to ISINs](sources/fund-reports.md#matching-names-to-isins)). |
| `match_score` | For `name_match` rows, how sure the match is: 0.9 to 1. Empty otherwise. |
| `figi_name`, `figi_ticker`, `security_type`, `market_sector`, `composite_figi` | From OpenFIGI. An empty `composite_figi` on a derived ISIN is worth checking. |
| `depth` | 0 for direct holdings, 1 for holdings of a sub-fund, and so on. |
| `n_paths` | How many routes reach this ISIN (e.g. held directly and via a sub-fund). |
| `via_fund` | The fund whose holdings list the row came from. |
| `source_ref` | Filing accession number, the iShares or SPDR file URL, or TickerInside's citation. |

### Unresolved weight

`weight_pct` on the unresolved row is `100 - resolved - unexpanded`: what
neither securities nor sub-funds that could not be looked through account
for. Nothing is rescaled, so this is the share of the fund the output does
not explain. It can be **negative**: funds report each position as a percent of
net assets, and the positions add up to more than 100 when a fund has
liabilities or purchases not yet paid for.

## `data/08_reporting/lookthrough_summary.csv`

One row per input: the fund matched and how (`matched_via`), the top-level
`source` and `as_of`, `sources_used` with the weight each contributed,
`sources_status` with what each source read returned (`ok`, `empty`,
`top10_only`, `http_403`, `network_error`, `parse_error` ...),
`reported_total_pct`, `resolved_pct`, `unexpanded_fund_pct` (sub-funds that
could not be looked through), `unresolved_pct`, `unidentified_lines_pct`
(lines with no ISIN, e.g. cash and swaps), `n_constituents`,
`max_depth_reached`, `fund_as_security` (the ISIN is a fund that no source
has holdings for), `status`, `error` and `seconds`.

`status` is `ok`, `partial`, `over_100`, `leveraged`, `no_holdings`,
`no_match` (nothing matched the ISIN) or `error` (the look-through of this
input raised; the others carry on). `note` says in words when a proxy was
used, why a fund has no holdings, when the source's lines are more than 5
points from 100, and when one ISIN-less line holds 50% or more, as in
swap-based ETFs.

## `data/08_reporting/lookthrough_unidentified.csv`

Every line with no ISIN, with its weight, name, CUSIP and `reason`, so the
unresolved weight can be checked line by line.

## `data/07_model_output/lookthrough_positions.parquet`

The rows before ISINs reached by several routes are summed, with the full
path through sub-funds.

## Derivatives

Some funds hold their exposure through derivatives: a buffer ETF holds FLEX
options on SPY, a synthetic fund holds a swap on an index fund. N-PORT says
which instrument each derivative references (`DESC_REF_OTHER`), so openbasket
follows it:

- A derivative on a **fund** is broken down into that fund's companies,
  using the fund's own holdings (SPY's daily file, for example). Each company's
  weight is the derivative's market value as a percent of the fund's net
  assets, times the company's weight in the underlying fund.
- A derivative on a **single company** becomes a row for that company's ISIN,
  marked `isin_source = derivative_reference`.

These rows say `via derivatives` in `via_fund` and `exposure = derivative`,
and the fund's `note` gives the share held this way. Market value is not the
same as economic exposure: buffers, caps, leverage and option deltas are not
modelled, so read the weights as "what the fund's derivatives are written on",
not as shares it owns. Set `lookthrough.look_through_derivatives: false` to
turn this off.
