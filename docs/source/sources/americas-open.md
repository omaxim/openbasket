# Americas open data (beyond the US and Brazil)

Regulators in Canada, Chile, Mexico, Colombia and Peru were checked for
security-level fund portfolios published under terms that allow reuse and
republication. One dataset qualifies and is built: the Superintendencia
Financiera de Colombia's **Formato 351** portfolios of Colombian pension and
severance funds, on `datos.gov.co` under CC BY-SA 4.0. The rest of this page
records what was checked elsewhere and why it is not built.

Status as tested on 2026-11-03.

| Country | Source | Status | Kind of data | Position date in use |
|---|---|---|---|---|
| Colombia | SFC Formato 351, mandatory pensions and cesantías (`ur2p-h4yf`) | Working (`americas_open.SfcColombiaStore`, family `FPO`) | monthly regulatory filing, full portfolio | 2026-08-31 |
| Colombia | SFC Formato 351, voluntary pensions (`hdp5-pgem`) | Working (family `FPV`) | semi-annual regulatory filing, full portfolio | 2026-06-30 |
| Colombia | SFC fondos de inversión colectiva (FIC) | Not built: no portfolio dataset | returns and financial statements only | |
| Canada | SEDAR+ fund filings | Not built: terms forbid automated collection | quarterly portfolio disclosure, financial statements (PDF) | |
| Chile | CMF cartera de inversiones de fondos mutuos | Not built: site unreachable from here, terms not verified | monthly text files per query | |
| Mexico | CNBV / datos.gob.mx | Not built: no fund portfolio dataset found, portal blocks automated clients | | |
| Peru | SMV / datosabiertos.gob.pe | Not built: only the register of funds is open data | fund list (ODC-BY) | |

## Colombia: SFC Formato 351

Every Colombian pension fund manager (AFP), and every trust company
(*fiduciaria*) and life insurer that runs a voluntary pension fund, reports
its portfolio security by security to the SFC on *Formato 351 – Composición
del portafolio de inversiones*. The SFC publishes these filings on the national
open data portal.

**Licence.** Each dataset page on datos.gov.co states the licence as
"Creative Commons Attribution | Share Alike 4.0 International" and links
<https://creativecommons.org/licenses/by-sa/4.0/legalcode>. Reuse and
republication are allowed with credit, a link to the licence and an
indication of changes. **Share-alike:** data adapted from these rows (the
weights openbasket computes) must be offered under CC BY-SA 4.0 too. Every row
carries the credit `Superintendencia Financiera de Colombia (SFC), Formato 351
… via datos.gov.co, CC BY-SA 4.0; weights computed by openbasket` and a note
naming the licence. `datos.gov.co/robots.txt` asks for `Crawl-delay: 1` and does
not disallow `/resource/`; the SODA API needs no key, log-in or consent.

### Endpoints

All are Socrata SODA queries, `https://www.datos.gov.co/resource/<id>.json`,
fetched through `openbasket.http` (one request a second per host):

| Id | Dataset | Frequency | Used for |
|---|---|---|---|
| `ur2p-h4yf` | Formato 351, mandatory pensions (Moderado, Conservador, Mayor Riesgo, Retiro Programado, Alternativo) and long-term cesantías | monthly | positions |
| `hdp5-pgem` | Formato 351, voluntary pension funds of AFPs, fiduciarias and life insurers | semi-annual (June, December) | positions |
| `hds9-4524` | daily valuation of mandatory funds and cesantías (Formato 491) | daily | net assets |
| `gpzw-wmxd` | daily valuation of voluntary pension funds | daily | net assets |

`refresh()` asks each portfolio dataset for its newest `fecha_de_corte`, then
pages through that date's rows (50,000 a page, `$order=:id`) selecting only the
columns in `PORTFOLIO_FIELDS`. Net assets come from the valuation dataset on
the same date, or the latest date before it:

- mandatory pension funds: Formato 491 capture unit 4, line (`cod_renglon`) 305,
  value column 2, *valor del fondo al cierre del día*; line 300 is the same
  before the day's returns (the unit counts of 300 and 305 are equal);
- cesantías: capture unit 3, line 110, *valor del portafolio al cierre del día*,
  after returns (line 105 is before);
- voluntary funds: `valor_fondo_cierre_dia_t_2`
  (`VALOR_FONDO_CIERRE_DIA_T_VALOR_PESOS`).

The store keeps `<root>/<FPO|FPV>/<date>/positions.parquet`,
`net_assets.parquet` and `meta.json`; the whole refresh is about 65,000 lines
and takes under a minute.

### How a fund reaches its holdings

These funds are not securities and **have no ISIN**, so they are keyed by the
SFC's codes: `portfolio_id` is `<family>:<entity type>:<entity code>:<fund
code>`, e.g. `FPO:23:3:1000` (Porvenir, mandatory, Moderado) or
`FPV:5:42:10110` (Fidudavivienda, voluntary fund Dafuturo). `products()`
returns `PRODUCT_COLUMNS` with `isin = None`, plus `manager`, `family`,
`as_of`, `lines`, `lines_with_isin`, `net_assets_cop` and
`portfolio_value_cop`. For that reason the source is **not registered as an
issuer adapter** (`validate_products` requires a fund ISIN); wiring it into
the universe needs a non-ISIN fund key.

`holdings(portfolio_id)` returns `(as_of, frame, notes)` with
`HOLDING_COLUMNS` plus `local_code` (the SFC *nemotécnico*),
`investment_class`, `investment_class_name` and `market_value_cop`.

### Weights and identifiers

Formato 351 gives market values in pesos (`valor_mercado_o_pres_pesos`) and no
weights, so **a line's weight is its market value over the fund value at close
on the same date, times 100**, computed by openbasket and never rescaled.
Derivatives (reported on Formatos 468 to 472), receivables and payables are not
in Formato 351; the rest is unresolved. Lots of one security (same ISIN or
local code, issuer, class and currency) are summed into one line.

The ISIN is the custodian's identifier (`no_iden_asignado_custodio`) when it
passes the check digit, else the *nemotécnico* when that is an ISIN. Colombian
TES (`COL17CT…`), local shares (`CO…`), foreign bonds and ETFs all carry
one. Lines without one are mostly private equity and real estate funds,
bank deposits, local collective funds (FIC) held by name and, in voluntary
funds, payroll loans (*libranzas*) and invoices.

The SFC investment class (`clase_inversion`) is mapped to `asset_class`:
`DEPV*` cash; `AAE*`, `ABE*`, `AME*`, `AEE`, `CRA*` equity; `CC*`, `PCC*`,
`FCP*`, `FIND*`, `PFIMM`, `PFMU*`, `PFPC`, `REITS` fund; bonds, TES, CDTs,
securitisations, libranzas and invoices debt.

### Personal data

Issuer tax ids (`nro_id_emisor`), guarantor and custodian ids and the entity's
own line numbers are never requested. Some voluntary-fund issuers are
individuals (payroll loans): the issuer name is kept only when
`tipo_id_emisor` is a code seen only on legal entities (3, 4, 5, 7, 8);
otherwise the line is named after its investment class. Raw API rows are not
written to disk and nothing from the source is committed; the test fixtures
are synthetic.

### Coverage (real run, 3 November 2026)

| Family | Funds | Managers | Lines filed | Lines with ISIN | Position date |
|---|---|---|---|---|---|
| FPO (mandatory, cesantías) | 21 | 4 (Porvenir, Protección, Colfondos, Skandia) | 25,379 | 16,433 | 2026-08-31 |
| FPV (voluntary) | 756 | 14 | 40,297 | 25,528 | 2026-06-30 |

774 of the 777 funds have a fund value on the valuation date; the other three
(two closed Alianza portfolios and one new Skandia portfolio) raise
`LookupError`. After merging lots there are 16,407 lines.

Weight sums: all 21 mandatory funds and cesantías are between 97.05% and
100.11% (median 99.17%), ISIN-identified weight median 85.7%. Voluntary
funds: median 100.01%, 77% between 95% and 105%; some umbrella funds are
valued separately from their sub-portfolios, so a few sum far below or above
100% (for example Porvenir's *Diversificado Extremo* at 359%). Voluntary-fund
ISIN-identified weight median is 90.1%.

**Bank-run managers** in the data: Porvenir (Grupo Aval), Fiduciaria Davivienda
(Banco Davivienda), Servitrust GNB Sudameris (Banco GNB Sudameris), Fiduciaria
Bancolombia, Credicorp Capital Fiduciaria (Credicorp) and Fiduciaria Coomeva
(Bancoomeva group). Fiduciaria Bancolombia's two voluntary funds are tiny in
this filing (about COP 0.4 million each).

Sample funds:

| Fund | Manager | As of | Lines (filed → merged) | Weight sum | ISIN weight |
|---|---|---|---|---|---|
| `FPO:23:3:1000` Fondo de Pensiones Obligatorias Moderado | Porvenir (Grupo Aval) | 2026-08-31 | 3,150 → 405 | 98.66% | 87.33% |
| `FPO:23:2:1000` Fondo de Pensiones Moderado | Protección | 2026-08-31 | 431 → 419 | 97.05% | 83.60% |
| `FPO:23:9:8000` Fondo Alternativo | Skandia | 2026-08-31 | 1,649 → 208 | 98.20% | 85.25% |
| `FPV:5:42:10110` F.V.P. Dafuturo | Fiduciaria Davivienda | 2026-06-30 | 1,562 → 339 | 97.77% | 59.68% |
| `FPV:5:34:58081` Fondo Voluntario de Pensiones GNB | Servitrust GNB Sudameris | 2026-06-30 | 80 → 58 | 100.15% | 93.36% |
| `FPV:5:59:38293` Fondo Voluntario Pensiones Correval | Credicorp Capital Fiduciaria | 2026-06-30 | 3,157 → 320 | 99.55% | 66.62% |

The largest ISIN-less weight in Porvenir Moderado is 8.9% in foreign private
equity funds; in Dafuturo it is bank deposits and certificates held by name.

### Freshness

Mandatory and cesantías filings are monthly; on 3 November 2026 the newest
was 31 August 2026 (dataset updated 24 October). Voluntary filings are
semi-annual and, per the dataset description, published in the first ten
days of July and January. The pipeline `americas_open` (`refresh_sfc_colombia`,
`build_sfc_colombia_products`) refreshes both and writes
`data/01_raw/americas_open/products.parquet`.

### Gaps

- No fund ISIN: these funds cannot be looked up by ISIN until the universe
  accepts another fund key.
- Colombia's **fondos de inversión colectiva** (the mutual funds, most run by
  bank-owned fiduciarias and brokers) are not in Formato 351 on datos.gov.co.
  The SFC publishes their returns (`qhpu-8ixx`) and supervisory financial
  statements, not their portfolios.
- Derivatives are missing; the swap, option and forward datasets (Formatos
  468, 471, 472) stopped in 2022–2023.
- Local collective funds held by a pension fund are given by name only, so
  they are not expanded further.

## Canada: SEDAR+

Investment funds file quarterly portfolio disclosure and annual and interim
financial statements on SEDAR+ (`www.sedarplus.ca`). The SEDAR+ terms of use
(`https://systems.securities-administrators.ca/terms-of-use/`, published by the
Alberta Securities Commission) allow limited unaltered extracts for
research, but forbid scraping: users must not "engage in any 'scraping' or the
use of any automated means" to reproduce public information, nor store it in
databases for third parties. There is no bulk download and the documents are
PDFs. Not built. Issuer sites (for example Vanguard Canada) are a separate
wave.

## Chile: CMF

The Comisión para el Mercado Financiero offers *Consulta de Cartera de
Inversiones por Fondo de Fondos Mutuos*
(`https://www.cmfchile.cl/institucional/estadisticas/ffm_cartera.php`), a form
that generates a text file of domestic, foreign, option and futures holdings
of every mutual fund for one month. On 3 November 2026 `www.cmfchile.cl`
refused or timed out every connection from this network (robots.txt, the form,
and the terms page), so neither the terms nor the file format could be
checked; a third-party search summary says CMF allows reproduction with credit,
but that is not verified. The only CMF data on `datos.gob.cl` is aggregated
2015 financial statements under a non-commercial licence. Not built; worth
re-checking from a network that can reach the CMF.

## Mexico: CNBV

No fund-level portfolio dataset was found. The CNBV publishes regulatory
report formats (R03 J-0311 *cartera de inversión* is filed daily by every fund)
and statistics, not the filings. `datos.gob.mx` answered the CKAN API with an
Akamai "Access Denied" page, which was not bypassed. Weekly and monthly
portfolios are published by each fund operator on its own site, which is out
of scope for this wave. Not built.

## Peru: SMV

`datosabiertos.gob.pe` has the SMV dataset *Información de los Fondos Mutuos y
de Inversión inscritos en la SMV* (Open Data Commons Attribution License), a
register of funds without holdings. No portfolio dataset is published as open
data; `www.smv.gob.pe` redirects to `www.gob.pe/smv`. Not built.
