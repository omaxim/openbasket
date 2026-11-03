# BNP Paribas Easy

Adapter `bnp-easy` (`src/openbasket/sources/issuers/bnp_easy.py`). Full
holdings of BNP Paribas Easy ETFs and index funds (BNP Paribas Asset
Management), domiciled in Luxembourg, Ireland and France, from the
"inventory" workbook BNP Paribas AM publishes for each share class.

## What it covers

Run on 3 November 2026, `products()` returned **485 share classes in 116
portfolios**: 398 LU, 78 IE and 9 FR ISINs. 231 share classes (in 114
portfolios) are UCITS ETF classes; the others are the same portfolios' index
fund classes (Track, Classic, Privilege, I), which hold exactly the same
securities. Share-class currencies: 347 EUR, 118 USD, 6 GBP, 5 MXN, 3 CHF, 2
JPY, 2 NOK, 1 AUD, 1 CZK.

Replication in the product list: 44 share classes (13 portfolios) `swap`, 22
(5 portfolios) `physical`, 419 unknown until their holdings are read (see
below). The call took 45 s: 10 s for the six share lists, the rest for the 11
inventory checks.

| Site | Profile | Share classes first found there |
|---|---|---|
| `en-gb` | `IP_UK-FSE;ENG;GBR` | 233 |
| `en-ch` | `IP_CH-FSE;ENG;CHE` | 213 |
| `en-lu` | `PV_LU-FSE;ENG;LUX` | 38 |
| `fr-fr` | `PV_FR-FSE;FRE;FRA` | 0 new after the above |
| `de-de` | `PV_DE-FSE;GER;DEU` | 0 new |
| `en-nl` | `PV_NL-FSE;ENG;NLD` | 1 |

Italy (`PV_IT-FSE;ITA;ITA`), Spain (`IP_ES-FSE;SPA;SPA`), Austria
(`IP_AT-FSE;GER;AUT`) and Sweden (`AM_SE-PRO;ENG;SWE`) were also read and
added nothing, so they are not in the default list. The Swiss site adds 213
share classes the UK site does not sell, including the CHF-hedged ones.

## Endpoints

Fund list
: `GET https://api.bnpparibas-am.com/push/sharesearchv2/<profile>/<language>`,
  e.g. `.../sharesearchv2/IP_UK-FSE/ENG` (5.9 MB, 1,569 share classes of all
  BNP Paribas AM ranges; the Swiss list is 14.8 MB). Rows whose
  `compart_name` contains `EASY` are kept. Each has `isin_code`,
  `compartment_id` (used as `portfolio_id`), `legal_name`, `val_currency`,
  `eng_asset_class`, `region` and `flags`. The ISIN prefix gives the
  domicile. This is the list behind the site's own fund search.

Profile code
: Each fund page sets it in inline JavaScript,
  `const defaultVal = "IP_UK-FSE;ENG;GBR";` (profile; language; country),
  the value the page uses before a visitor chooses an investor type. The
  adapter uses that default and never sets or accepts the investor-type
  choice; the API needs no cookie. The codes are stored in `SITES`; they were
  read from one fund page per site on 3 November 2026.

Fundsheet
: `GET https://api.bnpparibas-am.com/push/fundsheet/<profile>/<language>/<country>/<ISIN>`,
  e.g. `.../fundsheet/IP_UK-FSE/ENG/GBR/IE000A0GH076`, the data behind the
  fund page (40 to 80 KB). This is the product's `holdings_url`. Its
  `publications.<LANG>.documents[]` with `category ==
  "DOC_ACCOUNTING_INVENTORY"` link the holdings workbook, English first.
  `fundshare_selection.shares` lists the portfolio's other share classes.

Holdings
: `GET https://docfinder.bnpparibas-am.com/api/files/<uuid>/<version>`
  (`Content-Disposition: inline;filename=Fund_Holdings_INV_<ISIN>_en.xlsx`).
  The URL changes with every new version, so it is read from the fundsheet
  each time and reported as the source URL.

Product page
: `https://www.bnpparibas-am.com/<site>/fundsheet/<asset-class>/<slug>-<isin>`.
  The site finds the fund by the trailing ISIN, so the adapter builds the
  link from the share name without fetching anything.

`api.bnpparibas-am.com` and `docfinder.bnpparibas-am.com` have no
`robots.txt` (404). `www.bnpparibas-am.com/robots.txt` disallows URLs with a
query string, which the adapter never requests (it only links to fund pages).

## How an ISIN reaches its holdings

1. Find the ISIN in `products()`; its row gives the site (profile).
2. Fetch the fundsheet for that profile and ISIN and take its
   `DOC_ACCOUNTING_INVENTORY` document.
3. If the share class has none, try up to four other share classes of the
   same portfolio from `fundshare_selection.shares`, ETF classes first, and
   say so in the notes.
4. Download the workbook and parse it. Responses are cached in
   `ctx.cache_dir` as `bnp-easy_<ISIN>_<date>.xlsx` plus a small `.json`
   with the URL and notes, only after the workbook has parsed.

## The workbook

```text
row 1   Data Date | 2026-10-27
row 4   Inventory for BNP Paribas Easy MSCI World UCITS ETF UCITS ETF EUR Capitalisation - IE000A0GH076
row 10  Fund Name | ISIN | Share Category | Share Class | Replication | Share Reference Currency | ...
row 11  BNP Paribas Easy MSCI World UCITS ETF | IE000A0GH076 | UCITS ETF EUR | Capitalisation | Full | EUR | ...
row 14  Euronext Paris Ticker | WETFE FP          (ticker rows, only for ETF classes)
row 19  Asset Class | Name of entity | ISIN | Weight
row 20  Equity | APPLE | US0378331005 | 0.056
...
        TOTAL | 1
        Disclaimer ...
```

- `as_of` is the `Data Date`. The fundsheet's `reference_date` (the
  publication date) goes into the notes.
- `Weight` is a **fraction** of net assets rounded to four decimals, and the
  `TOTAL` row is 1. The adapter multiplies by 100 (so 0.0001 is 0.01%) and
  rounds away float noise at eight decimals; it does not rescale. If a file
  ever had a `TOTAL` above 1.5, the weights would be taken as percent already.
- Every line in the files read had a valid ISIN. There is no ticker, CUSIP,
  SEDOL, currency or country column.
- The asset class is `Equity` or `Bond` (`Action`/`Obligation` in French
  files), mapped to `equity` and `debt`.
- French workbooks repeat the whole table under the first one; parsing stops
  at the first `TOTAL`.
- The sheet declares a too-small dimension (`A1:L24`), so openpyxl's
  read-only mode must reset it or only 24 rows are read.
- The `Replication` label is mapped: `Full` and `Alpha Enhanced` to
  `physical`, `Optimised` to `sampled`, `Synthetic` to `swap`. It is written
  into the notes, e.g. `Replication (issuer label): Synthetic -> swap`.

## Real run (3 November 2026)

| Fund | Lines | Sum of weights | Weight with ISIN | As of | Seconds |
|---|---|---|---|---|---|
| MSCI World UCITS ETF EUR C (IE000A0GH076), equity, Full | 1,247 | 100.14% | 100% | 2026-10-27 | 2.3 |
| JPM Screened EMBI Global Div. Composite UCITS ETF C (LU1547515053), bonds, Optimised | 559 | 100.02% | 100% | 2026-10-30 | 2.5 |
| Same fund, UCITS ETF H CHF (LU2868143954) | 559 | 100.02% | 100% | 2026-10-30 | 3.5 |
| Same fund, Track IH CHF (LU2118274161, `en-ch`) | 559 | 100.02% | 100% | 2026-10-30 | 4.0 |
| Bloomberg Europe Defense UCITS ETF C (LU3047998896), Full | 37 | 99.98% | 100% | 2026-10-27 | 4.1 |
| Alpha Enhanced Europe UCITS ETF C (LU2993402101) | 213 | 98.97% | 100% | 2026-10-27 | n/a |
| S&P 500 UCITS ETF EUR C (FR0011550185), **Synthetic** | 148 | 87.96% | 100% | 2026-09-21 | 3.6 |
| STOXX Europe 600 UCITS ETF EUR C (FR0011550193), **Synthetic** | 53 | 90.97% | 100% | 2026-03-18 | 4.3 |
| Low Vol Europe UCITS ETF C (LU1377381717), **Synthetic** | 23 | 96.99% | 100% | 2026-10-29 | 3.7 |
| EUR Overnight PEA UCITS ETF C (LU3215541056), **Synthetic** | 21 | 93.27% | 100% | 2026-10-27 | 2.1 |
| Managed Futures UCITS ETF C (LU3307218399) | 0 | no inventory published | | | 2.6 |

Seconds include the fundsheet and the workbook download at one request per
second per host. All frames pass `validate_holdings`.

## Update frequency

`weekly`. The workbooks of LU and IE funds were dated three to six days
before publication (data dates 27 to 30 October, published 29 October to 2
November). The French-domiciled funds' files are much older: the S&P 500 file
is dated 21 September and the STOXX Europe 600 file 18 March 2026. Always use
`as_of`.

## Known gaps

- **Swap funds list their substitute basket.** For synthetic funds the file
  shows the securities the fund owns (for the S&P 500 ETF: ASML, BNP
  Paribas, Siemens Energy...), not the index it tracks; the swap itself is
  not listed, so the weights add up to less than 100%. The notes say so, and
  a lookthrough must not treat these lines as the index exposure.
- **Replication in the product list.** The share search has no replication
  field. `products()` marks `swap` where the fields prove it: a fund eligible
  for the French PEA (75% EU equities) that tracks a non-European or
  non-equity index, asset class `Other` or commodities, and the strategy
  funds whose pages say they use total return swaps (Premium Income, Equity
  Buffer, Long/Short, Managed Futures, Energy & Metals Enhanced Roll).
  PEA-eligible funds on pan-European indices are mixed (Low Vol Europe and
  STOXX Europe 600 are synthetic, Bloomberg Europe Defense is not), so their
  replication is read from one inventory per portfolio (11 portfolios, two
  requests each). Every other portfolio is `None` in the product list; its
  file's label is in the holdings notes. Pass `check_replication=False` to
  skip the extra requests.
- **Balance sheet securities only.** The disclaimer in each file says it
  shows the securities on the fund's balance sheet, which are only part of
  its assets: cash, futures, FX forwards and swaps are not listed.
- **Rounding.** Four-decimal fractions: positions under 0.005% show as 0
  (33 of 1,247 lines in the MSCI World file) and sums land between 98.97%
  and 100.14% for physical funds.
- **No inventory.** Some funds publish none on their fund page, e.g. Managed
  Futures, Equity Premium Income, Global Equity Long/Short and BNP Paribas
  Easy II Nasdaq 100. `holdings()` returns an empty frame with a note.
- Tickers are not filled: the share search gives Bloomberg codes per
  portfolio, not per share class.

## Terms and credit

Credit line: *Holdings: BNP Paribas Asset Management, fund inventory files
from bnpparibas-am.com*.

No login, cookie or consent is needed for the API or the files. But there is
no open licence. The disclaimer at the end of every inventory workbook says
the document and its contents are BNP Paribas Asset Management's exclusive
property and that reproducing or distributing them is forbidden without its
prior written authorisation. The site's legal information
page (`/en-gb/legal-information/`) says nothing more about reuse.
**Republishing these holdings on the public site needs BNP Paribas AM's
written permission**; the adapter records this in its `licence` text.

## Example

```python
from openbasket.sources.issuers import FetchContext, discover

bnp = discover()["bnp-easy"]
products = bnp.products()
row = products.loc[products["isin"] == "IE000A0GH076"].iloc[0].to_dict()
got = bnp.holdings(row, FetchContext(cache_dir="data/01_raw/issuers/holdings"))
got.as_of         # '2026-10-27'
got.url           # https://docfinder.bnpparibas-am.com/api/files/342245f8-.../160768
got.holdings.head(2)[["isin", "name", "weight_pct"]]
#            isin       name  weight_pct
# 0  US0378331005      APPLE        5.60
# 1  US67066G1040     NVIDIA        5.50
```
