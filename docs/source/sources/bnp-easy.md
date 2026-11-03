# BNP Paribas Easy

Adapter `bnp_easy` (`src/openbasket/sources/issuers/bnp_easy.py`). Full
holdings of BNP Paribas Easy ETFs and index funds (BNP Paribas Asset
Management), domiciled in Luxembourg, Ireland and France, from the
"inventory" workbook BNP Paribas AM publishes for each share class.

**Redistribution: local only.** Every inventory workbook says reproduction
or distribution is forbidden without BNP Paribas AM's prior written
authorisation. So the adapter is registered with
`redistribution="local_only"`: openbasket reads these holdings only for the
person running it. Under the publishing policy the public build does not
fetch this source, and none of its rows go to the openbasket site, API or
releases. A fund that only this source covers is listed there without
constituents, with a note that its holdings are available when you run
openbasket yourself. See [Adding a source](adding-a-source.md).

**Private-investor content only.** BNP Paribas AM's sites split their
content by investor type, and the professional segments say their content
is for professional clients only. The adapter reads only the
private-investor ("Personal Investor") segment of each site; see
[Investor segment](#investor-segment).

## What it covers

Run on 3 November 2026 with the private-investor profiles, `products()`
returned **487 share classes in 117 portfolios**: 405 LU, 73 IE and 9 FR
ISINs. 232 share classes are UCITS ETF classes; the others are the same
portfolios' index fund classes (Track, Classic, Privilege, I), which hold
the same securities. Share-class currencies: 343 EUR, 126 USD, 6 GBP, 4 MXN,
3 CHF, 2 JPY, 1 AUD, 1 CZK, 1 NOK.

Replication in the product list: 89 share classes (22 portfolios) `swap`,
276 (60) `physical`, 48 (9) `sampled` and 74 (26) unknown. The call took
409 s: about 10 s for the six share lists and the rest for one fund page
and inventory per portfolio (see [Replication](#replication)).

| Site | Private-investor profile | Share classes first found there |
|---|---|---|
| `en-gb` | `IP_UK-IND;ENG;GBR` | 242 |
| `en-ch` | `IP_CH-IND;ENG;CHE` | 213 |
| `en-lu` | `IP_LU-IND;ENG;LUX` | 27 |
| `fr-fr` | `IP_FR-IND;FRE;FRA` | 5 |
| `de-de` | `IP_DE-IND;GER;DEU` | 0 new after the above |
| `en-nl` | `IP_NL-FAD;ENG;NLD` | 0 new |

The Swiss site adds 213 share classes the UK site does not sell, including
the CHF-hedged ones. Earlier runs with the professional profiles found 485
share classes; nothing is lost by reading the private-investor segment.

## Investor segment

Each fund page maps the answers to its "What type of investor are you?"
question to a profile code in inline JavaScript, for example on `en-gb`:

```text
allSeg = {"2":"IP_UK-FSE;ENG;GBR","1":"IP_UK-FSE;ENG;GBR","0":"IP_UK-IND;ENG;GBR"}
defaultVal = "IP_UK-FSE;ENG;GBR"
```

Investor type 0 is "Personal Investor" ("Individual investors seeking
information on investments"); types 1 and 2 are financial advisers and
institutions ("I am a Professional Client"). The `FSE` profiles are those
professional segments, and their disclaimers restrict the content to
professional clients (UK) or professional investors under Swiss law (CH).
`defaultVal`, which a page uses before a visitor answers, is the
professional profile, so the adapter does **not** use it.

`SITES` holds investor type 0's profile for each site, read from one fund
page per site on 3 November 2026. On `en-nl`, type 0 maps to `IP_NL-FAD`.
`PROFESSIONAL_PROFILES` (`-FSE`, `-PRO`, `-INS`, `-INT`) are refused: the
adapter raises before it would build a URL for one. No cookie is set and no
investor type is declared; the API needs neither.

## Endpoints

Fund list
: `GET https://api.bnpparibas-am.com/push/sharesearchv2/<profile>/<language>`,
  e.g. `.../sharesearchv2/IP_UK-IND/ENG` (4.9 MB, all BNP Paribas AM ranges;
  the Swiss list is larger). Rows whose `compart_name` contains `EASY` are
  kept. Each has `isin_code`, `compartment_id` (used as `portfolio_id`),
  `legal_name`, `val_currency`, `eng_asset_class`, `region` and `flags`.
  The ISIN prefix gives the domicile. This is the list behind the site's
  own fund search.

Fundsheet
: `GET https://api.bnpparibas-am.com/push/fundsheet/<profile>/<language>/<country>/<ISIN>`,
  e.g. `.../fundsheet/IP_UK-IND/ENG/GBR/IE000A0GH076`, the data behind the
  fund page (40 to 80 KB). This is the product's `holdings_url`. Its
  `publications.<LANG>.documents[]` link the holdings workbook:
  `DOC_ACCOUNTING_INVENTORY` first, else `DOC_FUND_INVENTORY_ETF`, English
  first. `fundshare_selection.shares` lists the portfolio's other share
  classes, and `overview.disclaimers.investment_policy` is the fund's
  investment policy text.

Holdings
: `GET https://docfinder.bnpparibas-am.com/api/files/<uuid>/<version>`
  (`Content-Disposition: inline;filename=Fund_Holdings_INV_<ISIN>_en.xlsx`,
  or `axa_invetf_<id>_en_<date>.xlsx` for Easy II funds). The URL changes
  with every new version, so it is read from the fundsheet each time and
  reported as the source URL.

Product page
: `https://www.bnpparibas-am.com/<site>/fundsheet/<asset-class>/<slug>-<isin>`.
  The site finds the fund by the trailing ISIN, so the adapter builds the
  link from the share name without fetching anything.

`api.bnpparibas-am.com` and `docfinder.bnpparibas-am.com` have no
`robots.txt` (404). `www.bnpparibas-am.com/robots.txt` disallows URLs with a
query string, which the adapter never requests (it only links to fund pages).

## How an ISIN reaches its holdings

1. Find the ISIN in `products()`; its row gives the site (profile).
2. Fetch the fundsheet for that profile and ISIN and take its holdings
   document.
3. If the share class has none, try the other share classes of the same
   portfolio from `fundshare_selection.shares`, ETF classes first, and say so
   in the notes. The search stops at the first sibling whose fundsheet
   answers without a holdings document: the portfolio publishes none.
4. Download the workbook and parse it with the parser for its layout.
   Responses are cached in `ctx.cache_dir` as `bnp_easy_<ISIN>_<date>.xlsx`
   plus a small `.json` with the URL, notes, document type and replication,
   only after the workbook has parsed.

## The workbooks

### Accounting inventory (`DOC_ACCOUNTING_INVENTORY`)

BNP Paribas Easy funds (LU and FR, and the older IE funds).

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

### ETF inventory (`DOC_FUND_INVENTORY_ETF`)

BNP Paribas Easy II funds (the Irish ICAV formerly run by AXA IM, 17
portfolios). These fundsheets carry no accounting inventory; earlier
versions of the adapter reported "no inventory" for them.

```text
row 1   ISIN | Security Name | Asset Type | Currency | Weight in % | Nav Date
row 2   (none) | DEFERRED INT REC - FIXED INCOME | ASSET | GBP | -0.000123 | 2026-05-14
row 6   (none) | US DOLLAR | CASH | USD | 0.548 | 2026-05-14
...     XS... | <bond> | FIXED INCOME | EUR | 0.12 | 2026-05-14
...     (none) | SOLD USD/BOUGHT EUR | FOREIGN EXCHANGE | USD | -20.27 | 2026-05-14
        (none) | TOTAL EXPENSE RATIO | LIABILITIES | USD | -0.054 | 2026-05-14
```

- Weights are already **percent** of net assets (some cells are stored as
  text) and are kept as they are. The lines include cash, FX forwards,
  receivables and liabilities, so they sum to 100%.
- `as_of` is the latest `Nav Date`.
- `Asset Type` is mapped: `FIXED INCOME` to `debt`, `EQUITY` to `equity`,
  `CASH` to `cash`, `FOREIGN EXCHANGE` to `derivative (foreign exchange)`,
  `ASSET` and `LIABILITIES` to `other`.

## Replication

The share search has no replication field. `products()` first marks `swap`
where the share-search fields prove it: a fund eligible for the French PEA
(75% EU equities) that tracks a non-European or non-equity index, asset
class `Other` or commodities, and the strategy funds whose pages say they
use total return swaps (Premium Income, Equity Buffer, Long/Short, Managed
Futures, Energy & Metals Enhanced Roll). For **every other portfolio** it
reads one share class's fundsheet and:

1. the accounting inventory's `Replication` label, if there is one;
2. else the fund page's investment policy: "synthetic replication" →
   `swap`, "physical replication" or "full replication" → `physical`,
   "optimised" or "sampling" → `sampled`.

This catches synthetic funds that nothing in the share search marks, such
as BNP Paribas Easy MSCI World II (`LU3173209654`, no PEA flag), whose
policy says it invests "in a substitute basket ... (synthetic replication)"
and whose inventory says `Synthetic`. The check costs one or two requests
per portfolio, spread over two hosts; pass `check_replication=False` to
skip it. 26 portfolios stay unknown (no label and no telling policy text).

A swap-based fund's holdings are its substitute basket. `holdings()` labels
those lines `asset_class` `collateral (substitute basket)`
(`SUBSTITUTE_BASKET` in `openbasket.sources.issuers`) whenever the
inventory, the fund page or the products row says `swap`, and the notes say
so. The products row's `replication` `swap` also makes the look-through note
on the fund say its holdings are a substitute basket.

## Real runs (3 November 2026)

With the private-investor profiles:

| Fund | Lines | Sum of weights | Weight with ISIN | As of | Seconds |
|---|---|---|---|---|---|
| MSCI World II UCITS ETF EUR C (LU3173209654), **Synthetic**, `en-gb` | 25 | 95.59% | 95.59% | 2026-10-27 | 3.4 |
| Easy II Global High Yield Opportunities (H) CHF (IE000EOB5120), ETF inventory, `en-ch` | 830 | 100.00% | 99.38% | 2026-05-14 | 2.5 |

The MSCI World II file lists 25 US shares (Intel 7.66%, Tesla 7.54%,
Broadcom 5.57%), all labelled substitute basket. The Easy II file holds 792
bond lines, 30 FX forwards, 4 cash and 4 other lines; it is 173 days old,
and the notes say so.

Earlier run with the professional profiles, for comparison (the files are
the same documents):

| Fund | Lines | Sum of weights | Weight with ISIN | As of | Seconds |
|---|---|---|---|---|---|
| MSCI World UCITS ETF EUR C (IE000A0GH076), equity, Full | 1,247 | 100.14% | 100% | 2026-10-27 | 2.3 |
| JPM Screened EMBI Global Div. Composite UCITS ETF C (LU1547515053), bonds, Optimised | 559 | 100.02% | 100% | 2026-10-30 | 2.5 |
| Bloomberg Europe Defense UCITS ETF C (LU3047998896), Full | 37 | 99.98% | 100% | 2026-10-27 | 4.1 |
| S&P 500 UCITS ETF EUR C (FR0011550185), **Synthetic** | 148 | 87.96% | 100% | 2026-09-21 | 3.6 |
| STOXX Europe 600 UCITS ETF EUR C (FR0011550193), **Synthetic** | 53 | 90.97% | 100% | 2026-03-18 | 4.3 |
| Low Vol Europe UCITS ETF C (LU1377381717), **Synthetic** | 23 | 96.99% | 100% | 2026-10-29 | 3.7 |

Seconds include the fundsheet and the workbook download at one request per
second per host. All frames pass `validate_holdings`.

## Update frequency

`irregular`. Inventory age varies from days to many months per fund: most
LU and IE accounting inventories were dated three to six days before
publication (data dates 27 to 30 October, published 29 October to 2
November), but MSCI Emerging Min TE (`LU1291097779`) was dated 8 July, the
French-domiciled S&P 500 file 21 September and the STOXX Europe 600 file 18
March 2026, and the Easy II ETF inventories are from May. `holdings()` adds
a note when `as_of` is more than 30 days before the run date. Always use
`as_of`.

## Known gaps

- **Swap funds list their substitute basket.** For synthetic funds the file
  shows the securities the fund owns (for the S&P 500 ETF: ASML, BNP
  Paribas, Siemens Energy...), not the index it tracks; the swap itself is
  not listed, so the weights add up to less than 100%. The lines are
  labelled `collateral (substitute basket)` and a look-through must not
  treat them as the index exposure.
- **Balance sheet securities only** in accounting inventories. The
  disclaimer in each file says it shows the securities on the fund's balance
  sheet, which are only part of its assets: cash, futures, FX forwards and
  swaps are not listed. ETF inventories do list them.
- **Rounding.** Four-decimal fractions in accounting inventories: positions
  under 0.005% show as 0 (33 of 1,247 lines in the MSCI World file) and sums
  land between 98.97% and 100.14% for physical funds.
- **No inventory.** Some funds publish none on their fund page, e.g. Managed
  Futures, Equity Premium Income and Global Equity Long/Short. `holdings()`
  returns an empty frame with a note.
- Tickers are not filled: the share search gives Bloomberg codes per
  portfolio, not per share class.

## Terms and credit

Credit line: *Holdings: BNP Paribas Asset Management, fund inventory files
from bnpparibas-am.com*.

No login, cookie or consent is needed for the API or the files. But there is
no open licence. The disclaimer at the end of every accounting inventory
says the document and its contents are BNP Paribas Asset Management's
exclusive property and that reproducing or distributing them is forbidden
without its prior written authorisation. The professional segments of the
UK and Swiss sites add that their content is for professional clients only
and that reproduction is prohibited; the adapter does not read them. The
adapter is `local_only`, and its `licence` text records both points.

## Example

```python
from openbasket.sources.issuers import FetchContext, discover

bnp = discover()["bnp_easy"]
products = bnp.products()
row = products.loc[products["isin"] == "LU3173209654"].iloc[0].to_dict()
got = bnp.holdings(row, FetchContext(cache_dir="data/01_raw/issuers/holdings"))
got.as_of         # '2026-10-27'
got.url           # https://docfinder.bnpparibas-am.com/api/files/d12c507a-.../91648
got.holdings.head(2)[["isin", "name", "weight_pct", "asset_class"]]
#            isin                    name  weight_pct                     asset_class
# 0  US4581401001  INTEL CORPORATION CORP        7.66  collateral (substitute basket)
# 1  US88160R1014               TESLA INC        7.54  collateral (substitute basket)
```
