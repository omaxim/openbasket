# CNMV: Spanish funds and SICAVs

Adapter `cnmv-es-iic`, module `openbasket.sources.issuers.cnmv_es_iic`.

The Spanish securities regulator, the Comisión Nacional del Mercado de
Valores (CNMV), publishes what every Spanish UCI files with it each quarter:
its share classes, net assets and **every security it holds**. One source
therefore covers every Spanish investment fund (FI) and SICAV, whoever runs
it: CaixaBank AM, Santander AM, BBVA AM, Kutxabank, Ibercaja, Bankinter,
March and the rest, and the two Spanish listed ETFs.

## Coverage

From a run on 3 November 2026. The newest quarter on the site was June 2026,
published on 26 October 2026.

| | FI | SICAV | Total |
|---|---|---|---|
| Share-class ISINs (`products()` rows) | 3,128 | 404 | 3,532 |
| Compartments (`portfolio_id`) | 1,682 | 404 | 2,086 |
| Compartments with net assets filed | 1,667 | 400 | 2,067 |
| Net assets | EUR 489.5 bn | EUR 18.7 bn | EUR 508.2 bn |
| Holding lines | 107,127 | 23,348 | 130,475 |
| Market value with a valid ISIN | 95.7% | 98.6% | |
| Market value in reverse repos | 3.1% | 1.3% | |
| Median share of net assets in listed securities | 96.1% | 95.4% | |
| Median share of net assets with an ISIN | 94.3% | 94.7% | |

All rows have domicile `ES`. Replication, which the filings do not state
(see below): 44 share classes `physical`, 18 `swap`, 3,470 unknown. Share
class currencies: 3,507 EUR, 1 USD, 24 not given.

The largest groups by net assets are CaixaBank (EUR 112.3 bn, 116
compartments), Santander (74.5 bn, 221), BBVA (63.6 bn, 88), Kutxabank
(39.0 bn, 67), Ibercaja (36.1 bn, 72) and Bankinter (22.8 bn, 165).

## Endpoints

Listing
: `https://www.cnmv.es/portal/publicaciones/descarga-informacion-individual`
  (add `?ejercicio=YYYY` for an earlier year). Its table
  `grdDescargas` has one link per month,
  `https://www.cnmv.es/webservices/verdocumento/ver?e=<token>`, whose `title`
  is the month in Spanish (`Junio`). The token is read from the page.

Monthly ZIP
: `GET` of that link returns `application/zip`, `DOC.ZIP` (`HEAD` returns
  405). The June 2026 ZIP is 8.6 MB and unpacks to 84 MB. Only quarter-end
  months (March, June, September, December) hold portfolios; other months
  carry just `FONDREGISTRO` and `FONDMENS`. Each XML file ships with its XSD
  and a *Documento Explicativo* PDF.

Files read from a quarter-end ZIP (`FOND*` for funds, `SOC*` for SICAVs):

| File | Used for |
|---|---|
| `FONDREGISTRO` / `SOCREGISTRO` | Fund, compartment and share-class (`Clase` / `Serie`) ISINs, names, ETF flag, management company |
| `FONDTRIM` / `SOCTRIM` | Class currency and `Patrimonio`, `ClaseFondo` (index tracker, feeder...) and `VocacionInversora` (shown as `asset_class`) |
| `FONDPATRIMDISVAR` / `SOCPATRIMDISVAR` | Per compartment: domestic and foreign portfolio, accrued interest, doubtful assets, `Liquidez`, `Resto` and `TotalPatrimonio` |
| `FONDCART` / `SOCCART` | Every cash-market holding: `ClaseIF`, `DescripcionIF`, `CodigoISIN`, `DescripcionValor`, `Divisa`, `ValorMercado` |
| `FONDDERI` / `SOCDERI` | Derivatives: description, underlying, instrument, nominal commitment, purpose |

Fund page (the product link)
: `https://www.cnmv.es/portal/consultas/iic/fondo?isin=<ISIN>`, the CNMV's own
  register page of that share class.

`robots.txt` disallows only `*.shtml`. No log-in, cookie wall or bot check
is involved. A run makes one request for the listing (two in the months
before a year's first quarter appears) and one for the ZIP, through
`openbasket.http` at one request per second.

## From a fund ISIN to its holdings

1. `products()` reads the listing and takes the newest quarter-end month
   whose ZIP holds `FONDCART`. Each registry share class becomes a row, with
   `portfolio_id` = `Tipo-NumeroRegistro-NumeroCompartimento` (`FI-9-0`), so
   all classes of a compartment share it. `holdings_url` is the ZIP link.
2. `holdings(product, ctx)` loads that ZIP (from the day's cache if
   present) and selects the compartment's lines in `FONDCART` / `SOCCART`.
3. Each line's weight is
   `weight_pct = ValorMercado / TotalPatrimonio × 100`, using the
   compartment's `TotalPatrimonio` from `PATRIMDISVAR`. **The filings report
   values, not percentages, so this weight is derived**, and every result
   says so in its notes. Nothing is rescaled. For the one USD fund,
   `ValorMercado` adds up to its `CarteraExterior` in USD, so both values
   are in the fund's currency, although the explanatory PDF says euros.
   Where `TotalPatrimonio` is missing, the sum of the classes' `Patrimonio`
   is used if they share one currency.
4. Three more lines come from `PATRIMDISVAR`, without ISINs: `Liquidez
   (tesorería)` (cash), `Intereses de la cartera de inversión` (accrued
   interest) and `Resto (otros activos y pasivos)`. With them the lines
   account for the whole of net assets: FONMARCH's 111 lines sum to
   100.000%.
5. `as_of` is the last day of the file's `FechaDatos` month (`202606` gives
   `2026-06-30`).

Lines are normalised as follows:

- `asset_class` from `DescripcionIF`: *Renta Variable* is `equity`;
  *Renta Fija* and *Deuda Pública* are `debt`; *IIC* and *Entidades de
  Capital Riesgo* are `fund`; *Depósitos* and *Adquisición Temporal de
  Activos* are `cash`; doubtful assets are `other`.
- `isin` is `CodigoISIN` when its check digit is valid. Placeholders such as
  `XXXXXXXXXXXX` and `ZZ0000000000` become `None` (155 FI and 10 SICAV
  lines).
- **Reverse repos** (*Adquisición Temporal de Activos*) carry the ISIN of
  their collateral. The fund holds a loan, not that bond, so `isin` is
  `None` and the collateral ISIN is appended to the name, for example
  `REPO|BANCO INVERSIS, S.A.|2,14|2026-07-01 (colateral ES00000124C5)`.
- `name` is `DescripcionValor` as filed (`BONO|ESTADO ESPAÑOL|0,50|2030-04-30`:
  type, issuer, coupon, maturity). `currency` is `Divisa`. There is no CUSIP,
  SEDOL, ticker or country.
- An extra column `line_type` is `security` for portfolio lines and
  `balance` for the three net-asset lines.

Spanish funds that hold other funds (*IIC* lines; 22% of FI value) are
looked through from those funds' own sources, including other Spanish funds
in this source.

## Results of the real run

`products()` took 19.6 s, including the 8.6 MB download and parsing all
files (about 4 s). After that every `holdings()` call is a lookup in memory
(0.00 to 0.08 s); a new process reading the cached ZIP takes 1.4 s.

| Fund | Kind | Lines | Sum of weight | Securities | With ISIN |
|---|---|---|---|---|---|
| ACCION IBEX 35 ETF (ES0105336038) | Equity ETF, `physical` | 39 | 99.999% | 99.27% | 96.97% |
| FONMARCH, FI - Clase A (ES0138841038) | Bond, 3 classes | 111 | 100.000% | 97.93% | 97.58% |
| CaixaBank Monetario Rendimiento - Estándar (ES0138045002) | Money market, 8 classes | 445 | 99.999% | 94.15% | 79.25% |
| ABANCA Índice Renta Variable Emergente (ES0162956009) | Equity index, `swap` | 20 | 95.176% | 88.62% | 88.62% |
| Brunara SICAV (ES0137927135) | SICAV | 86 | 100.003% | 97.12% | 97.12% |
| Mutuafondo Mixto Dólar (ES0164745004) | USD fund | 66 | 100.000% | 84.18% | 84.18% |

All are as of 2026-06-30. The IBEX ETF's ISIN-less weight is a 2.31%
reverse repo plus cash. The money-market fund's gap is reverse repos and
deposits. The ABANCA fund's lines sum to 95.2% because its `FONDCART` lines
fall short of its reported portfolio. Its exposure comes from an index swap
with a nominal of 137% of net assets, so its bonds do not show what it
tracks.

## Update frequency

Quarterly (`frequency = "quarterly"`). Data for a quarter appears about four
months after it ends: June 2026 was published on 26 October 2026. Holdings
are therefore three to seven months old. The CNMV says files are not
re-issued except in exceptional cases. The ZIP is cached in
`ctx.cache_dir` as `cnmv-es-iic_<hash of URL>_<date>.zip`, one file per
document per day, written only after it parses.

## Replication

The filings have no replication field, so it is inferred from them:

- `swap` when the nominal of the compartment's total-return, index,
  credit-default or structured swaps (`FONDDERI` lines naming a swap,
  *permuta*, TRS or CDS) is at least 50% of net assets. Interest-rate swaps
  and futures do not count. In June 2026 this marked 18 share classes:
  ABANCA's emerging-markets index fund, FONDMAPFRE and Fondo Naranja
  guaranteed funds, Sabadell's CDS-based Buy and Watch, Evolución and
  Horizonte funds, and one bond fund with a 52% iBoxx TRS. Their securities
  are the swap's collateral, not their exposure.
- `physical` when the compartment tracks an index (ETF flag or `ClaseFondo`
  *Fondo que replica o reproduce un índice*) and its listed securities are
  at least 80% of net assets: 44 share classes, including the two ETFs and
  the CaixaBank, BBVA, Santander and ING index funds.
- `None` otherwise, which is most active funds.

## Terms and credit

Credit line: *Fuente: CNMV (Comisión Nacional del Mercado de Valores),
ficheros de información individual de IIC.*

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

The notice does not prescribe a credit wording; the line above names the
source. Each explanatory PDF says the data come from the funds' own filings,
the filers are responsible for their quality, and they may differ from CNMV
statistics that include later corrections.

## Known gaps

- **Lag**: quarterly, about four months behind (see above).
- **No reported weights**: weights are `ValorMercado / TotalPatrimonio`.
  Values are in whole currency units, and SICAV files round them to
  thousands of euros, so small SICAV lines are coarse.
- **Derivatives are not lines**: `FONDDERI` gives only nominal commitments,
  not market values, so derivatives are not added as holdings. Each result's
  notes give their count and total nominal as a share of net assets (BRUNARA
  SICAV: 9 positions, 20.31%). Their market value is inside `Resto`.
- **Swap and guaranteed funds** report collateral, not exposure (see
  Replication).
- **Reverse repos** (3.1% of FI value) and deposits are cash with no ISIN.
- **Portfolio shortfalls**: in 94 of 1,362 EUR fund compartments the
  `FONDCART` lines differ by more than 0.5% from the domestic plus foreign
  portfolio in `PATRIMDISVAR` (the worst lists 57% of it). Their lines sum
  to less than 100% and the rest counts as unresolved.
- **Missing filings**: 19 registry compartments have no net assets filed,
  and 26 that do (23 FI, 3 SICAV; EUR 1.5 bn, new or closing funds) have no
  holding lines. `holdings()` then returns an empty frame or just the
  balance lines, with a note.
- **Other Spanish UCIs**: only FI and SICAV appear in these files. Real
  estate funds, hedge funds (IICIL) and venture capital vehicles are not
  covered.

## Example

```python
from openbasket.sources.issuers import FetchContext
from openbasket.sources.issuers import cnmv_es_iic

p = cnmv_es_iic.products()
row = p[p["isin"] == "ES0105336038"].iloc[0].to_dict()
h = cnmv_es_iic.holdings(row, FetchContext(cache_dir="data/01_raw/issuers/holdings"))
h.as_of                        # '2026-06-30'
h.holdings.head(3)[["isin", "name", "weight_pct", "asset_class"]]
#            isin                                                            name  weight_pct asset_class
# 0          None  REPO|KINGDOM OF SPAIN|2,23|2026-07-01 (colateral ES00000125L3)    2.308618        cash
# 1  ES0130960018                                              ACCIONES|ENAGAS SA    0.431060      equity
# 2  ES0132105018                                            ACCIONES|ACERINOX SA    0.371502      equity
```
