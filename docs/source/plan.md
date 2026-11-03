# Plan

## Goal

Take one ETF, fund or bond by ISIN, match it, and output the percentage of
each individual security behind it, combining free sources in one Kedro
pipeline.

## Rules

- ISIN is the key throughout.
- Weights are never rescaled to 100%. What the constituents do not explain
  is reported as unresolved weight, even when that is negative.
- Every row says where it came from (`source`, `source_ref`, `as_of`) and
  how its ISIN was obtained (`isin_source`).
- Sources with licence terms are credited row by row.
- Large tables stay in DuckDB and Parquet, not pandas.

## Pipelines

```text
ingest ──► security_master ──► consolidate ──► lookthrough
```

`ingest`
: Download the N-PORT quarters and SEC's fund ticker list; convert the four
  N-PORT tables needed to Parquet.

`security_master`
: Join holdings to their identifiers; build the CUSIP to ISIN map (reported,
  then derived from the CUSIP); build an ISIN-keyed security master and a
  ticker to ISIN map.

`consolidate`
: Pick each series' latest filing; attach the best ISIN to every holding of
  it; build the fund master (series, LEI, share-class tickers).

`lookthrough`
: Match each input ISIN, expand it recursively, and write the output.

## Matching an input ISIN

1. A configured proxy.
2. N-PORT: a holding with this ISIN (or its CUSIP) whose issuer LEI is a fund
   series' LEI.
3. OpenFIGI: the ISIN's US ticker, found in SEC's fund ticker list.
4. Not a fund: if the ISIN is in the security master it is its own
   constituent at 100% (a single stock or bond, shown with its issuer).
5. Otherwise 100% unresolved.

## Expanding a fund

For each source in `source_order` that has holdings for the fund, count how
much weight resolves to ISINs. Use the first source that reaches
`min_resolved_pct` (90 by default), otherwise the one resolving the most.
TickerInside therefore wins for US equity ETFs, because it is fresher, and
N-PORT for bond funds, which TickerInside does not list security by security.

A holding is a fund if its issuer LEI is a series LEI, or its ticker is in
SEC's fund ticker list. It is expanded in turn, with its weights multiplied
by its weight in the parent, up to `max_depth` and never around a cycle.
Stocks and bonds are end points.

## Status

Done (2026-11-03):

- Repository, `.env` handling, Kedro 1.7 scaffold, Sphinx docs, tests.
- N-PORT end to end for 2026 Q3: 13,200 fund series with a latest filing, 13,192 of them with holdings.
- TickerInside issuer holdings with ticker to ISIN matching.
- OpenFIGI matching and enrichment with an on-disk cache.
- Recursive fund-of-funds expansion; direct stocks and bonds.

Sample results, from 2026 Q3 N-PORT and TickerInside as of 2026-10-27:

| ISIN | Fund | Source | Resolved | Unresolved |
|---|---|---|---|---|
| US4642872000 | iShares Core S&P 500 ETF (IVV) | TickerInside | 99.81% | 0.19% |
| US4642872265 | iShares Core U.S. Aggregate Bond ETF (AGG) | N-PORT | 101.88% | −1.88% |
| US4642898674 | iShares Core 60/40 Balanced Allocation ETF (AOR) | N-PORT, 7 sub-funds | 103.58% | −3.58% |
| IE00B5BMR087 | iShares Core S&P 500 UCITS ETF (CSPX), via IVV proxy | TickerInside | 99.81% | 0.19% |
| US037833EE62 | Apple Inc bond | Security master | 100% | 0% |

The negative figures are what the filings say: AGG's lines, and those of
AOR's bond sub-funds, add up to more than 100% of net assets. Why (for
example, purchases not yet settled) is not yet broken down.

## Next

1. **Issuer files.** Find the iShares endpoints that replaced the `.ajax`
   CSVs (UK first, to drop the CSPX proxy), then SPDR's daily Excel files,
   whose header row starts with `Name`.
2. **More N-PORT quarters.** Load 2026 Q2 alongside Q3 for funds whose
   latest filing fell earlier; add 2026 Q4 when the SEC publishes it.
3. **Top-holdings fallbacks** (justETF, FT) for UCITS funds with no issuer
   file, clearly marked as partial.
4. **Swap-based ETFs.** Add proxies for the common ones and flag funds whose
   N-PORT is mostly swaps.
5. **Ticker matching.** Resolve TickerInside tickers that are newer than the
   fund's last N-PORT filing through the global security master.
