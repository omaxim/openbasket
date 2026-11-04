# CNMV Spain: quarterly fund and SICAV portfolios

Adapter `cnmv_spain` (`openbasket.sources.issuers.cnmv_spain`), reading
`openbasket.sources.cnmv_spain`; pipeline `reg_spain`. Redistribution class
`public` (CNMV legal notice, see below). Enabled by default.

The Spanish securities regulator, the Comisión Nacional del Mercado de
Valores (CNMV), publishes what every Spanish collective investment scheme
files with it each quarter under Circular 4/2008: its share classes, net
assets and **every security it holds**. One source therefore covers every
Spanish investment fund (FI) and SICAV, whoever runs it: CaixaBank AM,
Santander AM, BBVA AM, Kutxabank, Ibercaja, Bankinter, March and the rest,
and the two Spanish listed ETFs.

Status as tested on 2026-11-03: working.

## Coverage

From a run on 3 November 2026. The newest quarter on the site was June 2026,
published on 26 October 2026.

| | FI | SICAV | Total |
|---|---|---|---|
| Share-class ISINs (`products()` rows) | 3,110 | 400 | 3,510 |
| Compartments with net assets filed | 1,667 | 400 | 2,067 |
| Net assets | EUR 489.5 bn | EUR 18.7 bn | EUR 508.2 bn |
| Holding lines | 107,127 | 23,348 | 130,475 |
| Market value with a valid ISIN | 95.7% | 98.6% | |
| Market value in reverse repos | 3.1% | 1.3% | |

All rows have domicile `ES`. Share-class currencies: 3,507 EUR, 1 USD, 2 not
given. Replication, which the filings do not state (see below): 44 share
classes `physical`, 9 `swap`, the rest unknown.

The largest groups by net assets are CaixaBank (EUR 112.3 bn, 116
compartments), Santander (74.5 bn, 221), BBVA (63.6 bn, 88), Kutxabank
(39.0 bn, 67), Ibercaja (36.1 bn, 72) and Bankinter (22.8 bn, 165).

## Endpoints

Listing
: `https://www.cnmv.es/portal/publicaciones/descarga-informacion-individual`
  (add `?ejercicio=YYYY` for an earlier year). Its table has one link per
  month, `https://www.cnmv.es/webservices/verdocumento/ver?e=<token>`, whose
  `title` is the month in Spanish (`Junio`). The token is read from the page.

Monthly ZIP
: `GET` of that link returns `application/zip` (`HEAD` returns 405). The June
  2026 ZIP is 8.6 MB and unpacks to 84 MB. Only quarter-end months (March,
  June, September, December) hold portfolios; other months carry just
  `FONDREGISTRO` and `FONDMENS`. Each XML file ships with its XSD and a
  *Documento Explicativo* PDF.

Files read from a quarter-end ZIP (`FOND*` for funds, `SOC*` for SICAVs):

| File | Used for |
|---|---|
| `FONDREGISTRO` / `SOCREGISTRO` | Fund, compartment and share-class (`Clase` / `Serie`) ISINs, names, ETF flag, management company and group |
| `FONDTRIM` / `SOCTRIM` | Class currency and `Patrimonio`, `ClaseFondo` (index tracker, feeder ...) and `VocacionInversora` (shown as `asset_class`) |
| `FONDPATRIMDISVAR` / `SOCPATRIMDISVAR` | Per compartment: domestic and foreign portfolio, accrued interest, doubtful assets, `Liquidez`, `Resto` and `TotalPatrimonio` |
| `FONDCART` / `SOCCART` | Every cash-market holding: `ClaseIF`, `DescripcionIF`, `CodigoISIN`, `DescripcionValor`, `Divisa`, `ValorMercado` |
| `FONDDERI` / `SOCDERI` | Derivatives: description (rights or obligations), underlying, instrument, nominal commitment, purpose |

Fund page (the product link)
: `https://www.cnmv.es/portal/consultas/iic/fondo?isin=<ISIN>`, CNMV's own
  register page of that share class.

`robots.txt` disallows only `*.shtml`. No log-in, cookie wall or bot check
is involved. A refresh makes one or two listing requests and one ZIP request,
through `openbasket.http`.

## From a fund ISIN to its holdings

The `reg_spain` pipeline (or the adapter's first `products()` call) keeps the
newest quarter-end ZIP that holds portfolio files in `data/01_raw/reg_spain`
(`$OPENBASKET_REG_SPAIN_DIR`), parsed to Parquet. If the newest quarter-end
ZIP has no portfolio files, the quarter before is used.

1. `products()`: each registry share class becomes a row, with
   `portfolio_id` = `Tipo:NumeroRegistro:NumeroCompartimento` (`FI:208:0`),
   so all classes of a compartment share it. `holdings_url` is the ZIP link
   and `product_page_url` the class's CNMV page. The name is the
   compartment's (or the fund's) plus the class when there are several.
2. `holdings(product)`: the compartment's lines in `FONDCART` / `SOCCART`.
3. Each line's weight is `ValorMercado / TotalPatrimonio × 100`, using the
   compartment's `TotalPatrimonio` from `PATRIMDISVAR`. **The filings report
   values, not percentages, so this weight is derived**, and every result
   says so in its notes. Nothing is rescaled. Where `TotalPatrimonio` is
   missing, the sum of the classes' `Patrimonio` is used if they share one
   currency. For the one USD fund, `ValorMercado` adds up to its
   `CarteraExterior` in USD, so both values are in the fund's currency,
   although the explanatory PDF speaks of euros.
4. Three more lines come from `PATRIMDISVAR`, without ISINs: cash
   (`Liquidez`), accrued interest and the rest (`Resto`, other assets and
   liabilities, which also holds the derivatives' market value). With them the
   lines account for the whole of net assets: across 2,067 compartments the
   5th to 95th percentiles of the sum are 99.65% to 100.12%, median 100.00%.
5. `as_of` is the last day of the files' `FechaDatos` month (`202606` gives
   `2026-06-30`), read from the data, not from the file name.

Lines are normalised as follows:

- `asset_class` from `DescripcionIF`: *Renta Variable* is `equity`;
  *Renta Fija* and *Deuda Pública* are `debt`; *IIC* and *Entidades de
  Capital Riesgo* are `fund`; *Depósitos* are `cash`; *Adquisición Temporal
  de Activos* is `repurchase agreement`; doubtful assets are `other`.
- `isin` is `CodigoISIN` when its check digit is valid. Placeholders such as
  `XXXXXXXXXXXX` and `ZZ0000000000` become `None`.
- **Reverse repos** carry the ISIN of their collateral. The fund holds a
  loan, not that bond, so `isin` is `None` and the collateral ISIN is
  appended to the name: `REPO|BANCO INVERSIS, S.A.|2,14|2026-07-01
  (colateral ES00000124C5)`.
- `name` is `DescripcionValor` as filed (`BONO|ESTADO ESPAÑOL|0,50|2030-04-30`:
  type, issuer, coupon, maturity). `currency` is `Divisa`. There is no CUSIP,
  SEDOL, ticker or country.
- Extra columns: `portfolio_section` (`ClaseIF`, or `PATRIMONIO` for the
  three balance lines), `kind` (`DescripcionIF`) and `market_value_eur`
  (`ValorMercado`).

Spanish funds that hold other funds (*IIC* lines; 22% of FI value) are
looked through from those funds' own sources, including other Spanish funds
in this source.

## Derivatives and replication

`FONDDERI` gives each derivative's nominal commitment, not its market value,
so derivatives are not lines. CNMV files every swap twice, once among the
rights (*Derechos*) and once among the obligations (*Obligaciones*), with
the same nominal. The notes therefore give the nominal per side: ABANCA
Índice Renta Variable Emergente has "2 lines, rights 68.32% and obligations
68.32% of net assets", which is one index swap of 68.3% and its Euribor
paying leg, not 137%. Brunara SICAV's 9 lines are all obligations, 20.31%.

The filings have no replication field, so it is inferred:

- `swap` when the nominal of the compartment's total-return, index,
  credit-default or structured swaps (`FONDDERI` lines naming a swap,
  *permuta*, TRS or CDS, but not interest-rate swaps or futures), counted
  once per swap (the larger side), is at least 50% of net assets. In June
  2026 this marks 9 compartments: ABANCA's emerging-markets index fund and
  eight Sabadell Buy and Watch and Evolución funds built on CDS. Their
  securities are the swap's collateral, not their exposure. FON FINECO Renta
  Fija Internacional, whose one iBoxx TRS is 25.9% of net assets, is not
  marked; nor are the guaranteed funds that cross 50% only when both legs are
  added.
- `physical` when the compartment tracks an index (ETF flag or `ClaseFondo`
  *Fondo que replica o reproduce un índice*) and its listed securities are
  at least 80% of net assets: 44 share classes, including the two ETFs and
  the CaixaBank, BBVA, Santander and ING index funds.
- `None` otherwise, which is most active funds.

## Real run (3 November 2026)

| Fund | Kind | Lines | Sum of weight | Securities | With ISIN |
|---|---|---|---|---|---|
| ACCION IBEX 35 ETF (ES0105336038) | Equity ETF, `physical` | 39 | 99.999% | 99.27% | 96.97% |
| FONMARCH, FI - Clase A (ES0138841038) | Bond, 3 classes | 111 | 100.000% | 97.93% | 97.58% |
| CaixaBank Monetario Rendimiento - Estándar (ES0138045002) | Money market, 8 classes | 445 | 99.999% | 94.15% | 79.25% |
| ABANCA Índice Renta Variable Emergente (ES0162956009) | Equity index, `swap` | 20 | 95.176% | 88.62% | 88.62% |
| Brunara SICAV (ES0137927135) | SICAV | 86 | 100.003% | 97.12% | 97.12% |
| Mutuafondo Mixto Dólar (ES0164745004) | USD fund | 66 | 100.000% | 84.18% | 84.18% |

All are as of 2026-06-30. The IBEX ETF's ISIN-less weight is a reverse repo
plus cash. The money-market fund's gap is reverse repos and deposits. The
ABANCA fund's lines sum to 95.2% because its `FONDCART` lines fall short of
its reported portfolio. Parsing the quarter's XML takes about 4 s; after
that `products()` takes 2 s and each `holdings()` call is a lookup.

## Freshness

Quarterly, published about four months after the quarter (June 2026 data on
26 October 2026), so holdings are three to seven months old. CNMV says files
are not re-issued except in exceptional cases, so a quarter is downloaded
once.

## Terms and credit

Credit line: "Comisión Nacional del Mercado de Valores (CNMV), ficheros de
información individual de IIC (FONDCART/SOCCART quarterly portfolios)".

The CNMV legal notice (`https://www.cnmv.es/portal/Utilidades/NotaLegal`),
read on 3 November 2026, allows use beyond private consultation on these
conditions:

- the information is reproduced faithfully, without manipulating or altering
  it. Names and values are passed through as filed;
- calculations made from it may be published. The weights here are such a
  calculation and are labelled as derived;
- anyone who sells it must tell buyers that it is free on the CNMV site;
- links to the CNMV site must open in a separate window, not inside another
  site's frame.

Each explanatory PDF says the data come from the funds' own filings, the
filers are responsible for their quality, and they may differ from CNMV
statistics that include later corrections.

## Known gaps

- **Lag**: quarterly, about four months behind.
- **No reported weights**: weights are `ValorMercado / TotalPatrimonio`.
  SICAV files round values to thousands of euros, so small SICAV lines are
  coarse; a note says so.
- **Derivatives are not lines** (see above); their market value is inside
  `Resto`.
- **Swap and guaranteed funds** report collateral, not exposure.
- **Reverse repos** and deposits are cash with no ISIN.
- **Portfolio shortfalls**: in 94 of 1,362 EUR fund compartments the
  `FONDCART` lines differ by more than 0.5% from the domestic plus foreign
  portfolio in `PATRIMDISVAR`. Their lines sum to less than 100% and the rest
  counts as unresolved.
- **Missing filings**: 19 registry compartments have no net assets filed and
  are left out; 26 that do (EUR 1.5 bn, new or closing funds) have no
  holding lines, so `holdings()` returns just the balance lines, with a note.
- **Other Spanish UCIs**: only FI and SICAV appear in these files. Real
  estate funds, hedge funds (IICIL) and venture capital vehicles are not
  covered. Most ETFs sold in Spain are Irish or Luxembourg funds and are not
  here.

## Example

```python
from openbasket.sources.cnmv_spain import CnmvStore

store = CnmvStore("data/01_raw/reg_spain")
store.refresh()                      # or: kedro run --pipeline=reg_spain
p = store.products()
pid = p.loc[p["isin"] == "ES0105336038", "portfolio_id"].iloc[0]   # 'FI:3499:0'
as_of, df, notes = store.holdings(pid)
as_of                                # '2026-06-30'
df[["isin", "name", "weight_pct", "asset_class"]].head(3)
```
