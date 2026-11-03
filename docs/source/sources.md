# Sources

Status as tested on 2026-11-03.

| Source | Status | Used for |
|---|---|---|
| SEC Form N-PORT | Working | Full holdings of US-registered funds and ETFs, equity and bond, with ISINs, CUSIPs and LEIs. |
| SEC `company_tickers_mf.json` | Working | Fund and ETF ticker to series ID. |
| TickerInside | Working | Daily issuer holdings for US equity ETFs, keyed by ticker. |
| OpenFIGI | Working | ISIN to name, ticker and security type; ISIN to US ticker when matching a fund. |
| iShares holdings files | Broken | The `.ajax` CSV endpoints (US `1467271812596`, UK `1506575576011`) now return the HTML product page. |
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
- **Non-US funds** (UCITS, including IE- and LU-ISIN ETFs) are not in N-PORT.
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

## iShares, SPDR and the rest

The `etf-scraper` package relied on the iShares `.ajax` endpoints, which now
return HTML, so it was not used. Issuer files are the next source to add;
until then UCITS funds can only be covered through a labelled proxy.
