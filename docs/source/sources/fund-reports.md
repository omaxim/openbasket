# Statutory fund reports

Several large issuers block automated access to their own websites: UBS
(Akamai), Invesco EMEA (Fastly, HTTP 406), Pictet (AWS WAF), Fidelity
(Akamai), WisdomTree Europe and Swiss Life (Cloudflare), and Franklin's
holdings sit behind an unpublished GraphQL query. Their funds still have to
publish their holdings: every UCITS fund publishes an **annual and a
semi-annual report** with the full **schedule of investments** of each
sub-fund, and Swiss funds publish the same inventory under the Collective
Investment Schemes Act (KAG). This source finds those reports by ISIN on
public hosts without a gate and reads the schedule out of the PDF.

**What a row from this source is.** It is the fund's holdings on the report
date, as printed in its annual or semi-annual report, not its current daily
holdings. Every result says so: the first note on each result is the kind
and the position date, for example `semi-annual report, positions as of
2026-06-30`, and the source link is the report PDF itself.

Modules: `openbasket.sources.fund_reports` (locators, download cache,
`lookup(isin, fund_name)`), `openbasket.sources.report_parser` (the PDF
schedule parser) and `openbasket.sources.issuers.statutory_reports`
(the registered adapters). The reports name holdings without ISINs; see
[Matching names to ISINs](#matching-names-to-isins).

**Redistribution: `local_only`.** The reports are the issuers' documents.
Their terms were reviewed on 2026-11-05 (see [Terms and credit](#terms-and-credit)):
every issuer whose reports are read bars republishing them without written
permission, so the adapters run only when you run openbasket yourself.
They are enabled by default for local runs and listed in `issuers.disabled`
in `conf/public`, so the public build never runs them.

## Adapters

| Adapter | Issuer family | Report hosts used |
|---|---|---|
| `ubs_reports` | UBS (Irl) ETF plc, UBS (Lux) Fund Solutions, UBS (Lux) Equity/Bond funds | AVL |
| `invesco_reports` | Invesco Markets plc, II, III | AVL |
| `pictet_reports` | Pictet (Luxembourg SICAV) | AVL |
| `wisdomtree_reports` | WisdomTree Issuer ICAV | WisdomTree's own document service, then AVL |
| `fidelity_reports` | Fidelity UCITS ICAV, UCITS II ICAV, Fidelity Funds | AVL |
| `franklin_reports` | Franklin Templeton ICAV and Luxembourg funds | AVL |
| `lombardodier_reports` | LO Funds (Luxembourg) | AVL |

Each adapter's fund list is the SIX Swiss Exchange list of funds and ETFs
(`https://www.six-group.com/fqs/ref.csv?...&where=PortalSegment=FU`, one
CSV, 3,088 rows), filtered on `IssuerNameFull` and kept to the share classes
a report route covers. Any other share class can be looked up directly with
`fund_reports.lookup(isin, fund_name)`; without a name, the fund name is read
from the share class's KID on AVL.

## Endpoints

AVL Investmentfonds document library
: `https://dl.avl-investmentfonds.de/sitemap.xml` lists twelve
  `sitemaps/sitemap-fds*.xml` files (about 4.3 MB and 35,000 URLs each). Each
  URL is `/fds/<ISIN>-<TYPE>-<LANG>-<YYYYMMDD>.pdf`. `TYPE` is
  `RECHENSCHAFT` (annual report), `HALBJAHRBER` (semi-annual report), `KID`,
  `PRIIP` or `VERKPROSP` (prospectus); the date is the report's **period
  end**, so the position date is known before downloading. The library keeps
  the latest report of each type per ISIN. On 3 November 2026 it listed
  414,584 files for 88,514 ISINs, 87,452 of them with a report (86% with a
  2026 report), mostly LU and IE; in the first of the twelve files 72% of
  the URLs were LU, 17% IE and only 35 CH. The index is
  rebuilt weekly (twelve requests) and cached as
  `avl_reports_index.parquet`. The reports are the issuers' own documents;
  for UBS (Irl) ETF plc and Franklin Templeton ICAV only the German edition
  is listed.

WisdomTree document service
: `https://dataspanapi.wisdomtree.com/pdr/documents/<TYPE>/UCITS/GB/EN-GB/<ISIN>/`
  with `TYPE` `ANNUAL_REPORT` or `SEMI_ANNUAL_REPORT` returns the umbrella
  report as a PDF attachment whose file name carries the fund name
  (`SEMI_ANNUAL_REPORT-EN-GB-IE00BZ56RN96-WisdomTree Global Quality Dividend Growth UCITS ETF - USD.pdf`).
  This is WisdomTree's own host and is not behind the Cloudflare challenge
  that blocks `www.wisdomtree.eu`. It sends `Last-Modified`, which orders the
  two reports; it has no `robots.txt` (404).

extraETF's document links (`docs.extraetf.com/download/<ISIN>/...`) were
used for Swiss ETFs in the first version. **They are not used any more**:
extraETF's terms forbid automated querying, even though its `robots.txt`
allows `/download/`. Swiss-domiciled ETFs such as `UBS ETF (CH)` therefore
have no report route (AVL lists almost no CH ISINs).

How an ISIN reaches its holdings:

1. Find the share class's reports (WisdomTree service for WisdomTree, else
   the AVL index) and order them newest period first, English before German.
2. Download one (at most four per fund), extract the text of every page
   with `pypdf`, and cache the page texts gzipped (the PDF is not kept). AVL
   and WisdomTree serve the same umbrella report once per share class; a
   cached copy with the same host, kind, language, period and domicile that
   contains the sub-fund is reused instead of downloading it again (the
   second UBS Core S&P 500 share class took 1.6 s, the second WisdomTree
   fund 1.6 s).
3. Find the schedule pages (`Schedule of Investments`, `Statement of
   investments and other net assets`, `Inventory of Fund assets`,
   `Aufstellung des Wertpapierbestandes`, `Aufstellung der
   Wertpapierbestände`, `Anlagenportfolio`, `Inventaire du portefeuille`,
   ...), split them into sub-funds by the titles printed on them, and pick
   the title that is this fund's **both ways**: every word of the title is in
   the fund name, in order, and every word of the fund's own name (share-class
   words such as `USD acc`, `hEUR`, `PfHdg`, currencies and legal forms
   aside) is in the title. An umbrella prefix may be missing on either side
   (`UBS ETF (CH) –`, `Lombard Odier Funds SICAV -`). Numeric ranges are
   whole words, so `TIPS 1-10` is not `TIPS 10+`, and a sibling whose title
   is only part of the name (`UBS MSCI World` for `UBS MSCI World ex USA`) is
   refused. A fund that is not in the report is never guessed: the next
   report is tried.
4. Parse each line as `[quantity] name [currency] [quantity] [value]
   percent`. Totals, sub-totals, unrealised gain/loss summaries and the
   analysis of total assets are skipped; cash, bank overdrafts, other net
   assets and current assets and liabilities are kept as residual lines
   (`asset_class` `cash` or `other`, `residual` true). Section headings set the
   asset class of the lines below them, including headings that carry their
   weight (`Investment Funds: 0.10% (30 September 2025: 0.08%)`); country
   headings (`Belgium: 0.79% (...)`, `Japan`) set `country`. Forward currency
   contracts (`Buy ... USD to Sell ... GBP`) are derivatives, liquidity funds
   and ETFs are funds, and a futures description wrapped over several lines
   above its amounts keeps its name. Parsing stops at the net-assets total.
5. Accept the report when the lines sum to 97-103% of net assets if the
   schedule ends with a net-assets total, or to 50-150% if it has none (some
   layouts leave cash to the statement of net assets); otherwise try the
   next one. A schedule with no net-assets total, or outside 97-103%, has
   `complete` false and a note.

The frame has `HOLDING_COLUMNS` plus `quantity`, `market_value`, `residual`
and `page` (the PDF page the line is on). `weight_pct` is the report's
percent of net assets, never rescaled. Identifiers are only those printed in
the line and passing their check digit.

**Swiss inventories** print each line's percent of **total** fund assets,
not net assets. When the inventory ends with `Total fund assets` and `Net
fund assets`, each line's weight is restated as its market value divided by
net fund assets (or, where the market value is missing or disagrees with the
printed percent, the printed percent times total / net assets). This is a
change of basis, not a rescaling to 100: the lines then sum to a little over
100, by the liabilities. Without both totals the printed weights are kept,
`weight_basis` is `total_assets` (on the result and in its stats) and a note
says so; otherwise `weight_basis` is `net_assets`.

## What the data shows (3 November 2026)

Real runs on 3 November 2026, fifteen funds from seven families. Seconds are
for the first run, including the download (a report already cached, or the
same umbrella report already read for another share class, takes one to five
seconds):

| Fund | Report, positions as of | Lines | Sum of % | Lines with ISIN | Seconds |
|---|---|---|---|---|---|
| UBS Core MSCI World UCITS ETF (IE00BD4TXV59) | semi-annual, 2026-06-30 (German edition, AVL) | 1,603 | 99.85 | 0% | about 35 (30 of them text extraction, 1,043 pages) |
| UBS Core S&P 500 UCITS ETF (IE00BD4TXW66) | same report, reused | 533 | 100.02 | 0% | 1.6 |
| UBS MSCI World Socially Responsible UCITS ETF (LU0950674332) | semi-annual, 2026-06-30 (AVL) | 365 | 100.00 | 0% | 20.1 |
| UBS ETF (CH) – UBS SMI ETF (CH0017142719) | annual, 2026-06-30 (extraETF, route since removed) | 23 | 99.99\* | 0% | 8.1 |
| UBS (Lux) Equity Fund – Biotech (LU0069152568) | semi-annual, 2026-05-31 (German edition, AVL) | 46 | 100.00 | 0% | 11.3 |
| UBS (Lux) Bond Fund – Euro High Yield (LU0086177085) | annual, 2026-03-31 (German edition, AVL) | 299 | 99.41 | 0% | 49.5 |
| Invesco MSCI World UCITS ETF (IE00B60SX394) | semi-annual, 2026-05-31 (AVL) | 385 | 100.00\*\* | 0% | 22.4 |
| Invesco Artificial Intelligence Enablers UCITS ETF (IE000LGWDNE5) | semi-annual, 2026-06-30 (AVL) | 51 | 100.00 | 0% | 1.7 (cached) |
| WisdomTree Global Quality Dividend Growth UCITS ETF (IE00BZ56RN96) | semi-annual, 2026-06-30 (WisdomTree service) | 724 | 100.00 | 0% | 130.8 |
| WisdomTree Artificial Intelligence UCITS ETF (IE00BDVPNG13) | semi-annual, 2026-06-30 (WisdomTree service) | 67 | 100.00 | 0% | 67.9 (1.6 reused) |
| Pictet – Biotech (LU0255977455) | semi-annual, 2026-03-31 (AVL) | 70 | 100.00 | 0% | 37.6 |
| Fidelity Global Quality Income UCITS ETF (IE00BYXVGZ48) | semi-annual, 2026-07-31 (extraETF, route since removed)\*\*\* | 475 | 99.58 | 0% | 15.1 |
| Franklin FTSE Emerging Markets UCITS ETF (IE0004I037N4) | annual, 2025-06-30 (German edition, AVL)\*\*\*\* | 1,472 | 99.95 | 0% | 165.0 |
| LO Funds – Convertible Bond (LU0159201655) | semi-annual, 2026-03-31 (AVL) | 163 | 95.76\*\*\*\*\* | 0% | 16.2 |

\* Swiss inventories print percent of **total fund assets**, not net assets;
restated to net assets this is 100.94%. \*\* A swap-based ETF: the substitute basket plus the
swaps; the result says so. \*\*\* AVL files the Fidelity UCITS II ICAV
semi-annual report under this ISIN, which does not contain the fund; AVL's
annual report (2026-01-31, 675 lines, 99.62%) parsed, and extraETF's newer
semi-annual report was then preferred. Without extraETF the annual report is
used. \*\*\*\* The
German semi-annual edition on AVL lost its digits (broken font map) and the
English one on extraETF is letter-spaced with columns run together, so the
older annual report was used. \*\*\*\*\* LO schedules have no net-assets
line; cash is in the statement of net assets and stays unresolved.

Parse quality: wherever the schedule ends with a net-assets total the lines
sum to 99.4-100.02% of net assets. Sub-funds were matched by name in every
umbrella tried (up to 121 sub-fund titles in one Invesco report, 66 in the
UBS (Irl) ETF plc report), and a fund missing from a report was never
guessed.

**No ISINs.** None of the UBS, Invesco, WisdomTree, Pictet, Fidelity,
Franklin or LO schedules tried prints an ISIN per line (Pictet and LO print
share-class ISINs elsewhere in the report). Lines keep their name, quantity,
market value, currency and country heading, and `isin` is `None`; the parser
does pick up any ISIN printed in a line. The look-through then matches the
names to ISINs, below.

## Matching names to ISINs

With `lookthrough.name_matching: true` (the default for local runs; off in
`conf/public`), every adapter line that carries no identifier at all is
given to `openbasket.matching`, which gives it an ISIN only when exactly one
security fits:

- **Candidates**: the security master (every ISIN N-PORT filings hold, with
  the names and titles they give it and the N-PORT country), its
  `security_terms` (currency, and for bonds coupon and maturity, from N-PORT's
  `DEBT_SECURITY` table), equity names in the OpenFIGI cache, a FIRDS extract
  if `lookthrough.name_matching_firds` names one, and the holdings file of a
  peer fund: for a fund named after a plain index (`UBS Core MSCI World`, an
  SMI or S&P 500 ETF) the largest iShares or SPDR fund on the same index
  (never a swap-based one), or the funds listed for it in
  `lookthrough.name_matching_peers`.
- **Names** are compared after dropping accents, punctuation, legal forms
  (AG, SA, PLC, Inc, Corp, NV, SE, Ltd ...), share descriptors (`Reg`,
  `Namen`, `CHF0.10`, `NPV`, `144A`) and expanding abbreviations (`HLDGS`,
  `INTL`). Share classes (`A`, `B`, `'H'`, `Class C`, `Pref`, `PRP`, bearer)
  and depositary receipts are kept apart, and a line that names no class is
  matched only to an ordinary share of a company that has no other line under
  that name.
- **Equities** match on the name and, where the report gives them, the
  country heading (against the N-PORT country or the ISIN's prefix) and the
  currency. A peer's line must have about the same weight.
- **Bonds** match on the issuer's name, the coupon and the maturity date, and
  the currency where printed; of a bond's Reg S and 144A tranches the one
  whose own name says it is the other tranche is ruled out. Floating, zero
  and perpetual notes are not matched.
- **Never a guess**: two ISINs that fit equally leave the line unresolved
  (Alphabet without a class, Roche's participation certificate and bearer
  share, an old and a new ISIN after a corporate action). An ISIN two lines
  of one fund would share, or that another line already reports, is taken
  away again. A peer's file is today's, so it does not overrule another ISIN
  filed under the same plain name: the report may predate a corporate action.

Matched rows say `isin_source` `name_match` and carry a `match_score`
between 0.9 and 1 (1.0 for an exact name confirmed by country, currency or a
peer's weight; less for reordered or abbreviated names or a match with
nothing to confirm it). The fund's note gives the share of net assets
matched by name.

Measured on the cached reports on 3 November 2026 (weights in percent of net
assets; "securities" is the equity and bond lines, the rest being cash,
derivatives, funds and other net assets):

| Fund | Lines | Securities | With ISIN before | After | With an index peer |
|---|---|---|---|---|---|
| UBS Core MSCI World UCITS ETF (IE00BD4TXV59) | 1,603 | 99.15 | 0 | 80.38 | 89.55 (iShares Core MSCI World) |
| UBS ETF (CH) – UBS SMI ETF (CH0017142719)\* | 23 | 99.68 | 0 | 75.78 | no peer listed |
| Invesco Artificial Intelligence Enablers UCITS ETF (IE000LGWDNE5) | 51 | 99.93 | 0 | 85.70 | no index |
| Pictet – Biotech (LU0255977455) | 70 | 98.44 | 0 | 88.57 | no index |
| WisdomTree Global Quality Dividend Growth UCITS ETF (IE00BZ56RN96) | 724 | 99.59 | 0 | 90.80 | no index |
| UBS (Lux) Bond Fund – Euro High Yield (LU0086177085) | 299 | 91.10 | 0 | 28.85 | no index |
| Invesco MSCI World UCITS ETF (IE00B60SX394, substitute basket) | 385 | 100.07 | 0 | 84.40 | 90.07 |
| WisdomTree Artificial Intelligence UCITS ETF (IE00BDVPNG13) | 67 | 99.96 | 0 | 77.30 | no index |
| Fidelity Global Quality Income UCITS ETF (IE00BYXVGZ48) | 475 | 99.05 | 0 | 77.12 | no index |
| UBS (Lux) Equity Fund – Biotech (LU0069152568) | 46 | 99.40 | 0 | 54.86 | no index |
| LO Funds – Convertible Bond (LU0159201655) | 163 | 86.31 | 0 | 39.49 | no index |

\* Read from the copy of its annual report fetched before the extraETF route
was removed; it has no route now. Weights restated to net assets.

Building the matcher takes about 6 seconds; matching a fund then takes
milliseconds. What stays unresolved is mostly companies with several lines
under one name (share classes, a new ISIN after a corporate action), names
no candidate source knows (small caps and bonds no US fund holds), short
names with nothing to confirm them, and in bond funds floating-rate and
perpetual notes and Reg S / 144A pairs that cannot be told apart.

**Precision.** Two random samples of 30 matches (five from each of the first
six funds above, 58 different lines) were checked by hand against the
report's own name, the ISIN's N-PORT names and its OpenFIGI name, ticker and
exchange, and for bonds coupon and maturity: all 58 are right. Independently,
the 1,021 matches made for UBS Core MSCI World *without* a peer were compared
with iShares Core MSCI World's own file: 979 of the ISINs are in it; the only
match where iShares holds another ISIN under the same name is Hologic, whose
iShares line is now the contingent value right that replaced the share after
the report date (the match, to the share, is right for June); the 41 others
are companies no longer in the index in November (RPM International, Kenvue
and the others checked are the right ISINs). Errors found while building the
matcher, and the rule that now prevents each: an ADR filed under the
company's own name (Porsche AG; a US-numbered ISIN of a foreign company is
treated as a receipt), an ordinary share where the index holds the
preference share (Sartorius; a line without a class is not matched when the
company has another line), class B where the index holds class A (First
Citizens; the same rule), a newer ISIN after a corporate action taken from
the peer's file (Exxon Mobil, Honeywell, UPM; the peer guard), and the
voting and non-voting Schindler lines both given the participation
certificate (the peer guard and the collision rule).

## Coverage

SIX-listed share classes with a report route (3 November 2026), matched on
`IssuerNameFull`:

| Adapter | SIX share classes | With an AVL report | In `products()` | Portfolios |
|---|---|---|---|---|
| `ubs_reports` | 469 | 386 | 386 | 201 |
| `invesco_reports` | 167 | 162 | 162 | 142 |
| `pictet_reports` | 53 | 35 | 35 (Pictet ICAV ETFs, launched 2025, have no report yet) | 22 |
| `wisdomtree_reports` | 61 | 60 | 61 (WisdomTree's service) | 45 |
| `fidelity_reports` | 72 | 67 | 67 | 48 |
| `franklin_reports` | 75 | 75 | 75 | 62 |
| `lombardodier_reports` | 8 | 7 | 7 | 4 |
| Swiss Life | 2 | 0 | not registered | |

793 share classes in all (825 while Swiss ETFs were read through extraETF).
Beyond SIX, `lookup` works for any of the 87,452
ISINs with a report in the AVL index, given a fund name or a KID to read it
from.

## Freshness

Semi-annual: an annual report appears about four months after the financial
year end and a semi-annual report about two months after the half-year, so
positions are two to ten months old. The AVL file name and the report's own
schedule header give the position date, which is used as `as_of`.

## Terms and credit

The reports are statutory documents each fund must publish. Every row is
credited to the issuer's annual or semi-annual report and links the PDF it
was read from. AVL Investmentfonds' `robots.txt` allows `/fds/` and
`/sitemaps/` for all agents and shuts out a list of named AI crawlers.
This client identifies itself as `openbasket`, which the
rules for all agents allow; whether to rely on AVL at all is the call of the
person running openbasket, which is one reason these adapters are
`local_only`. WisdomTree's document service has no `robots.txt`. AVL's
legal notice and imprint say nothing about reuse; the reports on it are the
issuers' documents. The client
keeps to one request per second per host, reads one report per umbrella and
period, and keeps only the extracted text.

Reviewed on 2026-11-05, the website terms of every issuer whose reports are
read bar reproducing or republishing their content without prior written
permission, with no exception for non-commercial use:

| Issuer | Terms page | What it says |
|---|---|---|
| UBS | `ubs.com/global/en/legal.html` | no use beyond personal viewing, "whether for commercial purposes or otherwise" |
| Invesco | `invesco.com/emea/en/insights/footer/site-policies.html` | no reproduction or transmission; personal, informational use only |
| Pictet | `am.pictet.com/be/en/terms-and-conditions` | no reproduction, transmission or public or commercial use |
| WisdomTree | `wisdomtree.com/gb/terms-and-conditions` | no copying, publishing or distributing without written consent |
| Fidelity International | `fidelityinternational.com/important-legal-information` | must not be reproduced or circulated without permission |
| Franklin Templeton | `franklintempleton.lu/footer/important-legal-information` | not to be copied, reproduced or redistributed |
| Lombard Odier | `am.lombardodier.com/professional/disclaimers/legal-information.html` | no reproduction or public use without written permission |
| DWS (Xtrackers) | `etf.dws.com/en-lu/footer/terms-of-use/` | publication or distribution needs prior written consent |
| Legal & General | `am.landg.com/terms-and-conditions/` | no publishing, re-distributing or reproducing |
| SIX Swiss Exchange | `six-group.com/en/services/legal/terms-and-conditions/terms-of-use.html` | not to be reproduced or reused in any way |
| Migros Bank | `migrosbank.ch` Nutzungsbedingungen (2021) | any republication strictly prohibited without consent |
| Vontobel | `vontobel.com/en-ch/legal-notice/terms-and-conditions-of-use/` | no reproduction without prior written consent |

None of these pages mentions automated access. The reports stay
`local_only` unless an issuer gives permission.

Not used:

- **extraETF** (`docs.extraetf.com/download/...`): its terms forbid automated
  querying.
- **fundinfo** (`api.fundinfo.com/document/...`): `robots.txt` disallows
  `/document/` for all agents.
- **swissfunddata.ch**: needs an investor-type disclaimer to be accepted.
- **Fidelity's** `fidelityinternational.com/legal/documents/...` report
  links and **Invesco's** `etf.invesco.com/sites/default/files/documents/...`:
  behind the same Akamai and Fastly blocks as their sites.
- **ubs.com** and **swisslife-am.com**: Akamai and Cloudflare blocks.

## Gaps

- **Swiss Life**: no route. Its two SIX-listed real-estate funds are not on
  AVL, its site is behind Cloudflare and swissfunddata.ch needs a
  disclaimer click. Swiss Life is therefore not registered as an adapter.
- **Swiss funds** (UBS ETF (CH), UBS (CH) equity funds, Swiss Life,
  Lombard Odier (CH) funds): AVL lists only 35 CH ISINs; their reports are on
  swissfunddata.ch behind the disclaimer, and extraETF is not used.
- **No text layer**: some reports are printed to PDF without a usable text
  layer (UBS (Irl) ETF plc's 2025 annual report on extraETF uses fonts with
  no Unicode map) or, like Franklin's German semi-annual report, lose their
  digits. They are detected and skipped; OCR is not attempted.
- **Swap-based ETFs** (Invesco MSCI World) report the substitute basket and
  the swap; the result notes it.
- **Space-separated thousands** (Swiss and LO reports) are joined by
  heuristics; a quantity directly followed by a value can occasionally be
  read as one number, so `quantity` is left empty when the columns are
  ambiguous. Weights are not affected.
- **Wrong document on the host**: AVL occasionally files another umbrella's
  report under an ISIN (Fidelity, above). The sub-fund is then not found and
  the next report is used.
- **The newest report may be skipped** when it does not parse (Franklin): an
  older one is used, after the other hosts were asked for a newer one that
  parses.
- **Letter-spaced text**: some editions (Franklin's English semi-annual
  report) are typeset letter by letter, and `pypdf` runs the value and weight
  columns together. Reading character positions (for example with
  `pdfplumber`) would recover them; not built.
