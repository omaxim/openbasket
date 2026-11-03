# Franklin Templeton ETFs

Adapters `franklin` (UCITS ETFs from the Irish site plus US ETFs, daily) and
`franklin_ca` (ETF series of Franklin's Canadian funds, quarterly), in
`src/openbasket/sources/issuers/franklin.py`.

## What it covers

Run on 3 November 2026:

| Adapter | Site | Share classes | Domicile | Replication |
|---|---|---|---|---|
| `franklin` | franklintempleton.ie | 59 (47 portfolios) | IE 59 | physical 30, sampled 10, unknown 19 (active funds) |
| `franklin` | franklintempleton.com | 89 | US 89 | unknown 89 |
| `franklin_ca` | franklintempleton.ca | 28 | CA 28 | unknown 28 |

The Irish site lists every Franklin UCITS ETF, including the share classes
sold in Switzerland: the Swiss site (`franklintempleton.ch`) lists 53 share
classes, all of them on the Irish site too, such as the CHF-hedged class of
the Sustainable Euro Green Bond UCITS ETF (IE0001JJF0Q9). Franklin runs no
swap-based ETF; the Irish site labels each index fund "Full Replication"
(physical) or "Optimised" (sampled). Active funds and all US and Canadian
funds carry no replication label, so it is left unknown.

US funds also file N-PORT; this source is fresher (one day old instead of two
to five months) and gives ISINs directly.

## Endpoints

All three sites are the same app over one public GraphQL service, called by
the sites' own fund pages without a login:

Fund list
: `POST https://www.franklintempleton.ie/api/pds/price-and-performance?op=IntlIndentifiers`
  (Franklin's spelling) with body
  `{"operationName": "IntlIndentifiers", "query": "query IntlIndentifiers(...) { PPSS(countrycode, languagecode, productType) { fundid fundname assetclass etftype methodology basecurrcode shareclass { shclname shclcurr identifiers { fundid shclcode ticker isin cusip sedol } } } }", "variables": {"countrycode": "IE", "languagecode": "en_GB", "productType": "etf"}}`.
  The US site takes `US` / `en_US` on `www.franklintempleton.com`, the
  Canadian site `CA` / `en_CA` on `www.franklintempleton.ca`. The query text
  is the `IntlIndentifiers` and `IntlCorePpss` queries from the site's
  fund-listing bundle, trimmed to the fields used.

Holdings
: The same endpoint with `?op=Holdings&fundid=<fundid>` and the site's
  `Holdings` query: `Portfolio(fundid, countrycode, languagecode) { portfolio
  { dailyholdings {...} fullholdings {...} } }`. Each line has `isinsecnbr`,
  `cusipnbr`, `secticker`, `secname`, `quantityshrpar`, `pctofnetassets`,
  `mktvalue`, `assetclasscatg`, `mktcurr` and `asofdate`.

Product pages
: Each site's product sitemap, for example
  `https://www.franklintempleton.ie/binaries/content/assets/global/sitemaps/google/en-ie_product.xml`,
  links the page of each share class as
  `.../products/<fundid>/<shclcode>/<slug>/<ISIN or ticker>`. These pages are
  the `product_page_url` shown as the source link.

`robots.txt` on all three hosts allows these paths (the `.com` file only
disallows `/llm.txt`; the `.ie` file is empty).

## From a fund ISIN to its holdings

1. `products()` reads the fund list, one row per share-class ISIN, with
   `portfolio_id` = Franklin's `fundid`, shared by the share classes of one
   portfolio (the Acc and Dis classes of the FTSE Emerging Markets UCITS ETF
   are both `40163`).
2. `holdings(product, ctx)` posts the `Holdings` query for that `fundid`.
   The IE and US sites return `dailyholdings`; the Canadian site returns only
   `fullholdings`, with several quarters at once, of which the latest
   `asofdatestd` is kept.
3. The as-of date is read from the lines: `asofdate` is `DD/MM/YYYY` on the
   Irish site and `MM/DD/YYYY` on the US and Canadian sites.

Responses are cached per fund per day under `ctx.cache_dir/franklin/`, only
after they parse and only when they have lines.

## Fields

- `weight_pct` is `pctofnetassets`, already in percent, two decimals, as
  reported. Nothing is rescaled.
- `cusipnbr` holds a CUSIP for North American lines and a SEDOL for the
  others. It goes to `cusip` or `sedol` only if its check digit is right.
- `isin` is `isinsecnbr` when its check digit is right.
- `asset_class` maps `assetclasscatg`: COMMON STOCK, PREFERRED STOCK, RIGHT,
  Equities to `equity`; CORPORATE / FOREIGN / GOVERNMENT BOND, MORTGAGE
  RELATED, TREASURY BILL, Bond to `debt`; CASH, CURRENCY, MONEY MARKET to
  `cash`; CURRENCY CONTRACT to `derivative (foreign exchange)`; FUTURE to
  `derivative (futures)`; MUTUAL FUND to `fund`.

## Numbers from a real run

3 November 2026, holdings as of 2 November (IE, US) and 30 September (CA):

| Fund | Lines | Sum of weights | With ISIN | Seconds |
|---|---|---|---|---|
| Franklin FTSE Emerging Markets UCITS ETF Acc (IE0004I037N4) | 1,637 | 100.27% | 100.15% | 0.6 |
| Franklin US Mega Cap 100 UCITS ETF Acc (IE0008M1R3N4) | 105 | 99.97% | 99.92% | 0.7 |
| Franklin Euro IG Corporate UCITS ETF Acc (IE0001PY6688) | 98 | 99.97% | 97.65% | 0.7 |
| Franklin Sustainable Euro Green Bond UCITS ETF CHF Hedged Acc (IE0001JJF0Q9) | 79 | 99.98% | 97.04% | 1.0 |
| Franklin International Aggregate Bond ETF (FLIA, US35473P6117) | 130 | 97.78% | 97.17% | 0.3 |
| Franklin International Core Equity Fund ETF Series (FCRI, CA35360M2004) | 214 | 100.00% | 98.58% | 1.0 |

The fund list takes about 3 seconds per adapter (one list call and one
sitemap per site).

## Known gaps

- **Two decimals.** Weights are rounded to 0.01%, so a broad fund has many
  lines at 0.00 or 0.01 and the lines add up to 99.9–100.3%.
- **Cash and FX.** Lines without an ISIN are cash, "Net Current Assets",
  currency balances, futures and FX forwards. The US bond fund lists both
  legs of each forward (+28.46% and offsetting negatives), which is why its
  lines add up to 97.78%; the derivatives net to −0.17%.
- **Canada is quarterly.** The Canadian site publishes full holdings as of
  each quarter end; on 3 November 2026 the latest was 30 September. Some Canadian lines are deposits with
  no ISIN (0.93% of FCRI).
- **Liquidated funds.** The US list still carries liquidated funds (13 on
  3 November 2026, such as FLHK); their product page moves under
  `/investments/closed/` and their holdings are a final cash line, so
  `products()` drops them.
- **Crypto and gold ETFs.** The US Bitcoin, Ethereum, Solana, XRP, crypto
  index and gold ETFs hold one asset with no ISIN, reported as `other`.
- 14 share classes (new funds and some US bond ETFs) are not in the
  sitemap yet and have no product page.
- Australia: the Australian site lists a single ETF, run by Betashares with
  Western Asset; it is not read.

## Terms and credit

Credit line: "Holdings data: Franklin Templeton (franklintempleton.com), as
published on its website." The data is public, read without login through
the same API the fund pages use. Franklin's site terms have not been reviewed
for automated or bulk use; `openbasket.http` keeps to one request a second per
host and each fund is fetched at most once a day.

## Example

```python
from openbasket.sources.issuers import FetchContext, franklin

funds = franklin.products()
row = funds[funds["isin"] == "IE0004I037N4"].iloc[0].to_dict()
h = franklin.holdings_for(row, FetchContext(cache_dir="data/01_raw/issuers/holdings"))
h.as_of               # '2026-11-02'
len(h.holdings)       # 1637
h.holdings["weight_pct"].sum()   # 100.27
```
