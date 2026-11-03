# SEC Form N-PORT, older quarters

The SEC publishes Form N-PORT as one ZIP per calendar quarter of **filing
date**. Most US funds file a public report once a quarter, so most fund
series are in every quarter's file, but not all of them: a series whose last
public report was filed in an earlier quarter is missing from the newest file.
Loading older quarters as well brings those series back, each with the
portfolio of its own latest filing.

Module: `openbasket.sources.sec_nport_history`; parameters
`conf/base/parameters_nport_history.yml`; pipeline `nport_history`, which
writes `data/08_reporting/nport_history_coverage.csv`.

## How it works

`nport.quarters` in `parameters.yml` names the newest quarter(s) as before.
`nport_history` adds older ones:

```yaml
nport_history:
  lookback: 2     # quarters before the newest of nport.quarters
  quarters: []    # further quarters by name, e.g. [2025q4]
```

Kedro refuses the same top-level key in two parameter files, so the history
settings are their own block rather than a second `nport.quarters`. The
`download_nport` node in `ingest` takes `params:nport_history` and loads the
union, oldest first: with `nport.quarters: [2026q3]` and `lookback: 2` that is
`2026q1, 2026q2, 2026q3`. `lookback: 0` and `quarters: []` restore the old
behaviour. To see the list the parameters give:

```console
$ python -m openbasket.sources.sec_nport_history
2026q1-2026q2-2026q3
```

Nothing downstream changes. Every table already carries a `QUARTER` column and
`security_master` and `consolidate` read every loaded quarter.
`latest_filings` keeps one filing per series over all of them: the latest
report date, then the latest filing date. So a fund in both 2026q2 and 2026q3
gets its 2026q3 filing, and an amendment (`NPORT-P/A`) filed in a later
quarter replaces the original. No filing appears in two quarters' files (0 of
41,630 accession numbers in 2026q1 to 2026q3), and no `HOLDING_ID` repeats
across quarters, so holdings and identifiers do not mix between quarters. The older
quarters also feed the CUSIP-to-ISIN map and the fund-holding index used to
match a fund ISIN to its series.

Each row keeps its own filing: `as_of` is the filing's `REPORT_DATE` (the
position date), and `source_ref` names the accession number, so a fund whose
latest report is from January shows January.

## Endpoints

Index
: `https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets`,
  which links every quarter from 2019q4 to 2026q3.
  `available_quarters()` reads the list from it.

Data
: `https://www.sec.gov/files/dera/data/form-n-port-data-sets/{YYYY}q{N}_nport.zip`,
  about 450 MB each (2026q2: 440.7 MB). The ingest downloads and streams these
  as before (`openbasket.sources.sec.download`).

Remote tables
: The server accepts HTTP range requests (`Accept-Ranges: bytes`), so
  `remote_tables_to_parquet(quarter, out_dir)` reads single tables out of a
  ZIP on sec.gov without downloading it: five range requests for `SUBMISSION`
  and `FUND_REPORTED_INFO` of one quarter. This is how the coverage below was
  measured before choosing the default, and what the network test uses. All
  requests go through `openbasket.http` at one per second, with the
  `SEC_USER_AGENT` the SEC asks for.

## Coverage

Measured on 3 November 2026 with `nport.quarters: [2026q3]`, adding one older
quarter at a time. "Added" are series with no filing in any newer quarter.

| Quarters loaded | Series | Added | Added, still with a ticker | Added, flagged final filing | Added net assets | Positions as of |
|---|---|---|---|---|---|---|
| 2026q3 | 13,200 | | | | | 2026-04-30 to 2026-07-31 |
| + 2026q2 | 13,414 | 214 | 151 | 50 | USD 21.3 bn | 2025-11-30 to 2026-05-31 |
| + 2026q1 | 13,565 | 151 | 116 | 42 | USD 29.7 bn | 2025-11-28 to 2026-01-31 |
| + 2025q4 | 13,701 | 136 | 91 | 42 | USD 42.4 bn | 2025-05-31 to 2025-10-31 |
| + 2025q3 | 13,949 | 248 | 123 | 32 | USD 47.2 bn | 2025-02-28 to 2025-07-31 |

"Still with a ticker" means a share class of the series is in
`company_tickers_mf.json` today. "Final filing" is `IS_LAST_FILING = Y`: the
fund said it was liquidating or merging. Only 3 of the 214 series 2026q2 adds
are simply between filings, meaning their next public report (period plus three
months plus 60 days) is not due yet. The rest missed a report they owed: funds
merged into another series, liquidated without flagging it, or filed late. Their last
portfolio is still what an investor holding that ISIN (or its statements)
was invested in, so it is kept, with its date.

**Default: `lookback: 2`** (2026q1 to 2026q3), +365 series, every added
portfolio less than a year old (oldest position date 2025-11-28). 2025q4 and
2025q3 add 384 more, but with positions 12 to 20 months old and none of them
merely between filings; add them with `quarters: [2025q4]` if wanted. Each
extra quarter costs one more ZIP of about 450 MB, 1.2 GB of extracted text and
5.3 to 5.9 million more holding rows in `nport_positions`.

Funds the history adds include bank-run series, for example BNY Mellon
Municipal Opportunities, BNY Mellon National Intermediate Municipal Bond and
six more BNY Mellon funds (last filed January 2026), Goldman Sachs
Government Income, Morgan Stanley Europe Opportunity, UBS Engage For Impact,
Xtrackers (DWS) Risk Managed USD High Yield Strategy and California Municipal
Bond ETFs, RBC BlueBay Short Duration Fixed Income and DWS Floating Rate.

## A real run

`ingest` (N-PORT nodes), `security_master`, `consolidate` and `nport_history`
with the default, on 3 November 2026. Downloading 2026q1 and 2026q2 (881 MB)
and converting all three quarters took 15 minutes; `security_master` took 17 s
and `consolidate` 4 s.

| | 2026q3 only | 2026q1 to 2026q3 |
|---|---|---|
| Holding rows read (`FUND_REPORTED_HOLDING`) | 5,627,083 | 16,916,020 |
| Series with holdings in `nport_holdings` | 13,192 | 13,554 |
| Rows in `nport_holdings` | 4,370,557 | 4,418,343 |
| CUSIPs with an ISIN (`cusip_isin_map`) | 386,518 | 422,294 |
| Securities in `security_master` | 402,883 | 440,567 |

No accession number is in two quarters, and no `HOLDING_ID` repeats across
the 16.9 million holdings (the node logs a warning if either ever happens).

Five funds the older quarters bring back, looked through as the universe run
does it (share-class ISIN attached to its series in `security_master` by
issuer LEI = series LEI, then the series' latest filing). Weights are
`PERCENTAGE` as filed, never rescaled. "With ISIN" counts lines with an ISIN,
whether reported in the filing or found for its CUSIP.

| Fund (manager) | Fund ISIN | Quarter | As of | Lines | Weight sum | With ISIN (reported) |
|---|---|---|---|---|---|---|
| BNY Mellon Intermediate Bond Fund (BNY) | US05569M8148 | 2026q1 | 2025-11-30 | 101 | 99.51% | 99.51% (98.90%) |
| BNY Mellon Corporate Bond Fund (BNY) | US05569M3685 | 2026q1 | 2025-11-30 | 145 | 101.94% | 101.94% (98.36%) |
| BNY Mellon Mid Cap Multi-Strategy Fund (BNY) | US05569M5094 | 2026q2 | 2026-02-28 | 574 | 100.15% | 100.15% (100.07%) |
| DWS Floating Rate Fund (DWS, Deutsche Bank) | US25157W8001 | 2026q2 | 2026-02-28 | 8 | 99.77% | 99.77% (99.74%) |
| Columbia Bond Fund (Columbia Threadneedle) | US19765Y2744 | 2026q1 | 2026-01-31 | 447 | 106.88% | 106.88% (102.35%) |
| ClearBridge Variable Appreciation Portfolio (Franklin) | US52467W8828 | 2026q2 | 2026-03-31 | 62 | 99.94% | 99.94% (99.94%) |

Each source link is the filing on EDGAR, e.g.
`https://www.sec.gov/Archives/edgar/data/1111565/000094040026002285/` for the
first. DWS Floating Rate's filing is flagged as its last: 95.1% of it is in
the DWS Central Cash Management Government Fund, the portfolio of a fund being
wound up. Columbia Bond's lines sum to 106.88% as filed (50.1% of it in
mortgage-backed lines); it is kept as filed, not rescaled.

Further bank-run series the history brings back, read by series ID because no
share-class ISIN for them is in the loaded filings: Goldman Sachs Government
Income (S000009304, 2025-12-31, 384 lines, 110.43%), UBS Engage For Impact
(S000063381, 2026-03-31, 52 lines, 106.04%), Xtrackers Risk Managed USD High
Yield Strategy ETF (S000074793, 2026-02-27, 4 lines, 126.91%), Morgan Stanley
Europe Opportunity (S000002387, 2026-01-31, 25 lines, 99.86%) and RBC BlueBay
Short Duration Fixed Income (S000043718, 2026-03-31, 86 lines, 97.89%).

## Freshness

The SEC adds a quarter's ZIP a few weeks after the quarter ends (2026q2 was
posted on 9 July 2026). Public N-PORT reports are filed up to 60 days after the
period, so the newest quarter's positions are two to five months old and
the oldest of the default three about eleven months old. When a new quarter is
added to `nport.quarters`, `lookback` moves with it.

## Terms

The SEC's website policy (<https://www.sec.gov/privacy>, "Website
Dissemination"):

> Information presented on sec.gov is considered public information and may
> be copied or further distributed by users of the web site without the SEC's
> permission.

It asks for citation of the SEC as the source and that the SEC seal and logos
not be used. N-PORT data is a work of the US government. `robots.txt` does not
disallow `/files/` or `/data-research/`. Credit line, as for the newest
quarter: "Holdings from SEC Form N-PORT filings, U.S. Securities and Exchange
Commission". N-PORT holds no personal data in the tables read here (fund,
filing and position rows).

## Gaps

- **Money market funds** file N-MFP, not N-PORT. They are covered by the
  separate `sec-nmfp` source (branch `src/sec-nmfp`), so this work does not
  build N-MFP.
- **Filings without a series ID** (737 of 14,066 in 2026q3: closed-end funds,
  BDCs and interval funds registered without series) are dropped by
  `latest_filings`, in every quarter alike.
- **Fund ISINs.** N-PORT names a series, not its share-class ISINs. A
  share-class ISIN is known only where another fund holds it (issuer LEI =
  series LEI in `security_master`); that finds one for 30 of the 365 added
  series. The rest are reached by series ID or ticker, as for any N-PORT
  fund.
- **Single-ISIN look-through.** The `lookthrough` pipeline matches an ISIN to
  a series through holdings in the latest filings only
  (`DataProviders._series_from_nport`), so an ISIN that appears only in an
  older filing is not matched there. The `universe` run attaches it through
  `security_master`, which reads every loaded quarter.
- **Stale funds.** Nothing filters out funds that closed. Their rows carry the
  old as-of date, and `added_final_filing` in the coverage report counts the
  ones that said so.
- **CI cache key.** `.github/workflows/publish.yml` keys its N-PORT download
  cache on `nport.quarters` only. With history on, it should use
  `python -m openbasket.sources.sec_nport_history` instead, or the older
  quarters are downloaded on every run after the first cache hit.
