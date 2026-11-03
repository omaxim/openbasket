# Swiss bank funds

Funds run by Swiss banks and their fund management companies: UBS (with the
former Credit Suisse funds), Zürcher Kantonalbank/Swisscanto, the other
cantonal banks, Raiffeisen, PostFinance, Pictet, Lombard Odier, Julius Bär,
Vontobel, UBP, J. Safra Sarasin, EFG, Migros Bank, Bank Cler and Valiant.

Status as tested on 3 November 2026. **Short version:** every one of these
funds can be *found* through open registers (GLEIF, ESMA FIRDS) and local-only
ones (SIX, FINMA): 9,560 rows, 6,434 share classes with an ISIN, of which
7,844 rows and 6,341 ISINs come from the openly licensed sources. Full
*holdings* are reachable for three banks only: UBS physical index ETFs through
a peer-ETF stand-in (on by default), and the Vontobel Fund and Migros Bank
umbrellas through their statutory reports (both off by default, see below).
Every other bank's fund documents sit behind a domicile or investor-type
gate, are disallowed by robots.txt, or are covered by terms that forbid
automated collection.

```bash
kedro run --pipeline=swiss                       # registry, stand-ins, coverage
OPENBASKET_ENABLE=swiss_vontobel,swiss_migros \
  kedro run --pipeline=swiss                     # also the report routes, after deciding to use them
```

Code: `openbasket.sources.swiss_banks` (bank catalogue and attribution),
`swiss_registry` (who runs which fund), `swiss_reports` (report parsers and
name matching), `swiss_fetch` (robots.txt-aware fetching) and the adapters
`sources/issuers/swiss_standin.py`, `swiss_vontobel.py` and `swiss_migros.py`;
pipeline `src/openbasket/pipelines/swiss/`, configured in
`conf/base/parameters_swiss.yml` and `conf/base/catalog_swiss.yml`.

## Publishing

Every module declares `REDISTRIBUTION`, and every output row carries a
`redistribution` column.

| Module / source | `REDISTRIBUTION` | `ENABLED_BY_DEFAULT` | Why |
|---|---|---|---|
| `swiss_registry`: GLEIF | public | yes | GLEIF data is CC0 1.0. |
| `swiss_registry`: ESMA FIRDS | public | yes | ESMA register data, reuse with acknowledgement. |
| `swiss_registry`: SIX | local_only | yes | Responses say "(c) Copyright by SIX Group Ltd. All rights reserved." |
| `swiss_registry`: FINMA | local_only | yes | FINMA's terms: "Any reproduction requires the prior written consent of the copyright holder." |
| `swiss_standin` | local_only | yes | iShares (BlackRock) site data; terms restrict republication. |
| `swiss_vontobel` | local_only | **no** | Reports behind an unsubmitted investor-type overlay; no republication licence. |
| `swiss_migros` | local_only | **no** | Reports behind an unsubmitted Swiss-residence overlay; no republication licence. |

`swiss_fund_registry_public` holds only the public rows. On GitHub Actions
(`GITHUB_ACTIONS=true`) the pipeline reads only GLEIF and FIRDS and runs no
holdings route, so a public build never fetches or writes restricted data.
The swiss outputs are not read by `publish`.

An adapter that is off by default returns no products and refuses
`holdings()` until it is switched on with `OPENBASKET_ENABLE=<name>` (comma
list) or the `swiss.enable` parameter; `OPENBASKET_DISABLE` switches any of
them off.

## How requests are made

All requests go through `openbasket.http` (project User-Agent, retries, one
request per second per host) via `swiss_fetch.get`, which first reads the
host's `robots.txt` with Protego (an RFC 9309 parser) and refuses any URL, or
redirect target, that it disallows. A `robots.txt` that cannot be read
disallows the site. No log-in, CAPTCHA, cookie banner, disclaimer, investor-type
or residence form is ever submitted. Downloads are cached per day under
`data/01_raw/swiss/` (gitignored): GLEIF API pages, the GLEIF ISIN-LEI file
(33 MB), the FIRDS `FULINS_C` file (4 MB), SIX lists, FINMA lists, report
PDFs and peer holdings.

## The registry: knowing the funds

`swiss_registry.build_registry()` writes `data/03_primary/swiss_fund_registry.parquet`
with columns `bank`, `attributed_by`, `isin`, `name`, `umbrella`, `manager`,
`manager_lei`, `fund_lei`, `domicile`, `currency`, `fund_type`, `legal_form`,
`product_line`, `replication`, `underlying`, `finma_id`, `valor`,
`listed_in`, `source`, `source_url`, `credit`, `redistribution`, `as_of`.

| Source | Endpoint | What it gives |
|---|---|---|
| GLEIF fund managers | `https://api.gleif.org/api/v1/lei-records/{manager LEI}/managed-funds?page[size]=200` | every fund (sub-fund) LEI a manager runs: legal name, jurisdiction, legal form, status |
| GLEIF ISIN-LEI | `https://mapping.gleif.org/api/v2/isin-lei/latest` (zip link) | share-class ISINs of those fund LEIs |
| ESMA FIRDS | index `https://registers.esma.europa.eu/solr/esma_registers_firds_files/select`, file `FULINS_C_<date>_01of01.zip` | EU-traded share classes whose issuer is one of those fund LEIs, with full name, CFI, currency |
| SIX Swiss Exchange | `https://www.six-group.com/fqs/ref.json?select=ISIN,FundLongName,IssuerNameFull,LegalStructureCountryCode,AssetClassDesc,ReplicationMethodDesc,FundUnderlyingDescription,ProductLine,TradingBaseCurrency,ValorNumber&where=PortalSegment=FU` | listed funds and ETFs with issuer, domicile, replication method and underlying index |
| FINMA | `https://www.finma.ch/en/~/media/finma/dokumente/bewilligungstraeger/xlsx/afch.xlsx` and `.../afetr.xlsx` | every authorised Swiss scheme (name, umbrella, FINMA ID, fund management company, custodian, legal form) and every foreign scheme approved for offering in Switzerland; no ISINs |

The fund-manager LEIs are in `swiss_banks.BANKS` (checked against the GLEIF
API): UBS Fund Management (Switzerland) AG 854 funds, UBS Asset Management
(Europe) S.A. 736, UBS Asset Management (UK) 42, Swisscanto Fondsleitung AG
304, Swisscanto Asset Management International 59, GERIFONDS 126 and
GERIFONDS (Luxembourg) 54, LUKB Expert Fondsleitung 21, Pictet Asset
Management SA 82 and (Europe) S.A. 267, Lombard Odier AM (Switzerland) 106 and
Lombard Odier Funds (Europe) 285, Vontobel Fonds Services 41 and Vontobel
Asset Management S.A. 64, UBP Asset Management (Europe) 146, J. Safra Sarasin
Investmentfonds 18 and Fund Management (Luxembourg) 59. Credit Suisse Funds
AG and Credit Suisse Fund Management S.A. now list no funds (merged into
UBS). Julius Bär, EFG, Raiffeisen, PostFinance, Migros Bank, Bank Cler,
Valiant and most cantonal banks have no fund management company of their own
in GLEIF: their funds are run by UBS, Swisscanto, Vontobel Fonds Services,
1741 Fund Solutions, LLB Swiss, Carne and others, and are attributed by the
brand in the fund or umbrella name.

**Attribution** (`swiss_banks.attribute`): the leftmost bank brand in the
fund or umbrella name wins ("Einkommen (CHF)" in umbrella "BKB Anlagelösung",
run by UBS Fund Management, is a BKB fund); only without a brand does the
manager decide. `attributed_by` says which: `brand` or `manager` (a fund
the bank's management company runs unbranded or for someone else, such as
UBS-run institutional funds of pension schemes).

A FINMA row (no ISIN) is linked to a GLEIF fund when the bank behind the
fund management company and the normalised sub-fund name are unique on both
sides; unlinked FINMA rows stay as name-only rows.

## Coverage by bank (run of 3 November 2026)

"Share classes" are registry rows with an ISIN; "portfolios" counts fund LEIs
plus unlinked FINMA schemes; "branded" counts share classes whose name carries
the bank's brand. "With holdings route" counts the share classes an adapter
lists.

| Bank | Share classes (ISIN) | Branded | Portfolios | FINMA schemes | SIX-listed | With holdings route | Route | robots.txt / gate / terms | `REDISTRIBUTION` | On by default |
|---|---|---|---|---|---|---|---|---|---|---|
| UBS (incl. Credit Suisse) | 3,199 | 2,094 | 2,234 | 919 | 469 | 76 | peer stand-in (physical index ETFs) | ubs.com: domicile/role gate; robots disallows `/bin/` (data) and `/2/` (documents); footer: personal viewing only | local_only | yes |
| ZKB / Swisscanto | 913 | 773 | 493 | 233 | 69 | 0 | none | terms forbid systematic access without written permission; robots disallows `/*.pdf` on products.swisscanto.com | – | – |
| BCV (GERIFONDS) | 281 | 165 | 189 | 100 | 10 | 0 | none | modal asks for country, investor profile and acceptance of the terms | – | – |
| Pictet | 705 | 640 | 459 | 160 | 53 | 0 | none | investor-profile gate; robots disallows `/*.pdf$` except `/*/discoverable/*.pdf$`; WAF | – | – |
| Lombard Odier | 285 | 85 | 523 | 164 | 7 | 0 | none | robots disallows `*/investment-funds/fund/` and `/files/` | – | – |
| Vontobel | 139 | 96 | 143 | 69 | 19 | 49 | annual / semi-annual report | robots allows; unsubmitted overlay asks country and investor type | local_only | **no** |
| UBP | 25 | 25 | 190 | 43 | 5 | 0 | none | fund pages redirect to `/en/asset-management/disclaimer` | – | – |
| J. Safra Sarasin | 120 | 65 | 112 | 51 | 8 | 0 | none | robots allows; pages rendered from an encrypted client-side config, no document links | – | – |
| Julius Bär | 0 | 0 | 32 | 32 | 0 | 0 | none | juliusbaer.com answers 403; funds run by third parties | – | – |
| EFG (New Capital) | 3 | 3 | 37 | 35 | 0 | 0 | none | newcapital.com answers 403 | – | – |
| Raiffeisen | 79 | 79 | 52 | 39 | 9 | 0 | none | download pages redirect to `/rch/de/disclaimer.html`; robots disallows dam PDFs outside `/rch/` | – | – |
| PostFinance | 1 | 1 | 1 | 1 | 1 | 0 | none | funds run by third parties; no document host | – | – |
| Migros Bank | 78 | 78 | 87 | 43 | 0 | 54 | semi-annual / annual report | robots allows; unsubmitted Swiss-residence overlay | local_only | **no** |
| Bank Cler | 16 | 16 | 16 | 8 | 0 | 0 | none | run by UBS FM; no documents on cler.ch | – | – |
| Valiant | 26 | 26 | 23 | 18 | 1 | 0 | none | run by Lombard Odier; robots disallows `/documents/` | – | – |
| LUKB | 32 | 32 | 21 | 18 | 0 | 0 | none | residence confirmation and acceptance of the legal notice | – | – |
| BKB | 53 | 53 | 33 | 17 | 0 | 0 | none | run by UBS FM; documents only on swissfunddata / fundinfo | – | – |
| BEKB | 113 | 113 | 42 | 20 | 4 | 0 | none | redirect to a domicile inquiry | – | – |
| St.Galler KB | 2 | 2 | 16 | 16 | 2 | 0 | none | run by 1741 Fund Solutions (publishes on fundinfo / swissfunddata only) | – | – |
| Zuger KB | 27 | 27 | 8 | 7 | 0 | 0 | none | residence confirmation; no documents in the page | – | – |
| Graubündner KB | 63 | 63 | 31 | 16 | 1 | 0 | none | run by 1741 and Swisscanto; no documents on gkb.ch | – | – |
| Thurgauer KB | 37 | 37 | 18 | 9 | 0 | 0 | none | run by Swisscanto | – | – |
| Aargauische KB | 42 | 42 | 38 | 19 | 0 | 0 | none | run by Swisscanto; robots disallows `/*.pdf/*` | – | – |
| Basellandschaftliche KB | 75 | 75 | 32 | 19 | 0 | 0 | none | run by Swisscanto, UBS FM, BLKB FM | – | – |
| Schwyzer KB | 72 | 72 | 19 | 18 | 0 | 0 | none | run by UBS FM | – | – |
| Other cantonal banks | 48 | 48 | 41 | 29 | 0 | 0 | none | white-label funds of Swisscanto, GERIFONDS, LLB Swiss | – | – |

The third-party document platforms do not help either: Swiss Fund Data
(SIX, the official publication platform of Swiss funds) requires an investor
category and acceptance of its legal terms before any search;
`www.fundinfo.com` and `api.fundinfo.com` disallow `/document/` (and
`/file/`) in robots.txt; Fundsquare disallows its download flows; LLB Swiss
disallows its fund documents; the Luxembourg Business Registers disallow
their API, through which filed accounts are downloaded.

## Holdings routes

### Peer stand-in for physical index ETFs (`swiss_standin`)

SIX lists 454 Swiss-bank ETFs (410 physical, 44 synthetic). A target is a
Swiss-bank ETF that SIX gives as `Physical`, whose underlying normalises to
one of 20 plain indices in `swiss_standin.INDEXES` (SMI, SLI, SMIM, SPI,
S&P 500, MSCI World / World ex USA / USA / EMU / EMU Small Cap / Europe / EM /
EM ex China / Japan / Canada / Australia / UK / Pacific ex Japan, EURO STOXX
50, FTSE 100) **and** whose name names that index, so ESG, SRI, "Universal",
equal-weight, factor and actively managed funds (the Pictet "AI Enhanced"
ETFs carry MSCI benchmarks) are never matched. Synthetic and commodity ETFs
are never stood in for. 76 share classes qualify, all UBS.

The peer is a physically replicating iShares ETF on the same index, found by
ISIN in the iShares UK or Swiss screener and read through
`openbasket.sources.ishares.holdings` (the Swiss site entry is added at run
time when the main `ishares` module lacks it). Rows are the peer's lines,
weights in percent of the **peer's** net assets, never rescaled; the as-of
date is the peer file's. The first note reads, for example:

> stand-in: index composition of the Swiss Market Index (SMI) (SIX) as held
> by iShares SMI ETF (CH) (CH0008899764), a physically replicating iShares
> ETF on the same index, positions as of 2026-11-02; used as a stand-in for
> UBS SMI ETF CHF dis (CH0017142719); not the fund's own holdings

SIX does not say whether a physical fund replicates fully or samples, and the
note says so; a sampled fund holds a subset. Hedged share classes also hold
currency forwards the peer does not show.

### Statutory reports (`swiss_reports`, `swiss_vontobel`, `swiss_migros`)

Reports print names, quantities, market values and percent of net assets to
two decimals; none of the reports seen prints ISINs. Three layouts are parsed
from pypdf text, sub-fund by sub-fund, and the positions date is read from
the report:

| Layout | Used by | Heading | Line |
|---|---|---|---|
| `lux_portfolio` | Vontobel Fund (LU) | "Portfolio as at February 28, 2026" | `2,575,890 BRL <name, may wrap> 77,296,538 1.97` |
| `ch_inventar` | Swiss funds run by UBS Fund Management (Switzerland) AG (Migros Bank (CH) Fonds) | "Inventar des Fondsvermögens" | `NOVARTIS AG CHF0.49 (REGD)* CHF 469 785 8 430 478 215 62 812 345 11.23` |
| `lux_aufstellung` | LU funds administered by UBS Asset Management (Europe) (Migros Bank (Lux) Fonds) | "Aufstellung der Wertpapierbestände und anderer Nettovermögenswerte per ..." | `EUR ERSTE GROUP BK AG NPV 607.00 58 116.69 0.03` |

Totals, cash and the summary lines are never positions. Asset class comes
from the headings (shares, bonds, investment funds, ...), and the country
heading is kept as an ISO code.

**Names to ISINs.** `NameResolver` matches *equity* lines only, against
SIX's share list (`PortalSegment=EQ`: issuer name and share type) and the
local security master (`data/03_primary/security_master.parquet`, N-PORT and
issuer files). Names are normalised (legal suffixes, nominal values such as
`CHF0.10`, `(REGD)`, share-class marks, German transliterations), a match
must be to a single issuer, the share type must agree (bearer, registered,
participation certificate, preferred: a preferred line never takes the
common share), and a listing in the country of the heading or currency beats
an ADR. Matched rows say `isin_source = name_match`. Bonds, fund units and
derivatives keep `isin = None`. On the Migros SwissStock fund, the 25 largest
lines all matched the right issuer; unmatched are truncated names
("ACCELLERON INDUSTR") and issuers missing from both lists.

**Vontobel.** `https://am.vontobel.com/en/view/{ISIN}` links the share
class's documents; the newest `Annual-Report_<date>_EN_Vontobel-Fund.pdf` or
`Semi-Annual-Report_<date>_EN_Vontobel-Fund.pdf` is read and the sub-fund is
found by its GLEIF legal name (LEI 529900LO1T9ADP03SQ41 manages 64 funds; 49
LU share classes are in the GLEIF ISIN map or FIRDS). Sub-funds of Vontobel
Fund II and the Variopartner SICAV are not in that report and fail with a
clear error. The 28 February 2026 semi-annual report (352 pages) parses into
40 sub-funds; equity sub-funds sum to 96.4-100.1% and 81-97% of their equity
lines are matched to ISINs.

**Migros Bank.** The fund page links the (CH) and (Lux) umbrella reports;
the newest of each is read and its table of contents gives every sub-fund's
share-class ISINs (16 + 6 sub-funds, 54 share classes). All 22 sub-funds
parse to 95.4-99.4% of net assets.

## Sample runs (3 November 2026)

`kedro run --pipeline=swiss` with `OPENBASKET_ENABLE=swiss_vontobel,swiss_migros`,
in 42 seconds (report PDFs and GLEIF files cached from the survey earlier
that day). Output in `data/08_reporting/swiss_sample_runs.csv`.

| Fund | Bank | Route | As of | Lines | Weight sum | ISIN weight |
|---|---|---|---|---|---|---|
| UBS SMI ETF CHF dis (CH0017142719) | UBS | stand-in: iShares SMI ETF (CH) | 2026-11-02 | 26 | 100.00% | 99.70% |
| UBS SLI ETF CHF dis (CH0032912732) | UBS | stand-in: iShares SLI ETF (CH) | 2026-11-02 | 35 | 100.00% | 98.96% |
| UBS SMIM ETF CHF dis (CH0111762537) | UBS | stand-in: iShares SMIM ETF (CH) | 2026-11-02 | 34 | 100.00% | 99.25% |
| UBS Core S&P 500 UCITS ETF USD dis (IE00B7K93397) | UBS | stand-in: iShares Core S&P 500 | 2026-11-02 | 509 | 100.00% | 99.86% |
| UBS Core MSCI EMU UCITS ETF EUR dis (LU0147308422) | UBS | stand-in: iShares Core MSCI EMU | 2026-11-02 | 224 | 100.00% | 99.67% |
| UBS Core MSCI Europe UCITS ETF EUR dis (LU0446734104) | UBS | stand-in: iShares Core MSCI Europe | 2026-11-02 | 398 | 100.00% | 99.50% |
| UBS Core MSCI Japan UCITS ETF JPY dis (LU0136240974) | UBS | stand-in: iShares MSCI Japan | 2026-11-02 | 175 | 100.00% | 98.97% |
| Vontobel Fund – mtx Emerging Markets Leaders (LU0571085413) | Vontobel | semi-annual report | 2026-02-28 | 56 | 98.49% | 90.26% (name match) |
| Vontobel Fund – US Equity (LU0035765741) | Vontobel | semi-annual report | 2026-02-28 | 40 | 99.48% | 95.94% (name match) |
| Vontobel Fund – Global Environmental Change (LU0384405600) | Vontobel | semi-annual report | 2026-02-28 | 64 | 99.86% | 89.35% (name match) |
| Migros Bank (CH) Fonds – SwissStock (CH0023406587) | Migros Bank | semi-annual report | 2025-12-31 | 112 | 99.17% | 88.31% (name match) |
| Migros Bank (CH) Fonds – EuropeStock (CH0023406629) | Migros Bank | semi-annual report | 2025-12-31 | 314 | 98.04% | 76.82% (name match) |
| Migros Bank (Lux) Fonds SwissStock (LU0261663909) | Migros Bank | semi-annual report | 2025-12-31 | 122 | 99.42% | 85.98% (name match) |

Thirteen funds from three banks. No other bank has a route that meets the
rules, so the target of five banks was not reached.

## Routes that need a decision or a permission request

For the person running openbasket to decide or to ask. Contacts are from
each bank's legal, imprint or contact page.

| Bank / platform | What would be needed | Contact |
|---|---|---|
| Vontobel | Decide whether reading the reports behind the unsubmitted country/investor-type overlay is acceptable, or ask Vontobel for permission to read and show them | Vontobel Holding AG, Gotthardstrasse 43, 8002 Zurich, vontobelgroup@vontobel.com (imprint) |
| Migros Bank | Same decision for the Swiss-residence overlay, or ask Migros Bank | Migros Bank AG, Seidengasse 12, 8001 Zurich; https://www.migrosbank.ch/de/ueber-uns/rechtliche-informationen.html |
| UBS Asset Management | Permission to read ubs.com fund documents and holdings (robots.txt, gate and "personal viewing" terms) or a data feed | contact form via https://www.ubs.com/global/en/legal.html |
| Swisscanto / ZKB | Written permission for systematic access (terms of use), for the holdings files and reports | Swisscanto Fondsleitung AG, Bahnhofstrasse 9, 8001 Zurich |
| SIX Swiss Fund Data | Permission or a licence to use the publication platform without its investor-category click-through; it carries the reports of almost every Swiss fund, including all cantonal-bank funds | SIX Financial Information / Swiss Fund Data, via https://www.swissfunddata.ch |
| FE fundinfo | Permission to fetch `/document/` (robots.txt disallows it); 1741 Fund Solutions, UBS and others publish there | FE fundinfo (Switzerland) AG, Staffelstrasse 12, 8045 Zurich, customersupport@fefundinfo.com |
| FINMA | Consent to republish the scheme lists (used locally now) | FINMA, Laupenstrasse 27, 3003 Bern, info@finma.ch |
| GERIFONDS (BCV) | Accepting its terms and investor-profile modal is the user's call; or ask | GERIFONDS SA, Rue du Maupas 2, Case postale, 1001 Lausanne, +41 21 321 32 00 |
| LUKB, Zuger KB, BEKB, Raiffeisen, UBP, Pictet | Their residence or investor-type gates are server-side or require accepting legal terms: ask each bank | legal pages: https://www.lukb.ch/ueber-uns/rechtliches, https://www.zugerkb.ch/rechtliche-hinweise/fidleg, https://www.bekb.ch/, direct@raiffeisen.ch, UBP Rue du Rhône 96-98 1211 Geneva, https://am.pictet.com/ch/en/intermediaries |
| 1741 Fund Solutions (St.Galler KB, Graubündner KB funds) | Their documents are only on fundinfo and swissfunddata | info@1741group.com |
| Lombard Odier, Valiant | robots.txt disallows the fund and document paths: ask | Lombard Odier Asset Management (Switzerland) SA, Geneva |

## Known gaps

- Report holdings are six to twelve months old (positions date of the
  latest report) and at two decimals; cash is the unexplained remainder.
- No report seen prints ISINs; name matching covers equities only.
- Wrapped names in the Swiss layout lose their first line ("KUEHNE&").
- Fund-of-funds lines (the Migros strategy funds hold Migros institutional
  funds and ETFs) keep `isin = None`; they could be matched to the registry
  by name in a later step.
- The stand-in covers UBS only; Swisscanto's ETFs are gold or SDG-index
  funds, Pictet's are actively managed, and the Swiss index funds that are not
  ETFs have no replication method on SIX.
- GLEIF's ISIN-LEI map is incomplete for some managers (UBP: 25 ISINs for
  190 funds), so the registry undercounts their share classes.
