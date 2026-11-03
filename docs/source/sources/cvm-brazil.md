# CVM Brazil: fund portfolio filings (CDA)

Adapter `cvm_brazil` (`openbasket.sources.issuers.cvm_brazil`), reading
`openbasket.sources.cvm_brazil`; pipeline `reg_brazil`. Redistribution class
`public` (ODbL 1.0, see below). Enabled by default.

Every Brazilian fund class (each *classe* under CVM Resolution 175) files a
monthly *Composição e Diversificação das Aplicações* (CDA) with the Comissão
de Valores Mobiliários, which publishes all of them as open data. One source
therefore covers every Brazilian fund that files, whoever runs it: ETFs,
listed infrastructure (FI-Infra), private-equity (FIP) and credit-receivable
(FIDC) funds, and the ordinary funds (FIF) sold to the public. B3, Brazil's
numbering agency and exchange, supplies the link from a fund's ISIN to the
CNPJ (company number) CVM files under, and the tickers of listed funds.

Status as tested on 2026-11-03: working.

## Coverage

From a run on 3 November 2026 with the September 2026 filing:

| | |
|---|---|
| Fund classes with a filing (Sep 2026) | 26,432 |
| Fund ISINs in `products()` | 27,428 |
| Listed on B3 (with a ticker) | 266 |
| Index funds (ETFs) with a replication | 202: 191 `physical`, 11 `swap` |
| Position lines in the month | 590,031, of which 183,218 report an ISIN |

Earlier months (files published 2026-10-31 to 2026-11-03):

| | Jul 2026 | Aug 2026 | Sep 2026 |
|---|---|---|---|
| Funds with a filing | 26,301 | 26,200 | 26,322 |
| Net assets (BRL trillion) | 14.30 | 14.50 | 14.58 |
| Confidential share of net assets | 2.2% | 27.9% | 27.8% |
| Weight with an ISIN, one level down | 74.9% | 59.8% | 59.6% |

All products are domiciled in Brazil and priced in BRL.

## Endpoints

CDA directory
: `https://dados.cvm.gov.br/dados/FI/DOC/CDA/DADOS/`, an index of
  `cda_fi_{YYYYMM}.zip` with each file's last-modified time. The last twelve
  months are there; older years are in `.../DADOS/HIST/`, which is not read.
  Dataset page and data dictionary: `https://dados.cvm.gov.br/dataset/fi-doc-cda`.

CDA month
: `https://dados.cvm.gov.br/dados/FI/DOC/CDA/DADOS/cda_fi_{YYYYMM}.zip`
  (September 2026: 15.6 MB zipped; July 2026: 26.9 MB). Semicolon-separated
  Latin-1 CSVs keyed by `CNPJ_FUNDO_CLASSE` and `DT_COMPTC`:

  | File | Contents |
  |---|---|
  | `cda_fi_PL` | net assets `VL_PATRIM_LIQ` of every fund class that filed |
  | `cda_fi_BLC_1` | government bonds with `CD_ISIN`, `CD_SELIC`, `TP_TITPUB`, `DT_VENC`; repos |
  | `cda_fi_BLC_2` | fund units, with the held fund's `CNPJ_FUNDO_CLASSE_COTA` |
  | `cda_fi_BLC_3` | swaps (`DIFERENCIAL DE SWAP A RECEBER/PAGAR`) |
  | `cda_fi_BLC_4` | listed securities with B3 code `CD_ATIVO` and `CD_ISIN`: shares, BDRs, units, debentures, futures, options |
  | `cda_fi_BLC_5`, `BLC_6` | bank deposits, private credit, agribusiness and real-estate notes (issuer CNPJ, no ISIN) |
  | `cda_fi_BLC_7` | investments abroad (`DS_ATIVO_EXTERIOR`, country, exchange code) |
  | `cda_fi_BLC_8` | cash, receivables, payables, other |
  | `cda_fie` | ETFs (`CLASSES FIIM`), FIDCs and FIAGROs: every block in one file, net assets on every row, B3 code in `CD_ATIVO` and `CD_SELIC` for bonds, but no ISIN column |
  | `cda_fi_CONFID`, `cda_fie_CONFID` | positions still confidential, one line per kind of asset, with the date the confidentiality ends |

  The legacy name `cda_fiim_*` (before 15/11/2025, per the dataset page) is
  not parsed.

B3 ISIN register
: `https://sistemaswebb3-listados.b3.com.br/isinProxy/IsinCall/GetTextDownload/`
  returns the id of the latest full file (`geralPt`), and
  `.../GetFileDownload/<base64 of the id>` the ZIP (8 MB): `NUMERACA.TXT`
  lists every ISIN with a four-character issuer code and `EMISSOR.TXT` gives
  each issuer's CNPJ. Fund quotas have asset type `CTF`. This is the download
  the public page `https://sistemaswebb3-listados.b3.com.br/isinPage/` offers.

B3 instruments
: `https://arquivos.b3.com.br/api/download/requestname?fileName=InstrumentsConsolidatedFile&date=YYYY-MM-DD`
  returns a one-time token; `https://arquivos.b3.com.br/api/download/?token=...`
  returns the day's CSV (20 MB, about 88,000 instruments): ticker `TckrSymb`,
  `ISIN`, segment, category and issuer name. Fixed-income ETFs such as
  `IMAB11` are in the `FORWARD` segment, not `CASH`. One file is kept; the
  newest of the last seven days is used.

`dados.cvm.gov.br/robots.txt` disallows `/api/` and asks for
`Crawl-Delay: 10`; only `/dados/` files are read, with 10 seconds between
requests to that host. Neither B3 host has a robots.txt (both answer 404);
requests to them are spaced 2 seconds apart. No log-in, consent gate or bot
check is involved.

## From a fund ISIN to its holdings

The `reg_brazil` pipeline (or the adapter's first `products()` call) keeps a
local store in `data/01_raw/reg_brazil` (`$OPENBASKET_REG_BRAZIL_DIR`): the
newest three months (`reg_brazil.months`), the ISIN register and the
instruments file, each converted to Parquet with DuckDB (about 1.2 s a
month). A month is downloaded again only when the listing's timestamp
changes, since CVM regenerates old months as confidential positions are
released.

1. `products()`: every active fund-quota ISIN (`CTF`) of B3's register whose
   CNPJ filed in a stored month. `portfolio_id` is the 14-digit CNPJ, so
   every quota of a fund shares it. A listed fund gets its trading code
   (`ticker`, e.g. `BOVA11`) and B3 category (`asset_class`, e.g.
   `ETF EQUITIES`) from the instruments file; other funds get CVM's fund type.
2. `holdings(product)`: the fund's lines from the newest stored month in
   which at most 10% of net assets is confidential
   (`reg_brazil.max_confidential_pct`); if none qualifies, the month with the
   least. The notes say which month was used and why.
3. Weights: the CDA has no weight column, so a line's weight is its market
   value (`VL_MERC_POS_FINAL`) over the fund's net assets in the same filing,
   times 100. Nothing is rescaled. Lines on the liability side are reported as
   positive values and given a negative weight: `Valores a pagar`,
   `DIFERENCIAL DE SWAP A PAGAR`, `Obrigações por ações e outros TVM
   recebidos em empréstimo`, `Obrigações por compra a termo a pagar`,
   `Opções - Posições lançadas` and `Outras operações passivas e
   exigibilidades`. Futures are kept as reported (their value is the margin
   adjustment and carries its sign), and so is `Obrigações por venda a termo
   a entregar` (bond ETFs that sell forward, such as 3PRE11, only add up
   that way). With this rule 97.5% to 97.9% of the roughly 26,000 fund classes
   with net assets above BRL 1m add up to within 0.5 points of 100% in each
   month from June to September 2026, and 95% to 97% of the ETFs.
4. ISINs come from, in order, and each line says which in `isin_source`:
   1. `reported`: the filing's own `CD_ISIN` (bonds, shares, debentures);
   2. `cda_selic`: for a government bond that an index fund gives only by
      SELIC code and maturity, the ISIN ordinary funds report for that pair
      in block 1 of the same month;
   3. `b3_fund_cnpj`: for a held fund, B3's quota ISIN for its CNPJ, used
      only when that CNPJ has a single quota ISIN (FIDCs with senior and
      subordinated quotas do not);
   4. `cda_ticker`: for a line that gives only a B3 code (`VALE3`, sometimes
      in `DS_ATIVO` instead of `CD_ATIVO`), the ISIN funds report next to
      that code in block 4 of the same month, if one ISIN has at least 95% of
      those rows;
   5. `b3_ticker`: the instruments file's ISIN for that code;
   6. `name_text`: for an offshore line, a valid ISIN written in its name
      (`GALAXY BBG BITC ETF - KYG376981000 - NAV`).

   Repos (`Operações Compromissadas`) carry the collateral bond's ISIN in the
   filing; they are not holdings of that bond, so they get no ISIN, class
   `repurchase agreement`, and the bond in the name.
5. Offshore lines (`Investimento no Exterior`) keep the foreign exchange
   ticker the filing gives (`IAU`, `VXUS US`) in `ticker`, and a name that is
   only CVM's numeric issuer code is replaced by that ticker. Their asset
   class comes from `TP_ATIVO` (offshore fund units are `fund`).
6. Besides `HOLDING_COLUMNS`, each line carries `isin_source`, `fund_cnpj`
   (of a held fund), `issuer_cnpj`, `block`, `tp_aplic`, `tp_ativo`,
   `maturity`, `market_value_brl` and `confidential_until`.

Replication of index funds (`replication`): read from the filing, since CVM
has no field for it. A filing that does not add up to between 90% and 110% of
its net assets gives `None`. An equity, foreign-index or crypto ETF (B3
category `ETF ...`) whose shares and fund units are less than half its net
assets, but which holds futures or swaps, gets its exposure from derivatives
over bonds or repos: `swap` (S&P 500 futures ETFs, gold, dollar and
commodity-futures ETFs). Any other index fund that adds up is `physical`.
Funds that are not index funds get `None`.

Confidential positions. A fund may withhold the detail of positions for up to
90 days. In the files published on 2026-11-03, 2.2% of net assets is
confidential for July 2026 but 27.8% for September (38% of funds have more
than 10% confidential). Confidential totals stay in the frame as lines named
"Confidential positions: <kind>" with no ISIN, so they count as unresolved.

Privacy. Blocks 6 and 8 and `cda_fie` name individuals (CPF numbers) as
issuers of some private credit (`PF_PJ_EMISSOR = PF`). Their CPF and name are
dropped at conversion; only company issuers keep their CNPJ and name.

## Real run (3 November 2026)

| Fund | ISIN | As of | Lines | Sum of weights | Weight with ISIN |
|---|---|---|---|---|---|
| iShares Ibovespa (BOVA11), equity | BRBOVACTF003 | 2026-09-30 | 158 | 100.00% | 98.32% |
| It Now IMA-B (IMAB11), government bonds | BRIMABCTF004 | 2026-09-30 | 20 | 100.00% | 100.02% |
| Trend ETF LBMA Ouro (GOLD11), gold via IAU | BRGOLDCTF015 | 2026-09-30 | 13 | 100.00% | 0.01% |
| Brasilprev Top TPF FIF RF | BRBPVTCTF000 | 2026-09-30 | 36 | 100.00% | 94.19% |
| BB RF Curto Prazo Automático FIC FIF | BR09SYCTF003 | 2026-09-30 | 13 | 100.00% | 100.00% |
| iShares S&P 500 FIC (IVVB11), feeder | BRIVVBCTF001 | 2026-09-30 | 4 | 100.00% | 0.00% |

BOVA11's lines without an ISIN are receivables, payables, a repo, a cash fund
and an index future. IMAB11 files its government bonds by SELIC code only;
17 of its 20 lines get their ISIN from `cda_selic`. GOLD11's 100.02% line is the US iShares Gold Trust, shown
with its ticker `IAU`. Brasilprev's unresolved 5.8% is repos. The BB fund of
funds is one line, the quota of `BR0CC3CTF008`, which is itself in the fund
list and expands from its own filing. IVVB11 holds the US iShares Core S&P 500
ETF, reported with an internal code and no ISIN; a proxy to IVV in
`lookthrough.proxies` is the way to expand it.

Downloading one month, the register and the instruments file and converting
them took 34 s (most of it the 10-second crawl delay); `products()` then takes
about 5 s and each `holdings()` call 0.1 s.

## Freshness

Monthly, as of month end, published about one month later (September 2026 on
3 November). CVM refreshes the three newest months every Tuesday to Saturday
with refilings, and months four to twelve every Monday, so old months change
as confidential positions are released.

## Terms and credit

The dataset page gives the licence as "Licença Aberta para Bases de Dados
(ODbL) do Open Data Commons", the Open Database License 1.0: use is free if
CVM is credited, and a database derived from it must be offered under the
same licence. openbasket does so; see {doc}`../data-licence`. Credit line:
"Comissão de Valores Mobiliários (CVM), Dados Abertos: Fundos de
Investimento, Composição e Diversificação das Aplicações (CDA); fund ISINs
and tickers from B3".

B3's ISIN register and instruments file are free public downloads that need
no log-in; their terms were not found on the download pages. Only identifiers
are taken from them (ISIN, trading code, category).

## Known gaps

- **Feeder and offshore funds.** IVVB11 holds IVV and HASH11 a Cayman feeder;
  offshore lines carry a name, an exchange ticker or an internal code but
  rarely an ISIN, so they stay unresolved unless the ticker or a proxy maps
  them.
- **Unlisted funds.** Lines that point to unlisted funds whose CNPJ has
  several quota ISINs (or none) keep the CNPJ in `fund_cnpj` but no ISIN.
  Their portfolios are in the same CDA files, so a look-through by CNPJ is
  possible but not built.
- **Private credit**, bank deposits and receivables are identified only by
  issuer CNPJ.
- **Synthetic ETFs** show the collateral and a futures line whose value is
  the margin adjustment, not the exposure.
- **Inconsistent filings.** Some funds' lines do not add up to their net
  assets (BOVS11 36.7% in September 2026; LFIN11, LFIX11 and NLFA11 near 0%).
  A note on the holdings says so and `replication` is left empty.
- **Weights are derived** from values in BRL, so they carry the CVM's
  rounding of market values, not an issuer's rounded percentages.
- ETFs' daily portfolios are not here; the CDA is month-end.

## Example

```python
from openbasket.sources.cvm_brazil import CdaStore

store = CdaStore("data/01_raw/reg_brazil")
store.refresh()                      # or: kedro run --pipeline=reg_brazil
funds = store.products().set_index("isin")
as_of, month, df, notes = store.holdings(funds.at["BRBOVACTF003", "portfolio_id"])
as_of                                # '2026-09-30'
df[["isin", "ticker", "name", "weight_pct", "isin_source"]].head()
```
