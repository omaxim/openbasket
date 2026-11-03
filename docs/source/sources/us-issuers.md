# US issuers: Vanguard, Avantis, First Trust

US-registered funds are already covered by SEC Form N-PORT, which runs two to
five months behind, and by TickerInside for equity ETFs. The issuers' own
files are fresher and carry CUSIPs, and for Vanguard and Avantis ISINs too.
Three US issuers beyond iShares and SPDR are built as plug-ins:

| Adapter | Module | Funds | Holdings updated | Identifiers on holdings |
|---|---|---|---|---|
| `vanguard_us` | `sources/issuers/vanguard_us.py` | 383 share classes (116 ETFs, 267 mutual fund classes) of 215 portfolios | Monthly, month-end, about a month late | ISIN, CUSIP, SEDOL |
| `avantis` | `sources/issuers/avantis.py` | 41 (33 ETFs, 8 mutual funds) | ETFs daily; mutual funds month-end | ISIN, CUSIP, SEDOL |
| `first_trust` | `sources/issuers/first_trust.py` | 334 ETFs on the list | Daily | CUSIP and ticker only |

Status as tested on 3 November 2026. Other US issuers were checked and are not
built; see [Not built](#not-built) for why.

All three are US-domiciled, USD funds with no swap-based products, so
`replication` is never `swap`. Every row carries the adapter's `credit` text
and the URL its holdings came from.

## How a fund ISIN reaches its holdings

1. `products()` downloads the issuer's fund list and gives one row per
   share-class ISIN. The fund ISIN is the US ISIN of the share class CUSIP
   (`US` + CUSIP + check digit) for Vanguard and Avantis, and the ISIN shown on
   the fund's summary page for First Trust.
2. The row's `holdings_url` is the machine URL for that fund (by ticker or
   page slug); `holdings(product, ctx)` fetches and parses it.
3. A response is written to `ctx.cache_dir` (one file per fund per day, for
   example `vanguard_us_VOO_2026-11-03.json`) only after it has parsed, so a
   block page or a truncated download is never cached.

Weights are the issuer's percent of net assets, never rescaled. All three
issuers round to two decimals. Lines whose CUSIP, ISIN or SEDOL fails its
check digit (Vanguard's `CMT001142` cash fund, Avantis's `999USDZ92` dollar
line) get `None` for that identifier. Where a line has only a CUSIP, the
ISIN is left to the pipeline's CUSIP lookup. Each frame also has a
`market_value` column in USD, so the issuer's rounding can be checked.

## Vanguard (US)

Fund list
: `https://investor.vanguard.com/investment-products/list/funddetail/all`,
  the JSON behind the site's product list (4.8 MB, one request). Each entity
  has `profile.ticker`, `profile.cusip`, `longName`, `isETF`,
  `fundCategory.high.name` and `associatedFundIds`, which lists the ETF,
  Investor, Admiral and Institutional classes of one portfolio. The
  `portfolio_id` is the first of the ETF, Admiral, Investor, Institutional
  and Institutional Plus ids, so VOO and VFIAX share `0968`. Funds
  marked `isExternalFund` are dropped.

Holdings
: `https://investor.vanguard.com/irr/funds/profile/<TICKER>-AdditionalFundData`,
  the JSON the profile page's portfolio tab loads. `holdingDetails.asOfDate`
  is the month-end date; the lines are split into `equityHoldings`,
  `fixedIncomeHoldings`, `shortTermReservesHoldings` and
  `derivativeHoldings`. Each line has `isin`, `cusip`, `sedol`, `ticker`,
  `securityType` (`EQ.STOCK`, `FI.CORP`, `MM.CASH`, `DE.IND`, `CT.FOREX` ...),
  `marketValuePercentage` (`"8.39%"`) and `marketValueBaseCurrency`.

An older endpoint, `https://investor.vanguard.com/vmf/api/<TICKER>/portfolio-holding/stock`
(and `/bond`), still answers with the same month-end lines, but in pages of
500 and one asset type per call, so it is not used. The
daily portfolio composition file the ETF pages link
(`https://etfportal.vanguard.com/extrapi/v2/us/standard/`) is the creation
basket, not the fund's holdings, and is not used either.

`replication` is `sampled` for bond index funds (Vanguard samples its bond
indexes) and `physical` for equity index and active funds.

What the data shows (3 November 2026, holdings as of 30 September 2026):

| Fund | Lines | Sum of weights | Weight with an ISIN | Seconds |
|---|---|---|---|---|
| Vanguard S&P 500 ETF (VOO, US9229083632) | 519 | 100.11% | 100.13% | 1.0 |
| Vanguard Total Bond Market ETF (BND, US9219378356) | 16,327 | 83.49% | 82.58% | 1.5 |
| Vanguard Total International Stock ETF (VXUS, US9219097683) | 9,293 | 93.60% | 90.83% | 1.3 |
| Vanguard Target Retirement 2060 Fund (VTTSX, US92202E8396) | 10 | 99.99% | 98.86% | 0.5 |

`products()` took 0.8 seconds: 383 rows, all domiciled in the US; 310
`physical`, 73 `sampled`; 210 equity, 139 bond, 28 balanced and 6 money market.

**Rounding.** The two-decimal percentages lose a lot in funds with many small
lines. Of BND's 16,327 lines, 12,028 show `0.00%` and 1,176 tiny mortgage
pools show no percentage at all (kept as NaN, with their market value), so the
reported weights add up to 83.5% while the market values add up to the whole
fund. VXUS adds up to 93.6% for the same reason. The lost weight shows up as
unresolved; N-PORT, which reports percentages to more decimals, is the better
source for those two funds. The share of weight with an ISIN can exceed the
sum because cash and FX lines without an ISIN are negative.

Other gaps: US agency mortgage lines in bond funds often carry no identifier
at all. A fund of funds (target-date funds, VTTSX) lists other Vanguard funds
by ISIN; its largest line, VSMPX, is itself in the fund list, so the
look-through can expand it.

## Avantis Investors (American Century)

The site is a SvelteKit app: next to each page, `__data.json` returns the
page's data in SvelteKit's "devalue" encoding (a flat array whose objects and
lists hold indices into the array). `avantis.devalue()` decodes it.

Fund list
: `https://www.avantisinvestors.com/avantis-investments/en-us/__data.json?t=perf&type=Mutual+Fund&type=ETF`
  (0.6 MB, one request). `fundList` has the ticker, `fundNumber`, product type,
  share class and asset class; `documents` has, per fund number and share
  class, prospectus links whose `fid=` parameter is the share class CUSIP;
  `urlSlugs` maps a fund number to its page. `portfolio_id` is the fund
  number.

Holdings
: `https://www.avantisinvestors.com/avantis-investments/en-us/<slug>/__data.json`
  (about 1 MB for a 2,000-line fund). `selectedClass.portfolio.etfHoldings`
  lists every line with `name`, `ticker`, `securityType`, `cusip`, `isin`,
  `sedol`, `baseMarketValue`, `weight` (`"5.83%"`), `coupon`, `maturityDate`
  and `country`; the date is `etfHoldingsAsOfDate`. The fund's net assets
  (`assetsInShareClass`) and NAV date are added to the notes for ETFs.

The page itself (`https://www.avantisinvestors.com/avantis-investments/<slug>/`)
is cited as the product page. The data address without `en-us/` answers
with a SvelteKit redirect, which the adapter treats as an error.
A data host the page mentions, `distribution-discovery-avantis-products-production.americancentury.workers.dev`,
is behind a Cloudflare Access sign-in and is not used.

What the data shows (3 November 2026):

| Fund | As of | Lines | Sum of weights | Weight with an ISIN | Seconds |
|---|---|---|---|---|---|
| Avantis U.S. Equity ETF (AVUS, US0250728856) | 2026-11-02 | 1,946 | 99.10% | 99.02% | 2.5 |
| Avantis Core Fixed Income ETF (AVIG, US0250725621) | 2026-11-02 | 897 | 118.90% | 118.55% | 1.8 |
| Avantis International Equity ETF (AVDE, US0250727031) | 2026-11-02 | 3,396 | 98.00% | 97.81% | 3.4 |
| Avantis Moderate Allocation ETF (AVMA, US0250721828) | 2026-11-02 | 18 | 99.99% | 99.91% | 2.1 |
| Avantis U.S. Equity Fund, Institutional (AVUSX, US0250728369) | 2026-09-30 | 1,587 | 99.62% | 98.95% | 2.4 |

`products()` took 2.1 seconds: 41 rows, all US; 35 equity, 5 bond, 1
allocation. Every share class in the list had a CUSIP in its documents.

Gaps:

- **Rounding.** Lines below 0.005% show `0.00%`: AVUS's weights add up to
  99.1% while its market values add up to 100.03% of its net assets.
- **AVIG adds up to 118.9%.** Its TBA mortgage purchases (shown with
  Bloomberg-style generic TBA CUSIPs such as `21H0506C1`) are not netted
  against the cash set aside for them, and its two credit default swaps are
  weighted at 2.14% while their market value is about zero. Weights are kept
  as published.
- **Mutual funds** show month-end holdings only.
- AVMA holds 14 Avantis ETFs, which are found in the same fund list by ISIN
  and expanded.

## First Trust

Fund list
: `https://www.ftportfolios.com/Retail/etf/etflist.aspx` (0.9 MB) lists all
  334 ETFs, including the FT Vest buffer funds, by name and ticker, with no
  identifiers.

Fund ISIN
: The CUSIP and ISIN are only on each fund's summary page,
  `https://www.ftportfolios.com/Retail/Etf/EtfSummary.aspx?Ticker=<T>`
  ("Fund Overview"). The page is about 2.2 MB (0.8 MB compressed), so
  `products()` reads it only until the ISIN appears (about 60% of the way in)
  and then closes the connection. ISINs never change, so they are kept in
  `first_trust_isins.json` in the cache folder (`products(cache_dir=...)`,
  else `$OPENBASKET_ISSUER_CACHE`, else `data/01_raw/issuers` if it exists),
  and later runs look up only funds not yet in that file. The first full run
  costs one request per fund, about 17 minutes at one request a second; a fund
  whose summary page fails is left out of that run and tried again next time.
  `products(limit=n)` caps the lookups per run.

Holdings
: `https://www.ftportfolios.com/Retail/Etf/EtfHoldings.aspx?Ticker=<T>`, an
  HTML page with the full table and "Holdings of the Fund as of 11/2/2026".
  Columns: Security Name, Identifier (ticker, `$USD` for cash, an OCC symbol
  for options), CUSIP, Classification (sector, equity funds only), Shares /
  Quantity, Market Value, Weighting (percent, two decimals). Parsed with the
  standard library's HTML parser.

robots.txt disallows `/Common/` for all crawlers, which covers the CUSIP list
PDF, the fact sheets and the page's "Export to Excel" files, so none of them
is used.

What the data shows (3 November 2026):

| Fund | Lines | Sum of weights | Weight with a CUSIP | Seconds |
|---|---|---|---|---|
| First Trust Dow Jones Internet Index Fund (FDN, US33733E3027) | 42 | 100.01% | 99.90% | 1.5 |
| First Trust Enhanced Short Maturity ETF (FTSM) | 743 | 100.03% | 100.99% | 1.7 |
| First Trust Preferred Securities and Income ETF (FPE) | 264 | 99.98% | 99.46% | 1.5 |
| FT Vest U.S. Equity Buffer ETF - January (FJAN) | 5 | 100.00% | 0.00% | 1.2 |

In development `products(limit=5)` was run, which looked up five summary pages
in 15.7 seconds and returned those five funds (FAAR, FTHI, ESBG, NTRL, FTGC).
A full `products()` run has not been made, to avoid crawling 334 large pages
during development.

Gaps:

- **No ISINs on holdings lines.** Every line carries a CUSIP; the pipeline
  maps CUSIPs to ISINs. Foreign shares held through their local listing may
  have only a ticker.
- **Buffer and target-outcome funds** (FT Vest) hold FLEX options on an ETF
  (FJAN: a deep in-the-money call on SPY at 99.78%, plus puts and a short
  call). Those lines are `derivative (options)` with no identifier, so the
  fund stays unresolved; map such funds to the underlying ETF with
  `lookthrough.proxies` if that approximation is wanted.
- `asset_class` and `replication` are not published on the list and are
  left empty. `asset_class` per line is inferred: the Classification column
  for equity funds, otherwise from the name (coupon or maturity: debt; "ETF":
  fund).
- Preferred shares in FPE without a coupon in their name are labelled equity.

## Terms and credit

None of the three sites publishes terms that cover automated use, and none
was reviewed for it; all three pages are public, need no log-in and are
allowed by each site's robots.txt. Every request goes through
`openbasket.http` (one request a second per host, retries on 429 and 5xx),
each fund is fetched at most once a day, and every row is credited:

- Vanguard: "Holdings data: The Vanguard Group, Inc., investor.vanguard.com"
- Avantis: "Holdings data: Avantis Investors (American Century Investment Services, Inc.), avantisinvestors.com"
- First Trust: "Holdings data: First Trust Portfolios L.P., ftportfolios.com".
  Its pages add that CUSIP identifiers come from CUSIP Global Services
  (FactSet) and may not be used as a substitute for a CUSIP service; they are
  used here only to identify the holdings shown.

## Not built

| Issuer | What was found (3 November 2026) |
|---|---|
| Schwab Asset Management | `https://www.schwabassetmanagement.com/robots.txt` and `/products/schb` return HTTP 403 "Access Denied" from the CDN to openbasket's user agent. Bot protection is not bypassed, so Schwab ETFs stay on N-PORT and TickerInside. |
| Invesco US (QQQ, RSP) | The US product pages and the holdings download `https://www.invesco.com/us/financial-products/etfs/holdings/main/holdings/0?audienceType=Investor&action=download&ticker=QQQ` return HTTP 406 with an empty body, as does `https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/QQQ/holdings/fund?idType=ticker&productType=ETF`, with or without an `Accept: application/json` header. Treated as a block. QQQ files N-PORT. |
| ARK Invest | The daily holdings CSVs live on `assets.ark-funds.com`, whose robots.txt says `User-agent: *` / `Disallow: /`. The fund pages fill their holdings table from script and show a "read and accept the Terms and Conditions" dialog. |
| Dimensional | `https://www.dimensional.com/us-en/funds` serves a "Global Splash Page" (audience and country choice) before any fund content; openbasket does not accept such gates. |
| Global X (US) | Workable but not built yet. `https://assets.globalxetfs.com/funds/holdings/<ticker>_full-holdings_<YYYYMMDD>.csv` gives daily full holdings (QYLD for 2026-11-02 worked; a missing date returns a 404 page) with `% of Net Assets`, `Ticker`, `Name`, `SEDOL`, `Market Value`, but no ISIN or CUSIP, and the fund ISIN is only on each fund page (`/api/` is disallowed in robots.txt). |

## Example

```python
from openbasket.sources.issuers import FetchContext, discover

vanguard = discover()["vanguard_us"]
funds = vanguard.products()
voo = funds.set_index("ticker").loc["VOO"].to_dict() | {"ticker": "VOO"}
h = vanguard.holdings(voo, FetchContext(cache_dir="data/01_raw/issuers/holdings"))
h.as_of                        # '2026-09-30'
len(h.holdings)                # 519
h.holdings["weight_pct"].sum() # 100.11
h.holdings.loc[0, ["isin", "name", "weight_pct"]].tolist()
# ['US67066G1040', 'NVIDIA Corp', 8.39]
```
