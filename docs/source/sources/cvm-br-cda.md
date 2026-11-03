# CVM fund portfolios (CDA), Brazil

Brazil's securities regulator, the CVM (Comissão de Valores Mobiliários),
publishes the month-end portfolio of every Brazilian fund as open data: the
*Composição e Diversificação das Aplicações* (CDA). The adapter
`src/openbasket/sources/issuers/cvm_br_cda.py` (issuer name `cvm`, site `br`)
uses it for the funds listed on B3 that have an ISIN: ETFs (equity, fixed
income, real-estate and crypto), listed infrastructure funds (FI-Infra),
private-equity funds (FIP) and credit-receivable funds (FIDC). B3 supplies
the link from a fund's ISIN to its CNPJ, the company number the CVM files
under.

## Coverage

From a real run on 3 November 2026:

| B3 type | Products | Net assets (BRL bn) | Replication |
|---|---|---|---|
| ETF (equity, incl. foreign-index and commodity) | 127 | 72.0 | 112 physical, 14 swap, 1 unknown |
| ETF-RF (fixed income) | 63 | 63.6 | 60 physical, 3 unknown |
| ETF-CRIPTO | 25 | 6.9 | 23 physical, 2 swap |
| ETF-FII (real-estate fund units) | 2 | 0.1 | physical |
| FI-INFRA | 39 | 47.8 | not applicable |
| FIP | 7 | 1.1 | not applicable |
| FIDC | 1 | 0.0 | not applicable |
| **All** | **264** | **191.5** | |

All 264 are domiciled in Brazil and priced in BRL. B3's directory lists 303
funds of these types; 39 are left out because they have no CDA filing with
net assets in the last four months (new funds, most listed FIPs and FIDCs,
which report elsewhere) or no ISIN in B3's instruments file yet. 243 products
use the September 2026 filing, 2 August, 14 July and 5 June.

`replication` is `swap` when an equity or crypto ETF holds less than half of
its net assets in shares, offshore funds or fund units but holds futures or
swaps: it gets its index from derivatives over bonds or repos (S&P 500
futures ETFs such as SPXR11, gold, dollar and commodity-futures ETFs, two
bitcoin ETFs). It is `None` where the filing does not add up to between 90%
and 110% of the reported net assets (BOVS11, LFIN11, LFIX11, NLFA11).

## Endpoints

CDA directory
: `https://dados.cvm.gov.br/dados/FI/DOC/CDA/DADOS/`, an nginx index of
  `cda_fi_{YYYYMM}.zip` with each file's last-modified date. The last twelve
  months are there; older years are in `.../DADOS/HIST/`. Dataset page and
  data dictionary: `https://dados.cvm.gov.br/dataset/fi-doc-cda`.

CDA month
: `https://dados.cvm.gov.br/dados/FI/DOC/CDA/DADOS/cda_fi_{YYYYMM}.zip`
  (September 2026: 15.6 MB zipped, 170 MB unzipped; July 2026: 26.9 MB and
  301 MB). `;`-separated latin-1 CSVs, keyed by `CNPJ_FUNDO_CLASSE` and
  `DT_COMPTC`:

  | File | Contents |
  |---|---|
  | `cda_fi_BLC_1` | government bonds, with `CD_ISIN`, `CD_SELIC`, `TP_TITPUB`, `DT_VENC`; repos |
  | `cda_fi_BLC_2` | fund units, with the held fund's `CNPJ_FUNDO_CLASSE_COTA` |
  | `cda_fi_BLC_3` | swaps (`DIFERENCIAL DE SWAP A RECEBER/PAGAR`) |
  | `cda_fi_BLC_4` | listed securities with B3 code `CD_ATIVO` and `CD_ISIN`: shares, BDRs, units, debentures, futures, options |
  | `cda_fi_BLC_5`, `BLC_6` | bank deposits, private credit, agribusiness and real-estate notes (issuer CNPJ, no ISIN) |
  | `cda_fi_BLC_7` | investments abroad (`DS_ATIVO_EXTERIOR`, country, exchange code) |
  | `cda_fi_BLC_8` | cash, receivables, payables, other |
  | `cda_fi_PL` | net assets `VL_PATRIM_LIQ` per fund class |
  | `cda_fie` | ETFs (`CLASSES FIIM`), FIDCs and FIAGROs: all blocks in one file, net assets in every row, B3 code in `CD_ATIVO` but no ISIN column |
  | `cda_fi_CONFID`, `cda_fie_CONFID` | positions still confidential, one line per asset block, with the date the confidentiality ends |

B3 listed funds
: `https://sistemaswebb3-listados.b3.com.br/fundsListedProxy/Search/GetListFunds/{base64 JSON}`
  with `{"language":"pt-br","typeFund":"ETF","pageNumber":1,"pageSize":120,"keyword":""}`
  for each type (`ETF`, `ETF-RF`, `ETF-FII`, `ETF-CRIPTO`, `FI-INFRA`, `FIP`,
  `FIDC`); then `GetDetailFund/{base64 JSON}` with
  `{"language":"pt-br","idFNET":"<id>","idCEM":"<acronym>","typeFund":"<type>"}`
  returns the fund's `cnpj`, `tradingCode` (e.g. `BOVA11`) and manager. These
  are the calls B3's own listed-funds pages make; the host has no
  `robots.txt`. 303 funds take about 310 calls.

B3 instruments
: `https://arquivos.b3.com.br/api/download/requestname?fileName=InstrumentsConsolidatedFile&date=YYYY-MM-DD`
  returns a one-time token; `https://arquivos.b3.com.br/api/download/?token=...`
  returns the day's CSV (20 MB, about 88,000 instruments): ticker `TckrSymb`,
  `ISIN`, segment, category and issuer name. Fixed-income ETFs such as
  `IMAB11` are in the `FORWARD` segment, not `CASH`. `robots.txt` on
  `arquivos.b3.com.br` disallows nothing.

## From a fund ISIN to its holdings

1. `products()` reads B3's directory, maps each `tradingCode` to its ISIN
   with the instruments file, and keeps funds with a CDA filing in the last
   four months. `portfolio_id` is the 14-digit CNPJ; `product_page_url` is the
   fund's B3 page. The directory is cached as `cvm_b3_funds_<date>.json` when
   a cache folder is given.
2. `holdings(product, ctx)` lists the CDA directory and loads the newest
   month that has the CNPJ with its net assets. If a fund's whole portfolio is
   still confidential it has no net assets in the newest files (CAPE11 in
   August and September 2026), so an older month is used and a note says so.
   If more than 5% of net assets is confidential, up to four months are
   tried for a more complete one.
3. Each line's weight is `VL_MERC_POS_FINAL / VL_PATRIM_LIQ * 100`, the
   reported market value as a percent of the reported net assets. Nothing is
   rescaled, but the weights are derived, not reported. Liability lines are
   reported as positive values and counted as negative: `Valores a pagar`,
   `DIFERENCIAL DE SWAP A PAGAR`, `Obrigações por ações e outros TVM recebidos
   em empréstimo`, `Obrigações por compra a termo a pagar`, `Opções - Posições
   lançadas` and `Outras operações passivas e exigibilidades`. Futures lines
   are kept as reported (their value is the margin adjustment and carries its
   sign), and so is `Obrigações por venda a termo a entregar` (bond ETFs that
   sell forward only add up that way). With this rule 97.5% to 97.9% of the
   roughly 26,000 fund classes with net assets above BRL 1m add up to within
   0.5 points of 100% in each month from June to September 2026, and 95% to
   97% of the ETFs.
4. The ISIN of a line is, in order: `CD_ISIN` from the filing; for a
   government bond in the ETF file, which has only `CD_SELIC` and maturity,
   the ISIN other funds report in `BLC_1` for that pair; for a B3 code, the
   instruments file's ISIN, then the ISIN other funds report in `BLC_4` for
   that code (the code is sometimes in `DS_ATIVO` instead of `CD_ATIVO`); for
   units of a listed fund, that fund's ISIN by CNPJ. Repos are cash and get no
   ISIN: their SELIC code is the collateral.
5. Besides `HOLDING_COLUMNS`, each line carries `cnpj` (held fund or issuer),
   `value_brl` (signed), `code` (B3 or CETIP code), `tp_aplic` (CVM asset
   block) and `block` (source file).

The month is cached in `ctx.cache_dir` as
`cvm_cda_<YYYYMM>_<last-modified>/lines.parquet` and `funds.parquet`, written
only after the ZIP parses. One ZIP serves every fund, so it is fetched once
per revision rather than once per fund per day.

## Real run (3 November 2026)

| Fund | ISIN | As of | Lines | Sum of weights | Weight with ISIN |
|---|---|---|---|---|---|
| iShares Ibovespa (BOVA11), equity | BRBOVACTF003 | 2026-09-30 | 158 | 100.00% | 98.14% |
| It Now IMA-B (IMAB11), government bonds | BRIMABCTF004 | 2026-09-30 | 20 | 100.00% | 100.02% |
| It Now S&P 500 Futures (SPXR11), synthetic | BRSPXRCTF004 | 2026-09-30 | 9 | 100.00% | 100.18% |
| Galapagos Tesouro Pré 3 anos (3PRE11), bonds and forwards | BR3PRECTF003 | 2026-09-30 | 14 | 100.00% | 87.12% |
| iShares BOVESPA BR+ Cap 5% (CAPE11), older month | BRCAPECTF020 | 2026-07-31 | 92 | 100.00% | 98.60% |
| iShares S&P 500 (IVVB11), feeder | BRIVVBCTF001 | 2026-09-30 | 4 | 100.00% | 0.00% |
| Hashdex Nasdaq Crypto Index (HASH11) | BRHASHCTF018 | 2026-09-30 | 8 | 99.24% | 0.00% |
| Kinea Infra (KDIF11), FI-Infra fund of funds | BRKDIFCTF003 | 2026-09-30 | 9 | 100.00% | 2.52% |

BOVA11's lines without an ISIN are receivables (3.25%), payables (−2.27%), a
repo (0.68%), a BlackRock cash fund (0.17%) and an index future (0.03%).
`products()` took 86 s when it downloaded two monthly ZIPs and the instruments
file (B3's directory read from the day's cache) and 20 to 47 s with the months
cached. In a new process the first `holdings()` call took 30 s (directory
listing, cached months, instruments file) and each further fund 0.1 to 0.8 s.
B3's directory alone, 310 calls at one per second, took about 25 minutes on
the day because B3 answered slowly.

## Update frequency and lag

Monthly, as of month end. The CVM says the three newest months are refreshed
every Tuesday to Saturday at 08:00 with refilings, and months four to twelve
every Monday. On 3 November 2026 the newest month was September 2026 (lag of
about one month). Fund managers can ask for positions to stay confidential
for a time (90 days was the usual end date seen, e.g. 2026-12-29 for
September); those appear only as totals per asset block in the `CONFID`
files and are shown as single "Confidential position" lines with no ISIN.
September 2026's `cda_fi_CONFID` file is 9.5 MB against 1.4 MB for July, so
recent months are noticeably less complete.

## Terms and credit

The dataset page states the licence as the Open Data Commons Open Database
License (ODbL): use is free with attribution, and a database derived from it
must be shared under the same licence. Credit line used:
"Source: CVM - Comissão de Valores Mobiliários, Dados Abertos (CDA); fund
ISINs from B3". `dados.cvm.gov.br/robots.txt` disallows `/api/` and asks
for `Crawl-Delay: 10`; the adapter reads only `/dados/` files and waits 10 s
between requests to that host. B3's pages and instruments file are public
and need no log-in, but their terms of use have not been reviewed.

## Known gaps

- **Feeder and offshore funds.** IVVB11 holds iShares Core S&P 500 (IVV) and
  HASH11 a Cayman feeder; offshore lines carry a name and an internal code but
  no ISIN, so they stay unresolved. Several foreign-index ETFs are like this.
- **Unlisted funds.** Fund-of-fund lines that point to unlisted Brazilian
  funds (KDIF11's 98% in its master fund, BOVA11's cash fund) carry the held
  fund's CNPJ in `cnpj` but no ISIN, because unlisted funds' ISINs are not in
  the open data. Their portfolios are in the same CDA files, so a look-through
  by CNPJ is possible but not built.
- **Private credit** (bank deposits, debentures without an ISIN in the
  filing, CRA/CRI) is identified only by issuer CNPJ.
- **Synthetic ETFs** show the collateral (government bonds, repos) and a
  futures line whose value is the margin adjustment, not the exposure.
- **Confidential positions** are totals per asset block, and funds whose
  whole portfolio is confidential use an older month.
- **Inconsistent filings.** Some funds' lines do not add up to their net
  assets (BOVS11 36.7% in September 2026; LFIN11, LFIX11 and NLFA11 near 0%,
  their letras financeiras missing from the filing). A note on the holdings
  says so and `replication` is left empty.
- **Weights are derived** from values in BRL, so they carry the CVM's
  rounding of market values (two decimals of a real), not an issuer's
  rounded percentages.

## Example

```python
from openbasket.sources.issuers import FetchContext
from openbasket.sources.issuers import cvm_br_cda as cvm

funds = cvm.products(cache_dir="data/01_raw/issuers/holdings")
bova = funds[funds["ticker"] == "BOVA11"].iloc[0].to_dict()
h = cvm.holdings(bova, FetchContext(cache_dir="data/01_raw/issuers/holdings"))
h.as_of          # '2026-09-30'
h.url            # 'https://dados.cvm.gov.br/dados/FI/DOC/CDA/DADOS/cda_fi_202609.zip'
h.holdings.head()
```
