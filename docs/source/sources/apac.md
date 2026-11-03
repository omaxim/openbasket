# Asia-Pacific issuers

Seven adapters cover ETFs domiciled in Australia, Japan, Korea, Hong Kong and
Singapore. Each one reads the issuer's own public files. Shared code (cached
fetching, the ASX ISIN list, Korean ISINs, frame checks) is in
`openbasket.sources.issuers._apac`.

Status as tested on 3 November 2026:

| Adapter | Issuer | Funds | Holdings | Identifier per line | Update |
|---|---|---|---|---|---|
| `ssga_apac` | State Street SPDR (AU, SG, JP; HK-listed 2821) | 25 | Full | ISIN (+ SEDOL) | Daily |
| `nomura` | Nomura NEXT FUNDS (JP) | 76 | Full | ISIN + local code | Monthly (month-end) |
| `kodex` | Samsung KODEX (KR) | 245 | Full (daily PDF) | ISIN + KRX code | Daily |
| `ishares_au` | iShares Australia (BlackRock) | 56 | Full | ISIN, CUSIP, SEDOL | Daily |
| `betashares` | Betashares (AU) | 113 | Full | Bloomberg ticker; ISIN for ASX lines | Daily |
| `vanguard_au` | Vanguard Australia | 37 | Full | Ticker; ISIN for ASX lines | Monthly (month-end) |
| `trahk` | Tracker Fund of Hong Kong (2800) | 1 | Full | ISIN + SEDOL | Daily |

Weights are always percent of net assets as the issuer reports them. Nomura
and Samsung publish **fractions** (0.117392 = 11.7392%); those adapters
multiply by 100. Nothing is rescaled.

## A real run (3 November 2026)

`products()` per adapter, by domicile and replication:

| Adapter | Products | Domicile | Replication |
|---|---|---|---|
| `ssga_apac` | 25 | AU 18, JP 5, SG 2 | physical 25 |
| `nomura` | 76 | JP 76 | physical 69, unknown 7 (leveraged/inverse and commodity futures funds) |
| `kodex` | 245 | KR 245 | physical 206, swap 18, unknown 21 (leveraged/inverse futures) |
| `ishares_au` | 56 | AU 56 | physical 56 |
| `betashares` | 113 | AU 113 | physical 90, unknown 23 (geared and bear funds) |
| `vanguard_au` | 37 | AU 35, US 2 (VEU, VTS) | physical 37 |
| `trahk` | 1 | HK 1 | physical 1 |

`holdings()` on real funds (sum of `weight_pct`, and the part of it on lines
with an ISIN):

| Fund | Lines | Sum | With ISIN | As of | Seconds |
|---|---|---|---|---|---|
| SPDR S&P/ASX 200 (STW, AU000000STW9) | 206 | 100.00 | 99.99 | 2026-11-02 | 1.1 |
| SPDR S&P/ASX iBoxx Australian Bond (BOND, AU00000BOND4) | 263 | 100.00 | 99.90 | 2026-11-02 | 1.0 |
| ABF Pan Asia Bond Index Fund (2821, SG9999002026) | 680 | 99.05 | 98.93 | 2026-11-02 | 1.0 |
| SPDR S&P 500 ETF, JPY unhedged (449A, JP3050990005) | 506 | 101.69 | 98.08 | 2026-10-30 | 0.7 |
| NEXT FUNDS Nikkei 225 (1321, JP3027650005) | 226 | 99.95 | 98.79 | 2026-10-30 | 1.8 |
| NEXT FUNDS FTSE WGBI ex Japan (2511, JP3048620003) | 724 | 98.55 | 98.55 | 2026-10-30 | 3.8 |
| NEXT FUNDS Nikkei 225 Leveraged (1570, JP3047460005) | 1 | 193.99 | 0.00 | 2026-10-30 | 4.8 |
| KODEX 200 (069500, KR7069500007) | 202 | 100.00 | 100.00 | 2026-11-03 | 1.5 |
| KODEX US S&P 500 (379800, KR7379800006) | 504 | 100.00 | 100.00 | 2026-11-03 | 2.1 |
| KODEX AA- composite bond active (273130, KR7273130005) | 387 | 100.00 | 100.00 | 2026-11-03 | 3.1 |
| KODEX CD rate active, synthetic (459580, KR7459580007) | 33 | 99.83 | 66.59 | 2026-11-03 | 2.0 |
| KODEX Leverage (122630, KR7122630007) | 210 | 200.09 | 200.09 | 2026-11-03 | 2.1 |
| iShares Core S&P/ASX 200 (IOZ, AU000000IOZ4) | 206 | 100.00 | 99.86 | 2026-11-02 | 2.5 |
| iShares S&P 500 (IVV, AU000000IVV8) | 3 | 100.00 | 99.93 | 2026-11-02 | 1.8 |
| Betashares Australia 200 (A200, AU00000A2000) | 202 | 100.00 | 99.66 | 2026-11-02 | 5.2 |
| Betashares Nasdaq 100 (NDQ, AU000000NDQ6) | 103 | 100.00 | 0.00 | 2026-11-02 | 1.0 |
| Betashares Diversified All Growth (DHHF, AU0000059586) | 6 | 100.00 | 37.73 | 2026-11-02 | 1.0 |
| Vanguard Australian Shares (VAS, AU000000VAS1) | 308 | 99.41 | 99.40 | 2026-09-30 | 3.7 |
| Vanguard MSCI International Shares (VGS, AU000000VGS8) | 1,220 | 98.48 | 0.00 | 2026-09-30 | 2.6 |
| Vanguard Diversified High Growth (VDHG, AU00000VDHG0) | 13 | 99.90 | 22.73 | 2026-09-30 | 1.8 |
| Tracker Fund of Hong Kong (2800, HK2800008867) | 98 | 102.23 | 99.94 | 2026-11-03 | 2.5 |

Seconds include the first download of the ASX ISIN list (about 2 MB) where an
adapter needs it; later calls on the same day use the cached copy.

## How a fund ISIN reaches its holdings

Every adapter's `products()` gives each share class its ISIN and the
`holdings_url` (or the fixed id the holdings URL is built from), so an input
ISIN found in `issuer_products` goes straight to one file. `holdings()` caches
each response in `ctx.cache_dir`, one file per fund per day, and only after it
parsed, so a block page is never cached.

### State Street (`ssga_apac`)

- Fund lists: `https://www.ssga.com/bin/v1/ssmp/fund/fundfinder?country=<au|sg|hk|jp>&language=<en_gb|en|en|ja>&role=institutional&product=etfs&ui=fund-finder`.
  The `intermediary` role used by the US/EMEA module returns nothing outside
  Australia. Only funds domiciled in the site's own country are kept, so SPY,
  GLD and the US funds the Japanese site lists stay with the US module.
- Fund ISIN: the ISIN in the entry's `keywords`; for Australian funds without
  one, the ASX ISIN list; otherwise the most frequent ISIN on the fund page.
- Holdings: `https://www.ssga.com/library-content/products/fund-data/etfs/apac/holdings-daily-<au|sg|jp>-<en|ja>-<ticker>.xlsx`,
  and for the ABF Pan Asia Bond Index Fund the
  `hsbc-paif-hk-fullholdings-download-sg-en-daily.xlsx` file linked from its page.
  Header lines with the fund name, ticker and as-of date (`As of 02-Nov-2026`,
  `Holdings as of: 02-Nov-2026` or `2026年10月30日現在`), then a table with
  `ISIN` and `Weight (%)` / `Weight %` / `Percent of Fund` / `組入比率`.
- Japanese files are a business day behind the Australian ones (30 October
  against 2 November in the run above).

### Nomura NEXT FUNDS (`nomura`)

- Fund list: `https://nextfunds.jp/monthly_holdings/` (76 funds, grouped as
  Japanese equity, foreign equity, bonds, REIT, commodity, leveraged/inverse).
- Fund ISIN: from each fund page `https://nextfunds.jp/lineup/<code>/`, so
  `products()` makes 77 requests and takes about two and a half minutes.
- Holdings: `https://www.nomura-am.co.jp/fund/monthly_holdings/<code>_brd_data.xlsx`,
  sheet `保有明細`: as-of date (`2026年10月30日現在`), the fund's ISIN, then
  `銘柄コード`, `ISINコード`, names, quantity, value and `純資産比率 % of NAV`
  as a fraction. The `合計` and `純資産総額` rows are dropped.
- Nomura also publishes daily PCF baskets
  (`https://www.nomura-am.co.jp/fund/etf/portfolio/NK225-YYYYMMDD-001.csv`),
  but they give four-digit codes and share counts only, no ISIN and no
  weights, so they are not used.

### KODEX (`kodex`)

- Fund list: `https://www.samsungfund.com/api/v1/kodex/product.do?ordrColm=NAV&ordrSort=DESC&pageNo=<n>&srchTerm=w&themeCd=&srchVal=`
  (13 pages of 20).
- Fund ISIN: built from the KRX short code as `KR7` + code + `00` + check
  digit. The KRX data portal (`data.krx.co.kr`) now answers `LOGOUT` without
  a log-in, so it is not used. The rule was checked against Samsung's own
  files (Samsung Electronics `005930` → `KR7005930003`) and an alphanumeric
  code (`0048J0` → `KR70048J0007`). Preferred shares do not follow it.
- Holdings: `/api/v1/kodex/product-pdf/<fId>.do?gijunYMD=<YYYY.MM.DD>` gives
  the latest date and `/excel_pdf.do?fId=<fId>&gijunYMD=<YYYYMMDD>`, an
  `.xls` with `ISIN`, `종목코드`, `비중(%)`. Despite its header the weight is
  a fraction. The file is read with `xlrd` (pandas' xlrd engine is
  deprecated and calamine garbles its UTF-16 text).
- The PDF is the creation-unit basket, dated for the next trading day (3
  November in a run on 3 November). Its weights sum to exactly 100% (200% for
  2x funds): it is the basket, scaled.

### iShares Australia (`ishares_au`)

- Fund list: `https://www.blackrock.com/au/product-sitemap.xml`. The fund
  list page `/au/products/investment-funds` answers 403 to scripts and is
  not used. Each `ISHARES ...` name in the ASX ISIN list is matched to a
  sitemap slug after ASX abbreviations are expanded (`GBL`, `AUS`, `AUD HDG`);
  a hedged fund is never matched to an unhedged page. 56 of the 57 iShares
  ASX lines match; `IGB` (named only "ISHARES TREASURY ETF" on the ASX) does
  not.
- Holdings: `/au/products/<id>/fund/1478358644060.ajax?tab=all&fileType=json`
  (rows of cells with CUSIP, ISIN, SEDOL and five-decimal weights, but no
  date or column names) and
  `...1478358644060.ajax?fileType=csv&fileName=<ticker>_holdings&dataType=fund`
  (as-of date and column names; the JSON inserts the three identifiers just
  before `Price`). Two requests per fund.
- IVV (AU) holds the US iShares Core S&P 500 ETF (US4642872000), which the US
  iShares adapter expands.

### Betashares (`betashares`)

- Fund list: the `BETASHARES ...` rows of the ASX ISIN list, without the
  `... BSK` in-specie basket securities. Fund pages come from
  `https://www.betashares.com.au/fund-sitemap.xml`, matched by name; funds
  whose ASX name is too abbreviated to match link to a site search.
- Holdings: `https://www.betashares.com.au/wp-admin/admin-ajax.php?action=fund_holdings_csv&ticker=<code>`.
  `Date,YYYY-MM-DD`, then `Ticker,Name,...,Weight (%)`; columns differ for
  bond and multi-asset funds.
- Lines are Bloomberg tickers (`BHP AT`, `AAPL UW`). `AT`/`AU` lines get an
  ISIN from the ASX ISIN list; everything else stays unresolved, so foreign
  equity funds such as NDQ have 0% ISIN-identified weight. Weights in the file
  sum to exactly 100%.

### Vanguard Australia (`vanguard_au`)

- Fund list: `https://www.vanguard.com.au/personal/api/products/personal/funds/etf`
  (the API behind the site; 37 ETFs, plus 43 unlisted managed funds that are
  skipped). VEU and VTS are US Vanguard ETFs quoted on the ASX under their
  own AU ISINs; they keep Vanguard's US fund numbers as `portId` and are
  given domicile US.
- Holdings: `https://www.vanguard.com.au/personal/api/data/products/holdings-download/<portId>`,
  month-end (`effectiveDate`), ticker, name, `marketValPercent`,
  `countryCode`. No ISIN, CUSIP or SEDOL; lines with `countryCode` AU are
  matched to the ASX ISIN list by ticker (bonds excluded). The diversified
  ETFs hold Vanguard's unlisted wholesale funds (`VANAUST` and similar), which
  have no ISIN in any free source.

### Tracker Fund of Hong Kong (`trahk`)

- One fund, ISIN HK2800008867 (checked against the HKEX list of securities).
- Holdings: `https://rbwm-api.hsbc.com.hk/pws-hk-hase-hsvm2-papi-prod-proxy/v1/hsvm/csv/trahkfund/holdings?mode=daily`,
  the download linked from `https://www.trahk.com.hk/en-hk/trahk-fund/fund-information/`.
  `Holdings as of:,DDMMYYYY`, then `ISIN,SEDOL,Ticker,...,Weight (%)` to two
  decimals.

## Reference lists

ASX ISIN list
: `https://www.asx.com.au/content/dam/asx/issuers/ISIN.xls`, free, about
  14,300 ASX codes with name, security type and ISIN, updated by the ASX. Used
  for Australian fund ISINs and to resolve ASX-listed holdings. Downloaded once
  a day per run (cached in `ctx.cache_dir` when one is given).

HKEX list of securities
: `https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx`,
  free, about 14,000 HK stock codes with ISIN. It would resolve the HK-listed
  lines of local-code sources such as CSOP; not used yet.

Japanese codes
: Japanese ISINs (`JP3...`) cannot be derived from four-character exchange
  codes. The Securities Identification Code Committee's code database is a
  paid service, and JPX's free ETF lists carry no ISIN. Nomura, State Street
  and Amova give ISINs in their files; sources with codes only (MAXIS) cannot
  be resolved for free.

Korean codes
: Ordinary shares and ETFs: `KR7` + six-character code + `00` + check digit.
  KRX's data portal needs a log-in.

## Terms and credit

All files are public downloads on the issuers' or exchanges' websites, with
no log-in, consent gate or bot challenge. No issuer's terms have been
reviewed for automated reuse. Each adapter's `credit` names the issuer and,
where the ASX list supplies ISINs, ASX Limited. robots.txt was checked for
every host: nothing used is disallowed. The CSOP site disallows URLs with
query strings, and VanEck Australia asks for a 25-second crawl delay.

## Known gaps

- **Monthly sources.** Nomura and Vanguard Australia publish month-end
  holdings only, one to five weeks old.
- **Futures-based funds.** Leveraged and inverse funds (Nomura 1570/1571/1357/1472/2251,
  KODEX leveraged/inverse, Betashares geared and bear funds) hold index futures
  with notional weights (194% for 1570, 200% for KODEX Leverage) and no ISIN.
  Their replication is left unknown.
- **Synthetic KODEX funds** (`(합성)` in the name, 18 funds) list the total
  return swap (`스왑(...)`) and its collateral, not the index; their
  replication is `swap`.
- **Ticker-only lines.** Betashares and Vanguard foreign lines and all their
  bonds have no ISIN; they stay unresolved and show in the unresolved weight.
- **Rounding.** TraHK gives two decimals; Betashares and KODEX weights are
  normalised to exactly 100% by the issuer.
- **Cash.** KODEX's `KRD010010001` is a KRX pseudo-code for won cash, not an
  ISIN, and is dropped from the `isin` column.

## Not built, with evidence

| Source | Why |
|---|---|
| Daiwa iFreeETF (`daiwa-am.co.jp`) | Every page, including `robots.txt`, answers HTTP 403 to a script. Not bypassed. |
| Mirae Asset TIGER / Global X Korea (`tigeretf.com`, `investments.miraeasset.com`) | HTTP 403, including `robots.txt`. |
| Amova (ex-Nikko) Listed Index Funds | Holdings are public and good: `https://www.amova-am.com/products/etf/api/portfolioFiles?id=<fund code>` lists `/files/etf/_shared/xls/portfolio/<code>_<YYYYMM>.xls`, a daily-updated full portfolio with an ISIN per line and fractional weights. But no free source gives the funds' own ISINs: not the API (`/products/etf/api/funds`), the fund pages, JPX's lists or JPX's per-fund PDFs. Buildable once a fund ISIN list is available. |
| MAXIS (Mitsubishi UFJ) | `https://maxis.am.mufg.jp/fund_xml/kumiire/<fund_cd>.xml` → `/fund_file/kumiire/<fund_cd>.csv` (cp932): full holdings, but by local code or Bloomberg ticker only, with weights to two decimals. Japanese codes cannot be turned into ISINs for free. |
| CSOP (HK) | `https://website-api.csopasset.com/cmsApi/Holdings/product/list` (POST, JSON) gives full holdings with local codes and two-decimal weights, no ISIN. A-share lines (most of the big funds) have no free ISIN source; HK lines could use the HKEX list. Not built yet. |
| VanEck Australia, Global X Australia/HK | Not surveyed in depth. VanEck asks for a 25-second crawl delay. |
