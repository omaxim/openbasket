# J.P. Morgan Asset Management ETFs

Adapter `jpmorgan` (`src/openbasket/sources/issuers/jpmorgan.py`). It reads
J.P. Morgan's own fund pages for two fund ranges:

- **UCITS ETFs** from JPMorgan ETFs (Ireland) ICAV, sold across Europe,
  including Switzerland;
- **US ETFs** from the J.P. Morgan Exchange-Traded Fund Trust (JEPI, JPST,
  the BetaBuilders range and others).

Both come from `https://am.jpmorgan.com/FundsMarketingHandler/`. No login,
cookie or consent step is involved, and `robots.txt` does not disallow these
paths. Holdings are updated **daily**.

**Redistribution: local only.** J.P. Morgan's terms of use have not been cleared for republication. So the adapter is registered with
`redistribution="local_only"`: openbasket reads J.P. Morgan holdings only for the
person running it. Under the publishing policy the public build does not
fetch this source, and none of its rows go to the openbasket site, API or
releases. A fund that only this source covers is listed there without
constituents, with a note that its holdings are available when you run
openbasket yourself. See [Adding a source](adding-a-source.md).

## Coverage

Real run on 3 November 2026:

| | Share classes | Portfolios |
|---|---|---|
| Ireland (UCITS) | 195 | 58 |
| United States | 78 | 78 |
| **Total** | **273** | **136** |

UCITS asset classes: 108 equity, 84 bond and 3 balanced share classes. 16
share classes are in CHF (CHF-hedged). US asset classes: 29 U.S. equity, 17
international equity, 2 global equity, 19 taxable and 6 tax-exempt fixed
income, 3 specialty, 1 asset allocation, 1 money market.

**Replication.** J.P. Morgan publishes no replication field. Every fund in
both ranges holds its securities directly (active, research-enhanced or
BetaBuilders index funds); none uses a total-return swap for its exposure,
so every row is set to `physical`. Some funds add derivatives on top of the
physical portfolio: the premium-income funds sell index call options (UCITS)
or hold equity-linked notes (US), and the hedged-equity funds hold option
ladders.

## Endpoints

Fund list
: `https://am.jpmorgan.com/FundsMarketingHandler/fund-explorer?country=<cc>&role=adv&userLoggedIn=false&language=en&fundType=etf`
  returns a JSON list of the share classes registered in one country. Without
  `fundType` it returns HTTP 400. Each record has `identifier` (the
  share-class ISIN on European sites, the CUSIP on the US site), `ticker`,
  `shareclassName`, `assetClass`, `shareclassCurrencyCode`, `domicile` and
  `fundIdentifier`. `fundIdentifier` is shared by the share classes of one
  portfolio, so it is used as `portfolio_id`.

  No single country lists every UCITS share class. On 3 November 2026, `gb`
  listed 175, `de` 159, `nl` 162, `ch` 98, `it`/`lu`/`fr` 156, `es`/`at` 155
  and `se` 158. Together they cover 195. CHF-hedged classes appear only on
  the Swiss site and MXN-hedged ones only on the Dutch site. The adapter reads
  `gb, ch, de, nl, it, lu, fr, es, at, se` and `us` (11 requests, about 10 MB)
  and keeps each share class under the first country that lists it. `hk` and
  `sg` return an empty list.

UCITS holdings
: `https://am.jpmorgan.com/FundsMarketingHandler/excel?type=dailyETFHoldings&cusip=<ISIN>&country=<cc>&role=adv&fundType=N_ETF&locale=en-<CC>&isUnderlyingHolding=false&isProxyHolding=false`
  returns an `.xlsx` workbook (about 40 KB). The fund ISIN goes into the
  `cusip` parameter. **`country` must be a site that lists the share class**.
  For example, `IE000UPAYVL7` (CHF hedged, Swiss site only) returns HTTP 500
  with `country=gb` and the workbook with `country=ch`. This is why each
  product keeps its `site`.

  Layout: five blank rows, then
  `Daily holdings | JPMorgan ETFs (Ireland) ICAV - <fund> | As of Date:02/11/2026`,
  then a header row starting with `Name`:
  `Name, ISIN, Asset class, Country, Currency, Weight, Base market value, Price`.
  The table ends at the first blank row, after which come six disclaimer rows.
  The as-of date is day first, and its separator follows the locale (`02/11/2026`
  on `gb`, `02.11.2026` on `ch`).

US holdings
: `https://am.jpmorgan.com/FundsMarketingHandler/product-data?cusip=<CUSIP>&country=us&role=adv&language=en&userLoggedIn=false`
  is the JSON behind the US product page (0.2 to 1 MB).
  `fundData.dailyHoldingsAll` holds every line with `effectiveDate`.
  Each line has `securityId` (a CUSIP for US securities, a SEDOL for non-US
  shares, an internal code for notes, futures and cash), `securityTicker`,
  `securityDescription`, `securityType`, `netAssetValuePercent`,
  `currencyCode` and `country`. The US holdings workbook (the same `excel`
  URL with `country=us`) has **no identifier column** (ticker and
  description only), so the JSON is used instead.

Product page (the source link shown for each row)
: `https://am.jpmorgan.com/<cc>/<lang>/asset-management/adv/products/<slug of share-class name>-<identifier>`,
  for example `.../gb/en/asset-management/adv/products/jpm-ac-asia-pacific-ex-japan-research-enhanced-index-equity-active-ucits-etf-usd-acc-ie00bmdv7354`.
  `<lang>` is `en` on most sites, but the `de`, `at`, `it`, `fr` and `es`
  sites have no English pages: `/de/en/...` returns HTTP 404 and
  `/de/de/...` returns the page (checked 3 November 2026). So those sites
  link to `/de/de/`, `/at/de/`, `/it/it/`, `/fr/fr/` and `/es/es/`. The
  holdings URL keeps its `en-<CC>` locale, which works on every site.

## How a fund ISIN reaches its holdings

1. `products()` lists the ISIN with its `site` (country) and a fixed
   `holdings_url`. For US funds the ISIN is derived from the fund CUSIP
   (`46641Q332` becomes `US46641Q3323`).
2. `holdings(product, ctx)` fetches that URL through `openbasket.http`
   (one request per second per host, retries on 429/5xx) and parses it.
3. Each response is cached as `jpmorgan_<site>_<ISIN>_<date>.xlsx|json` in
   `ctx.cache_dir`, and only after it parses. For US funds only the
   `dailyHoldingsAll` block is kept.

## Weights and identifiers

- **UCITS:** `Weight` is a **fraction** of net assets (0.029384), so it is
  multiplied by 100 (2.9384%). It is given to six decimals of the fraction,
  four decimals in percent. In every file read, the lines including
  `Cash and Cash Equivalent` add up to exactly 100%, so the cash line appears
  to hold the residual. Weights are passed through, never rescaled.
  Constituents carry their ISIN. Options carry an OCC symbol in the ISIN
  column (`SPXW  261125C07825000`), which fails the check digit and becomes
  `None`.
- **US:** `netAssetValuePercent` is already in percent, **rounded to two
  decimals**, so sums usually run a little off 100% (JEPI 100.33%), and a
  few funds are far off (see Known gaps). Lines carry no
  ISIN. The CUSIP goes to `cusip` and a SEDOL to `sedol` (both are
  check-digit validated), and the shared providers map them to ISINs
  through N-PORT. Tickers are kept for shares and funds only, because bond
  lines carry the issuer's ticker (`T`, `FNMA`). US files also use internal
  pseudo currency codes for collateral and futures margin (`CCC`, `USQ`,
  `EUQ`, `JPQ`). A code ending in `Q` becomes the one ISO 4217 currency with
  the same first two letters (`USQ` to `USD`, `EUQ` to `EUR`); any other
  code that is not ISO 4217 becomes `None`.
- Asset classes are normalised from J.P. Morgan's labels (`Common Stock`,
  `Corp - Global Bond`, `Mtge - MBS 30yr`, `Option - Index`, `TREASURY NOTES`,
  `Equity Linked Notes`, ...) to `equity`, `debt`, `cash`, `money market`,
  `fund`, `derivative (...)` or `other`.

## Real run, 3 November 2026

| Fund | Site | As of | Lines | Sum of weights | With ISIN | With CUSIP/SEDOL only | Seconds |
|---|---|---|---|---|---|---|---|
| JPM Global Research Enhanced Index Equity Active UCITS ETF USD (acc), IE00BF4G6Y48 | gb | 2026-11-02 | 661 | 100.00% | 99.51% | 0 | 1.8 |
| JPM BetaBuilders EUR Govt Bond UCITS ETF EUR (acc), IE00BJK9HD13 | gb | 2026-11-02 | 403 | 100.00% | 99.92% | 0 | 1.0 |
| JPM US Equity Premium Income Active UCITS ETF CHF Hedged (dist), IE000UPAYVL7 | ch | 2026-11-02 | 277 | 100.00% | 96.94% | 0 | 1.1 |
| JPM Global Aggregate Bond Active UCITS ETF CHF Hedged (acc), IE000LKQ2K98 | ch | 2026-11-02 | 454 | 100.00% | 96.30% | 0 | 1.0 |
| JPMorgan Equity Premium Income ETF (JEPI), US46641Q3323 | us | 2026-11-02 | 135 | 100.33% | 0 | 86.82% | 0.2 |
| JPMorgan Ultra-Short Income ETF (JPST), US46641Q8371 | us | 2026-11-02 | 810 | 100.24% | 0 | 99.88% | 1.2 |
| JPMorgan BetaBuilders Japan ETF (BBJP), US46641Q2176 | us | 2026-11-02 | 179 | 99.09% | 0 | 98.90% | 0.8 |

`products()` took 10 to 18 seconds for the 11 country lists. A cached
`holdings()` call takes under 0.1 s.

After the fixes (same day): JPM Global Government Bond Active UCITS ETF SEK
Hedged (acc), IE000PYFQ9I0 (`de` site): 528 lines, 100.00%, 99.71% with an
ISIN, as of 2026-11-02, 1.1 s; its product page
`.../de/de/asset-management/adv/products/...-ie000pyfq9i0` answers HTTP 200.
JPMorgan Managed Futures Plus ETF (JPFP): 679 lines, 80.80%, with the
weight-sum note, and only ISO currency codes left.

What has no identifier: cash (0.08% to 3.7% in UCITS files), futures and
options, and in JEPI the 15 equity-linked notes (`SPX_7` to `SPX_21`, 13.63%
of net assets), which carry the fund's option income and have only an
internal code.

## Known gaps

- **Hedged share classes** report the portfolio's holdings. The currency
  forwards of the share-class hedge are not listed.
- **Futures** in UCITS files are not shown the same way in every fund. Some
  show a small market value (`F/C US 10YR ULTRA FUT DEC26`, 0.024%). Others
  show near-notional weights as large offsetting legs: in IE000PYFQ9I0,
  `F/C ICE 3MTH SONIA FU DEC26` is +9.41% and the SEP26 contract −9.39%.
  Futures can also carry exchange ISINs that pass the check digit
  (`GB00JBVNLX64`), so they count towards weight with an ISIN. Downstream
  code should go by `asset_class` `derivative (futures)`, not by whether a
  line has an ISIN.
- **Some US funds sum far below 100%.** JPMorgan Managed Futures Plus ETF
  (JPFP, US46654Q5264) sums to 80.80% on 2 November 2026: its 37 commodity
  futures lines carry 0.00% of net assets (their market value is 3.24%),
  and J.P. Morgan's own workbook shows the same 80.80%. The adapter passes
  the weights through as published, and `holdings()` adds a note when a US
  fund's weights sum to less than 95% or more than 105%, so the gap shows as
  unresolved with a reason.
- **Equity-linked notes** (US premium-income funds) cannot be looked through.
  `fundHoldingElnData` gives delta and gamma, but no underlying ISIN.
- **US weights** are rounded to two decimals.
- A share class that a country lists but that J.P. Morgan has not yet
  launched may have no holdings file; `holdings()` then raises, and nothing
  is cached.

## Terms and credit

Credit line: "Holdings data: J.P. Morgan Asset Management (am.jpmorgan.com),
daily ETF holdings." The workbooks call themselves an "unofficial report"
for information only, and J.P. Morgan accepts no liability for errors in
it. The site's terms of use have not been reviewed for automated or bulk
use. The adapter fetches each fund at most once a day, at one request per
second. It is registered with `redistribution="local_only"`.

## Example

```python
from openbasket.sources.issuers import FetchContext, discover

jpm = discover()["jpmorgan"]
products = jpm.products()
row = products.set_index("isin").loc["IE000UPAYVL7"].to_dict() | {"isin": "IE000UPAYVL7"}
h = jpm.holdings(row, FetchContext(cache_dir="data/01_raw/issuers/holdings"))
h.as_of            # '2026-11-02'
h.holdings.head()  # isin, cusip, sedol, ticker, name, weight_pct, asset_class, currency, country
```
