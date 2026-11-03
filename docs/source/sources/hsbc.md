# HSBC Asset Management ETFs (not built)

**Status: not built.** HSBC publishes full holdings files for its UCITS ETFs
with no login, but the website terms forbid automated access and
redistribution without written permission. This page records what was found
on 3 November 2026, so the source can be built quickly if HSBC gives
permission.

## Terms

The UK site's terms and conditions
(`https://www.assetmanagement.hsbc.co.uk/en/individual-investor/terms-and-conditions`)
say that, without HSBC's prior written permission, no one may:

- "spider, harvest or scrape the Content", or read or extract it "by machine
  based or automated means";
- "create any data base ... by systematically downloading and storing all or
  any of the Content";
- redistribute any of the Content, which the terms say includes using it in
  "any syndication, content aggregation, archive or similar service".

openbasket is a public aggregator that would fetch these files on a schedule
and republish their lines, which is what these clauses forbid. The German
site's terms
(`https://www.assetmanagement.hsbc.de/de/individual-investors/terms-and-conditions`)
also require HSBC's prior consent to reproduce information, content or data.
`robots.txt` on both hosts disallows only one PDF and sets `Crawl-delay: 2`.

**What is needed:** written permission from HSBC Asset Management to fetch
the fund list and holdings files automatically and to republish the holdings
with credit. Until then, HSBC ETFs are not covered. Their lines go through
the shared fallbacks, such as a physical proxy on the same index
(`lookthrough.proxies`), or stay unresolved.

## What exists

All of this was seen with anonymous requests: no login, cookie or consent was
needed. About 40 requests were made while evaluating the source, at most one
every two seconds.

Fund list
: `POST https://www.assetmanagement.hsbc.co.uk/api/v1/nav/funds`. It needs a
  bearer token from `POST /api/v1/token/issue`, sent with the headers
  `X-Country: GB`, `X-Language: EN` and `X-Component: <component id>`. The
  token is a JWT that lasts 20 minutes and works only for that one component.
  The component id and the request body's `pageInformation` are in the
  `window.HSBC.dpas` script literal on
  `https://www.assetmanagement.hsbc.co.uk/en/individual-investor/funds`. The
  literal is JavaScript, not JSON, because it has trailing commas. Pages
  hold at most 100 funds. The UK list has 186 funds and 823 share classes.
  71 of the funds are ETFs (56 in HSBC ETFs plc and 15 in HSBC Global Funds
  ICAV), with 220 share-class ISINs. The German site
  (`https://www.assetmanagement.hsbc.de/de/individual-investors/funds`, the
  same API) adds 9 more, for 229 ETF share-class ISINs, all Irish. The list
  has no asset class, replication method or ticker.

Fund details
: `POST /api/v1/detail/list` with a `pageInformation.shareId` of the ISIN.
  Its "Fund information" list (id `86faf988-e2eb-4aba-8874-f41605626665`)
  gives the asset class, domicile and umbrella. For HSBC ETFs plc funds it
  also gives a "Replication method" such as `Physical- Full`; the ICAV funds
  do not give one. `www.assetmanagement.hsbc.ch` returned 404, so there is
  no Swiss site. CHF-hedged share classes, such as IE000WMIYI33, are on the
  UK list.

Holdings
: `GET https://www.assetmanagement.hsbc.co.uk/api/v1/download/document/<isin in lower case>/gb/en/holdings`
  (and `.../<isin>/de/de/holdings` on the German host). It needs no token.
  A fund's page links this file only when there is one: non-ETF funds
  (UK OEICs, HSBC GIF SICAVs, ICAV index funds) have no holdings file, and
  some ETF share classes return 404 while another share class of the same
  fund has the file. For example, IE0007SZHO07 returns 404, but
  IE00BF5DZ143, another class of the Global Aggregate Bond fund, has a file.
  There are two formats:

  - **HSBC ETFs plc**: a BIFF8 `.xls` file (it needs `xlrd`) with a
    `Report` sheet. The sheet starts with `Name`, `Date` (an Excel date
    cell) and `Fund Size` rows, then the columns `ISIN`, `CUSIP`,
    `SecurityName`, `NumberOfShare`, `MarketValue`, `Country`,
    `LocalCurrencyCode` and `Weighting`. `Weighting` is already in percent,
    to five decimals.
  - **HSBC Global Funds ICAV**: an `.xlsx` file with the columns
    `PortfolioDate`, `FundName`, `PortfolioBaseCurrency`,
    `SecurityNameTPAProvided`, `NumberOfShares`, `MarketValue`, `Coupon` and
    `MaturityDate`. It has **no ISIN and no weight**, and every value is
    text. It covers the whole fund across all its share classes, including
    each class's cash and accrual control lines. A weight could only be
    worked out from the market values, so it would not be as reported.

What the files showed (3 November 2026):

| Fund | Format | As of | Lines | Sum of weights | Weight with an ISIN |
|---|---|---|---|---|---|
| HSBC Developed World Screened Equity (IE00BKY59K37) | xls | 2026-10-23 | 740 | 99.99994% | 99.93% |
| HSBC S&P 500, CHF hedged class (IE000WMIYI33) | xls | 2026-10-23 | 530 | 100.00005% | 99.67% |
| HSBC Hang Seng Tech (IE00BMWXKN31) | xls | 2026-10-23 | 45 | 99.99998% | 99.57% |
| HSBC Bloomberg Global ESG Aggregate 1-3 Year Bond (IE000XGNMWE1) | xls | 2026-10-29 | 466 | 99.99995% | 95.81% |
| HSBC Global Funds ICAV Euro Corporate Bond (IE0000KA1ZX3) | xlsx | 2026-10-30 | 1,331 | no weights | none |
| HSBC Global Funds ICAV US Treasury Bond (IE000D2UDLG6) | xlsx | 2026-10-30 | 255 | no weights | none |

The HSBC ETFs plc files are dated a few days to a week before they are
published, so updates are about weekly. Lines without an ISIN are cash,
share-class control and accrual lines, futures and FX forwards. In the
bond fund they also include new bonds that do not yet have an ISIN (at
least 3% of the fund). The HSBC ETFs plc funds checked (IE00BKY59K37 and
IE00B42TW061) report `Physical- Full` replication, and the ICAV files list
physical bonds.
