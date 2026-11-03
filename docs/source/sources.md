# Sources

Status as tested on 2026-11-03.

| Source | Status | Used for |
|---|---|---|
| SEC Form N-PORT | Working | Full holdings of US-registered funds and ETFs, equity and bond, with ISINs, CUSIPs and LEIs. |
| SEC `company_tickers_mf.json` | Working | Fund and ETF ticker to series ID. |
| TickerInside | Working | Daily issuer holdings for US equity ETFs, keyed by ticker. |
| OpenFIGI | Working | ISIN to name, ticker and security type; ISIN to US ticker when matching a fund. |
| iShares UK | Working | Daily full holdings of UCITS ETFs with ISINs and five-decimal weights, from the issuer's site. |
| iShares US | Working | Daily full holdings of US iShares ETFs with ISINs; equity weights to two decimals. |
| SPDR daily Excel | Not built | |
| justETF, FT and others | Not built | Top holdings only. |

## SEC Form N-PORT

Quarterly ZIPs of tab-delimited tables from
`https://www.sec.gov/files/dera/data/form-n-port-data-sets/{quarter}_nport.zip`.
2026 Q3 is 462 MB zipped and 1.6 GB unzipped; `FUND_REPORTED_HOLDING` alone is
5.6 million rows. The pipeline extracts four tables, converts them to Parquet
with DuckDB, and never loads the full quarter into pandas.

What the data shows:

- `PERCENTAGE` is in **percent** of net assets (IVV's lines sum to 100.12).
- A quarter holds several report periods per fund, plus late amendments
  (`NPORT-P/A`) to old ones. Each series uses its latest report date, and an
  amendment replaces the original filing for the same date.
- 96.6% of holding rows carry an ISIN in `IDENTIFIERS`. Where a row has
  only a CUSIP, the ISIN another filing reported for that CUSIP is used
  (385,195 CUSIPs), then one derived from the CUSIP (1,323 CUSIPs, US and
  Canadian numbers only).
- A fund holding another fund is linked by `ISSUER_LEI` = the held fund's
  `SERIES_LEI`.
- N-PORT is filed monthly but published quarterly, 60 days after the period,
  so holdings run two to five months behind.

Gaps:

- **Unit investment trusts** such as SPY and DIA do not file N-PORT. SPY is missing from `company_tickers_mf.json` for the
  same reason.
- **Non-US funds** (UCITS, including IE- and LU-ISIN ETFs) are not in
  N-PORT; iShares UCITS ETFs are covered from the issuer instead.
- **Swap-based ETFs** report the swap and its collateral, not the index. Map
  them to a physical ETF on the same index with `lookthrough.proxies`; the
  rows are labelled as a proxy.
- **Private funds** such as BlackRock Cash Funds stay as a single line.

## TickerInside

`pip install tickerinside`. The equity issuer layer (`source_layer ==
"issuer"`) is the issuer's daily file, a day or two old, keyed by ticker.
Tickers are matched to ISINs through the same fund's N-PORT lines and
OpenFIGI; a ticker that entered the fund after its last N-PORT filing stays
unresolved and appears in `lookthrough_unidentified.csv`. Other layers
(`sec_nport`, `sec_nport_bonds`) are rebuilt from N-PORT, which this package
reads in full, so they are skipped.

**Terms.** Current data is published under CC BY 4.0 and must be credited.
Every row from TickerInside carries its `cite_as` text in `source_ref`;
keep it in anything published from the output. History is under separate
terms and is not used.

## OpenFIGI

`POST https://api.openfigi.com/v3/mapping`. **It does not return ISINs**: a
mapping response has `figi`, `compositeFIGI`, `shareClassFIGI`, `name`,
`ticker`, `exchCode`, `securityType`, `securityType2`, `marketSector` and
`securityDescription`. So ISINs come from N-PORT, and OpenFIGI supplies names
and types and the ticker that links a fund ISIN to its SEC series.

## iShares

BlackRock moved its sites in 2026. The `.ajax` CSV endpoints that
`etf-scraper` used (US `1467271812596`, UK `1506575576011`) now return the
HTML product page, so `etf-scraper` was not used. Two JSON endpoints per
site replace them. The sites read are set in `issuers.ishares_sites`
(`[uk, us]`).

Fund list, UK
: `https://www.ishares.com/varnish-api/blk-product-screener-server/api/v1/product-screener/product-data?country=gb&language=en&siteName=ishares-uk&userType=individual`
  returns all 1,431 UK-site products keyed by portfolio ID, each with its
  `isin`, ticker, asset class and `totalNetAssets`.

Fund list, US
: The same endpoint with `country=us&siteName=ishares-us` returns HTTP 500
  (no configuration for the US site). The US screener page loads
  `https://www.ishares.com/us/product-screener/product-screener-v3.jsn?dcrPath=/templatedata/config/product-screener-v3/data/en/us-ishares/ishares-product-screener-backend-config&siteEntryPassthrough=true`
  instead, which returns `data.tableData` as a column list plus rows: 526
  products, every one with `isin`, `cusip`, `portfolioId` and ticker. The 44
  BlackRock mutual funds in it (`productType` `BLK_MUTUAL_FUND_DATA`) are
  dropped, because their holdings have no identifiers and N-PORT covers
  them; the 482 iShares ETFs are kept. A missing ISIN would be derived from
  the CUSIP.

`ingest` saves both lists as `data/01_raw/issuers/ishares_products.parquet`;
an input ISIN found there is matched to that fund before any other route.

Holdings
: `https://www.blackrock.com/varnish-api/<data_site>/product-data/api/v2/get-product-data?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=<site>&locale=<locale>&userType=individual&component=holdings&portfolioId=<id>`,
  with `uk-retail01-product-data`, `ishares-uk`, `en_GB` for the UK and
  `blk-one01-product-data`, `us-ishares`, `en_US` for the US (the US names
  are in the `apiHost` and `targetSite` config of each US product page).
  It returns the full holdings table column by column: `isin`, `ticker`,
  `issueName`, the weight, `assetClass`, `marketValue`, and for bond funds
  `couponRate`, `maturityDate` and `duration`, with `asOfDate`. US tables add
  `cusip` and `sedol`. Responses are cached per fund per day in
  `data/01_raw/issuers/holdings/`.

The weight column differs by fund. UK funds and US bond and fund-of-funds
tables give `holdingPercent` to five decimals. US equity tables give
`marketWeight` (and `notionalWeight`) to two decimals instead; `marketWeight`
is used, so IVV's lines sum to 100.06%. Where a US line has a CUSIP but no
ISIN, the ISIN is derived from the CUSIP (US and Canadian numbers only) and
`isin_source` in the parsed table says `derived_cusip`.

Each product page also links `latest-holdings.csv`, which still works, but it
has no ISIN column and rounds weights to two decimals. The fund download
(`get-fund-document?component=fundDownloadV2`) is a SpreadsheetML workbook
with the same limits. Neither is used.

What the data shows (2 November 2026):

| Fund | Lines | ISIN-identified weight |
|---|---|---|
| iShares Core S&P 500 UCITS ETF (IE00B5BMR087) | 509 | 99.86% |
| iShares Core MSCI World UCITS ETF (IE00B4L5Y983) | 1,270 | 99.65% |
| iShares Core UK Gilts UCITS ETF (IE00B1FZSB30) | 74 | 100.02% |
| iShares Core Global Aggregate Bond UCITS ETF (IE00B3F81409) | 20,000\* | 92.22% |
| iShares Core S&P 500 ETF (IVV, US4642872000) | 508 | 99.99% |
| iShares Core U.S. Aggregate Bond ETF (AGG, US4642872265) | 13,465 | 102.97% |
| iShares Core 60/40 Balanced Allocation ETF (AOR, US4642898674) | 9 | 99.91% |

\* Exactly 20,000 lines in both the JSON and the CSV, which looks like a cap
on the site's side. The lines still sum to 100.27%, so anything cut is a tail
of very small positions, but very large bond funds may be incomplete.

AGG's ISIN lines add up to more than 100% because its USD cash line is
−2.92%. Of its 35 lines without an ISIN, 25 (0.05%) are commercial mortgage
bonds whose ISIN is derived from the CUSIP. AOR's lines are seven iShares
ETFs and a cash fund, each found in the US fund list and expanded from its
own issuer file.

Lines without an ISIN are cash, futures and FX forwards, and in bond funds
US agency mortgage pools, which are listed by pool number only (about 7.7% of
the global aggregate fund). Swap-based funds such as
IE000OY7BDY9 report one index line of about 99.97% with no ISIN; the summary
flags any input where a single ISIN-less line holds 50% or more.

Issuer tickers are local codes, and cash lines use currency codes such as
`USD`, which is also a US ETF's ticker. So iShares lines are matched to funds
by ISIN only, never by ticker.

**Terms.** This is public data from the issuer's website. BlackRock's site
terms have not been reviewed for automated or bulk use; the client keeps to
one request per fund per day and pauses between requests.

## SPDR and the rest

SPDR's daily Excel files and the top-holdings fallbacks (justETF, FT) are
not built yet.
