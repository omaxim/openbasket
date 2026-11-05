# EDINET Japan: listed funds' annual securities reports

Holdings source `edinet` (source id `edinet_japan`), read by
`openbasket.sources.edinet_reports` through the API client
`openbasket.sources.edinet_japan`; pipeline `reg_japan`. Redistribution class
`public`. Enabled by default; it needs an EDINET API key (`EDINET_API_KEY`).

Japanese investment trusts file their annual securities report
(有価証券報告書, document type `120`) on EDINET, the Financial Services
Agency's disclosure system. Its financial statements end with a schedule of
every security the fund held at the end of the period (附属明細表, 有価証券明細表),
with its value in yen. openbasket reads that schedule for every listed
Japanese investment trust, which covers the ETFs on the Tokyo Stock Exchange:
Nomura NEXT FUNDS, iShares Japan, Daiwa iFreeETF, Amova (Nikko) Listed Index
Fund, SSGA and the others.

Status as tested on 2026-11-05: working.

## Coverage

From a run on 5 November 2026 over the filing lists of the previous 400
days:

| | |
|---|---|
| Listed domestic investment trusts in EDINET's fund code list | 427 |
| With an annual report in those 400 days (stored) | 377, none failed to parse |
| Japanese funds in openbasket's universe | 137, of which 122 have a stored report |
| Those 122: lines' total, median | 99.3% of net assets |
| Those 122: weight with an ISIN, median | 87.9%; 58 funds at 90% or more |
| Reports that go through a mother fund | 92 |
| Report dates | 2025-08-20 to 2026-08-10 |

Examples, as the public build reads them: iShares Core TOPIX ETF (1475)
97.9% resolved, 1,591 companies; NEXT FUNDS TOPIX (1306) 97.8%; NEXT FUNDS
Nikkei 225 (1321) 99.2%; MAXIS Nikkei 225 (1346) 99.2%. The iShares S&P 500
ETF (1655) holds the iShares Core S&P 500 ETF, so its report line is looked
through IVV's N-PORT filing: 506 companies, 99.9%.

Funds whose lines add up to less than 90% are mostly leveraged, inverse,
gold and futures-based funds: their schedule lists the government bonds and
deposits they hold as margin, and the futures sit in the unresolved weight.

## Terms

EDINET's terms of use (利用規約, revised 25 April 2025,
`https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0030.html`)
let anyone reuse its content under the Public Data License 1.0 (PDL1.0),
Japan's open government licence, which is compatible with CC BY 4.0. The
terms ask for the source to be credited, and for edited content to say who
edited it and not to be presented as the government's own. Every row
therefore carries the credit `出典：EDINET (Financial Services Agency, Japan),
the fund's annual securities report (有価証券報告書), PDL1.0; changed by
openbasket: holdings extracted from the securities schedule, weighted by the
fund's net assets and matched to ISINs by name`, and links the filed
document.

The same terms bar scraping the EDINET sites (section 2.2) and heavy bursts
of API calls (section 3.2). openbasket uses the API for filing lists and
documents, one request at a time at one per second, and downloads only the
two code-list files EDINET publishes for download, which the API does not
serve.

## The key

EDINET API v2 needs a free *Subscription-Key*, issued after registering an
account on the EDINET API site (e-mail and phone number). openbasket does
not register on anyone's behalf. Put the key in `.env` as `EDINET_API_KEY`;
the scheduled build reads it from the repository secret of the same name.
Without it, `reg_japan` fails softly and the `edinet` source reads whatever
reports are already stored. The key is sent as a query parameter and kept
out of logs and error messages.

## How a fund is read

1. **Which funds.** EDINET's fund code list (`Fundcode.zip`) gives every
   fund's EDINET code and, for a listed fund, its Tokyo code (証券コード).
   Only listed domestic investment trusts (内国投資信託受益証券) are read.
2. **Which report.** `reg_japan` reads EDINET's filing list for each of the
   last 400 days (`documents.json?type=2`, one request a day, stored, so a
   later run reads only new days) and keeps each listed fund's newest annual
   report. Semi-annual reports (`160`) are not used: they carry neither the
   securities schedule nor the major-holdings table.
3. **The document.** The report's inline XBRL (`type=1`) is downloaded and
   its tables are read; only the parsed lines are kept
   (`data/01_raw/reg_japan/reports/<docID>.parquet`), not the file.
4. **Which fund in the universe.** A Japanese fund ISIN is tied to the report
   by its Tokyo code: OpenFIGI's listing of the ISIN on the Tokyo exchange
   (`exchCode` `JT`), or in a local run the ticker on the issuer's own list.
5. **When.** The `edinet` source comes after `issuer`, `tickerinside` and
   `nport` in `lookthrough.source_order`. In a local run a fund whose issuer
   file resolves (Nomura, iShares daily files) keeps it. In the public build
   those issuers are `local_only`, so EDINET's report is the fund's holdings.

## Weights

A line's weight is its yen value divided by the fund's net assets
(純資産合計) from the same report's balance sheet, never rescaled. Lines in a
foreign currency (a US ETF held by a Japanese ETF, US Treasuries) are
converted with the yen subtotal that the schedule prints under each currency
group, or, where there is none, with the group's stated share of net assets
(組入時価比率).

Many Japanese funds invest through a mother fund (マザーファンド). The report
then adds the mother fund's balance sheet and schedule as reference
information, and openbasket replaces the fund's units of the mother fund with
the mother fund's lines, times the fund's share in it. The note says so.

The schedule's columns differ by fund: share count, unit price and value for
stocks (some reports add the shares lent out); face value and value for
bonds and funds. The value is taken by position, checked as shares × price
for stocks.

## ISINs

The schedule names holdings without codes, in Japanese for Japanese
companies (`トヨタ自動車`) and in English for foreign ones. ISINs are filled
only for exact names:

- **Japanese shares.** The name, folded to half-width (NFKC) with `株式会社`
  dropped, is looked up in EDINET's company code list (`Edinetcode.zip`).
  Only names one listed company has count. The company's Tokyo code gives
  the ISIN from FinanceDatabase's Tokyo listings (`NNNN.T`, MIT) or the
  OpenFIGI cache.
- **Foreign shares and funds.** English names go through the same exact-name
  indexes as CMVM's lines: the SEC N-PORT security master, then
  FinanceDatabase. A Japanese ETF that holds the iShares Core S&P 500 ETF
  gets IVV's ISIN, and the look-through then expands IVV's N-PORT holdings.
- **Bonds.** In a local run, openbasket's name matcher matches bonds by
  issuer, coupon and maturity. The public build does not use it, so bond
  lines keep their names and weight with no ISIN there.

`isin_source` is `name_match` for all of these.

## Known gaps

- Annual only: positions are as of the fund's last fiscal year end, up to a
  year old (plus up to three months until the report is filed).
- A company delisted or renamed since its fund's report is not in the
  current company list, so its line stays unresolved.
- Bonds have no ISINs in the public build.
- Daiwa's reports name companies by the exchange's short names (`日 立`,
  `信越化学`, `三菱UFJフィナンシャルG`), which exact matching does not resolve:
  about a quarter of iFreeETF TOPIX's weight stays unresolved.
- A schedule split into a second table for non-share securities in a
  foreign currency without its own yen subtotal (SSGA's S&P 500 ETF lists its
  REITs that way) is left out, and the note says how many lines.
- Listed funds with no ISIN openbasket knows (no issuer list or registry
  names them) are read but not shown.
- Derivatives (index futures, currency forwards) are in a separate table that
  is not read; their margin and value sit in the unresolved weight.
- J-REITs (投資法人) and unlisted investment trusts are not read.
