# Vanguard

Full month-end holdings of Vanguard ETFs and mutual funds domiciled in
Ireland, the UK, the US, Australia and Canada. That covers every Vanguard
fund sold in Switzerland, Germany, the rest of Europe, the US, Australia, New
Zealand and Canada. One service behind Vanguard's own websites provides all of
it, with an ISIN, SEDOL and CUSIP on most lines. The adapter is
`openbasket.sources.issuers.vanguard`.

Status as tested on 2026-11-03: **working**. Redistribution class:
**`local_only`** (see [Terms and credit](#terms-and-credit)).

This is the one Vanguard source openbasket lists funds from. The US and
Canadian readers built alongside it (`vanguard_us`, see
[US issuers](us-issuers.md), and `vanguard_ca`, see [Canada](canada.md)) are
not adapters of their own: they are its fallbacks for a US or Canadian fund
whose GPX holdings fail or come back empty, and a note on the result says
when one was used. GPX is preferred because it gives five-decimal weights
(investor.vanguard.com rounds to two, so BND's lines there sum to 83.5%) and
lists a Canadian fund of funds as the Vanguard ETFs it holds, with their
ISINs (VSP on 2026-11-03: Vanguard S&P 500 ETF, 99.77%), which the
look-through then expands; vanguard.ca's own view gives SEDOLs only.

## Coverage

A real `products()` run on 2026-11-03 took 28 s and returned 1,070 active
share classes in 457 portfolios:

| Domicile | ETFs | Mutual funds | Share classes | Portfolios |
|---|---|---|---|---|
| Ireland (UCITS) | 129 | 313 | 442 | 94 |
| United Kingdom (OEICs, unit trusts, LifeStrategy) | 0 | 86 | 86 | 39 |
| United States | 116 | 293 | 409 | 215 |
| Australia | 35 | 44 | 79 | 57 |
| Canada | 42 | 12 | 54 | 52 |
| **Total** | **322** | **748** | **1,070** | **457** |

- **Switzerland.** The Swiss site lists 457 of these share classes on its own
  (377 Irish funds and ETFs plus 80 US ETFs listed on SIX). 30 share classes
  are in CHF, most of them CHF-hedged. Vanguard has no Swiss-domiciled funds.
- **Europe.** The UK, Swiss, German, Austrian, Irish and Dutch registration
  lists together hold every Irish and UK share class. The other European
  countries' lists checked (France, Italy, Spain, the Nordics, Luxembourg,
  Portugal, Liechtenstein, Iceland, Poland, Czech Republic, Greece, Hungary)
  add nothing new.
- **Replication.** `etfReplicationMethodology` is `Physical` for 205 Irish
  share classes (all 129 Irish ETFs and 76 index funds). The service gives
  no value for the others, so their `replication` is left empty. Vanguard
  runs no swap-based funds.
- Closed share classes (`activityStatusCode` `DEACTIVE`) and the one UK fund
  with no ISIN are left out.

## Endpoints

GPX GraphQL
: `POST https://www.vanguard.co.uk/gpx/graphql` with headers
  `content-type: application/json` and `x-consumer-id: uk0`, which is what
  the UK site's own pages send. The same service answers at
  `www.ch.vanguard`, `www.de.vanguard` and `www.vanguard.ca` under
  `/gpx/graphql`. Introspection is turned off. The query shapes below are the
  ones the sites' pages use: their server-rendered Apollo state shows the
  field names. No login, cookie or token is needed.

Fund universe
: `portIdList(country: "United Kingdom") { count portIds { portId } }` lists
  every portId registered in a country. The country is a full English name:
  `"GB"` or `"UK"` returns nothing. Counts on 2026-11-03: United Kingdom 961,
  Switzerland 718, Germany 858, Ireland 867, Australia 269, Canada 128. Then
  `funds(portIds: [...]) { profile { portId fundFullName fundCurrency
  marketOfDomicile polarisPdtTypeIndicator assetClassificationLevel1
  etfReplicationMethodology activityStatusCode identifiers { altIdCode
  altIdValue } listings { ... } relatedShareClassesPolaris { portId } } }`
  gives each share class's ISIN, name, currency, domicile, ETF or fund,
  exchange listings with local tickers, and the other share classes of the
  same portfolio. It is called with up to 400 portIds at a time.

US fund list
: `GET https://investor.vanguard.com/investment-products/list/funddetail/all`
  (JSON, 4.8 MB) lists all 383 US ETFs and mutual funds that Vanguard sells
  to retail investors, each with `fundId`, ticker, CUSIP and
  `associatedFundIds` (the fund's other share classes). `fundId` is the same
  portId GPX uses, so all 383 resolve in GPX. Their institutional share
  classes are added through `associatedFundIds` (26 more), so that the
  Institutional Plus classes held by Target Retirement funds can be matched.
  The page loads a bot-detection script, but the JSON answered a plain
  request; nothing was bypassed.

Holdings
: `borHoldings(portIds: ["<portId>"]) { portId holdings(limit: 1500,
  lastItemKey: $k) { totalHoldings lastItemKey items { issuerName
  securityLongDescription securityShortDescription isin sedol1 cusip ticker
  securityType marketValuePercentage quantity effectiveDate couponRate
  maturityDate } } }`. Pages hold at most 1,500 lines ("The limit can't be
  greater than 1500"). The next page is requested by passing back
  `lastItemKey`, an opaque JSON string, until it comes back null.
  `totalHoldings` is checked against the lines received.

## From a fund ISIN to its holdings

1. `products()` maps each share-class ISIN to its GPX `port_id`. GPX gives
   most ISINs directly. Where it does not (some US share classes), the ISIN
   is derived from the US list's CUSIP. All 383 derived ISINs matched GPX
   where GPX also had one.
2. `holdings(product, ctx)` pages `borHoldings` for that `port_id`.
3. Every share class of a portfolio returns the same lines, including hedged
   classes: VWRA and VWRL both return 4,235 lines, and the CHF- and
   EUR-hedged Global Aggregate Bond ETFs both return 13,803. `portfolio_id`
   is the smallest portId among the share classes linked by
   `relatedShareClassesPolaris` (Europe, Australia, Canada) or
   `associatedFundIds` (US).
4. Responses are cached in `ctx.cache_dir` as
   `vanguard_<portId>_<date>.json`, one file per fund per day. A file is
   written only after its response parses.

A share class that several sites list keeps one row. Sites claim rows in
this order: `us`, `au`, `ca`, `uk`, `ch`, `de`, `ie`, `nl`. So a US ETF
listed in Zurich keeps its US page and ticker, and Irish funds get the UK
page. `products(sites=("ch",))` reads the Swiss list alone (3 s, 457 share
classes). The ticker comes from the listing on the site's home exchange,
preferring the line in the fund's own currency (VWRA, not VWRP).

Product pages are built from the portId:
`https://www.vanguard.co.uk/professional/product/<etf|fund>/<equity|bond>/<portId>/<slug>`,
`https://www.vanguard.com.au/personal/invest-with-us/<etf|fund>?portId=<portId>`,
`https://www.vanguard.ca/en/product/<etf|fund>/<equity|fixed-income>/<portId>/<slug>`
and `https://investor.vanguard.com/investment-products/<etfs|mutual-funds>/profile/<ticker>`.
The European sites find a page by its portId, so the slug only affects how
the link reads. The Australian link is the portId form the Australian site
itself uses for its fund pages; it opens the fund's page (HTTP 200, the
fund's name and figures in the page), though the page title is generic.

## Fields and weights

- `weight_pct` is `marketValuePercentage`, which is already **percent of net
  assets**, given to five decimals. It is kept exactly as reported. Totals
  come within 0.0006 of 100 for every fund tested.
- `isin` and `cusip` are kept only when their check digits pass. US funds
  use internal codes such as `CMT001142` for Vanguard's cash fund; these
  become `None`. `sedol` is `sedol1`.
- `asset_class` comes from `securityType`:

  | `securityType` | `asset_class` |
  |---|---|
  | `EQ.*` | equity |
  | `EQ.ETF`, `MF.MF` | fund |
  | `FI.*`, `MM.TBILL` | debt |
  | `MM.CASH`, `CRNY` | cash |
  | other `MM.*` | money market |
  | `CT.FOREX`, `CT.SPOT` | derivative (foreign exchange) |
  | `CT.PORTSWAP` | derivative (swap) |
  | `DE.IND` | derivative (futures) |
  | `DE.COMM` | derivative (commodity) |

- Extra columns: `issuer_name`, `security_type`, `coupon_pct` and
  `maturity_date` (bonds only), and `quantity`. The service has no country or
  currency per line. `currency` is set only on cash lines, which are named by
  their currency code.
- `as_of` is the `effectiveDate` on the lines.

## Update frequency

**Monthly**, as of month end, with roughly a one-month lag. On 2026-11-03
every fund tested was dated 2026-09-30. The adapter is registered with
`frequency="monthly"`.

## What a real run shows (2026-11-03)

| Fund | Lines | Sum of weights | Weight with an ISIN | As of | Seconds |
|---|---|---|---|---|---|
| FTSE All-World UCITS ETF USD Acc (IE00BK5BQT80, VWRA) | 4,235 | 99.9996% | 99.58% | 2026-09-30 | 2.4 |
| Global Aggregate Bond UCITS ETF CHF Hedged Acc (IE00BG47KF31, VAGX) | 13,803 | 99.9999% | 98.04% | 2026-09-30 | 10.1 |
| LifeStrategy 100% Equity Fund Acc (GB00B41XG308) | 11 | 100.0000% | 99.97% | 2026-09-30 | 0.8 |
| S&P 500 ETF (US9229083632, VOO) | 519 | 100.0000% | 100.02% | 2026-09-30 | 1.0 |
| Total Bond Market ETF (US9219378356, BND) | 16,327 | 99.9994% | 99.10% | 2026-09-30 | 11.3 |
| Australian Shares Index ETF (AU000000VAS1, VAS) | 316 | 99.9999% | 99.41% | 2026-09-30 | 0.7 |
| All-Equity ETF Portfolio (CA92201C1077, VEQT) | 8 | 100.0000% | 99.98% | 2026-09-30 | 1.0 |

Large funds take about one second per 1,500-line page, most of it the
one-second pause between requests to one host.

## Known gaps

- **Month-end only.** Holdings run one to two months behind; there are no
  daily files.
- **Lines without an ISIN** are FX forwards and spot contracts, index futures,
  total return swaps (`CT.PORTSWAP`, for example "TRS:GOLDMAN SACHS GROUP INC
  USD 2028-Aug-31"), cash, and Vanguard's internal cash fund. They are 0.42%
  of VWRA. In the CHF-hedged aggregate bond ETF they are 1.96%: the hedges of
  every share class are listed, each FX forward leg on its own line, positive
  or negative. Every bond line there has an ISIN.
- **Weights over 100%.** VOO's ISIN lines add up to 100.02% because its cash
  line is negative.
- **Fund-of-funds** (LifeStrategy, Target Retirement, VEQT and the other
  Canadian asset-allocation ETFs) list Vanguard sub-funds by ISIN
  (`MF.MF` or `EQ.ETF`). All the sub-funds checked are themselves in
  `products()` and can be looked through, except Vanguard Total Bond Market
  II Index Fund (US92203C1053), which is held only by other Vanguard funds
  and is not on the US list. It stays a single line here; N-PORT covers it.
  `borTotalHoldings` also reports a `totalDelayeredHoldings` count, but the
  adapter keeps the holdings as reported and leaves look-through to the
  pipeline.
- **No country or currency** on lines other than cash.
- **No replication flag** outside Europe. Vanguard funds are physically
  replicated, but the service does not say so for US, Australian or Canadian
  funds, so those rows say nothing.
- **Product links** are built from patterns (see above), not read from the
  site.

Checked and not used:

- `https://investor.vanguard.com/vmf/api/<ticker>/portfolio-holding/stock.json`
  (US) gives the same holdings with ISINs, but weights only to two decimals
  (`percentWeight` "8.38").
- `https://www.vanguard.com.au/personal/api/data/products/holdings/<portId>`
  (Australia) gives the same lines and weights with a `countryCode`, but no
  ISIN.
- The Canadian site uses the GPX service itself.

## Terms and credit

`robots.txt` on vanguard.co.uk, ch.vanguard, de.vanguard, vanguard.ca and
investor.vanguard.com allows every path used here for all user agents.

Vanguard's website terms are restrictive. The UK/Europe terms (read
2026-11-03 at `https://www.vanguard.co.uk/professional/terms-and-conditions`,
covering Vanguard Investments Switzerland GmbH too) grant a licence to view
and download content "solely for your personal, informational and
non-commercial use or as expressly authorised by Vanguard in writing". Unless
Vanguard authorises it, they also forbid copying, publishing or
redistributing data from the site. The Canadian terms
(`https://www.vanguard.ca/en/website-terms-of-use`) limit content to
personal, non-commercial use in the same way. The US and Australian terms
were not read.

The UK terms also forbid "using devices or software that provide repeated
automated access to this website, other than those made generally available
by Vanguard". The adapter is a script that reads the GPX service the sites'
own pages call, throttled to about one request a second and one fetch per
fund per day, for the person running it.

**Redistribution class: `local_only`.** The adapter runs when you run
openbasket yourself (it is on by default; `issuers.disabled: [vanguard]`
switches it off) and your local site and API show its rows. The public build
never fetches it and publishes nothing from it. A fund only Vanguard covers
appears in the public index with status `local_only`, no constituents, and a
note that its holdings are available from Vanguard when you run openbasket
yourself.

Every holdings request goes to the UK site's GPX endpoint with the header the
UK site sends (`x-consumer-id: uk0`), US, Australian and Canadian funds
included; the US fund list comes from investor.vanguard.com.

Registered credit line: "Holdings: Vanguard (The Vanguard Group, Inc. and its
regional entities), month-end portfolio data from Vanguard's GPX fund-data
service, reached through vanguard.co.uk; the US fund list comes from
investor.vanguard.com. © Vanguard."

## Example

```python
from openbasket.sources.issuers import FetchContext, vanguard

products = vanguard.products()                       # about 30 s, 1,070 rows
vwra = products.set_index("isin").loc["IE00BK5BQT80"]
h = vanguard.holdings(vwra.to_dict() | {"isin": "IE00BK5BQT80"},
                      FetchContext(cache_dir="data/01_raw/issuers/holdings"))
h.as_of                       # '2026-09-30'
len(h.holdings)               # 4235
h.holdings["weight_pct"].sum()  # 99.9996
h.url                         # the fund's page on vanguard.co.uk
```
