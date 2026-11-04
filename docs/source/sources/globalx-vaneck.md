# Global X ETFs Europe and VanEck

Two smaller UCITS issuers, one adapter each:
`openbasket.sources.issuers.globalx` (`issuer = globalx`) and
`openbasket.sources.issuers.vaneck` (`issuer = vaneck`). Both publish the
full holdings of each fund every business day, with constituent ISINs, on
their own public websites. Neither needs a login, and neither file is
openly licensed (see **Terms** below).

**Redistribution: local only.** Both issuers' terms forbid republication
(Global X: "may not be reproduced, redistributed or used to create any
derivative works"; VanEck: no commercial reproduction without consent). So
both adapters are registered with `redistribution="local_only"`: openbasket
reads their holdings only for the person running it. Under the publishing
policy the public build does not fetch these sources, and none of their rows
go to the openbasket site, API or releases. A fund that only these sources
cover is listed there without constituents, with a note that its holdings
are available when you run openbasket yourself. See
[Adding a source](adding-a-source.md).

## Global X ETFs Europe

Fund list
: `https://globalxetfs.eu/explore` (`/funds/` redirects there) is a Next.js
  page. Its embedded flight data (`self.__next_f.push`) holds one record per
  fund with `ETF_NAME`, `PRIMARY_TICKER`, `PRIMARY_ISIN`, `CLASSNAME`
  (`USD Acc`) and `isCrypto`. Only one ISIN per fund is listed there, so the
  adapter also reads each fund page, `https://globalxetfs.eu/funds/<ticker>`
  (the slug is the lower-case primary ticker, as in `sitemap.xml`). Its
  `ComponentFundsTradingInfo` block gives `accIsin` and `distIsin` with their
  listings (London, Xetra, Borsa Italiana, SIX in CHF, Mexico), and
  `ComponentFundsFundDetails` gives `domicile`, `assetClass`, `mgmtStyle`
  and `swapConterparties`.

Holdings
: `https://globalxetfs.eu/api/funds/<ticker>/topholdingscsv`. Despite the
  name this is the full file: the fund page button reads "Download Full
  Holdings (.csv)". Header
  `AS_OF_DATE,SEDOL,NAME,NET_ASSETS,TICKER,MARKET_PRICE,SHARES_HELD,MARKET_VALUE,ISIN,COUNTRY`.
  `NET_ASSETS` is the weight in **percent** of net assets, three decimals,
  and is used as is. `AS_OF_DATE` is on every row. The file ends with a
  quoted disclaimer line, which is dropped.

How a fund ISIN reaches its holdings: ISIN → products row (either share
class) → `portfolio_id` (the ticker slug) → the CSV above. Both share
classes of a fund point at the same file.

Replication comes from `mgmtStyle`: "Physical - Full Replication" →
`physical`, "Physical - Sampled" → `sampled`, "Synthetic" → `swap`.

**Synthetic funds list their substitute basket.** For a fund marked `swap`,
or any file with a `TRS ...` line, the lines with ISINs are the securities
the fund holds as collateral for its total return swap, not the index it
tracks. They are labelled `asset_class` `collateral (substitute basket)`
(`SUBSTITUTE_BASKET` in `openbasket.sources.issuers`), and the result
carries a note saying so. The swap line keeps `derivative (swap)` and cash
lines keep `cash`. The products row's `replication` `swap` also makes the
look-through note on the fund say its holdings are a substitute basket.

Lines without an ISIN keep their weight and are labelled from the name:
`CURRENCY CONTRACT - XXX` → `derivative (foreign exchange)`, `TRS ...` →
`derivative (swap)`, currency balances, `CASH` and `OTHER PAYABLE &
RECEIVABLES` → `cash`. Lines with an ISIN get the fund's asset class, since
the file has no per-line class. `SEDOL` is kept when its check digit is
valid. Currency-forward legs (`CURRENCY CONTRACT - JPY/USD`) often have a
blank `NET_ASSETS` in the file (six of them in the copper miners fund); they
are kept with no weight (`NaN`), which the look-through counts as 0.

**Tickers.** Each share class takes the first ticker its own listings give.
Both classes of SuperDividend (`IE000YICM5P9` Acc and `IE00077FRP95` Dist)
carry `SDIV LN`, because Global X's own fund page lists both London
listings under that ticker (with different SEDOLs, BMFQCT9 and BMFQCS8;
checked 3 November 2026).

## VanEck (Europe)

Fund list
: `https://www.vaneck.com/Main/FundSearchPanelBlock/GetSearchPanelData?blockId=<id>&language=en`,
  with `<id>` read on each run from
  `<form id="ucitsSearchForm" data-blockid="281068">` on
  `https://www.vaneck.com/de/en/`. Each row has `Values` (ISIN, WKN, name,
  TER, net assets with a currency sign), `Keywords` (the ticker first) and
  `Url`, the fund page. Asset classes come from the fund explorer: the page
  `https://www.vaneck.com/de/en/fundlisting/overview/etfs/` gives
  `currentPageId` and the asset-class filter codes, and a POST of
  `filterJson={"InvType":"all","AssetClass":[...],"TableType":"ov",...}` to
  `https://www.vaneck.com/Main/FundListingUcits/GetFundData/` returns
  `FundSet` rows with `FundID`, `ISIN`, `TickerGroup` (ETF, ETN, MF) and an
  `assetClass` cell (Equity, Income, Balanced, Digital Assets). The German
  site is used because it lists the whole European range (Irish and Dutch
  UCITS ETFs, German-issued ETNs and the Irish UCITS funds); the fund explorer
  queried with `CountryIso` `ch` returned the same 65 ISINs as with `de`.

Holdings
: `<fund page>downloads/holdings/`, for example
  `https://www.vaneck.com/DE/en/investments/semiconductor-etf/downloads/holdings/`,
  returns `SMH_asof_20261102.xlsx`. The sheet is named
  `<TICKER>_asof_<YYYYMMDD>` (the as-of date used), row 0 reads
  `All Holdings  11/02/2026` (US date order, used as a fallback), and the
  table header starts with `Number`.
  Equity, multi-asset and ETN files: `Number, Holding Name, Ticker, ISIN,
  Shares, Market Value, % of Net Assets`, weights as text such as `"10.73%"`
  (two decimals). Bond files: `Number, Holding Name, ISIN, Coupon, Maturity,
  Composite Rating, % of Net Assets, State, Market Value`, weights such as
  `"4.1025"` (four decimals). Both are **percent** of net assets; the `%`
  sign is dropped and the number kept. The last line is `Other/Cash`.

How a fund ISIN reaches its holdings: ISIN → search panel row → its `Url`
→ `Url + "downloads/holdings/"`. The `portfolio_id` is the last segment of
the fund page path, shared by the share classes of the UCITS funds.

The first request to the site redirects to `?cken=true` (a check that the
client keeps cookies) and back. The adapter keeps a cookie jar so that
works; nothing is accepted. The search panel returns no rows to a client
without the session cookies from that first page.

`robots.txt` on www.vaneck.com sets `Crawl-delay: 25`, so the adapter sets
`http.set_min_interval("www.vaneck.com", 25)`. Redirects are followed by
hand, one request per hop through `openbasket.http`, so each hop of the
`?cken=true` round trip also waits its 25 seconds. `products()` (four
requests plus the first contact's two redirect hops) takes about two
minutes and each holdings file about 25 seconds. A daily run over all 56
files takes about 25 minutes.

Replication: no list the adapter reads has it, and the fund page HTML does
not either (the GDX page fetched on 3 November 2026 has no replication
text; the fund details are filled in by scripts). A fund whose name says
`swap`, `synthetic` or `TRS` is marked `swap`; every other row is `None`.
In a holdings file, a line whose name says swap is labelled
`derivative (swap)` and the securities next to it
`collateral (substitute basket)`, with a note.

## What the data shows (3 November 2026, positions of 2 November)

**Global X**: `products()` returned 63 share classes of 53 funds in 53 s
(one explore page plus 53 fund pages at one per second): 58 Irish UCITS ETF
share classes (48 funds, 10 of them with both an accumulating and a
distributing class) and 5 Jersey crypto ETPs (GB ISINs). By replication:
53 physical, 9 swap, 1 sampled. (The run itself labelled the Uniswap ETP
`swap` because its style, "Physical - Backed by Uniswap", contains the
letters "swap"; the match is now on the whole word, and the counts here are
corrected.) Asset classes: 57 Equity, 5 Digital Asset,
1 Fixed Income. Currencies: USD, EUR, GBP base; the CHF lines on SIX are
listings of the same ISINs.

| Fund | Replication | Lines | Sum of weights | With ISIN | As of | Seconds |
|---|---|---|---|---|---|---|
| SuperDividend UCITS ETF (IE00077FRP95, also SDIV SW in CHF) | physical | 164 | 99.998% | 99.389% (102 lines) | 2026-11-02 | 2.2 |
| 1-3 Month T-Bill UCITS ETF (IE000GSIFIB0) | sampled | 11 | 99.999% | 99.983% | 2026-11-02 | 0.3 |
| S&P 500 Annual Buffer UCITS ETF (IE0009BM62P2) | swap | 39 | 99.999% | 101.020% | 2026-11-02 | 1.0 |
| Uranium UCITS ETF (IE000NDWFGA5) | physical | 74 | 99.997% | 99.798% | 2026-11-02 | 1.7 |
| Nasdaq 100 Covered Call UCITS ETF (IE00BM8R0J59) | swap | 0 | | | | 1.0 |
| Bitcoin ETP (GB00BLBDZV05) | physical | 0 | | | | 0.3 |

**VanEck**: `products()` returned 64 share classes of 58 portfolios in 78 s
(four requests, 25 s apart): 41 UCITS ETFs (30 Irish, 11 Dutch), 15 crypto
ETNs issued in Germany (DE ISINs) and 8 share classes of two Irish UCITS
funds. Asset classes: 33 Equity, 15 Digital Assets, 7 Income, 3 Balanced, 6
none. 56 rows have a holdings file. Replication is unknown for all (see
gaps). The explorer also lists a Peruvian "El Dorado" ETF (PE ISIN) that is
not in the European search panel; it is left out.

| Fund | Lines | Sum of weights | With ISIN | As of | Seconds |
|---|---|---|---|---|---|
| Semiconductor UCITS ETF (IE00BMC38736) | 26 | 100.01% | 99.98% (25 lines) | 2026-11-02 | 23.4 |
| iBoxx EUR Corporates UCITS ETF (NL0009690247) | 41 | 100.00% | 99.43% | 2026-11-02 | 24.8 |
| Multi-Asset Conservative Allocation UCITS ETF (NL0009272764) | 238 | 99.99% | 99.50% | 2026-11-02 | 24.2 |
| J.P. Morgan EM Local Currency Bond UCITS ETF (IE00BDS67326) | 450 | 100.00% | 99.78% | 2026-11-02 | 26.1 |
| Morningstar Developed Markets Dividend Leaders UCITS ETF (NL0011683594) | 101 | 100.03% | 99.47% | 2026-11-02 | 23.7 |
| Bitcoin ETN (DE000A28M8D0) | 1 | 100.00% | 0% | 2026-11-02 | 24.8 |

After the redirect change (3 November 2026, same day): `products()` 64
share classes, then VanEck Gold Miners UCITS ETF (IE00BQQP9F84): 60 lines,
100.03%, 99.99% with an ISIN, as of 2026-11-02, 24.4 s.

The seconds are almost all the 25-second crawl delay; the download itself
takes under a second. The multi-asset fund holds bonds and shares directly, not
other ETFs; its bond lines repeat the ISIN in the `Ticker` column, which is
read as "not a ticker". The EM local-currency file has one line named
`Other` (0.06%) besides `Other/Cash`.

## Known gaps

- **Global X synthetic funds** (9 share classes: the covered-call funds
  QYLD, XYLU, SYLD and DYLD and the S&P 500 buffer and tail-hedge funds
  SPAB, SPAH, SPQB and SPQH) hold a basket of shares plus a total return
  swap with Citi. Their files list the basket the fund actually holds,
  which is not the index, plus a `TRS CITI ...` line with no ISIN (between
  −2.5% and +1.3% of net assets in the files checked). Those lines are
  labelled `collateral (substitute basket)` (see above). Example, SYLD
  (Euro Stoxx 50 Covered Call, IE000SAXJ1M1) on 3 November 2026: 48 lines
  summing to 100.002%, 99.0% with an ISIN, all US large caps (Amgen 7.919%,
  JPMorgan 7.38%, Goldman Sachs 6.792%): 45 substitute-basket lines, 2 cash
  lines and the swap. The QYLD file was empty (only the disclaimer), so
  `holdings()` returns an empty frame with a note and the whole weight stays
  unresolved.
- **Crypto ETPs and ETNs**: Global X's ETP files are empty; VanEck's ETN
  files have one line, the coin, at 100% with no ISIN. Both stay unresolved.
- **VanEck replication** is not in any list the adapter reads (see above),
  so `replication` is empty for every VanEck row unless the name says swap.
  On 3 November 2026 no name did, and no VanEck holdings file seen showed a
  swap line.
- **VanEck UCITS funds** (8 share classes of "VanEck – Emerging Markets Bond
  UCITS" and "VanEck – Global Resources UCITS") have no holdings download
  (HTTP 404); their `holdings_url` is empty and `holdings()` raises
  `LookupError`. Six of them also have no asset class in the explorer.
- **VanEck share-class currency** is read from the net-assets sign in the
  search panel (€, $) or the `UCITS-EUR` part of a fund name; `domicile` is
  the ISIN's country prefix (IE, NL, DE).
- **Global X currency** is the base currency in the explore record's
  `CLASSNAME` and is applied to both share classes; no hedged classes were
  seen. The explore record labels some distributing ISINs "Acc"; the fund
  page's `accIsin`/`distIsin` are used instead.
- **Rounding**: Global X weights have three decimals, VanEck equity weights
  two and bond weights four, so sums differ from 100% by a few hundredths.
- **Missing ISINs**: Global X FX forwards and cash have none; an occasional
  equity line has none (two `VERDERA ENERGY CORP` lines in the uranium
  fund, 0.006%). VanEck equity files sometimes list a share by ticker only
  (Hyundai `005380 KS` in the multi-asset fund).
- Neither file gives a per-line currency; Global X gives `COUNTRY` (ISO
  code), VanEck bond files give `State` (a country name).

## Terms and credit

**Global X.** Every holdings CSV ends with: "The information contained
herein may not be reproduced, redistributed or used to create any derivative
works." The site's terms of use say the content is copyright Global X ETFs
ICAV, allow printing one copy and downloading extracts "for your personal
reference", and forbid using any part "for commercial purposes without
obtaining a licence". robots.txt allows everything. The adapter's licence
text says this, and the adapter is `local_only`: Global X rows are never
published on the public site. Credit: "Holdings data: Global X ETFs Europe
(Global X ETFs ICAV), globalxetfs.eu".

**VanEck.** The site terms say no information on the site "may be
reproduced, transmitted, displayed, distributed, published or otherwise used
for commercial purposes without the prior consent of VanEck"; personal use
is allowed, and the adapter is `local_only`. robots.txt asks for a
25-second crawl delay, which the adapter keeps on every request and
redirect hop, and none of the paths used is disallowed. Credit: "Holdings
data: VanEck, vaneck.com".

## Example

```python
from openbasket.sources.issuers import FetchContext, globalx, vaneck

ctx = FetchContext(cache_dir="data/01_raw/issuers/holdings")

gx = globalx.products()
sdiv = gx[gx["isin"] == "IE00077FRP95"].iloc[0].to_dict()
got = globalx.holdings(sdiv, ctx)
print(got.as_of, len(got.holdings), got.holdings["weight_pct"].sum(), got.url)

ve = vaneck.products()          # about 80 s: 25 s between requests
smh = ve[ve["isin"] == "IE00BMC38736"].iloc[0].to_dict()
got = vaneck.holdings(smh, ctx)
print(got.as_of, got.holdings[["isin", "name", "weight_pct"]].head())
```

Responses are cached per fund per day in `ctx.cache_dir`, after they parse.
