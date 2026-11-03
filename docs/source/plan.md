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

1. A configured proxy (it wins over an issuer listing, for swap ETFs).
2. An issuer's fund list (iShares UK and US; SPDR US and UCITS; every
   registered issuer adapter).
3. N-PORT: a holding with this ISIN (or its CUSIP) whose issuer LEI is a fund
   series' LEI. Placeholder CUSIPs and ISINs reported under more than three
   series LEIs are ignored.
4. OpenFIGI: the ISIN's US ticker, found in SEC's fund ticker list.
5. Not a fund: if the ISIN is in the security master it is its own
   constituent at 100% (a single stock or bond, shown with its issuer). An
   ISIN the security master lists as a fund (issuer type RF or PF, or a
   short-term investment vehicle) is reported as a fund with no holdings
   instead.
6. Otherwise 100% unresolved.

## Expanding a fund

For each source in `source_order` that has holdings for the fund, count how
much weight resolves to ISINs. Use the first source that reaches
`min_resolved_pct` (90 by default), otherwise the one resolving the most.
The issuer's own file wins where there is one. Otherwise TickerInside wins
for US equity ETFs, because it is fresher, and N-PORT for bond funds, which
TickerInside does not list security by security.

A holding is a fund if its ISIN is in an issuer's fund list, its issuer LEI
is a series LEI, or (for US listings from TickerInside only) its ticker is in
SEC's fund ticker list. It is expanded in turn, with its weights multiplied
by its weight in the parent, up to `max_depth` and never around a cycle.
Stocks and bonds are end points. A sub-fund that cannot be expanded stays a
row of its own, counted as unexpanded weight, not as resolved. TickerInside
is asked only for US funds.

## Status

Done (2026-11-03):

- Repository, `.env` handling, Kedro 1.7 scaffold, Sphinx docs, tests.
- N-PORT end to end for 2026 Q3: 13,200 fund series with a latest filing, 13,192 of them with holdings.
- TickerInside issuer holdings with ticker to ISIN matching.
- OpenFIGI matching and enrichment with an on-disk cache.
- Recursive fund-of-funds expansion; direct stocks and bonds.
- iShares UK issuer holdings with ISINs, replacing the CSPX proxy.
- iShares US issuer holdings with ISINs: IVV, AGG and AOR (and AOR's
  sub-funds) now come from the issuer instead of TickerInside or N-PORT.
- SPDR US and UCITS daily holdings, covering SPY and DIA, with CUSIP and
  SEDOL lookups through N-PORT.
- Universe and publish pipelines, data contract 1.1 (row counts,
  breakdowns, statuses), issuer adapters used automatically, share classes
  grouped, failures isolated per input and per source, OpenFIGI optional at
  run time, placeholder CUSIPs dropped.

Sample results, from 2026 Q3 N-PORT, TickerInside as of 2026-10-27 and
iShares and SPDR as of 2026-11-02:

| ISIN | Fund | Source | Resolved | Unresolved |
|---|---|---|---|---|
| US4642872000 | iShares Core S&P 500 ETF (IVV) | iShares | 99.99% | 0.01% |
| US4642872265 | iShares Core U.S. Aggregate Bond ETF (AGG) | iShares | 102.97% | −2.97% |
| US4642898674 | iShares Core 60/40 Balanced Allocation ETF (AOR) | iShares, 7 sub-funds | 101.31% | −1.31% |
| IE00B5BMR087 | iShares Core S&P 500 UCITS ETF (CSPX) | iShares | 99.86% | 0.14% |
| IE00B4L5Y983 | iShares Core MSCI World UCITS ETF (SWDA) | iShares | 99.65% | 0.35% |
| IE00B1FZSB30 | iShares Core UK Gilts UCITS ETF (IGLT) | iShares | 100.02% | −0.02% |
| IE00B3F81409 | iShares Core Global Aggregate Bond UCITS ETF (AGGG) | iShares | 92.22% | 7.78% |
| IE000OY7BDY9 | iShares MSCI North America Swap UCITS ETF | iShares, flagged as swap-based | 0% | 100% |
| US78462F1030 | SPDR S&P 500 ETF Trust (SPY) | SPDR | 99.90% | 0.10% |
| US78463X8891 | SPDR Portfolio Developed World ex-US (SPDW) | SPDR | 99.21% | 0.79% |
| IE00B6YX5C33 | SPDR S&P 500 UCITS ETF | SPDR | 100.00% | 0.00% |
| US037833EE62 | Apple Inc bond | Security master | 100% | 0% |

The negative figures are what the issuer files say: AGG's lines, and those
of AOR's bond sub-funds, add up to more than 100% of net assets, offset by a
negative USD cash line (−2.92% in AGG), most likely purchases not yet
settled. IVV's US equity weights are given to two decimals only.

## Next

1. **Other UCITS issuers**: Vanguard, Xtrackers, Amundi, Invesco.
2. **Other iShares regions.** Find the fund-list endpoint and product-data
   service names for the Swiss and German sites, as was done for the US.
3. **Mortgage pools.** Match iShares pool numbers to CUSIPs through N-PORT
   so bond funds' agency MBS resolve.
4. **More N-PORT quarters.** Load 2026 Q2 alongside Q3 for funds whose
   latest filing fell earlier; add 2026 Q4 when the SEC publishes it.
5. **Top-holdings fallbacks** (justETF, FT) for UCITS funds with no issuer
   file, clearly marked as partial.
6. **Swap-based ETFs.** Add proxies for the common ones; the summary already
   flags them, and proxies now win over issuer listings.
7. **Canadian CUSIPs.** A CUSIP with no reported ISIN gets a `CA` prefix when
   N-PORT's investment country (country of risk) is Canada; most of those
   are not confirmed by OpenFIGI. Use the country of issue, or OpenFIGI,
   instead.
8. **Stale filings.** Flag N-PORT series whose latest report is more than a
   quarter old.
