# Sources

Every holding openbasket shows comes from one of the sources below, and is
shown with that source's name, link, credit line and as-of date. Each source
has its own page with its endpoints, coverage, terms and known gaps.

```{toctree}
:maxdepth: 1
:glob:

*
```

Status as tested on 2026-11-03. *Redistribution* says whether the source's
terms allow openbasket to republish what it reads (`public`) or only to read
it for the person running openbasket (`local_only`); the public build and the
website use `public` sources only (see [](../publishing.md) and
[](../data-licence.md)). *Enabled by default* is whether a local run reads it
without any configuration.

## Holdings sources

| Source | Coverage | Redistribution | Enabled by default | Freshness |
|---|---|---|---|---|
| SEC Form N-PORT (`nport`) | Full holdings of US-registered funds and ETFs (equity and bond), with ISINs, CUSIPs and LEIs | public (US government data) | yes | monthly positions, published quarterly about 60 days after the period: two to five months old |
| {doc}`SEC Form N-MFP <sec-nmfp>` (`sec_nmfp`) | Full portfolios of the 328 US money market fund series | public (US government data) | yes | monthly, about one month after month end |
| {doc}`CVM Brazil <cvm-brazil>` (`cvm_brazil`) | Every Brazilian fund class that files a CDA: 27,428 fund ISINs, 266 of them listed on B3 | public (ODbL 1.0, see [](../data-licence.md)) | yes | monthly, about one month after month end; confidential positions released up to 90 days later |
| {doc}`CNMV Spain <cnmv-spain>` (`cnmv_spain`) | Every Spanish fund (FI) and SICAV: 3,510 share classes in 2,067 compartments | public (CNMV legal notice) | yes | quarterly, about four months after quarter end |
| {doc}`CMVM Portugal <cmvm-portugal>` (`cmvm_portugal`) | Every Portuguese securities fund (UCITS, PPR, alternative): 210 fund ISINs, EUR 28.6 bn; positions without ISINs, share lines matched by name | public (official regulator publication, see the page) | yes | quarterly, about one month after quarter end |
| TickerInside (`tickerinside`) | Daily issuer holdings of US equity ETFs, keyed by ticker | public (CC BY 4.0) | yes | daily, a day or two old |
| {doc}`iShares <ishares-regions>` (`ishares`, 21 regional sites) | Daily full holdings of iShares ETFs with ISINs, from BlackRock's sites (UK and US sites by default) | local_only (BlackRock site terms) | yes | daily |
| {doc}`Amundi ETF <amundi>` (`amundi`) | Full holdings of Amundi (and former Lyxor and ComStage) ETFs | local_only (Amundi site terms) | yes | daily |
| {doc}`Vanguard <vanguard>` (`vanguard`) | Month-end full holdings of Vanguard funds in Europe, the US, Australia and Canada | local_only (Vanguard site terms) | no: the UK terms forbid repeated automated access; switch it on in `conf/local` | monthly |
| {doc}`Avantis, First Trust <us-issuers>` (`avantis`, `first_trust`) | Holdings of US ETFs and funds from the issuers' sites | local_only (issuer site terms) | yes | daily |
| {doc}`Franklin Templeton <franklin>` (`franklin`, `franklin_ca`) | Daily holdings of UCITS and US ETFs, monthly or quarterly for Canada | local_only (issuer site terms) | yes | daily to quarterly |
| SPDR (`spdr`) | Full holdings of US ETFs, including SPY and DIA (185), and UCITS ETFs (177) | local_only (State Street site terms) | yes | daily |
| {doc}`J.P. Morgan ETFs <jpmorgan>` (`jpmorgan`) | Daily full holdings of Irish UCITS and US ETFs | local_only (issuer site terms) | yes | daily |
| {doc}`Global X ETFs Europe <globalx-vaneck>` (`globalx`) | Daily full holdings of Irish UCITS ETFs; synthetic funds list their substitute basket | local_only (issuer site terms) | yes | daily |
| {doc}`VanEck Europe <globalx-vaneck>` (`vaneck`) | Daily full holdings of UCITS ETFs and ETNs, 25 seconds between requests | local_only (issuer site terms) | yes | daily |
| {doc}`BNP Paribas Easy <bnp-easy>` (`bnp_easy`) | Holdings inventories of LU, IE and FR ETFs and index funds, private-investor segment only | local_only (issuer site terms) | yes | irregular |
| {doc}`Asia-Pacific issuers <apac>` (`ssga_apac`, `nomura`, `kodex`, `ishares_au`, `betashares`, `vanguard_au`, `trahk`) | Full holdings of ETFs domiciled in Australia, Japan, Korea, Hong Kong and Singapore | local_only (issuer site terms) | yes | daily |
| {doc}`US stand-ins <stand-ins>` (status `stand_in`) | For a fund with no holdings the public build may show, the N-PORT holdings of a US fund named after the same index: about 400 European index funds (S&P 500, MSCI World, Nasdaq-100, MSCI Europe, MSCI Japan ...) | public (US government data, labelled as not the fund's own) | yes | the US fund's N-PORT filing: two to five months old |
| {doc}`Stand-ins <exchange-index>` (`index_proxy`) | A physically replicating peer ETF's holdings, for blocked physical funds on the SMI, SLI, S&P 500, Nasdaq-100 and MSCI EMU | local_only (built from issuer files) | yes | daily |
| {doc}`EDINET Japan <edinet-japan>` | Japanese investment trust reports; client only, nothing parsed | public (PDL1.0, with credit) | no (needs a personal API key) | annual and semi-annual reports |

## Reference sources (no holdings)

| Source | Coverage | Redistribution | Enabled by default | Freshness |
|---|---|---|---|---|
| SEC `company_tickers_mf.json` | Fund and ETF share-class tickers with SEC series and class IDs | public (US government data) | yes | daily |
| OpenFIGI | ISIN to name, ticker and security type; ISIN to US ticker when matching a fund | public (names and types) | yes | live API |
| {doc}`ESMA FIRDS <firds>` (`reference` pipeline) | Every instrument traded on an EU or EEA venue: name, CFI, issuer LEI, currency, bond terms | public (ESMA legal notice) | lookups yes; bulk files off (`firds.bulk.enabled`) | daily deltas, weekly full files |
| {doc}`Fund and bond directory <directory>` (`directory` pipeline): ESMA FIRDS, ECB eligible assets, GLEIF | 18,698 EU-traded fund ISINs, about 280,000 bonds, fund managers and umbrellas | public | yes, when the pipeline is run | ESMA weekly, ECB and GLEIF daily |
| {doc}`Fund and bond directory <directory>`: SIX, Xetra, Euronext lists | Swiss, Xetra and Euronext-listed funds and bonds, with issuer and replication (SIX) | local_only (exchange site terms) | yes, when the pipeline is run; never in the public build | daily |
| {doc}`Fund and bond directory <directory>`: FCA FIRDS | London-listed fund share classes | local_only (FCA terms) | no | weekly |

## Not usable

| Source | Coverage | Redistribution | Enabled by default | Freshness |
|---|---|---|---|---|
| {doc}`Xtrackers (DWS) <xtrackers>` | Daily full holdings of Xtrackers UCITS ETFs | not usable: robots.txt disallows the holdings paths | no adapter, and none may read them | daily |
| {doc}`HSBC <hsbc>` | Full holdings of HSBC UCITS ETFs | not usable: site terms forbid automated access and redistribution | no adapter | daily |
| {doc}`Swisscanto (ZKB) <swisscanto>` | Holdings files of Swisscanto ETFs and index funds | not usable: terms forbid systematic access without written permission | no adapter | daily |
| {doc}`UBS <ubs>` | UBS ETFs (371 share classes on SIX) | not usable: legal-terms gate and site terms forbid automated access | no adapter | daily |
| {doc}`justETF, extraETF <top10-fallback>` | Top 10 holdings of UCITS ETFs | not usable: terms forbid automated retrieval and republishing | no adapter | monthly |
| FT, Morningstar | Top holdings | not evaluated | no adapter | |

Regulators checked but not built (India, Australia, Korea, Taiwan) are listed
on {doc}`regulator-filings`.

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
site replace them. This section describes the UK and US sites; the
`ishares` adapter now reads every regional site (`issuers.ishares_sites`),
see [iShares regional sites](ishares-regions.md).

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
ISIN, it goes through the same lookup as SPDR lines (see below): the ISIN
N-PORT reports for that CUSIP (`nport_cusip`), then one derived from the CUSIP
(`derived_cusip`) only if OpenFIGI knows it.

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
bonds with only a CUSIP: 24 are found in N-PORT and one more is a derived
ISIN that OpenFIGI confirms. AOR's lines are seven iShares
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

**Terms.** BlackRock's site terms allow personal, non-commercial use and
forbid reproduction, so iShares is `local_only`: openbasket reads it only
when you run it yourself, and the public build lists iShares funds by name
and ISIN without their holdings (see {doc}`../running-locally` and
[iShares regional sites](ishares-regions.md#terms-and-credit)). The client
keeps to one request per fund per day and pauses between requests.

## SPDR

State Street publishes a daily workbook per ETF. Two regions are used:

Fund list
: `https://www.ssga.com/bin/v1/ssmp/fund/fundfinder?country=us&language=en&role=intermediary&product=etfs&ui=fund-finder`
  (185 US ETFs with a holdings file) and the same with
  `country=uk&language=en_gb`, whose `uk-etfs` list holds the 177 UCITS ETFs.
  The fund's ISIN is in each entry's `keywords` text and the holdings file is
  linked in `documentPdf`. `ingest` adds them to
  `data/01_raw/issuers/issuer_products.parquet` with `issuer = spdr`.

Holdings
: `https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-<ticker>.xlsx`
  and `.../emea/holdings-daily-emea-en-<ticker>-<exchange>.xlsx`. A few
  header lines (name, ticker, as-of date), then a table whose header row
  starts with `Name` (US) or `ISIN` (UCITS). Bond funds add coupon, par value
  and maturity columns. Weights are percent of the fund.

**SPY and DIA** are unit investment trusts and file no N-PORT, so this is the
only free source of their full holdings.

UCITS files carry an `ISIN` column. US files identify a line by `Identifier`
(a CUSIP, an ISIN for some bonds, or a State Street code) and `SEDOL`, so an
ISIN is found, in order, from:

1. the file's own ISIN;
2. the ISIN N-PORT filings report for the CUSIP (`nport_cusip`);
3. the ISIN N-PORT filings report next to the SEDOL (`nport_sedol`), from
   `data/03_primary/sedol_isin_map.parquet`, about 25,400 SEDOLs;
4. the ISIN derived from the CUSIP (`derived_cusip`), **only if OpenFIGI
   knows it**. State Street gives some foreign shares CUSIP-shaped internal
   codes (Samsung's GDR is `494281900`); 345 such ISINs derived for the
   developed-world fund turned out not to exist, so unverified ones are dropped.

What the data shows (2 November 2026):

| Fund | Constituents | Resolved |
|---|---|---|
| SPDR S&P 500 ETF Trust (SPY, US78462F1030) | 503 | 99.90% |
| SPDR Dow Jones Industrial Average ETF Trust (DIA, US78467X1090) | 30 | 99.95% |
| SPDR Portfolio Developed World ex-US (SPDW, US78463X8891) | 2,359 | 99.21% |
| SPDR Portfolio Aggregate Bond (SPAB, US78464A6495) | 8,707 | 99.09% |
| SPDR S&P 500 UCITS ETF (IE00B6YX5C33) | 504 | 100.00% |
| State Street Global High Yield Bond UCITS ETF (LU3357226557) | 2,361 | 93.75% |

What stays unresolved: State Street's own money-market fund in US bond funds
(3.1% of SPAB, internal code only), and new or 144A bonds that UCITS bond
files list with no ISIN (5.4% of the high-yield fund).

**Terms.** State Street's site terms restrict republication, so SPDR is
`local_only`, read only when you run openbasket yourself; files are fetched
once per fund per day.

## Top-holdings fallbacks

justETF and extraETF are not usable under their terms; see
{doc}`top10-fallback`. FT and Morningstar have not been evaluated.
