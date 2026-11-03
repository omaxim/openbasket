# Canada: Vanguard Canada

Adapter `vanguard_ca` (`src/openbasket/sources/issuers/vanguard_ca.py`). It
covers the Canadian-domiciled ETFs and mutual funds of Vanguard Investments
Canada Inc. The other large Canadian ETF issuers were surveyed too. None of
them publishes free full holdings with identifiers, so they are not built
(see [Other Canadian issuers](#other-canadian-issuers)). The iShares Canada
ETFs belong to BlackRock and are handled with the other iShares sites.

## Coverage

Real run on 3 November 2026:

- `products()` returned **54 share classes in 52 portfolios**, in 1.4 s:
  42 ETFs and 12 mutual-fund share classes (Series F and M). All are
  domiciled in Canada (`CA` ISINs) and priced in CAD. By asset class: 31
  equity, 12 bond and 11 balanced (asset-allocation portfolios). Series F
  and Series M of one mutual fund share a `portfolio_id`.
- All 54 are `replication = physical`. They hold securities directly, or
  hold other Vanguard ETFs. Vanguard Canada has no swap-based funds.
- `holdings()` results:

| Fund | Lines | Sum of weights | Weight with a valid SEDOL | Weight with an ISIN | As of | Seconds |
|---|---|---|---|---|---|---|
| Vanguard S&P 500 Index ETF (VFV, CA92205Y1051), look-through | 537 | 100.00% | 99.78% | 0% | 2026-09-30 | 2.0 |
| Vanguard Canadian Aggregate Bond Index ETF (VAB, CA92203E1016) | 1,326 | 100.00% | 93.43% | 0% | 2026-09-30 | 3.0 |
| Vanguard All-Equity ETF Portfolio (VEQT, CA92201C1077), look-through | 14,266 | 100.00% | 99.02% | 0% | 2026-09-30 | 11.6 |
| Vanguard FTSE Canada All Cap Index ETF (VCN, CA92205P1045) | 224 | 100.00% | 99.42% | 0% | 2026-09-30 | 2.8 |
| Vanguard Windsor U.S. Value Fund Series F (CA92210Y1088) | 148 | 100.00% | 93.03% | 0% | 2026-09-30 | 2.6 |

The seconds include the per-host pause of one second between requests.
VEQT took ten pages of 1,500 lines.

## Endpoints

Fund list
: `GET https://www.vanguard.ca/en/product`. The server-rendered page embeds
  its site configuration as JSON (`<script id="serverApp-state">`), and its
  `portIds` string lists every product the Canadian site shows, for example
  `1811,…,9563,…,D021`.

Fund details
: `POST https://www.vanguard.ca/gpx/graphql` with the `FundFinderFunds`
  query the site itself uses. For each `portId` it returns the profile:
  `fundFullName`, `fundCurrency`, `assetClassificationLevel1`,
  `polarisPdtTypeIndicator` (`ETF` or `FUND`), `marketOfDomicile`, the
  `ISIN` (and FundServ code for mutual funds), and each listing's
  `Ticker - Canada` and `SEDOL`.

Holdings
: The same GraphQL endpoint with the site's own `borHoldings` queries:
  `dailyHoldings`, `delayeredHoldings` and `holdings`, each `limit: 1500`
  per page and continued with `lastItemKey`.

The gateway rejects requests without an `x-consumer-id` header ("x-consumer-id
must be provided"). The value `ca0` is the public application id that the
site's front end sends with every request (`consumerId` in
`main.<hash>.js`). It is not a key or a log-in. No cookie or consent is
needed. `robots.txt` allows all paths.

## From a fund ISIN to its holdings

1. `products()` maps the ISIN to its `portId` (column `port_id`; the
   product page URL also contains it, as
   `/en/product/<etf|mf>/<equity|fixed-income|asset-allocation>/<portId>/<slug>`).
2. `holdings()` tries the three layers in the product page's order and uses
   the first that has lines:
   - `dailyHoldings`: daily lines with an `isin`. None of the funds tried
     publishes them today.
   - `delayeredHoldings`: Vanguard's own look-through for funds that hold
     other Vanguard funds. VFV holds the US-domiciled Vanguard S&P 500 ETF
     and VEQT holds four Vanguard ETFs, so for these funds the result is
     the underlying stocks, apportioned by Vanguard, rather than one line
     per fund. Funds that hold securities directly return nothing here.
   - `holdings`: the fund's direct month-end positions.
3. `marketValuePercentage` is used as `weight_pct`. It is already in
   percent of net assets and is not rescaled. Both direct and look-through
   lines sum to 100.00% with five decimals.

A note on each result says which layer it came from and the `portId`.
`IssuerHoldings.url` is the fund's public product page.

## Identifiers

Month-end lines identify a security by `sedol1` and `ticker` only. The
GraphQL schema's month-end type has no ISIN or CUSIP field in the site's
queries. So:

- `sedol` is kept when it passes the SEDOL check digit. Vanguard uses
  `99X…` codes for temporary lines, such as a share or a CVR after a
  corporate action, and these fail the check and are dropped (14 lines,
  0.002%, in VEQT).
- `isin` is set only for daily lines and for a line that is itself a
  Vanguard Canada ETF, matched through the listing SEDOLs in the fund list.
  In the run above every line came from a month-end layer, so no ISINs were
  set.
- Resolving the SEDOLs to ISINs happens downstream through the SEDOL map
  (`sedol_isin_map`, from N-PORT). That map is built from US filings, so it
  will cover US and many global stocks better than Canadian provincial and
  corporate bonds.

`asset_class` is mapped from Vanguard's `securityType` codes: `EQ.*` to
equity (`EQ.ETF` to fund), `FI.*` to debt, `CRNY`/`MM.CASH` to cash,
`CT.FOREX`/`CT.SPOT` to FX derivatives, `DE.*` to futures and
`CT.PORTSWAP` to swaps. Bond names get their coupon and maturity appended,
for example `Canadian Government Bond 2.75% 2031-03-01`. `country` is
Vanguard's `bloombergIsoCountry`.

## Update frequency

Monthly. On 3 November 2026 every fund showed month-end positions as of
30 September 2026, so the lag is one to two months. Responses are cached per
fund per day (`vanguard_ca_<portId>_<date>.json` in `ctx.cache_dir`), and
only after they parse.

## Terms and credit

Credit line: "Holdings data: Vanguard Investments Canada Inc., vanguard.ca".

The [website terms of use](https://www.vanguard.ca/en/website-terms-of-use)
license the site's content for "personal and noncommercial use" only, unless
Vanguard authorises more in writing, and reserve all other rights. No such
authorisation has been obtained. openbasket is
non-commercial, but republishing the lines goes beyond personal use, so ask
Vanguard before relying on this source in public output.

## Known gaps

- **No ISINs** in month-end data, only SEDOLs. Coverage depends on the
  SEDOL map. VAB's lines without a SEDOL (248 lines, 6.2%) are mostly
  Canada Housing Trust, provincial and CPPIB bonds, identified by issuer
  ticker only.
- **Month-end only.** Positions lag by one to two months.
- **Look-through is Vanguard's own.** For fund-of-funds the weights are
  Vanguard's apportionment, as of the same month-end. The direct holdings
  can be read with `fetch_layer(port_id, "holdings")`.
- **Cash and derivatives**: FX forwards (`CT.FOREX`) appear in pairs with
  opposite signs. Equity index futures are listed at their exposure (for
  example 2.46% in the Windsor fund).
- `replication` is `physical` for all products. Vanguard does not publish a
  replication field in these queries, and sampled bond funds cannot be told
  apart from fully replicated ones.

## Example

```python
from openbasket.sources.issuers import FetchContext, vanguard_ca

funds = vanguard_ca.products()
vab = funds[funds["ticker"] == "VAB"].iloc[0].to_dict()
h = vanguard_ca.holdings(vab, FetchContext(cache_dir="data/01_raw/issuers/holdings"))
h.as_of                         # '2026-09-30'
h.holdings["weight_pct"].sum()  # 99.9999
h.holdings.head(1)[["sedol", "name", "weight_pct", "asset_class"]]
#      sedol                                       name  weight_pct asset_class
#    BMHXSS9  Canadian Government Bond 2.75% 2031-03-01       1.802        debt
```

## Other Canadian issuers

Surveyed live on 3 November 2026 with openbasket's own User-Agent and no
cookies:

| Issuer | What is published | Status |
|---|---|---|
| BMO ETFs (`bmogam.com`, `bmo.com/gam`) | n/a | **Not reachable.** Every request from a non-browser client, `robots.txt` included, is dropped (HTTP/2 stream reset with curl, read time-out with `requests`). Getting through would mean imitating a browser to pass bot protection, so this is not a source. |
| Global X Canada (ex-Horizons, `globalx.ca`) | Top 10 holdings on each product page (names and weights, no identifiers); quarterly "Summary of Investment Portfolio" PDFs | **Not built.** The "Download holdings" link is commented out in the page template. `robots.txt` asks for `Crawl-delay: 30`. Several flagship funds, HXT among them, are total-return-swap funds. |
| TD Asset Management (`td.com/ca/en/asset-management`) | Fund list JSON (`/bin/fund-filter-data?productType=ETF&reqType=overview&lang=en`, 58 ETFs with `fundId`); each fund card (`/ca/en/asset-management/funds/solutions/etfs/fundcard?fundId=<id>`) embeds the full holdings list in `data-top-ten-table` | **Not built: no identifiers.** The list is complete (TTP: 319 lines summing to 100.12%, as of 30 September 2026), but it has only a name and a two-decimal weight per line, with no ISIN, CUSIP or SEDOL. It would add rows that could not be matched. |
| iShares Canada (BlackRock) | | Handled with the other iShares sites. |
