# iShares regional sites

Adapter: `ishares` (`openbasket.sources.issuers.ishares`), built on
`openbasket.sources.ishares`. Status as tested on 3 November 2026.
Redistribution class: **`local_only`** (see [Terms and credit](#terms-and-credit)).

BlackRock runs about twenty regional iShares and BlackRock websites. All of
them sit on the same two services: a product screener (the fund list) and
the `get-product-data` API (one fund's holdings). The adapter reads every
site, keeps one row per share-class ISIN, and serves full holdings with
constituent ISINs for **1,608 share classes** in the 3 November run (1,099
portfolios). Australia and Canada were capped at six funds each in that run
(see below); without the cap they add about 120 and 176 more.

## Coverage

Share classes with full holdings after merging the sites, by domicile:

| Domicile | Share classes | Notes |
|---|---|---|
| Ireland | 950 | iShares UCITS ETFs (UK, Swiss, EU, Nordic and LatAm sites) |
| United States | 483 | US-listed iShares ETFs |
| Germany | 68 | iShares (DE) ETFs; 26 are only on the German site |
| Japan | 56 | TSE-listed iShares ETFs |
| Switzerland | 12 | the 12 iShares ETFs (CH), e.g. SMI, SPI, CHF bonds |
| Hong Kong | 10 | HKEX-listed iShares ETFs |
| Canada | 8 | capped run; 176 Canadian iShares ETFs on the Canadian site |
| Brazil | 7 | B3-listed iShares fundos de índice (BOVA11, SMAL11, ...) |
| Australia | 6 | capped run; 121 funds in the Australian sitemap |
| Singapore | 4 | SGX-listed iShares ETFs |
| Mexico | 3 | iShares NAFTRAC, S&P 500 Peso Hedged TRAC, ESG MSCI Mexico |
| Colombia | 1 | iShares MSCI COLCAP |

Per site, as fetched (rows are share classes with a valid ISIN; "full" are
ETFs with a full holdings table, "top 10" are mutual and index funds):

| Site | Fund list | Rows | Full | Top 10 | Kept after merge |
|---|---|---|---|---|---|
| `uk` | varnish, ishares-uk | 1,431 | 923 | 507 | 923 |
| `ch` | varnish, ishares-ch | 1,019 | 556 | 462 | 57 |
| `us` | v3 | 482 | 482 | (dropped) | 482 |
| `de` | varnish, de-ishares-v2 | 1,322 | 865 | 456 | 26 |
| `nl` | varnish, ishares-nl | 1,222 | 865 | 357 | 19 |
| `it` | varnish, ishares-it | 788 | 779 | 8 | 0 |
| `se`, `dk`, `no`, `fi` | varnish, one-se ... one-fi | about 4,100 each | about 780 each | about 3,330 each | 0 |
| `fr` | legacy `.ajax` JSON | 4,554 | 837 | 3,717 | 4 |
| `es` | v3 | 4,590 | 834 | 3,756 | 1 |
| `latam` | varnish, latam (Americas offshore) | 6,455 | 1,499 | 4,956 | 1 |
| `hk` | varnish, hk-one | 10 | 10 | 0 | 10 |
| `sg` | v3 | 1,549 | 4 | 1,545 | 4 |
| `jp` | v3 | 56 | 56 | 0 | 56 |
| `au` | sitemap + key facts | 121 IDs | (6 in the capped run) | | 6 |
| `ca` | v3 + key facts | 176 | (6 in the capped run) | | 6 |
| `mx` | v3 | 803 | 553 | 250 | 5 |
| `br` | v3 | 199 | 186 | 13 | 7 |
| `co` | v3 | 42 | 42 | 0 | 1 |

Every site whose holdings were fetched returned the full table with ISINs
for ETFs (at least one fund checked on each: UK, CH, US, DE, NL, IT, FR, ES,
SE, DK, NO, FI, LatAm, HK, SG, JP, AU, CA, MX, BR, CO). Mutual and index funds (BGF, BSF,
the Swiss and Irish index funds, Mexican and Brazilian mutual funds) give
only ten names and weights, no identifiers, as of the last month end, on
every site. The adapter leaves them out (5,283 share classes after the
merge); `all_products()` and `ishares.products(site)` still list them with
`holdings_scope = "top10"`.

## Endpoints

Fund lists, by site:

varnish
: `https://www.ishares.com/varnish-api/blk-product-screener-server/api/v1/product-screener/product-data?country=ch&language=en&siteName=ishares-ch&userType=individual`
  returns a dict keyed by portfolio ID, each entry with `isin`, `fundName`,
  `productType` (`ISHARES_FUND_DATA`, `BLK_MUTUAL_FUND_DATA`, `ETP`),
  `domicile`, `aladdinAssetClass(Code)`, `aladdinFundTicker`,
  `localExchangeTicker`, `seriesBaseCurrencyCode`, `productPageUrl` and
  `totalNetAssets`. The same path on `www.blackrock.com` serves the Nordic
  (`country=se&language=en&siteName=one-se`, `dk`, `no`, `fi`), Hong Kong
  (`hk`, `hk-one`) and Americas offshore (`lm`, `latam`) sites. For the
  other sites it answers HTTP 500 ("reading configuration file").

v3
: `<site>/product-screener/product-screener-v3.jsn?dcrPath=/templatedata/config/product-screener-v3/data/<lang>/<site>/product-screener-backend-config&siteEntryPassthrough=true`
  returns `data.tableData` (columns plus rows). Paths, from each site's
  product-list page: US `www.ishares.com/us/...` (`en/us-ishares/ishares-...`),
  Spain `/es/particulares/...` (`es/one-es`), Singapore `/sg/en/...`
  (`en/sg-one`), Japan `/jp/individual-en/en/...` (`ja/jp-one`), Canada
  `/ca/investors/en/...` (`en/ca-one`), Mexico
  `/mx/intermediarios/lista-de-producto/...` (`es/latam/mexico`), Brazil
  `/br/product-list/...` (`pt/br-one`), Colombia
  `/co/lista-de-producto/...` (`es/latam/columbia`). The Canadian table has
  no `isin` column.

legacy (France)
: `https://www.blackrock.com/fr/intermediaries/products/product-list/1524727817804.ajax?tab=all&fileType=json`
  (the `screenerJsonURL` of the French product list) returns the same
  columns-plus-rows table, with a UTF-8 byte-order mark.

sitemap (Australia)
: The Australian product-list pages answer HTTP 403 to scripts, so they are
  not used. `https://www.blackrock.com/au/product-sitemap.xml`, which
  `robots.txt` lists, gives the portfolio IDs, and each fund's ISIN comes
  from its key facts (below). That is one extra request per fund (121).

Holdings
: `https://www.blackrock.com/varnish-api/<data_site>/product-data/api/v2/get-product-data?appType=PRODUCT_PAGE&appSubType=<ISHARES|ONE>&targetSite=<site>&locale=<locale>&userType=individual&portfolioId=<id>&component=holdings,keyFundFacts`.
  EMEA sites use `uk-retail01-product-data`, all others
  `blk-one01-product-data`; the parameters per site are in
  `ishares.SITES`, taken from the `apiHost`, `targetSite`, `locale` and
  `appSubType` in each site's product pages (`ISHARES` on ishares.com,
  `ONE` on blackrock.com). The holdings component has an `all` table for
  ETFs (`isin`, `ticker`, `issueName`, `holdingPercent`, `assetClass`,
  `marketCurrencyCode`, `countryOfRisk`, `marketValue`, plus `couponRate`,
  `maturityDate` for bonds; US tables add `cusip` and `sedol`) and only a
  `ten-largest` table (`name`, `fundPercentage`) for mutual and index funds.
  `keyFundFacts` adds `productStructure` (Physical, Synthetic) and
  `fundMethodologyTypeCode` (Replicated, Optimised) on EMEA sites, and the
  fund's `isin` or `cusip`, which is how Australian and Canadian fund ISINs
  are found (Canada: `CA` + CUSIP + check digit).

Each response also carries about 350 KB of page disclaimers (`content`),
which is dropped before caching.

## From a fund ISIN to its holdings

1. `products()` reads every site in `SITES` order and merges them: for a
   share class listed on several sites, the row from a site with full
   holdings wins, then the first site in the order uk, ch, us, de, nl, it,
   se, dk, no, fi, fr, es, latam, hk, sg, jp, au, ca, mx, br, co
   (English-language EMEA sites first, so asset-class labels are English).
   `sites` lists every site that has it.
2. `portfolio_id` is iShares' fund-level ticker (`aladdinFundTicker`, e.g.
   `ISIESPTTL` for all four share classes of the Spain Govt Bond ETF), shared
   by the share classes of one portfolio; sites without it borrow it from
   another site's row for the same share class, and US funds, which have no
   such ticker, keep the share-class ID. `share_class_id` is the
   iShares portfolio ID used in the holdings request, and `holdings_url` is
   that request.
3. `holdings(product, ctx)` fetches it, keeps `holdingPercent` (percent of
   net assets, as reported; US equity tables give `marketWeight` to two
   decimals, used instead) and maps the table to `HOLDING_COLUMNS`, plus the
   issuer's `sector`. Identifiers that fail their check digit become `None`,
   and so do the pseudo-CUSIPs Aladdin gives lines without one (`S` + the
   line's SEDOL + a check digit, e.g. `S64351455` on the Japanese site),
   which would otherwise turn into a wrong US ISIN. The as-of date is the
   table's `asOfDate`. Responses are cached as
   `<site>_<share class>_<date>.json` in `ctx.cache_dir`, only after they
   parse. A fund that has only a ten-largest table raises `NoFullHoldings`
   (the answer is cached too); the look-through records it as `top10_only`
   and moves on to the next source.

This adapter is the only way openbasket reads iShares. `ingest` calls
`products(sites=issuers.ishares_sites)` (every site above by default; `[]`
reads none), so one failing site is skipped and only funds with full
holdings reach `issuer_products`. The look-through reads holdings through
the same adapter. The per-site functions `ishares.products(site)` and
`ishares.holdings(share_class_id, site, cache_dir, top10=False)` remain for
tests and one-off use.

Product links: the Swiss screener gives `/ch/individuals/...` paths, which
answer 404; they are rewritten to `/ch/individual/...`, the path in the
site's own sitemap. Australian links come from the sitemap. Rows kept from
the Latin America offshore site, whose links redirect to a UK page that
answers 403, take the link another site gives for the same share class, or
none.

## Update frequency

ETF holdings daily (as of the previous business day: 2 November 2026 on
3 November). Top-10 tables of mutual and index funds monthly (30 September
2026 on 3 November).

## What the data shows (3 November 2026)

| Fund | Site | Lines | Sum of weights | Weight with ISIN | As of | Seconds |
|---|---|---|---|---|---|---|
| iShares SMI ETF (CH), CH0008899764 | ch | 26 | 100.00% | 99.70% | 2026-11-02 | 0.7 |
| iShares Core CHF Corporate Bond ETF (CH), CH0226976816 | ch | 896 | 100.00% | 101.05% | 2026-11-02 | 2.0 |
| iShares S&P 500 Swap UCITS ETF, IE00BMTX1Y45 | uk | 662 | 100.00% | 0.00% | 2026-11-02 | 1.2 |
| iShares MSCI World Swap PEA UCITS ETF, IE0002XZSHO1 | fr | 141 | 100.00% | 0.00% | 2026-11-02 | 0.8 |
| iShares STOXX Europe Mid 200 UCITS ETF (DE), DE0005933998 | de | 215 | 100.00% | 99.37% | 2026-11-02 | 1.2 |
| iShares Core Nikkei 225 ETF, JP3027710007 | jp | 228 | 100.00% | 99.20% | 2026-11-02 | 0.9 |
| iShares FTSE China A50 ETF, HK2823028546 | hk | 59 | 100.00% | 99.96% | 2026-11-02 | 0.9 |
| iShares Core S&P/ASX 200 ETF, AU000000IOZ4 | au | 206 | 100.00% | 99.86% | 2026-11-02 | 1.0 |
| iShares High Quality Canadian Bond Index ETF, CA46432H1038 | ca | 605 | 100.00% | 99.86% | 2026-11-02 | 1.0 |
| iShares NAFTRAC, MX1BNA060014 | mx | 36 | 100.00% | 99.91% | 2026-11-02 | 1.3 |
| iShares Ibovespa Fundo de Índice, BRBOVACTF003 | br | 79 | 100.00% | 98.65% | 2026-11-02 | 1.2 |
| iShares MSCI COLCAP, CORB6PA00015 | co | 26 | 100.00% | 99.69% | 2026-11-02 | 1.1 |
| iShares USD Asia High Yield Bond ETF, SG2D83975482 | sg | 197 | 100.00% | 99.98% | 2026-11-02 | 0.9 |

Weights are already in percent (no conversion). The CHF corporate bond
fund's ISIN lines add up to more than 100% because its cash line is
negative. Seconds include the one-second wait between requests to the same
host.

## Known gaps

- **Swap ETFs.** The holdings table of a swap ETF is the index swap as one
  line without an ISIN (100.02% for the S&P 500 Swap ETF), plus the
  substitute basket twice: held long (+97.85%) and as the swap's short leg
  (−97.85%). The ISIN lines therefore net to about zero and the fund shows
  as about 100% unresolved, which is the true picture: the exposure is the
  index, not the basket. Map such funds to a physical fund on the same index
  with `lookthrough.proxies`. In `products()`, `replication` is `swap` when
  the fund name says "Swap" (35 share classes); the issuer's own
  `productStructure` arrives with the holdings (EMEA sites only) and is added
  to the notes, with `physical` or `sampled` from
  `fundMethodologyTypeCode`. For other funds `replication` is unknown in the
  fund list. Some Hong Kong page slugs still say "synthetic" for funds that
  now hold the shares (the FTSE China A50 ETF holds 99.96% A-shares by
  ISIN), so slugs are not used.
- **Top-10-only funds** are left out of the adapter (see above).
- **Line cap.** No response had more than 20,000 lines; the largest bond
  funds stop there (noted in `notes`).
- **Australia and Canada need one key-facts request per fund** for the fund
  ISIN (121 and 176 requests at one per second). `products(max_lookups=n)`
  caps this for testing; the 3 November run used six per site.
- **Lines without an ISIN** are cash, futures, FX forwards, the swap line of
  swap funds and, in US bond funds, mortgage pools with a CUSIP only. In
  Brazil's Ibovespa fund the only line without an ISIN is BRL cash (1.35%).
- **Local labels.** Local-language sites give asset classes in their
  language (`Aktien`, `Renta Variable`, `Renda Variável`, `Actions` ...);
  those seen are mapped to the English labels. Holdings fetched through the
  Canadian and Mexican sites have no asset-class column.
- **ETPs** (the iShares Bitcoin ETP) are not funds with securities and are
  left out.
- **Sites not used.**
  - Chile (`cl-one`): the v3 screener works (418 share classes), but
    product data answers HTTP 500 for every `cl-one` parameter set tried and
    the product pages use only the old `.ajax` tables. All 418 share classes
    are also on other sites, so nothing is lost.
  - Mexico's `banamex` section redirects to a sign-in page and is not used;
    the public `intermediarios` section is.
  - Poland (`one-pl`), Czech Republic (`blk-cz`) and Portugal (`one-pt`):
    their varnish screeners work (814, 770 and 1,077 rows) but list no ETF
    share class missing above (the Czech site adds 164 mutual-fund share
    classes, top 10 only). Austria (`one-at`) and Belgium (`one-be`) answer
    HTTP 500 on the varnish screener. None of them are added.
  - Australia's product-list pages (HTTP 403) are not read; see above.

## Terms and credit

Credit shown with every row: "Holdings: iShares by BlackRock, from
BlackRock's regional websites (ishares.com, blackrock.com)."

**Redistribution class: `local_only`.** The adapter runs when you run
openbasket yourself (it is on by default) and your local site and API show
its rows. The public build never fetches it and publishes nothing from it.
A fund only iShares covers appears in the public index with status
`local_only`, no constituents, and a note that its holdings are available
from iShares when you run openbasket yourself.

Terms in short: the site terms, as read on the Swiss site's terms and
conditions page on 3 November 2026, say the content is protected by
copyright, all rights reserved, is provided for personal, non-commercial
use, and is not to be reproduced or redistributed. Reading it for yourself
fits that; republishing it would need BlackRock's permission.

Access: no login, cookie or consent gate is involved, and `robots.txt` does
not disallow the `varnish-api` or screener paths. The client fetches each
fund at most once a day, one request per second per host.

## Example

```python
from openbasket.sources.issuers import FetchContext, discover

ishares = discover()["ishares"]
funds = ishares.products()              # about 1,900 share classes, all sites
smi = funds.set_index("isin").loc["CH0008899764"].to_dict()
got = ishares.holdings(smi, FetchContext(cache_dir="data/01_raw/issuers/holdings"))
got.as_of                               # '2026-11-02'
got.holdings.head(3)                    # Roche, Novartis, Nestle, weight_pct 17.96, 16.17, 14.04
```
