# CMVM Portugal: quarterly portfolios of securities funds

Adapter `cmvm_portugal` (`openbasket.sources.issuers.cmvm_portugal`), reading
`openbasket.sources.cmvm_portugal`; pipeline `reg_portugal`. Redistribution
class `public` (official regulator publication, see below). Enabled by
default.

The Portuguese securities regulator, the Comissão do Mercado de Valores
Mobiliários (CMVM), publishes in its *Sistema de Difusão de Informação* the
*composição discriminada das carteiras* that every Portuguese securities fund
files at quarter end (Regulamento da CMVM 5/2013): every position with its
value, and the fund's net asset value. One source covers the funds of Caixa
Gestão de Ativos (CGD), IM Gestão de Ativos (Millennium bcp), Santander, BPI
(CaixaBank), GNB (Novo Banco), Bankinter, Montepio and the independent
managers, including PPR retirement savings funds and alternative securities
funds. Real-estate funds (FII) are not read.

Status as tested on 2026-11-04: working.

## Coverage

From a run on 4 November 2026. The newest quarter on the site was
30 September 2026.

| | |
|---|---|
| Portfolios (funds and compartments) | 230 |
| With an ISIN in CMVM's register (`products()` rows) | 210 |
| Net assets | EUR 29.1 bn (EUR 28.6 bn with an ISIN) |
| Holding lines | 17,052 |
| Net assets in lines with an ISIN | 14.3% |
| Share weight with an ISIN | 65.3% |

The largest managers by net assets are Caixa Gestão de Ativos (EUR 8.8 bn),
IM Gestão de Ativos (6.6 bn), Santander (4.1 bn) and BPI (3.8 bn). Every row
has domicile `PT` and currency `EUR`. Asset class is worked out from the
fund's own lines (65% rule): 68 Fixed Income, 59 Fund of funds, 42 Equity, 35
Mixed, 6 Money Market. Replication is not stated.

Twenty portfolios have no ISIN in CMVM's register (some GNB, BIZ and BTG
funds, and closed SICs) and so are not products.

## Terms

The CMVM site states no licence, terms of use or reuse policy. The portfolio
page says that the information reproduces what was filed with CMVM under the
law and is the sole responsibility of the entities concerned. CMVM publishes
these portfolios because the law requires it, and Portuguese law on the reuse
of public sector information (Lei 26/2016, which transposes the EU Open Data
Directive) makes documents that public bodies publish reusable, with the
source cited and the content not distorted. openbasket is non-commercial and
treats CMVM, as an official government source, as `public`. Every row carries
the credit `Comissão do Mercado de Valores Mobiliários (CMVM), Sistema de
Difusão de Informação: composição discriminada das carteiras dos fundos de
investimento mobiliário` and the position date. CMVM's datasets on
dados.gov.pt are CC BY, but they are aggregate statistics only.

`https://www.cmvm.pt/robots.txt` disallows nothing these calls use. The pages
need no login, key, cookie or consent click. They hold fund and company names
only, no personal data.

## Endpoints

The site is an OutSystems app with no bulk file. Its pages read JSON from
screen services, one POST per list or fund, under
`https://www.cmvm.pt/PInstitucional/screenservices/`:

List of portfolios
: `CMVM_SDI_Fun_GestAtivos_CW/ComposicaoDescriminadaCarteiras/FimList/DataActionFetchData`
  with `Date` `1900-01-01` for the newest quarter: every fund and
  compartment (`num_fun`, `num_cpa`, name) and `DateOut`, the quarter end.

One portfolio
: `CMVM_SDI_Fun_GestAtivos_CW/ComposicaoDescriminadaCarteiras/FIMDetail/DataActionDataAction1`:
  the positions (`FIM_QA`), each with a section code and name (`num_rub`,
  `DSC_RUB`), the description (`des_tit`), quantity, price, currency,
  accrued interest and value in euros (`val_tot`), and on every row the
  fund's net asset value (`vlg_fun`) and units in issue.

Register entry
: `CMVM_SDI_CapRiscoEmp_CW/UIFlow1/CapRiscoEmp_FundoDetail_Wb/DataActionGetFundoDetail`
  with `Fun_Num`: the fund's ISIN (`cod_isi`), management company, type and
  status, each active compartment with its own ISIN, and share-class ISINs
  where the fund lists them.

Each request names the app's module version (from
`/PInstitucional/moduleservices/moduleversioninfo`) and the action's API
version, which the client reads from the page script that makes the call
(`/PInstitucional/scripts/<module>.mvc.js`), so a redeploy of the site does
not break it. If CMVM answers that a version has changed, the client reads
both again and retries once. Requests carry OutSystems' anonymous CSRF token,
which is the same for every visitor.

A refresh is one list request, one register request per fund and one
portfolio request per fund or compartment: about 450 requests, at one per
second through `openbasket.http`, so about eight minutes. It runs only when
the newest quarter is not stored yet.

## Weights

Weight is `val_tot / vlg_fun * 100` for the same portfolio and quarter, never
rescaled. In every portfolio of the September 2026 quarter the lines add up
to the net asset value to within a millionth, because CMVM's sections for
other assets (`Valores ativos`) and liabilities (`Valores passivos`) are kept
as lines.

The section codes mean different things for different fund types (`CC04` is
shares in one fund and term deposits in another), so a line's asset class
comes from the section name: public debt, bonds and commercial paper are
`debt`; deposits and money-market placements `cash`; shares and rights
`equity`; units of other funds `fund`; options `derivative`; everything else
`other`.

## ISINs

CMVM gives no ISIN for a position, only a description, often a
Bloomberg-style short name (`BTPS 0.95 06/01/32`, `iShares EurGvt 1-3YD`).
The adapter fills ISINs only where there is no doubt:

1. An ISIN written into the description itself
   (`GALP Energia SGPS SA-B Shrs-PTGAL0AM0009`): `isin_source = reported`.
2. Share lines, by exact company name after dropping CMVM's prefixes and
   tags (`AC.`, `Acao`, `-Nom.`, `-REG`): first openbasket's SEC N-PORT
   security master ({mod}`openbasket.sources.name_isin`), then
   FinanceDatabase's company list (MIT), taking a name only if it has
   exactly one ISIN there. `isin_source = name_match`.

Near matches are never taken. Matching by prefix was tried and rejected
because it picked Canadian depositary receipts and foreign listings
(`Alphabet Inc` to a CAD-hedged CDR, `McDonalds Corp` to McDonald's Holdings
Japan). Bonds and fund units keep their name and weight with no ISIN, so they
count as unresolved. Their descriptions are too abbreviated for exact
matching: only about 1% of fund-unit value matches a fund name in FinanceDatabase or
the fund directory.

The security master is `data/03_primary/security_master.parquet`
(`$OPENBASKET_SECURITY_MASTER`) and the company list
`data/01_raw/financedatabase/equities.parquet`
(`$OPENBASKET_FINDB_EQUITIES`), written by the `security_master` and
`sectors` pipelines. Without them share lines stay unresolved too. The
scheduled build runs `sectors` before `universe` for this reason.

## Known gaps

- No ISINs for bonds and fund units (see above). A bulk file with ISINs
  would have to come from CMVM.
- Quarterly only, about one month after quarter end.
- Derivatives other than options are not shown separately; CMVM files them
  under *Outros instrumentos financeiros*.
- Real-estate funds (FII) and pension funds are not read.
