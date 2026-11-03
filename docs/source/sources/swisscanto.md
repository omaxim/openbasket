# Swisscanto (Zürcher Kantonalbank)

**Status: not usable: the terms of use forbid systematic access to build a
database or directory without written permission.** The data is technically
reachable, but this source stays out until Swisscanto or Zürcher Kantonalbank
grants that permission in writing. No adapter is registered; one written for
private use must be off by default (`enabled_by_default = False`) and switched
on in `conf/local`. Checked on 3 November 2026.

## Why it is not built

The [terms of use](https://www.swisscanto.com/ch/en/private/legal/terms-of-use.html)
(last updated January 2024) cover both `https://www.swisscanto.com` and
`https://products.swisscanto.com/`. They say that the information "may only
be freely used for personal use". They also say that, without the written
permission of a Swisscanto company or Zürcher Kantonalbank, it is prohibited
to access the site's content systematically in order to compile a
collection, a database or a directory.

That is exactly what an adapter does: list every share class and read each
fund's holdings file every day for a public directory. Other things noted:

- The human product pages open behind a disclaimer that asks for the
  visitor's country and investor type (stored in the `swc_dsclmr1` cookie).
  The JSON API answers without it, but the site plainly expects that
  self-declaration, and openbasket does not accept such gates on anyone's
  behalf.
- The terms restrict access by nationality and domicile (US persons in
  particular), and each holdings file ends with a disclaimer saying it is
  meant for distribution in Switzerland (and Ireland for the IE ETFs) only.
- `robots.txt` (`https://products.swisscanto.com/products/api/robots.txt`)
  disallows `/*.pdf` and `/rest/`. The JSON and `.xlsx` paths below are not
  disallowed, but robots.txt does not override the terms.

If permission is granted, the endpoints below are enough for a small adapter.

## What exists

Fund list
: `https://products.swisscanto.com/products/api/funds?segment={RETAIL|INSTITUTIONAL}&country=CH&language=en`
  returns `{"funds": [...]}`, one entry per share class with `id`,
  `fundName` (the portfolio), `shareClassName`, `isin`, `valor`, `wkn`,
  `bloombergCode`, `currency`, `fundDomicile`, `assetCategory`,
  `productCategory`, `managementStyle` and links to fact sheets and PRIIPs
  documents. The base URL comes from the site's own JavaScript bundle.

Fund detail
: `https://products.swisscanto.com/products/api/funds/{ISIN}?segment=INSTITUTIONAL&country=CH&language=en`
  lists the documents published for one share class. Funds with a holdings
  file have a publication named `funddetail.publication.HOLDING`.

Holdings
: `https://products.swisscanto.com/products/api/holdings/file-name/Holdings_{ISIN}_EN.xlsx`,
  one sheet `Holdings`: a title row, then the columns `Share Class Name`,
  `Share Class ISIN`, `NAV Date`, `Holding Name`, `ISIN`, `Currency`,
  `Price`, `Weight %`, then footer rows (a note that actual positions may
  differ, the data source, a disclaimer) with no ISIN. For a fund without a
  file the same URL answers HTTP 303 to an error page.

Product page
: `https://products.swisscanto.com/products/product/{ISIN}`, listed in
  `https://products.swisscanto.com/products/api/sitemap.xml` (1,025 URLs).

## Coverage seen

The institutional list is a superset of the retail one (every retail ISIN is
in it) and of the lists for `country=DE` and `country=LU` (364 share classes
each).

| List | Share classes | Portfolios | CH | LU | IE |
|---|---|---|---|---|---|
| `RETAIL` | 424 | 170 | 226 | 195 | 3 |
| `INSTITUTIONAL` | 1,025 | 226 | 658 | 364 | 3 |

By product category (institutional list): 578 active funds, 303 index funds,
77 investment-foundation groups (Anlagestiftung), 49 pillar-3 pension funds
(BVG 3) and 18 ETF share classes. Every entry has a valor and a Bloomberg
code; 965 have a WKN.

Of the 18 ETF share classes, 14 are physically backed precious-metal ETFs
(gold, silver, platinum, palladium; CH-domiciled). They hold metal, not
securities: their detail lists a bar list (`BARREN`, a PDF) and no holdings
file, so they would be listed as products with asset class commodity and no
look-through.

Only the four equity ETFs have a holdings file:

| Fund | Lines with ISIN | Sum of weights | NAV date |
|---|---|---|---|
| Swisscanto (CH) ESGen SDG Index Equity Switzerland ETF EA CHF (CH1408319130) | 68 | 99.96% | 2026-11-02 |
| Swisscanto (IE) ESGen SDG Index Equity EMU UCITS ETF ET EUR (IE000KIQ3DT5) | 31 | 99.73% | 2026-11-02 |
| Swisscanto (IE) ESGen SDG Index Equity USA UCITS ETF ET USD (IE00035F1RO6) | 120 | 99.79% | 2026-11-02 |
| Swisscanto (IE) ESGen SDG Index Equity World UCITS ETF ET CHF (IE000ZI3FFP9) | 336 | 99.74% | 2026-11-02 |

Notes on the files:

- Despite the `Weight %` header, weights are **fractions** of net assets
  (0.0549876408 means 5.49876408%), so they would be multiplied by 100.
- Cash is not listed; the 0.2 to 0.3% gap would stay unresolved weight.
- The IE files name HSBC Continental Europe as their source, the CH file
  Swisscanto Fondsleitung AG.
- Names are as the custodian gives them and can be wrong, so the ISIN is the
  reliable key.

The other sampled funds (active and index funds in CH and LU, a pension fund,
a real estate fund, an investment-foundation group) have no holdings file;
their positions appear only in annual and semi-annual report PDFs, which
robots.txt disallows.

## Update frequency

Daily: the fund list's prices and the holdings files' `NAV Date` were one
business day old (2 November 2026, read on 3 November).

## What would change this

Written permission from Swisscanto Holding Ltd. or Zürcher Kantonalbank to
use the fund list and the holdings files in a public, credited directory.
Without it, Swisscanto funds can still be reached where a third party
publishes their holdings under its own terms, and Swiss-listed Swisscanto
products appear in exchange reference data with their ISIN.
