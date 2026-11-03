# SEC Form N-MFP (US money market funds)

US money market funds file **Form N-MFP** (since 2024 the N-MFP3 version)
every month instead of N-PORT, so the N-PORT source has no holdings for
them. The SEC's Division of Economic and Risk Analysis (DERA) republishes
every filing as a monthly ZIP of tab-delimited tables. This source reads
those ZIPs: the full portfolio of every US money market fund (Fidelity,
Vanguard, Schwab, BlackRock, JPMorgan, Goldman Sachs, State Street, BNY
Dreyfus, Morgan Stanley, Federated Hermes and others), one month after
month end.

Module: `openbasket.sources.issuers.sec_nmfp`, adapter name `sec_nmfp`,
frequency `monthly`. Redistribution class `public` (US federal government
data, see Terms). Enabled by default.

Status as tested on 2026-11-03: working.

## Coverage

From a run on 3 November 2026 against the data set
`20260909-20261007_nmfp.zip` (filings accepted 9 September to 7 October
2026, i.e. the 30 September 2026 month-end reports):

| | |
|---|---|
| Fund series with a filing | 328 (337 filings: 328 N-MFP3, 9 amendments) |
| Net assets reported | USD 8.98 trn (USD 8.38 trn without the 32 feeder funds, which hold a master fund also counted) |
| Fund categories | 244 Government, 43 Prime, 25 Other Tax Exempt, 16 Single State |
| Share classes | 1,082, of which 886 have a ticker in `company_tickers_mf.json` |
| Position lines | 44,588 in the latest filing of each series |
| Weight with an ISIN | 60.4% of assets (91.8% outside repurchase agreements) |
| Weight with an ISIN or a CUSIP | 64.8% of assets (98.5% outside repurchase agreements) |
| Weight in repurchase agreements | 34.2% of assets |
| Sum of a fund's lines | median 100.20%, 10th to 90th percentile 97.6% to 103.1% |

Share-class ISINs found by `products()`: see "Fund list" below.

## Endpoints

Index
: `https://www.sec.gov/data-research/sec-markets-data/dera-form-n-mfp-data-sets`
  (HTML). It links one ZIP per monthly filing window,
  `https://www.sec.gov/files/dera/data/form-n-mfp-data-sets/{YYYYMMDD}-{YYYYMMDD}_nmfp.zip`,
  plus the quarterly files of 2010 to 2023 (`2011q1_nmfp.zip`), which are
  ignored. The newest window is used; a series missing from it is looked up
  in the one before.

Monthly ZIP
: About 11 MB zipped, 75 MB unzipped, 21 TSV tables plus
  `NMFP_readme.htm` and `NMFP_metadata.json` (W3C tabular metadata). The
  tables used:

  - `NMFP_SUBMISSION`: one row per filing with `ACCESSION_NUMBER`,
    `SUBMISSIONTYPE` (`N-MFP3`, `N-MFP3/A`), `REPORTDATE`, `FILING_DATE`,
    `CIK`, `SERIESID`, `NAMEOFSERIES`, `LEIOFSERIES`.
  - `NMFP_SERIESLEVELINFO`: `NETASSETOFSERIES`, `MONEYMARKETFUNDCATEGORY`,
    `FEEDERFUNDFLAG`, `MASTERFUNDFLAG`.
  - `NMFP_CLASSLEVELINFO`: `CLASSESID`, `CLASS_NAME`, `NETASSETSOFCLASS`.
  - `NMFP_SCHPORTFOLIOSECURITIES`: one row per position with
    `NAMEOFISSUER`, `TITLEOFISSUER`, `CUSIP_NUMBER`, `ISIN`, `LEI`,
    `INVESTMENTCATEGORY`, `FINALLEGALINVESTMENTMATURITYDA`,
    `INCLUDINGVALUEOFANYSPONSORSUPP` (USD value) and
    `PERCENTAGEOFMONEYMARKETFUNDNET`.
  - `NMFP_COLLATERALISSUERS`: the collateral of each repurchase agreement,
    linked to the repo line by `SECURITY_ID`.

Share-class tickers
: `https://www.sec.gov/files/company_tickers_mf.json` (class ID to ticker).

Share-class ISINs
: `https://api.gleif.org/api/v1/lei-records/{LEI}/isins`, one request per
  series LEI.

All sec.gov requests send the `SEC_USER_AGENT` from `.env` (a generic
User-Agent gets HTTP 403) and go through `openbasket.http`, one request a
second per host. GLEIF requests are spaced 1.1 seconds apart (GLEIF allows 60
a minute). `robots.txt` on sec.gov does not disallow `/files/` or
`/data-research/`.

## How a fund reaches its holdings

N-MFP identifies a fund by its SEC **series ID** (`S000007051`) and a share
class by its **class ID** (`C000019230`). It reports no share-class CUSIP or
ISIN. So:

1. `holdings(product, ctx)` needs only `product["portfolio_id"]`, the series
   ID. A fund ISIN reaches it through the series: an ISIN in `products()`
   carries its series as `portfolio_id`; otherwise the ISIN's US ticker
   (OpenFIGI) gives the class and series in `company_tickers_mf.json`, which
   is how the N-PORT route already works.
2. The newest data set is downloaded once and cached as
   `sec_nmfp_<window>.zip` in `ctx.cache_dir`, one file per month (every fund
   of the month is in it), and only after its tables parse.
3. For the series, the filing with the latest `REPORTDATE` is used, then the
   latest `FILING_DATE`; an amendment (`N-MFP3/A`) for the same report date
   replaces the original.
4. Its `NMFP_SCHPORTFOLIOSECURITIES` rows become the holdings. The as-of
   date is `REPORTDATE`; the source URL is the ZIP; the notes give the
   filing's EDGAR index page, e.g.
   `https://www.sec.gov/Archives/edgar/data/917286/000003540226006359/0000035402-26-006359-index.htm`.

## Fields

- `weight_pct` = `PERCENTAGEOFMONEYMARKETFUNDNET` × 100. The file gives a
  **fraction** of net assets to four decimals (the form asks for the nearest
  hundredth of a percent), so `.0002` is 0.02%. Weights are not rescaled; a
  fund's lines can sum above 100% (borrowings, unsettled purchases) or below
  (cash and receivables are not positions).
- `isin`: the reported `ISIN` when its check digit is valid. Values such as
  `0` or `N/A` are dropped. When a reported US or Canadian ISIN contradicts
  the reported CUSIP (some municipal lines carry an issuer-level ISIN), the
  ISIN is rebuilt from the CUSIP, and the notes count how many.
- `cusip`: the reported `CUSIP_NUMBER` when valid; the pipeline maps it to an
  ISIN as for other sources. Repurchase agreements often carry a trade code
  in this field (`ARP1…`), which is not a security, so it is kept only in
  `reported_cusip`.
- `asset_class`: `repurchase agreement` for the three repo categories,
  `fund` for `Investment Company`, `cash` for non-negotiable time deposits,
  `other` for `Other Instrument`, and `debt` for everything else (Treasury
  and agency debt, commercial paper, certificates of deposit, variable rate
  demand notes, tender option bonds, municipal and asset-backed securities).
- `currency` is `USD`: Rule 2a-7 funds may only hold US dollar securities,
  and N-MFP has no currency field. `country` is not reported.
- Extra columns: `title`, `lei`, `investment_category`, `final_maturity`,
  `value_usd`, `security_id`, `reported_cusip` and `held_series_id`, the
  money market series a line invests in (matched by LEI).

## Fund list

`products()` returns one row per share-class ISIN that can be tied to a
series through its LEI:

- ISINs that GLEIF's ISIN-to-LEI relationship data (CC0) links to the series
  LEI;
- ISINs that other money market funds report for positions whose issuer LEI
  is the series LEI (feeders holding their master, funds holding a
  government cash fund).

An ISIN tied to two series is dropped. Every row has `portfolio_id` = series
ID, `domicile` `US`, `currency` `USD`, `asset_class` `Money Market
(<category>)`, `replication` `physical` (money market funds hold the
securities they report), `product_page_url` = the latest filing's EDGAR page,
and extra columns `isin_via`, `registrant`, `series_lei`, `net_assets`,
`report_date` and `feeder`. `ticker` is set only when the series has a
single share class and it has a ticker, because nothing says which class an
ISIN belongs to (3 of the 78 series with one ticker have other, untickered
classes).

The registered adapter (what `ingest` calls) keeps the monthly ZIP and the
GLEIF answers in `data/01_raw/sec_nmfp` (`$OPENBASKET_SEC_NMFP_DIR`). GLEIF
answers are stored per monthly data set (`sec_nmfp_gleif_<window>.json`), so
only the first run of a month makes the 328 GLEIF requests.

`classes(dataset, fund_tickers())` lists all share classes with their class
IDs, net assets and tickers, for matching by ticker.

Run of 3 November 2026 (360 seconds, almost all of it the 328 GLEIF
requests; `products(gleif=False)` takes a few seconds): **115 share-class
ISINs for 49 of the 328 series**, which hold 39.1% of reported net assets.
All are domiciled in the US and `physical` (there are no swap-based money
market funds). By category: 93 Government, 14 Prime, 5 Single State and 3
Other Tax Exempt. By evidence: 95 from GLEIF, 18 from other funds'
positions, 2 from both. Fidelity Government Money Market Fund (USD 466 bn),
for example, gets one ISIN, `US31617H1023`, from GLEIF; Vanguard Federal
Money Market Fund gets none and is reached by its ticker.

## Example

```python
import openbasket.settings  # loads SEC_USER_AGENT from .env
from openbasket.sources.issuers import FetchContext, sec_nmfp

h = sec_nmfp.holdings({"portfolio_id": "S000007051"}, FetchContext(cache_dir="data/01_raw/issuers/holdings"))
h.as_of            # '2026-09-30'
len(h.holdings)    # 704
h.holdings["weight_pct"].sum()  # 102.31
sec_nmfp.repo_collateral({"portfolio_id": "S000007051"})  # collateral behind the 115 repo lines
```

Real funds, 3 November 2026 (data set 20260909-20261007, report date
30 September 2026; seconds per call with the month's ZIP cached, the first
call adds about 2 seconds to download it):

| Fund | Series | Lines | Sum of weights | Weight with ISIN | With ISIN or CUSIP | Seconds |
|---|---|---|---|---|---|---|
| Fidelity Government Money Market Fund (government, USD 466 bn) | S000007051 | 704 | 102.31% | 68.1% | 68.1% | 0.6 |
| Vanguard Federal Money Market Fund (government) | S000004462 | 352 | 103.97% | 75.6% | 75.6% | 0.1 |
| Schwab Prime Advantage Money Fund (prime) | S000004508 | 773 | 97.72% | 43.5% | 54.7% | 0.1 |
| Schwab Municipal Money Fund (tax exempt) | S000004511 | 1,392 | 98.90% | 100.0% | 100.0% | 0.1 |
| Schwab Government Money Market ETF (ETF share class) | S000092777 | 221 | 99.85% | 34.8% | 34.8% | 0.1 |
| State Street Federal Treasury Money Market Fund (feeder) | S000086381 | 1 | 100.00% | 0% | 0% | 0.1 |

The weight without an identifier is almost all repurchase agreements
(31.3% of Fidelity's fund, 65.1% of the Schwab ETF). The prime fund's
CUSIP-only lines are commercial paper and certificates of deposit.

## Known gaps

- **Share-class ISINs.** N-MFP has none, so `products()` lists only the
  ISINs GLEIF or other filings tie to a series. Most share classes are
  reached through their ticker instead (886 of 1,082 classes have one).
- **Repurchase agreements** (34% of assets) are one line per counterparty
  and trade, with no security identifier. Their collateral (Treasuries,
  agency mortgage pools, and for some prime funds corporate bonds and
  equities) is in `NMFP_COLLATERALISSUERS` and returned by
  `repo_collateral()`; it is not part of the look-through because it is
  security for a loan, not a holding, and is worth more than the repo
  (typically 102%).
- **Feeder funds** report a single line, the master fund. `held_series_id`
  names the master series and the notes say to read it; look-through of the
  master is not done here.
- **Rounding.** Weights are to the nearest hundredth of a percent, so small
  positions show as 0.00% (16 lines in Fidelity's government fund, 158 in
  Schwab's municipal fund). `value_usd` keeps the exact value.
- **Lag.** A month's filings are due by the fifth business day of the next
  month and the data set appears a few days after that, so holdings are one
  to two months old. Fresher per-filing XML (form N-MFP3) is on EDGAR but
  not read here.
- **Data quality.** DERA publishes filings as submitted, with the warning
  that they may contain inconsistencies; some funds report placeholder
  ISINs such as `0`, which are dropped.

## Terms and credit

Credit: *U.S. Securities and Exchange Commission, Form N-MFP data sets
(Division of Economic and Risk Analysis)*.

This is US federal government data, not subject to copyright in the United
States. The SEC's website dissemination notice says information on sec.gov
is public and may be copied or redistributed without permission, and asks
for citation to the SEC as the source. The readme notes the data sets are
filings as submitted and not a substitute for the filings themselves.
Automated access must declare a User-Agent with a contact and stay under ten
requests a second. Share ISINs from GLEIF are published under CC0.

## Freshness

Monthly. The data set for a month-end appears in the following month's
filing window: on 3 November 2026 the newest was the 30 September 2026
report (filed by 7 October). A live check that day read JPMorgan U.S.
Government Money Market Fund (S000003909): 282 lines summing to 97.71%, as of
2026-09-30, in 6 s including the download.
