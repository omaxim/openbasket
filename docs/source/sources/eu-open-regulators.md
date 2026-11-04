# Continental European regulators

A survey of the regulators and statistics offices of continental Europe for
**fund-level portfolio holdings published under a licence that allows reuse
and republication**. Spain (CNMV) is already built, see
[Regulator filings](regulator-filings.md). Nothing in this survey met the bar,
so no adapter was built; this page records what was checked so the next wave
does not repeat it.

Status as checked on 2026-11-03.

| Country | Source checked | Fund-level holdings? | Licence for reuse | Status |
|---|---|---|---|---|
| Portugal | CMVM *Sistema de Difusão de Informação*, "Composição discriminada das carteiras" | Yes, quarterly, every Portuguese securities and real-estate fund | None stated | Not built: no reuse licence, no ISINs |
| Portugal | CMVM on dados.gov.pt | No (long aggregate series) | CC BY | Not relevant |
| France | AMF open data on data.gouv.fr; AMF GECO / ROSA files | No (lists of management companies; fund and share-class lists with NAVs) | Open licence for the data.gouv.fr files | Not relevant |
| France | Banque de France statistics | No (aggregates) | | Not relevant |
| Italy | Banca d'Italia on dati.gov.it ("Portfolio securities", financial accounts of mutual funds) | No (aggregates) | CC BY 4.0 | Not relevant |
| Italy | Consob, Assogestioni | No public fund-level file found | | Not available |
| Germany | Bundesbank investment-fund statistics, GovData | No (aggregates) | | Not relevant |
| Germany | Bundesanzeiger (annual and semi-annual reports with the *Vermögensaufstellung* of every German fund) | Yes, as PDF/HTML reports | Not reviewed; a publication service, not open data | Not built: out of scope (report documents, terms not open) |
| Austria | OeNB "Investmentfonds in Österreich" on data.gv.at | No (volume, transactions, investor structure) | CC BY 4.0 | Not relevant |
| Austria | FMA, OeKB | No public fund-level holdings file found | | Not available |
| Netherlands | DNB investment-fund statistics on data.overheid.nl; AFM registers | No (aggregates; registers) | CC BY 4.0 (DNB) | Not relevant |
| Belgium | FSMA | No (lists of UCIs) | | Not available |
| Luxembourg | CSSF; data.public.lu | No (assets under management total; entity lists) | | Not available |
| Ireland | Central Bank of Ireland on data.gov.ie | No (Irish resident investment funds statistics) | CC BY 4.0 | Not relevant |
| Poland | KNF, IZFiA, dane.gov.pl | No central file: each TFI publishes its own semi-annual *zestawienie lokat* | | Not available (issuer sites are out of scope) |
| Czechia | ČNB, AKAT, data.gov.cz | No (aggregates) | | Not relevant |
| Greece | HCMC, data.gov.gr | No public fund-level file found | | Not available |
| EU | ESMA registers (UCITS/AIFMD entities, FIRDS) | No (entities and instruments) | Reproduction allowed with the source acknowledged | Not relevant |

Searches were run on each national portal that exposes an API and on
`data.europa.eu`, which harvests all of them, with terms for portfolio
composition in each language (*composition du portefeuille*, *composizione
del portafoglio*, *Vermögensaufstellung*, *carteira*, *skład portfela*,
*složení portfolia*, *beleggingsinstellingen portefeuille*, "UCITS
holdings"). Every investment-fund dataset found is an aggregate by fund type,
instrument or investor sector. Two outside the assignment looked similar and
are aggregates too: HANFA (Croatia) publishes the structure of investments by
fund category, and Danmarks Nationalbank's `DNIF*` tables are sector totals.

## Portugal: CMVM portfolio composition

The only continental regulator besides CNMV that publishes every fund's
portfolio is the Comissão do Mercado de Valores Mobiliários.

Where
: `https://www.cmvm.pt/PInstitucional/` > *Sistema de Difusão de
  Informação* > *Fundos / Gestão de Ativos* > *Composição discriminada das
  carteiras*, with one list for securities funds (FIM, including PPR and
  alternative securities funds) and one for real-estate funds (FII). The
  September 2026 securities-fund list has 222 entries, among them funds run by
  bank groups: ABANCA, Bankinter, BPI (CaixaBank), and others.

What a fund's page holds
: Quarter-end positions (`dat_mov`, for example `30-09-2026`), each with a
  section (`DSC_RUB`, such as "Títulos de dívida pública"), a free-text
  security description (`des_tit`, Bloomberg-style, for example
  `BTPS 0.95 06/01/32`), quantity, unit price and currency, accrued interest
  and total value in euros (`val_tot`), plus the fund's net asset value
  (`vlg_fun`) and units in issue. Weights could be computed as `val_tot /
  vlg_fun * 100`.

Why it is not built

- **No licence.** The CMVM site has no legal notice, terms of use or reuse
  policy (its footer links only to accessibility, data protection and a site
  map). The portfolio page states only that "A informação divulgada reproduz
  os dados remetidos à CMVM no cumprimento da legislação e regulamentação em
  vigor" and that it is the sole responsibility of the entities concerned.
  That is a disclaimer, not permission to republish. CMVM's datasets on
  dados.gov.pt are CC BY, but they are long aggregate series and do not
  include the portfolios.
- **No ISINs.** Neither the positions nor the fund carry an ISIN: holdings
  are identified by description only and funds by CMVM's internal number
  (`num_fun`). The "Versão para download" button builds an Excel file from the
  same displayed columns.
- **No bulk file.** The data comes from the site's internal OutSystems
  screen services (`/PInstitucional/screenservices/CMVM_SDI_Fun_GestAtivos_CW/
  ComposicaoDescriminadaCarteiras/FIMDetail/DataActionDataAction1`), one fund
  per request, behind versioned request bodies. That is not a published
  interface.

`robots.txt` at `www.cmvm.pt` does not disallow these pages for general
agents. The route to make this a source is to ask CMVM for permission to
reuse the portfolio data (under Lei 26/2016 on the reuse of public sector
information) and, ideally, for a bulk file with ISINs.

## Bank-run funds

The question for this wave was whether bank asset managers' funds (UBS,
Swisscanto, Raiffeisen, Deka, Union Investment, Santander, CaixaBank, BBVA,
KBC, ING, Erste and others) can be covered from regulators. In continental
Europe the answer is: only through CNMV for Spanish-domiciled funds, which
already include the funds of Santander Asset Management, CaixaBank AM, BBVA
AM, Bankinter, Ibercaja, Kutxabank, Unicaja and the other Spanish bank
managers. No regulator in Germany, Austria, Italy, the Benelux, France,
Poland or Czechia publishes fund portfolios as open data, so the German,
Austrian, Belgian, Dutch, French and Italian bank funds are not reachable this
way. Most of these funds are domiciled in Luxembourg or Ireland, whose
regulators publish no portfolios either.

## Not checked or out of scope

- Fund documents (annual and semi-annual reports, which list every holding)
  filed in national registers such as the Bundesanzeiger or Luxembourg
  Business Registers. They are documents, not datasets, and no open-data
  licence was found for them.
- The European Single Access Point (ESAP), which will collect fund documents
  in later phases; not live for funds yet.
- Nordic regulators (Sweden, Finland, Denmark, Norway): covered by another
  wave.
