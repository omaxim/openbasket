# UBS Asset Management (not built)

**Status: not buildable as a public source.** Tested on 2026-11-03. No
adapter is registered, so UBS funds stay unresolved, or are covered only
through a proxy (see below).

## Why it matters

UBS has more ETFs on SIX Swiss Exchange than any other issuer. The SIX
reference API
(`https://www.six-group.com/fqs/ref.json?select=ISIN,FundLongName,IssuerNameFull,LegalStructureCountryCode,AssetClassDesc,ReplicationMethodDesc&where=PortalSegment=FU*ProductLine=ET`)
lists 371 UBS ETF share classes:

| Umbrella (`IssuerNameFull`) | Share classes |
|---|---|
| UBS (Lux) Fund Solutions | 173 |
| UBS (Irl) ETF plc | 124 |
| UBS (Irl) Fund Solutions Plc | 42 |
| UBS Fund Management (Switzerland) AG | 32 |

By domicile that is 173 LU, 166 IE and 32 CH. By replication it is 328
physical and 43 synthetic (swap). By asset class: 167 developed-market
equity, 84 fixed income, 53 thematic equity, 26 commodities, 22 strategy
equity, 14 emerging-market equity, and a few money-market and volatility
funds. The 32 Swiss-domiciled ETFs include UBS SMI, SLI, SMIM, SXI Real
Estate Funds, the gold ETFs and the CMCI commodity ETFs. They are a large
part of what Swiss investors hold.

## What was tried

All requests used the project's own User-Agent through `openbasket.http`, at
no more than one request per second, and fetched a few pages only.

### ubs.com fund pages: behind a disclaimer gate

An earlier survey got HTTP 403 (Akamai "Access Denied") on every ubs.com URL.
On 2026-11-03 the same honest User-Agent got HTTP 200 for page HTML, for
example `https://www.ubs.com/ch/en/assetmanagement/funds/etf.html` and
`https://www.ubs.com/uk/en/assetmanagement/funds/etf.html`. The fund data,
however, is not in those pages:

- The pages use the `fundgate` template
  (`ubs/fit/components/pages/fundgate/v3/fundgate`). Every product block is
  an empty `<div class="georestrictedContent__placeholder">`.
- The content loads only after the visitor picks a domicile and an investor
  role in the "context disclaimer" overlay. That overlay has confirm and
  reject redirects and a `data-cookie-expiration` setting, so it records
  that the visitor accepted the legal terms. Accepting that gate for an
  automated client is a terms gate, which this project does not pass.
- The per-country sitemap (`https://www.ubs.com/ch/en.sitemap.xml`, 2,005
  URLs) has only six asset-management fund pages: the fund, ETF, glossary,
  asset-class, KIID and literature landing pages. It has no per-fund pages.

### ubs.com data APIs and files: disallowed by robots.txt

`https://www.ubs.com/robots.txt` (fetched 2026-11-03) has these rules for
`User-agent: *`:

- `Disallow: /bin/`. The pages call their data services under this path. The
  UK ETF page configures `https://www.ubs.com/bin/ubs/caas/v1/documents`
  (fund documents, with an API key in the page) and
  `https://www.ubs.com/bin/ubs/caas/sitesearch/v2/search`. They were not
  called.
- `Disallow: /2/`. Fund documents live under this path, such as the
  factsheet pattern `https://www.ubs.com/2/e/files/RET/FS_RET_{ISIN}_CH_EN.PDF`
  that the survey found (403 then). Any holdings files would be here too.
  They were not fetched.
- `Disallow: /ch/*.pdf` and `Disallow: /*content/dam/ubs`.

### Terms of use

Every ubs.com page footer says copying, distributing, linking or any other
use of the material "other than personal viewing" needs UBS's prior written
permission. A public aggregator that republishes holdings with a credit line
is not personal viewing. So even data reached without a gate would need
UBS's permission before openbasket could show it.

### Other UBS hosts

| Host | Result |
|---|---|
| `fundgate.ubs.com` | 301 to `https://www.ubs.com/global/en/asset-management/funds.html#fundgate` (the same gate) |
| `etf.ubs.com`, `www.ubs-etf.com`, `www.ubsetf.com`, `am.ubs.com`, `www.ubs.ch` | do not resolve |
| `api.ubs.com` | 503 |

### Third-party routes

| Route | Result |
|---|---|
| Swiss Fund Data (`https://www.swissfunddata.ch/sfdpub/`, run by SIX Financial Information) | Redirects to `sfd-eingang`, where you must pick an investor category and accept the legal terms ("Ich bin mit den rechtlichen Bestimmungen einverstanden") before searching. This is a consent gate, so it was not passed. It carries fund documents such as annual reports, not machine-readable holdings. |
| FE fundinfo (`www.fundinfo.com`) | `robots.txt` has `Disallow: /document/`, the path that serves UBS fund documents. |
| SIX Swiss Exchange (`/fqs/ref.json`) | Lists UBS ETFs with domicile and replication, but has no holdings. |
| SIX index data (SMI, SLI, SMIM, SPI) | Constituent weights are licensed and sit behind a login (SIX Index Data Center). They are also index weights, not the fund's own positions. |
| MSCI, S&P, Bloomberg, Solactive indices | Full constituent weights are licensed products; only top constituents are free. These are also index weights, not fund holdings. |
| Deutsche Börse / Börse Frankfurt, Euronext, Borsa Italiana | No full fund holdings published. |
| StockAnalysis, TradingView, justETF, investing.com | Third-party copies: top holdings only, or the full list behind a subscription, with site terms against scraping. |
| ESMA FIRDS, GLEIF | Identify UBS funds (ISIN, LEI, umbrella `UBS (IRL) ETF PUBLIC LIMITED COMPANY`, manager `UBS Asset Management (Europe) S.A.`), but have no holdings. |

## What would make it buildable

1. **Permission from UBS.** Ask UBS Asset Management (ETF desk, Zurich) for
   permission to republish daily holdings with a credit line, or for a data
   feed. With written permission and a documented access route, an adapter
   would follow the iShares or SPDR pattern. The fund list could come from
   SIX FQS (`IssuerNameFull` starting with `UBS`), which gives the ISIN,
   domicile and replication.
2. **A proxy for physical index ETFs.** Until then, `lookthrough.proxies`
   can map a physical UBS ETF to another issuer's physical ETF on the same
   index. For example, UBS Core S&P 500 UCITS ETF can be mapped to an S&P
   500 UCITS ETF from iShares or SPDR, and UBS SMI ETF to iShares SMI ETF
   (CH). The rows are then labelled as a proxy, never as UBS holdings. The
   43 synthetic UBS ETFs hold a substitute basket plus a swap, so a proxy
   shows the index exposure, not the fund's assets.

## Credit and licence (as found)

- Credit: none. No UBS data is used.
- Licence: "© UBS 1998 - 2026. All rights reserved." Any use other than
  personal viewing needs UBS's prior written permission (ubs.com footer,
  read 2026-11-03).
- Frequency: unknown. UBS shows daily holdings to visitors who pass the gate.
