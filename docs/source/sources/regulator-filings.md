# Regulator filings

Some regulators publish the portfolios that funds file with them as open data.
These are not issuer files: each row is labelled with the regulator, the
filing and the position date, and the holdings are the fund's own, as of that
filing. Two are built (Brazil, Spain); the rest of this page records what was
checked in other countries and why it is not built.

Status as tested on 2026-11-03.

| Country | Source | Status | Kind of data | Position date in use |
|---|---|---|---|---|
| Brazil | CVM CDA, with B3's ISIN register | Working (`cvm_brazil`) | monthly regulatory filing, full portfolio | 2026-09-30 (2026-07-31 where newer months are mostly confidential) |
| Spain | CNMV FONDCART / SOCCART | Working (`cnmv_spain`) | quarterly regulatory filing, full portfolio | 2026-06-30 |
| Japan | EDINET API v2 | Gated: free API key; client only | securities and semi-annual reports | not built |
| India | AMFI / AMC monthly portfolios | Not built | monthly portfolio per AMC, per-AMC Excel formats | |
| Australia | ASIC portfolio holdings disclosure | No central dataset | semi-annual, on each issuer's site | |
| Korea | KRX ETF PDF | Not built: needs a decision | daily ETF portfolio deposit file | |
| Taiwan | SITCA | Not built | no portfolio dataset found | |

Both working sources are issuer adapters (`openbasket.sources.issuers.cvm_brazil`
and `cnmv_spain`), so `discover()` picks them up. Each also has a pipeline
(`reg_brazil`, `reg_spain`) that refreshes its local store and writes the fund
list; the adapters read the same folders.

Every `IssuerHoldings` from these sources says in `notes` what it is, for
example `regulatory filing: CVM monthly portfolio composition (CDA), positions
as of 2026-09-30`.

## Brazil: CVM portfolio composition (CDA)

Every Brazilian fund class files a monthly *Composição e Diversificação das
Aplicações* with the Comissão de Valores Mobiliários, which publishes all of
them.

Portfolios
: `https://dados.cvm.gov.br/dados/FI/DOC/CDA/DADOS/cda_fi_<YYYYMM>.zip`
  (listing at the same folder; dataset page
  `https://dados.cvm.gov.br/dataset/fi-doc-cda`). One ZIP per month, about
  25 MB, of semicolon-separated Latin-1 CSVs:
  - `cda_fi_PL`: net assets (`VL_PATRIM_LIQ`) of every fund that filed;
  - `cda_fi_BLC_1` to `BLC_8`: positions by kind. 1 federal bonds and repos
    (with `CD_ISIN`), 2 fund quotas (with the held fund's CNPJ), 3 swaps,
    4 shares, debentures, options, BDRs (with B3 code and `CD_ISIN`), 5 bank
    deposits, 6 private credit, 7 foreign assets, 8 everything else;
  - `cda_fie`: structured and index funds (FIDC, FIP, FIAGRO and ETFs, called
    FIIM), with net assets on every row and B3 codes but no ISIN column;
  - `cda_fi_CONFID` / `cda_fie_CONFID`: positions a fund keeps confidential,
    one total per kind of asset, with the date they will be released.

Fund ISINs
: CVM identifies funds by CNPJ only. B3 is Brazil's national numbering agency
  and publishes its whole ISIN register:
  `https://sistemaswebb3-listados.b3.com.br/isinProxy/IsinCall/GetTextDownload/`
  returns the id of the latest full file (`geralPt`), and
  `.../GetFileDownload/<base64 of the id>` the ZIP (8 MB): `NUMERACA.TXT`
  lists every ISIN with a four-character issuer code and `EMISSOR.TXT` gives
  each issuer's CNPJ. Fund quotas have asset type `CTF`. This is the same
  download the public page `https://sistemaswebb3-listados.b3.com.br/isinPage/`
  offers.

How an input ISIN reaches its holdings: fund ISIN (e.g. `BRBOVACTF003`) to
issuer code `BOVA` to CNPJ `10406511000161` to that CNPJ's CDA rows.

Weights. The CDA has no weight column, so a line's weight is its market value
(`VL_MERC_POS_FINAL`) over the fund's net assets in the same filing, times 100.
Lines on the liability side are given a negative weight: written options,
borrowed securities sold, forward obligations, payables, swap legs payable and
"other liabilities" (`LIABILITY_APLIC` in the module). With that, the median
fund's lines sum to exactly 100.00% and the 5th and 95th percentiles of 25,305
FIF classes are 100.00% too (July 2026); without the signs the 95th
percentile was 115.7%.

ISINs come from, in order, and each line says which in `isin_source`:

1. `reported`: the filing's own `CD_ISIN` (bonds, shares, debentures);
2. `b3_fund_cnpj`: for a held fund, B3's quota ISIN for its CNPJ, used only
   when that CNPJ has a single quota ISIN (54,298 CNPJs do; FIDCs with senior
   and subordinated quotas do not);
3. `cda_ticker`: for index-fund lines that only give a B3 code (`VALE3`), the
   ISIN that funds report next to that code in block 4 of the same month, if
   one ISIN has at least 95% of those rows (4,567 codes).

Repos (`Operações Compromissadas`) carry the collateral bond's ISIN in the
filing; they are not holdings of that bond, so they keep no ISIN and are named
"Repo (operação compromissada) backed by ..." with the bond in the name.

Confidential positions. A fund may withhold the detail of positions for up to
90 days. In the files published on 2026-11-03, 2.2% of net assets is
confidential for July 2026 but 27.8% for September (38% of funds have more
than 10% confidential). The store keeps the newest three months; for each
fund the newest month with at most 10% confidential is used, else the month
with the least, and the notes say so. Confidential totals stay in the frame as
lines named "Confidential positions: <kind>" with no ISIN, so they count as
unresolved weight.

Privacy. Blocks 6 and 8 name individuals (CPF numbers) as issuers of some
private credit. Their CPF and name are dropped at conversion; only company
issuers keep their CNPJ and name.

What the data shows (files published 2026-10-31 to 2026-11-03):

| | Jul 2026 | Aug 2026 | Sep 2026 |
|---|---|---|---|
| Funds with a filing | 26,301 | 26,200 | 26,322 |
| Net assets (BRL trillion) | 14.30 | 14.50 | 14.58 |
| Confidential share of net assets | 2.2% | 27.9% | 27.8% |
| Weight with an ISIN, one level down | 74.9% | 59.8% | 59.6% |

27,950 fund ISINs (26,145 CNPJs) are in the fund list. 24,650 of the 25,625
FIF classes in the July net-asset file match a B3 quota ISIN.

| Fund | As of | Lines | Weight sum | With ISIN | Read in |
|---|---|---|---|---|---|
| iShares Ibovespa (BOVA11, BRBOVACTF003) | 2026-09-30 | 158 | 100.00% | 97.93% | 0.09 s |
| Brasilprev Top TPF FIF RF (BRBPVTCTF000) | 2026-09-30 | 36 | 100.00% | 94.19% | 0.02 s |
| BB RF Curto Prazo Automático FIC FIF (BR09SYCTF003) | 2026-09-30 | 13 | 100.00% | 100.00% | 0.02 s |
| iShares S&P 500 FIC (IVVB11, BRIVVBCTF001) | 2026-09-30 | 4 | 100.00% | 0.00% | 0.12 s |

BOVA11's unresolved 2.07% is receivables and cash. Brasilprev's 5.8% is
repos. The BB fund of funds is one line, 99.9997%, the quota of
`BR0CC3CTF008`, which is itself in the fund list and expands from its own
filing. IVVB11 holds the US iShares Core S&P 500 ETF, reported as a foreign
asset with an internal code and no ISIN, so it stays unresolved; a proxy to
IVV in `lookthrough.proxies` is the way to expand it.

Converting one month to Parquet takes about 1.2 s with DuckDB. The network
test (one month and the register from CVM and B3, plus the Spanish quarter)
ran in 32 s.

Freshness. Monthly data; the dataset is regenerated weekly, and old months change as confidential
positions are released (the store re-downloads a month when the listing's
timestamp changes).

Terms. CVM's open data is under the Open Database License (ODbL 1.0), as
the dataset page states: credit CVM, and share derived databases under the
same licence. Credit line: "Comissão de Valores Mobiliários (CVM), Dados
Abertos: Fundos de Investimento, Composição e Diversificação das Aplicações
(CDA); fund ISINs from B3's ISIN register". B3's ISIN register is a free
public download; its terms were not found on the download page.
`dados.cvm.gov.br/robots.txt` asks for a 10-second crawl delay, which the
client keeps (`http.set_min_interval`); `/api/` is disallowed and not used.

Gaps:

- Foreign assets (block 7, and foreign lines of index funds) have only a
  foreign ticker or an internal code, no ISIN.
- Bank deposits, private credit and receivables have no ISIN.
- Funds whose CNPJ has several quota ISINs are in the fund list, but a fund
  holding one of them gets no ISIN for that line (`fund_cnpj` keeps the
  CNPJ).
- ETFs' daily portfolios are not here; the CDA is month-end.
- Futures are reported as filed (the value is usually the margin
  adjustment, not the notional).

## Spain: CNMV quarterly portfolios

The Comisión Nacional del Mercado de Valores publishes, per month, ZIPs of
"ficheros de información individual de IIC": the data Spanish funds (FI) and
SICAVs file under Circular 4/2008.

Page
: `https://www.cnmv.es/portal/publicaciones/descarga-informacion-individual`
  (current year; `?ejercicio=YYYY` for earlier years) links each month's ZIP as
  `https://www.cnmv.es/webservices/verdocumento/ver?e=<token>`, with the
  month's name as the link title. The ZIP of a quarter-end month (March, June,
  September, December) has the quarterly files; June 2026 is 8.6 MB.

Files used (XML, each with its XSD and an explanatory PDF in the ZIP)
: - `FONDREGISTRO` / `SOCREGISTRO`: every fund and SICAV, its compartments,
    and the ISIN of each share class or series;
  - `FONDCART` / `SOCCART`: every security held at quarter end: `ClaseIF`
    (domestic, foreign, doubtful), `DescripcionIF` (equity, bonds over or
    under one year, government debt, IIC, deposits, reverse repos ...),
    `CodigoISIN`, `DescripcionValor`, `Divisa`, `ValorMercado` in euros;
  - `FONDPATRIMDISVAR` / `SOCPATRIMDISVAR`: net assets per compartment
    (`TotalPatrimonio`) split into financial investments, accrued interest,
    cash (`Liquidez`) and the rest.

How an input ISIN reaches its holdings: share-class ISIN to (type, register
number, compartment) in the registry, to that compartment's `FONDCART` or
`SOCCART` lines.

Weights are `ValorMercado / TotalPatrimonio * 100` for the same compartment
and quarter. Cash, accrued interest and "rest" from the net asset breakdown
are added as lines with no ISIN, so the weights add up to the fund: across
2,067 compartments the 5th to 95th percentiles of the sum are 99.65% to
100.12%, median 100.00%. Reverse repos keep the collateral in the name, not
as the line's ISIN.

What the data shows (June 2026, published 2026-10-26): 2,067 compartments of
funds and SICAVs, 3,510 share-class ISINs, 130,475 positions, of which
130,220 carry a valid ISIN.

| Fund | As of | Lines | Weight sum | With ISIN | Read in |
|---|---|---|---|---|---|
| Accion IBEX 35 ETF (ES0105336038) | 2026-06-30 | 39 | 100.00% | 96.97% | 0.01 s |
| CaixaBank Master Renta Variable USA (ES0171963004) | 2026-06-30 | 72 | 99.86% | 86.59% | 0.01 s |
| CaixaBank Master RF Deuda Pública 3-10 (ES0111223006) | 2026-06-30 | 94 | 100.01% | 95.49% | 0.01 s |
| CaixaBank Monetario Rendimiento (ES0138045002) | 2026-06-30 | 445 | 100.00% | 79.25% | 0.01 s |

The unresolved weight is cash (11.4% of the US equity fund) and reverse
repos (15% of the money-market fund). Parsing the quarter's XML took 3 s.

Freshness. Quarterly, published about four months after the quarter (June
2026 data on 2026-10-26). CNMV states the files are not re-edited after
publication, so a quarter is downloaded once.

Terms. CNMV's legal notice (`/portal/Utilidades/NotaLegal`) authorises reuse
and calculations on the information it publishes, provided it is reproduced
faithfully and unaltered, and that anyone selling a product built on it tells
buyers it is available free from the CNMV website. Credit line: "Comisión
Nacional del Mercado de Valores (CNMV), ficheros de información individual de
IIC (FONDCART/SOCCART quarterly portfolios)". robots.txt only disallows
`*.shtml`.

Gaps:

- Derivatives (`FONDDERI` / `SOCDERI`) are reported as commitment amounts,
  not market values, and are left out.
- Market values are in euros; the one fund whose currency is not the euro
  (USD) gets a note that its weights may be off by the exchange rate.
- Positions are four to seven months old by the time they are published.
- Spanish UCITS ETFs are few (two in the registry); most ETFs sold in Spain
  are Irish or Luxembourg funds and are not here.

## Japan: EDINET (gated)

Investment trusts file securities reports (document type `120`) and
semi-annual reports (`160`) on EDINET; the financial statements end with a
schedule of every security held (有価証券明細表). EDINET API v2
(`https://api.edinet-fsa.go.jp/api/v2/documents.json?date=YYYY-MM-DD&type=2`,
`/documents/<docID>?type=5`) needs a *Subscription-Key*; without one it answers
401 "invalid subscription key". The key is free but needs registering an
account (e-mail and phone) on the EDINET API site, which this project does not
do on anyone's behalf.

`openbasket.sources.edinet_japan` is a client only: with `EDINET_API_KEY` set
it lists the fund reports filed on a day and downloads a document. It does not
parse the schedule (that needs real documents to develop against) and no open
mapping from a fund ISIN (`JP90C...`) to EDINET's `fundCode` was found, so no
issuer adapter is registered. The key is sent as a query parameter and kept out
of error messages.

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
  by one.
