# German, Austrian and Liechtenstein fund reports

Three `local_only` adapters read the full holdings of funds domiciled in
Germany, Austria and Liechtenstein from their annual and semi-annual
reports. All three use one parser,
`openbasket.sources.kag_reports`, because these reports print every
position with its ISIN and its share of the fund's net assets. No name
matching is needed.

| Adapter | Funds | Where the reports come from | Format |
|---|---|---|---|
| `bundesanzeiger_de` | German funds (`DE` ISINs in openbasket's fund registry: 9,464 share classes, 6,852 funds) | The Bundesanzeiger, where every German public fund must publish its reports (KAGB § 107) | HTML |
| `avl_kag` | Austrian and Liechtenstein funds with a report from the last 400 days on AVL (1,885 Austrian share classes named through OpenFIGI, plus Liechtenstein) | AVL Investmentfonds' document library | PDF |
| `ifm_li` | Liechtenstein funds administered by IFM (500 share classes; those AVL also has go to `avl_kag`) | IFM's fund documents page | PDF |

Status as tested on 2026-11-05: working. They are on by default in local
runs. The public build never runs them.

## How a report is read

A holding is a line with an ISIN followed by at least three numbers, the
last of which is the weight: two or three decimals, between -100 and 100,
written the German way (`1,47`) or the Swiss way (`1.47%`, thousands as
`184’073`). Reading starts at the schedule's heading (`Vermögensaufstellung`
or `Vermögensinventar`, not the contents entry). It stops at the list of
transactions made during the period, so purchases and sales are not read
as holdings. The fund's own share-class ISINs are skipped. Section headings
give the asset class: shares, bonds, fund units, derivatives.

Liechtenstein and Swiss reports often print the ISIN on a line of its own
next to the line of figures; the two are joined.

A report for several funds (HANSAINVEST publishes one for four funds) has
one schedule per fund. It is split at each fund's heading, or at each
fund's closing total (`Fondsvermögen EUR … 100,00`). The block whose
surroundings name the fund is read.

PDFs are read with `pypdf`. If that gives no schedule that adds up, they
are read again with `pdfplumber`, which keeps a row's cells on one line
where `pypdf` puts each cell on its own line.

A report is used only if its holdings add up to between 60% and 105% of net
assets. Weights are as printed, never rescaled; the rest (cash,
receivables, derivatives' margin) is the unresolved weight.

## Coverage in tests (5 November 2026)

| Sample | Read to 60-105% | Typical total |
|---|---|---|
| 22 random German reports (AVL PDFs) | 19 | 90-100% |
| 22 random Austrian reports (AVL PDFs) | 20 | 94-99% |
| 12 IFM Liechtenstein reports | 7 | 88-99% |
| Bundesanzeiger: UniGlobal, Warburg, DWS ESG Dynamic, four HANSAINVEST funds | 7 of 8 | 93-100% |

The reports that fail are refused, not guessed at. Most failures are:
- multi-fund reports whose blocks name the fund in a way the parser does
  not recognise (MEAG);
- reports that name holdings without ISINs (LBBW);
- umbrella reports whose sub-funds share pages.

## The Bundesanzeiger

The free search on `www.bundesanzeiger.de` finds a fund's reports by ISIN
in the *Kapitalmarkt* section. Each result names the report, the funds and
their ISINs, and the publication date. The newest annual or semi-annual
report that lists the ISIN is opened. Its tables are read as text rows: the
ISIN runs straight into the name (`US0378331005Apple Inc.`) and the last
column is the percentage of the fund.

The site is a Wicket application, so links belong to a cookie session that
expires after 30 minutes. A fund is therefore searched and opened in one
session, and a new session is started every 20 minutes. Requests share
openbasket's per-host throttle (one a second). A lookup takes about two
seconds. A first run over all German funds takes several hours; later runs
search an ISIN again only after 30 days. Parsed reports are kept in
`data/01_raw/issuers/bundesanzeiger/`. Report dates come from the title
("Halbjahresbericht 01.10.2025 - 31.03.2026") or, where the title has none,
from the schedule's heading.

`robots.txt` disallows only `/nlp` and `/construction_page.html`. Fund
report pages show no CAPTCHA and need no login. The site states no terms
for readers of the free search. The paid Info-Dienst needs an account and
is not used. Special funds (Spezial-AIF) are in the registry but publish
nothing there, so they stay without holdings.

## AVL and IFM

AVL keeps one PDF per share-class ISIN at
`dl.avl-investmentfonds.de/fds/{ISIN}-{RECHENSCHAFT|HALBJAHRBER}-{lang}-{date}.pdf`.
Its `robots.txt` allows `/fds/`; it shuts out named AI crawlers, which
openbasket is not. German funds are left to the Bundesanzeiger, and only
`AT` and `LI` ISINs are listed. AVL gives no fund names, so names come
from OpenFIGI.

IFM's fund documents page (`www.ifm.li/FondsDokumente.aspx`) lists each
share class with links `/link/de/{AR|SAR}/{ISIN}` to its newest annual and
semi-annual report. The newer of the two is read. `robots.txt` disallows
`/JsonAPI/`, `/Tools/`, `/iFrame/` and `/Service/` only, and the pages need
no login or investor-type confirmation. IFM's terms forbid reproduction
without consent.

## Other sources checked (5 November 2026)

For the private version, where restrictive terms are acceptable but logins,
CAPTCHAs, investor-type gates and robots.txt are respected.

| Source | Finding |
|---|---|
| Security KAG (Austria) | Predictable report URLs, no gate; its funds are on AVL already, so not built |
| easybank / BAWAG "markets" | Latest reports of funds sold in Austria, open robots, no gate; no way found from an ISIN to its page, so not built |
| OeKB Fondsdokumente | All Austrian public funds, last 365 days; behind an "accept" disclaimer: the user's call |
| LAFV (Liechtenstein) | All Liechtenstein funds; country and investor-type gate: the user's call, or apply for its API |
| Erste AM, Amundi Austria, 3 Banken-Generali, Schoellerbank, Universal-Investment | Investor or country gates: the user's call |
| Raiffeisen KAG | Cloudflare bot challenge: blocked |
| Deka (downloads), fondsweb, fundinfo, Morningstar documents, Comgest, Arca, VP Bank, LLB quotes | Disallowed by `robots.txt` |
| OeKB FundsXML, Kneip, WM Datenservice | Account or contract: blocked |
| Union Investment, DWS, Allianz GI | Report PDFs from fund pages; redundant with the Bundesanzeiger |
| Amundi France, Carmignac, La Française | Full inventories in report PDFs, names only (no ISINs). Amundi's report links are built from the ISIN and not gated, but its fund pages are: the user's call |
| BNP Paribas AM, Natixis IM | Full inventories; links only reachable from gated fund pages |
| Italian funds | Reports list only the top 50 holdings (Banca d'Italia rule): no full-holdings source |
| Swiss funds | See {doc}`swiss-bank-funds`: every route is gated, disallowed or barred by terms |
