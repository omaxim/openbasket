# Funds whose issuer blocks holdings in robots.txt

Some issuers publish daily holdings, but only from paths their `robots.txt`
disallows. openbasket does not request those paths at all, not even locally.
This page records which funds that affects and the legitimate routes that now
cover them. Status as tested on 3 November 2026.

Every route here is **local only** (`REDISTRIBUTION = "local_only"`): the
holdings come from the issuers' statutory reports, which are their copyright
documents and carry no open licence. They run when you clone the repository
and run openbasket yourself; the public site shows these funds as
"holdings available when you run openbasket locally".

## Which funds are blocked

From the issuer adapter branches (`git branch --list 'src/*'`, the
`docs/source/sources/*.md` page on each), these issuers reported a
robots.txt block on the paths that serve holdings:

| Issuer | Host and disallowed paths | Funds affected | Covered here |
|---|---|---|---|
| Xtrackers (DWS) | `etf.dws.com`: `/api/`, `/etfdata/*`, `/export/*` | 474 IE and LU UCITS share classes (sitemap count) | yes, `xtrackers_reports` |
| L&G | `fundcentres.landg.com`: `/srp/api/`, `/srp/documents/`, `/srp/lit/` | 77 ETF share classes (also 545 ICAV and SICAV mutual-fund classes) | yes, `lgim_reports` (ETFs by default) |
| UBS | `www.ubs.com`: `/bin/`, `/2/`, `/*content/dam/ubs` (plus a terms gate) | about 371 ETF classes | no: handled on `src/fund-reports` and the Swiss-bank work |
| WisdomTree | `www.wisdomtree.com`/`.eu`: `/api` (plus Cloudflare) | about 61 UCITS classes | no: `src/fund-reports` reads WisdomTree's own document host |
| ARK Invest | `assets.ark-funds.com`: `Disallow: /` | US ETFs; about 10 UCITS classes | US funds come from SEC N-PORT; UCITS not built |
| Global X (US) | `globalxetfs.com`: `/api/` | US ETFs | SEC N-PORT |
| Swisscanto | `products.swisscanto.com`: `/*.pdf`, `/rest/` (and terms) | Swiss funds | no: Swiss-bank work |
| First Trust | `ftportfolios.com`: `/Common/` | none: the adapter uses allowed pages | n/a |

## Routes, in order of preference

1. **Statutory reports (built).** Every UCITS fund publishes an annual and a
   semi-annual report with the full schedule of investments of each sub-fund.
   The AVL Investmentfonds document library files each share class's latest
   reports under its ISIN, at a path its robots.txt allows.
2. **Third-party open holdings.** None found: European exchanges publish no
   free creation baskets, and index providers' full constituent weights are
   licensed (see the exchange-index page on `src/exchange-index`).
3. **A physical sibling on the same index (built, narrowly).** Only for
   sub-funds too new to have a report, and only where an Xtrackers sub-fund
   on the same index is read from its own report (`STAND_INS`, see below).

### Hosts checked

Each host's robots.txt was read with `protego`, which follows RFC 9309
(`openbasket.sources.robots`; Python's `urllib.robotparser` stops at the
first blank line and ignores `*`, so it reports `/etfdata/*` on etf.dws.com
as allowed). Agent token: `openbasket`.

| Host | robots.txt | Paths used | Used |
|---|---|---|---|
| `etf.dws.com` | 200; disallows `/api/`, `/etfdata/*`, `/export/*`, `/EntryGate/` and others | `/{culture}/sitemap.xml` (allowed) | fund list only |
| `dl.avl-investmentfonds.de` | 200; allows `/fds/`, `/pdf/`, `/sitemap.xml`, `/sitemaps/`, disallows the rest; shuts out named AI-training crawlers (`GPTBot`, `Google-Extended` and others) | `/sitemap.xml`, `/sitemaps/sitemap-fds*.xml`, `/fds/<ISIN>-<TYPE>-<LANG>-<DATE>.pdf` | reports |
| `fundcentres.landg.com` | 200; disallows `/srp/api/`, `/srp/documents/`, `/srp/lit/`, `/srp/literature/`, `/resources/literature/` | four `/{lang}/{country}/{audience}/sitemap.xml` files (allowed) | fund list only |
| `download.dws.com` | 404 (no file: all allowed) | none: document links are only found through the disallowed API | no |
| `www.dws.com`, `funds.dws.com` | 200; disallow `/EntryGate/`, `/api/` (funds) and others | none: no Xtrackers report index outside the entry gate | no |
| `docs.extraetf.com` | redirects to `extraetf.com/robots.txt`, `Allow: /` | none needed | no |
| `www.avl-investmentfonds.de` | 200; same AI-crawler groups | none | no |

No log-in, CAPTCHA, cookie or consent gate is involved on any path used. The
DWS literature endpoint that is allowed (`/product_literature/api/v1/...`)
returns nothing without the site's audience and entry-gate selection, which
openbasket does not make, so it is not used.

## How a fund ISIN reaches its holdings

Modules:

- `openbasket.sources.robots`: RFC 9309 checks, cached per host per day;
  every request in these modules goes through `robots.get` and then
  `openbasket.http.get` (one request a second per host).
- `openbasket.sources.avl_documents`: the AVL index (twelve sitemap files,
  about 174,000 report URLs, refreshed weekly), report download and cache.
- `openbasket.sources.schedule_tables`: finds sub-fund schedules and parses
  them.
- `openbasket.sources.name_isin`: adds ISINs by name from the security master.
- `openbasket.sources.report_route`: the steps shared by the adapters.
- `openbasket.sources.issuers.xtrackers_reports` and `.lgim_reports`: the
  registered adapters.

Steps:

1. **Fund list.** Xtrackers: the `en-gb`, `de-de`, `en-ch` and `en-lu`
   sitemaps of etf.dws.com list product pages
   `/{culture}/{ISIN}-{slug}/`; IE and LU ISINs are kept (474). The slug up
   to `ucits-etf` is the sub-fund key, shared by its share classes. L&G: the
   fund-centre sitemaps list `/fund-centre/{ETF|ICAV|SICAV}/{Name}/{ISIN}/`.
2. **Report.** The share class's AVL reports, newest period first, English
   before German. A cached umbrella report of the same kind, period and
   domicile that contains the sub-fund is reused (AVL files the same umbrella
   PDF under every ISIN). A share class AVL does not list uses a sibling
   class's report. At most three downloads per fund and twelve per process
   (`OPENBASKET_MAX_REPORT_DOWNLOADS`).
3. **Sub-fund pages.** From the report's contents (title and printed page,
   mapped to PDF pages by the printed page numbers), or, where the contents
   list only "Schedule of Investments" (L&G), from the sub-fund name each
   schedule page repeats above its heading. A fund is matched to a title by
   comparing letters and digits only, which survives the split words
   pdf text extraction leaves ("SW AP UCITS ETF"); a fund that is not in the
   report is never guessed.
4. **Schedule.** pdfplumber gives each line with its font. Bold lines are
   headings (asset class, country, industry) or totals; regular lines are
   positions; a regular line without numbers is the first half of a wrapped
   name. Two shapes are read: `qty name value pct` (Irish plc reports) and
   `name qty CCY cost value pct` (DWS Luxembourg reports). Share-class FX
   hedges are kept as one line from the report's "net unrealised" total; the
   "other net assets" line is kept as a residual line. Parsing stops at the
   net-assets total. A parse is used only if it found lines summing to 50 to
   150%.
5. **ISINs.** The reports print none. Equity lines are matched by
   normalised issuer name to openbasket's security master
   (`data/03_primary/security_master.parquet`, built from SEC N-PORT, public
   domain), only where one ISIN clearly dominates; `isin_source` is
   `name_match`. Bonds are not matched, so their weight stays unresolved.

`weight_pct` is the report's percent of net assets, never rescaled. Extra
columns: `quantity`, `market_value`, `residual`, `group` (the heading above
the line), `page` and `isin_source`.

Every result's first note is the kind and date, for example
`semi-annual report, positions as of 2026-06-30`, followed by the route
(why the issuer's own holdings are not used), the sub-fund title, umbrella,
edition and PDF pages, and the source PDF with its credit. Swap funds get a
note that the schedule is the substitute basket; Luxembourg schedules, which
end at total investments, get a note that cash and other net assets are
unresolved.

### Stand-ins

`xtrackers_reports.STAND_INS` maps a sub-fund with no report yet to an
Xtrackers sub-fund on the same index. It holds one entry: Xtrackers STOXX
Europe 600 II UCITS ETF (IE00003M6Z62, IE0000QMH1B4, launched after the last
report) uses the report holdings of Xtrackers STOXX Europe 600 UCITS ETF
(LU0328475792). The first note then reads "holdings of ... on the same index
(STOXX Europe 600), used as a stand-in; ...; not the fund's own holdings".
Swap funds are never mapped.

## What a real run showed (3 November 2026)

Every Xtrackers share class in the sitemaps and every L&G ETF share class was
run through the adapters. The reports read were the 2026-06-30 semi-annual
editions for Xtrackers (German; AVL lists the English edition for one
annual report only) and the 2026-06-30 annual report for L&G (English).

### Coverage

| Issuer | Blocked share classes | With holdings | Statutory report | Stand-in | No route |
|---|---|---|---|---|---|
| Xtrackers (DWS) | 474 (211 IE, 263 LU) | **460** (200 IE, 260 LU) | 458 | 2 | 14 |
| L&G ETFs | 77 | **74** | 74 | 0 | 3 |
| Total | 551 | **534** | 532 | 2 | 17 |

By sub-fund: 280 of 288 Xtrackers sub-funds and 54 of 56 L&G ETF sub-funds.

The 17 without a route are sub-funds launched after the last report date,
so no schedule exists yet: Xtrackers Global Corporate Bond (4 classes),
Global High Yield Corporate Bond (3), EURO STOXX (2), Eurozone Government
Bond 1-10 (1), EUR Investment Grade and EUR High Yield Corporate Bond Active
(1 each), Scalable MSCI AC World Leveraged Daily Swap (1), the World Net Zero
Pathway Paris Aligned UCITS CCF (1, not an ETF and not in the ETF reports);
L&G LSF African Government Bond (1) and WTW Global Equity Diversified (2).
They will be picked up by the next semi-annual report. The two stand-ins are
the STOXX Europe 600 II share classes.

L&G's 545 ICAV and SICAV share classes (mutual funds, also behind
`/srp/api/`) were not run; set `OPENBASKET_LGIM_TYPES=ETF,ICAV,SICAV` to
include them. ARK's UCITS ETFs (about 10 classes; the latest AVL report is
the 2025-06-30 annual) were not built.

Of the 534 results, weights sum to between 96.6% and 105.9%. Sums under
100% are Luxembourg schedules that stop at total investments (cash and
other net assets are elsewhere in the report); sums over 100% are
Luxembourg swap funds whose substitute basket is worth more than the fund
because the swap is valued negative. ISIN coverage depends on the asset
class: for funds with equity lines the median is 95% of net assets by name match (10th percentile 86%, over 340 results), bond funds 0%
(their lines keep name, coupon and maturity only).

### Sample runs

| Fund | Route | As of | Lines | Weight sum | With ISIN |
|---|---|---|---|---|---|
| Xtrackers MSCI World UCITS ETF 1C (IE00BJ0KDQ92) | semi-annual report | 2026-06-30 | 1,293 | 100.00% | 93.79% |
| Xtrackers MSCI Emerging Markets UCITS ETF 1C (IE00BTJRMP35) | semi-annual report | 2026-06-30 | 1,212 | 100.00% | 80.91% |
| Xtrackers MSCI World Swap UCITS ETF 1C (LU0274208692) | semi-annual report (substitute basket) | 2026-06-30 | 565 | 98.95% | 88.66% |
| Xtrackers EURO STOXX 50 UCITS ETF 1D (LU0274211217) | semi-annual report | 2026-06-30 | 54 | 99.86% | 97.78% |
| Xtrackers USD Corporate Bond UCITS ETF 1D (IE00BZ036H21) | semi-annual report | 2026-06-30 | 3,333 | 100.00% | 0% |
| Xtrackers II Germany Government Bond UCITS ETF 1C (LU0643975161) | semi-annual report | 2026-06-30 | 70 | 98.88% | 0% |
| Xtrackers STOXX Europe 600 II UCITS ETF 1C (IE00003M6Z62) | stand-in: LU0328475792's semi-annual report | 2026-06-30 | 607 | 99.69% | 90.60% |
| L&G Gerd Kommer Multifactor Equity UCITS ETF (IE0001UQQ933) | annual report | 2026-06-30 | 4,250 | 100.00% | 92.32% |
| L&G Global Equity UCITS ETF (IE00BFXR5S54) | annual report | 2026-06-30 | 1,377 | 100.00% | 93.84% |
| L&G Russell 2000 US Small Cap UCITS ETF (IE00B3CNHJ55) | annual report | 2026-06-30 | 1,037 | 100.00% | 97.89% |
| L&G USD Corporate Bond Screened UCITS ETF (IE00BLRPRD67) | annual report | 2026-06-30 | 1,776 | 100.00% | 0% |

A share class reusing a cached umbrella report takes under two seconds; the
largest schedules (5,275 lines) take about six seconds.

### Downloads

24 report PDFs were fetched in development (305 MB), but only 10 were
distinct documents: AVL serves the same umbrella PDF under every ISIN, and
funds launched after the report date each fetched their own copy before
finding nothing. The adapters now record a byte-identical copy per edition
(brand, domicile, kind, period, language) and do not fetch that edition
again for another share class; with that rule a full first run fetches one
copy per umbrella plus at most one duplicate per edition. The AVL index
itself (twelve sitemap files) was not re-fetched for this run: a same-day
copy already in the local cache was used.

## Gaps

- **Report dates, not daily holdings.** Positions are four to ten months
  old. Each result says so in its first note.
- **No ISINs in the reports.** Bond lines stay unresolved; equity lines rely
  on name matching (`isin_source = name_match`), which leaves out names that
  normalise to more than one issuer (Rio Tinto PLC and Ltd, share classes of
  Alphabet). It needs the security master, which the `security_master`
  pipeline builds from N-PORT; without it lines carry names only.
- **German editions.** AVL lists the German edition of most Xtrackers
  reports; names of securities are as printed (English), headings German.
- **Swap funds** show the substitute basket the fund holds, not the index.
- **Duplicate rule.** Once an edition has proved to be a duplicate, a share
  class whose sub-fund is in an umbrella not yet read for that edition is not
  fetched. Delete `report_duplicates.json` in the cache to force it.
- **Terms.** AVL's legal notice says nothing about reuse or automated use; the
  client keeps to one request a second, reads `/fds/` and `/sitemaps/` only,
  and keeps the files under `data/` (gitignored). The reports are the
  issuers' copyright: never published from the public build.
