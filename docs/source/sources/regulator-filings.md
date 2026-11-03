# Regulator filings

Some regulators publish the portfolios that funds file with them as open data.
These are not issuer files: each row is labelled with the regulator, the
filing and the position date, and the holdings are the fund's own, as of that
filing. Their licences allow republication, so they are `public` sources and
the public build can use them. Each built regulator has its own page; this
page records what was checked in other countries and why it is not built.

Status as tested on 2026-11-03.

| Country | Source | Status | Kind of data | Position date in use |
|---|---|---|---|---|
| United States | SEC Form N-PORT, Form N-MFP | Working (`nport`, {doc}`sec_nmfp <sec-nmfp>`) | monthly filings, full portfolio | N-PORT 2026 Q3 data set; N-MFP 2026-09-30 |
| Brazil | CVM CDA, with B3's ISIN register | Working ({doc}`cvm_brazil <cvm-brazil>`) | monthly regulatory filing, full portfolio | 2026-09-30 (older where newer months are mostly confidential) |
| Spain | CNMV FONDCART / SOCCART | Working ({doc}`cnmv_spain <cnmv-spain>`) | quarterly regulatory filing, full portfolio | 2026-06-30 |
| Japan | EDINET API v2 | Gated: free API key; client only ({doc}`edinet-japan`) | securities and semi-annual reports | not built |
| India | AMFI / AMC monthly portfolios | Not built | monthly portfolio per AMC, per-AMC Excel formats | |
| Australia | ASIC portfolio holdings disclosure | No central dataset | semi-annual, on each issuer's site | |
| Korea | KRX ETF PDF | Not built: needs a decision | daily ETF portfolio deposit file | |
| Taiwan | SITCA | Not built | no portfolio dataset found | |

The working sources are issuer adapters, so `discover()` picks them up. CVM
and CNMV also have a pipeline (`reg_brazil`, `reg_spain`) that refreshes a
local store and writes the fund list; the adapters read the same folders.
Every `IssuerHoldings` from them says in `notes` what it is, for example
`regulatory filing: CVM monthly portfolio composition (CDA), positions as of
2026-09-30`.

## Not built

India
: SEBI requires every AMC to publish each scheme's full portfolio monthly
  (fortnightly for debt schemes) on its own site and AMFI's. The scheme list
  with ISINs is open (`https://portal.amfiindia.com/spages/NAVAll.txt`, which
  answered), but `www.amfiindia.com`, where the portfolio disclosure page is,
  timed out from here and refused connections from a second network, so the
  central page could not be checked. The portfolios themselves are Excel files
  in each AMC's own layout (over 40 AMCs), which needs one parser per AMC.

Australia
: ASIC's portfolio holdings disclosure rules make each fund publish its
  holdings twice a year on its own website. There is no central dataset at
  ASIC, so this is an issuer route.

Korea
: KRX publishes each ETF's daily portfolio deposit file (PDF) on its Data
  Marketplace (`data.krx.co.kr`). The data is served by an internal JSON
  endpoint for the site's own pages, not a documented download, and it was not
  probed; whether it now needs a log-in was not established. The public data
  portal (`data.go.kr`) needs a free service key and was only found to carry
  ETF prices, not portfolios.

Taiwan
: Only SITCA's fund registers (ISINs, links to monthly reports) were found as
  open data, no security-level portfolio dataset. Not checked further.

Other EU regulators
: CNMV was the only EU national regulator found in this pass that publishes
  security-level fund portfolios as open data. The others were not checked one
  by one. ESMA's FIRDS ({doc}`firds`) is reference data only: what an ISIN is,
  not what a fund holds.
