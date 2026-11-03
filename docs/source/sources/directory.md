# Fund and bond directory

The directory holds no holdings. For any fund or bond ISIN it records who
issued it, where it is domiciled and where it trades, so that

- an ISIN nobody has looked up yet can be sent to the right issuer adapter
  (`holdings_route`), and
- coverage can be reported honestly: a fund that is in the directory but has
  no adapter is *fund known, no holdings source*.

```bash
kedro run --pipeline=directory
```

Code: `openbasket.sources.directory` (parsers and merge) and the `directory`
pipeline (`src/openbasket/pipelines/directory/`), configured in
`conf/base/parameters_directory.yml` and `conf/base/catalog_directory.yml`.
The pipeline downloads about 120 MB, so it is not part of a plain `kedro run`;
run it with `--pipeline=directory`.

Each source has a redistribution class (column *Class* below). ESMA FIRDS,
the ECB list and GLEIF are `public`; the SIX, Xetra and Euronext lists are
`local_only`, read for the person running openbasket only. The public build
(`kedro run --env public`, `conf/public/parameters.yml`) sets
`directory.public_only: true`: the `local_only` sources are not downloaded
at all, and the directory is built from the public ones, with
`esma_firds_funds` required to give rows.

Status as tested on 2026-11-03: working.

## Outputs

| Dataset | Rows (run of 2026-11-03) | Contents |
|---|---|---|
| `fund_directory` (`data/03_primary/fund_directory.parquet`) | 19,963 | One row per fund, ETF, ETN or ETC ISIN. |
| `bond_directory` (`data/03_primary/bond_directory.parquet`) | 281,399 | One row per bond ISIN. |
| `directory_coverage` (`data/08_reporting/directory_coverage.csv`) | | ISINs per issuer and domicile, with and without a holdings adapter. |
| `directory_sources` (`data/08_reporting/directory_sources.csv`) | one per source | What each source gave in the run: `status` (`ok`, `empty`, `off`, `local_only`, `failed`), rows, newest as-of date, redistribution class and the error if it failed. |
| `dir_<source>` (`data/02_intermediate/directory/`) | | Each source parsed on its own. |

`fund_directory` columns: `isin`, `name`, `issuer` (adapter-style key such as
`ishares`, `spdr`, `xtrackers`), `issuer_name`, `domicile`, `currency`,
`asset_class`, `replication` (`physical`, `swap` or empty), `instrument_type`
(`ETF`, `fund`, `ETN`, `ETC`, `REIT`, ...), `management` (active/passive, Xetra
only), `ticker`, `venues` (MIC codes), `fund_lei`, `manager_lei`,
`manager_name`, `umbrella_lei`, `umbrella_name`, `adapter` (the adapter that
can read this ISIN's holdings, see step 7 below), `holdings_route` (the
adapter, or `no_holdings_source`), a `<field>_source` column for every derived field,
`listed_in` (every source that lists the ISIN), and the citation columns
`source`, `source_url`, `credit` and `as_of`.

`bond_directory` columns: `isin`, `name`, `issuer_name`, `issuer_lei`,
`issuer_country`, `currency`, `maturity`, `coupon_pct`, `amount_issued`,
`bond_type`, `venues`, `<field>_source` columns, `listed_in`, `source`,
`source_url`, `credit`, `as_of`.

Every row carries its source, a link, a credit line for every source that
lists it, and an as-of date (the newest of the dates the listing sources
state; a FIRDS-only row has the FIRDS publication date).

## Sources

| Key | What | Endpoint | ISINs | As-of | Default | Class |
|---|---|---|---|---|---|---|
| `six_funds` | SIX Swiss Exchange funds and ETFs, with issuer, domicile, replication, asset class | `https://www.six-group.com/fqs/ref.json?select=...&where=PortalSegment=FU` | 3,088 (2,355 ETF rows, 877 other funds; 144 listed in two currencies) | `delayedDateTime` in the response | on | local_only |
| `six_bonds` | SIX-listed bonds with issuer, coupon, maturity, amount | same API, `where=PortalSegment=BO` | 3,011 (2,501 CHF) | `delayedDateTime` | on | local_only |
| `xetra` | Deutsche Boerse "all tradable instruments" for Xetra: ETF, ETN, ETC rows | CSV linked from `https://www.cashmarket.deutsche-boerse.com/cash-en/trading/Tradable-Instruments-Xetra/` | 3,641 (3,072 ETF, 364 ETN, 205 ETC) | `Date Last Update` line | on | local_only |
| `euronext_etfs` | Euronext ETF/ETP list, all Euronext markets and Borsa Italiana's ETFplus | POST to the `jsongateway_download` URL on `https://live.euronext.com/en/products/etfs/list` | 3,358 | date line of the CSV | on | local_only |
| `euronext_bonds` | Euronext bond list (Paris, Amsterdam, Brussels, Lisbon, Oslo, MOT, EuroTLX, ...) | same, from `https://live.euronext.com/en/products/fixed-income/list` | 14,986 | date line of the CSV | on | local_only |
| `ecb_eligible` | ECB eligible marketable assets: EUR bonds with issuer name and residence | `ea_csv_<yymmdd>.csv.gz` linked from `https://www.ecb.europa.eu/mopo/coll/assets/html/list-MID.en.html` | 31,375 | date in the file name | on | public |
| `esma_firds_funds` | ESMA FIRDS weekly full file `FULINS_C`: every fund unit traded on an EU venue, with the fund's LEI, CFI code and every venue | index `https://registers.esma.europa.eu/solr/esma_registers_firds_files/select`, files `https://firds.esma.europa.eu/firds/FULINS_C_<date>_01of01.zip` | 18,698 | publication date | on | public |
| `esma_firds_debt` | ESMA FIRDS `FULINS_D` (4 files, about 70 MB zipped): bonds traded on EU venues, with issuer LEI, coupon, maturity | same index, `FULINS_D_<date>_0Nof04.zip` | 274,321 (structured products CFI `DE`/`DS` left out) | publication date | on | public |
| `fca_firds_funds` | FCA FIRDS `FULINS_C` (London and other UK venues) | `https://api.data.fca.org.uk/fca_data_firds_files` | 21,027 in the 2026-10-31 file | publication date | **off** | local_only |
| `gleif` | GLEIF ISIN-LEI file, level 2 relationship file (`IS_FUND-MANAGED_BY`, `IS_SUBFUND_OF`), LEI records API for names and jurisdictions | `https://mapping.gleif.org/api/v2/isin-lei/latest`, `https://goldencopy.gleif.org/api/v2/golden-copies/publishes/latest` (`rr` CSV), `https://api.gleif.org/api/v1/lei-records?filter[lei]=...` | 97,789 ISIN-LEI pairs matched; 227,604 active fund links | daily golden copy | on | public |

All downloads go through `openbasket.http` (one request per second per host;
the ECB's `Crawl-delay: 5` and GLEIF's 60 requests a minute are honoured).
Raw files are cached under `data/01_raw/directory/<source>/` (one file per
day, per publication for FIRDS) and written only after they parsed. Where the
file's URL carries its date (the ECB's `ea_csv_<yymmdd>`, Xetra's CSV link),
that URL is saved next to the cached file, so a rerun on the same day keeps
the as-of date and the file link. A full run fetches about 120 MB and took
5.5 minutes on 2026-11-03; a rerun on the same day reads the cache. A source
that fails is logged, reported in `directory_sources` and skipped (or fails
the run with `strict: true`). When there are more LEIs to name than
`gleif_api_max_leis`, managers and umbrellas are looked up first and the
number left out is logged.

### How a fund ISIN is resolved

1. All fund listings are stacked. Each field is taken from the best source
   that has it (order in `FUND_PRIORITY`): the name from SIX, else Euronext's
   full name, else FIRDS, else Xetra's abbreviated name; domicile and
   replication from SIX; and so on. The source is kept in `<field>_source`.
2. `fund_lei` is the FIRDS issuer of the units (the sub-fund's own LEI), else
   GLEIF's ISIN-LEI map.
3. GLEIF's relationship file gives the fund manager (`IS_FUND-MANAGED_BY`)
   and umbrella (`IS_SUBFUND_OF`); a sub-fund without its own manager link
   takes its umbrella's (`manager_source = gleif_umbrella`). Names and
   jurisdictions of those LEIs come from the GLEIF API (about 100 per
   request).
4. `issuer` is the first brand found in the fund name, then the SIX issuer,
   then the manager, umbrella and fund legal names (`ISSUER_PATTERNS` in the
   module, leftmost match wins: "iShares J.P. Morgan EM Bond" is iShares).
   The brand in the name comes first because third-party management
   companies (Carne, Waystone, FundRock, Universal-Investment, Hansainvest)
   run many brands. Examples of the mapping: BlackRock, iShares, BGF ->
   `ishares`; State Street, SSGA, SPDR -> `spdr`; DWS, Xtrackers ->
   `xtrackers`; Amundi, Lyxor, Multi Units -> `amundi`; UBS and Credit Suisse
   -> `ubs`; Swisscanto, ZKB -> `swisscanto`; J.P. Morgan -> `jpmorgan`.
5. Domicile is SIX's legal-structure country, else the jurisdiction of the
   fund's LEI in GLEIF, else the ISIN's country prefix (`domicile_source =
   isin_prefix`; XS and EU prefixes are left empty).
6. Replication is SIX's (`Synthetic` -> `swap`), else `swap` when the name
   says "Swap" or "Synthetic" (`replication_source = name`).
7. The issuer key is the brand; the route is separate. `adapter` is set when
   an adapter of that name is registered in `openbasket.sources.issuers` (or
   listed in `extra_adapters`) and can read this ISIN: an adapter in
   `etf_only_adapters` (iShares, SPDR, Xtrackers, Amundi, UBS and other ETF
   issuers) routes only `instrument_type = ETF`, so a brand's mutual funds
   (BGF, DWS funds) stay `no_holdings_source`. `holdings_route` is that
   adapter or `no_holdings_source`.

Two brand rules: Raiffeisen funds get the key `raiffeisen`, except Swiss
Raiffeisen funds (domicile CH, or a GLEIF manager that is Vontobel), which
Vontobel runs and which get `vontobel`. Short brand keys that also occur as
ordinary words (`ark`, `gam`, `seb`, `purpose`, `efg`, `axa` and a few more)
are taken from a fund's name only when GLEIF's manager agrees, or when GLEIF
names no manager.

`openbasket.sources.directory.route(isin, fund_directory)` returns the row
for one ISIN.

## Numbers from the run of 2026-11-03

Funds: 19,963 ISINs. 8,173 ETFs, 8,232 open-ended funds, 2,116 other funds
(CFI `CM`), 537 ETNs, 249 ETCs, 240 funds of funds, 191 REITs, 118 hedge
funds, 67 unclassified ETPs, 40 private-equity and pension funds.

| Domicile | ISINs | Source of domicile | ISINs |
|---|---|---|---|
| LU | 6,904 | ISIN prefix | 9,529 |
| IE | 5,852 | GLEIF jurisdiction | 6,928 |
| US | 2,533 | SIX | 3,088 |
| DE | 1,239 | none (XS ETPs) | 418 |
| ES | 499 | | |
| DK | 463 | | |
| CH | 358 | | |
| AT, NL, FR, CA, JE, GB, KY | 316, 288, 191, 140, 113, 103, 61 | | |

Issuer known for 80.7% of ISINs (14,654 from the fund name, 1,334 from GLEIF
names, 113 from the SIX issuer); 89.7% of ETFs. Fund LEI known for 96.8%,
manager LEI for 85.2%.

| Issuer | ISINs | of which IE | LU | CH | DE | US |
|---|---|---|---|---|---|---|
| ishares | 1,886 | 970 | 375 | 12 | 69 | 395 |
| amundi | 1,360 | 122 | 1,067 | 0 | 26 | 0 |
| ubs | 876 | 199 | 598 | 72 | 6 | 1 |
| xtrackers | 747 | 216 | 412 | 3 | 84 | 25 |
| barings | 674 | 665 | 0 | 0 | 0 | 0 |
| invesco | 559 | 302 | 66 | 0 | 4 | 182 |
| vanguard | 538 | 440 | 1 | 0 | 0 | 61 |
| bnpparibas | 455 | 71 | 307 | 0 | 63 | 0 |
| spdr | 453 | 179 | 148 | 0 | 0 | 122 |
| jpmorgan | 442 | 203 | 200 | 0 | 0 | 37 |
| goldman_sachs, wisdomtree, fidelity, franklin, hsbc | 424, 375, 334, 318, 311 | | | | | |
| pictet, allianz, schroders, leverage_shares, deka | 258, 239, 238, 213, 206 | | | | | |
| swisscanto | 91 | | | 52 | | |
| unknown | 3,862 | 434 | 761 | 77 | 626 | 507 |

Swiss-domiciled funds (358): UBS 72, 21Shares 55, Swisscanto 52, LLB 25,
Vontobel 15, BCV (Gerifonds) 13, iShares 12, Pictet 10, unknown 77.

Replication is known for 2,201 funds: 1,847 physical, 354 swap (2,119 from
SIX, 82 from the name).

Bonds: 281,399 ISINs. By ISIN prefix: XS 163,847, US 41,259, FR 23,291, DE
20,056, NO 3,477, CH 3,206, ES 2,430, AT 2,407. By currency: EUR 122,539, USD
108,781, GBP 11,602, HKD 4,756, CHF 4,333. Filled: name 97.9%, issuer LEI
99.4%, currency 99.7%, maturity 100%, coupon 87.7%, issuer name 15.3% (SIX,
ECB and Euronext only; FIRDS gives the LEI, not the name).

Example rows:

| ISIN | Name | issuer | Domicile | Replication | holdings_route (no adapters in this checkout) |
|---|---|---|---|---|---|
| IE00B5BMR087 | iShares Core S&P 500 UCITS ETF USD (Acc) | ishares | IE (SIX) | physical | no_holdings_source |
| LU0490618542 | Xtrackers S&P 500 Swap UCITS ETF 1C | xtrackers | LU (SIX) | swap | no_holdings_source |
| IE00B3YCGJ38 | Invesco S&P 500 UCITS ETF | invesco | IE (SIX) | swap | no_holdings_source |
| CH0008899764 | iShares SMI ETF (CH) | ishares | CH (SIX) | physical | no_holdings_source |
| CH1416135338 | UBS Core SPI ETF CHF acc | ubs | CH (SIX) | physical | no_holdings_source |
| US4642872000 | iShares Core S&P 500 ETF (FIRDS name) | ishares | US (GLEIF) | | no_holdings_source |

| Bond ISIN | Name | Issuer | Currency | Maturity | Coupon | Listed in |
|---|---|---|---|---|---|---|
| CH1189217750 | 1.45 SFSG 22-27 | SFS Group AG | CHF | 2027-06-08 | 1.45 | SIX, FIRDS |
| DE0001102580 | BUND TF 0% FB32 EU | Federal Republic of Germany (ECB) | EUR | 2032-02-15 | 0 | ECB, FIRDS, Euronext |

## Update frequency

SIX, Xetra, Euronext and ECB lists are daily; ESMA FIRDS full files are
weekly (Saturday); GLEIF files are daily. Run the pipeline daily or weekly.

## Terms and credit

- **SIX**: public website data API, no login; responses carry "(c) Copyright
  by SIX Group Ltd. All rights reserved." Only reference fields are kept, no
  prices. Credit: "Reference data: SIX Swiss Exchange, (c) SIX Group Ltd".
- **Deutsche Boerse**: public CSV, no login, no licence stated; the website
  terms apply. Credit: "Instrument list: Deutsche Boerse AG (Xetra)".
- **Euronext**: public CSV download, no login; website terms apply. Price
  columns are dropped. Credit: "Product directory: Euronext N.V.".
- **ECB**: public download; the ECB's copyright notice permits reproduction
  provided the source is acknowledged. The `robots.txt` `Crawl-delay: 5` is
  honoured.
- **ESMA FIRDS**: ESMA's legal notice authorises reproduction provided the
  source is acknowledged. Credit: "Source: ESMA Financial Instruments
  Reference Data System (FIRDS)".
- **GLEIF**: CC0 1.0. Credit: "LEI data: Global Legal Entity Identifier
  Foundation (GLEIF), CC0".
- **FCA FIRDS** is **off by default**. The FCA website terms (clauses 3.3 and
  3.4 of `https://www.fca.org.uk/legal`) forbid storing its material in other
  websites or retrieval systems, or using it for a data feed, without written
  permission, and `data.fca.org.uk/robots.txt` sets `ai-train=no`. Switch it
  on (`sources.fca_firds_funds: true`) only for private use or with the
  FCA's permission. It would add London-listed share classes.

The SIX, Deutsche Boerse and Euronext lists are reference data published for
the public on the exchanges' own sites, without a stated open licence, and
the EU database right can cover reusing a substantial part of a database. So
they are `local_only`: a local run uses them to route and label ISINs, and
the public build does not read them.

## Known gaps

- **Issuer unknown for 3,862 ISINs**, mostly German, Danish and Spanish local
  funds and SICAVs, and funds run by white-label management companies
  (Universal-Investment, Hansainvest, Waystone, FundRock, IPConcept, Axxion)
  whose names carry no brand in `ISSUER_PATTERNS`. Add patterns as adapters
  appear.
- **Brand is not portfolio manager.** BlackRock's non-ETF ranges (BGF, BSF)
  get the issuer key `ishares` and DWS mutual funds `xtrackers`, but they are
  not routed to those ETF-only adapters (see step 7).
- **Public build.** Without SIX, the public directory has no replication from
  SIX and fewer issuer names; FIRDS, GLEIF and the ISIN prefix still give
  name, LEI, manager and domicile.
- **Replication is only known for SIX-listed funds** (and names containing
  "Swap"). Amundi and others run swap ETFs whose names do not say so; for
  those, replication is empty unless the fund is listed on SIX.
- **Asset class** is set for 28.6% of funds (SIX, Xetra's bond segment,
  FIRDS CFI commodity/real-estate codes, bond words in the name).
- **US funds** appear only when they trade on an EU venue (2,533, mostly
  ETFs). US mutual funds and most US ETFs are not in any of these lists; the
  SEC data sets (`company_tickers_mf.json`, N-PORT) carry tickers and series
  IDs, not fund ISINs.
- **UK**: without FCA FIRDS, GBP share classes listed only in London are
  missing.
- **Asia-Pacific and the Americas** outside the US are not covered by any
  free ISIN-keyed list found so far (JPX, HKEX and ASX lists use local codes).
- **Bond issuer names** are only filled where SIX, the ECB or Euronext name
  the issuer (15%); `issuer_lei` (99.4%) is the reliable key, and GLEIF names
  it.
- FIRDS lists one record per venue; venues are the union of all records. The
  ISIN prefix as domicile is right for almost all IE, LU, DE, FR and CH funds
  but is a proxy, labelled as such.
- SIX lists 144 ETFs twice (two trading currencies); they are kept once.
