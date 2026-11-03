# Fund registry (GLEIF, ESMA, SEC, regulators)

The registry holds no holdings. It lists every fund that open data names, with
its LEI, share-class ISINs, management company, the group that owns that
company, umbrella and domicile, so that

- an ISIN with no open portfolio can be shown as **known fund, no open
  holdings source** instead of "unknown", and
- an ISIN that one of the open regulator datasets covers is routed there
  (`open_holdings_source`).

```bash
kedro run --pipeline=registry
```

Code: `openbasket.sources.gleif_fund_registry` (downloads, parsers, merge,
bank rules) and the `registry` pipeline (`src/openbasket/pipelines/registry/`),
configured in `conf/base/parameters_registry.yml` and
`conf/base/catalog_registry.yml`.

Only sources whose terms allow reuse and republication are used (quotes under
[Terms](#terms-and-credit)). No issuer website is read. The `directory`
builder covers exchange lists (SIX, Xetra, Euronext); this registry does not
repeat them, and adds the whole GLEIF fund population, the ESMA fund registers
and the regulator fund lists.

## Outputs

| Dataset | Rows (run of 2026-11-03) | Contents |
|---|---|---|
| `fund_entities` (`data/03_primary/fund_entities.parquet`) | 386,219 | One row per fund: its LEI, else an ESMA register id, an SEC series id or a regulator portfolio id. |
| `fund_registry` (`data/03_primary/fund_registry.parquet`) | 275,181 | One row per fund ISIN (share class). |
| `registry_coverage` (`data/08_reporting/registry_coverage.csv`) | 500 | Funds and ISINs per manager group, with bank flag and domiciles. |
| `registry_banks` (`data/08_reporting/registry_banks.csv`) | 209 | Funds and ISINs per bank group, and how many have an open holdings source. |
| `registry_domiciles` (`data/08_reporting/registry_domiciles.csv`) | | Funds, bank-run funds and ISINs per domicile. |
| `registry_summary` (`data/08_reporting/registry_summary.json`) | | Headline counts. |
| `registry_<source>` (`data/02_intermediate/registry/`) | | Each source parsed on its own. |

`fund_registry` columns: `isin`, `lei`, `name`, `manager_lei`,
`manager_name`, `manager_group_lei`, `manager_group_name` (the management
company's GLEIF parent), `bank_group`, `bank_group_rule`, `umbrella_lei`,
`umbrella_name`, `master_lei` (for feeder funds), `domicile`, `legal_form`,
`legal_framework` (UCITS, AIF, EuVECA, ELTIF, EuSEF), `cfi`, `currency`,
`venues`, `fund_status`, `sec_series_id`, `open_holdings_source`, and a
`<field>_source` column for `lei`, `name`, `manager`, `umbrella`,
`domicile`, `legal_framework`, `cfi` and `currency`. Every row carries
`listed_in` (every source that lists it) and the citation columns `source`,
`source_url`, `credit`, `licence` (link to each source's terms) and `as_of`
(the newest date among the sources that list the row).

`fund_entities` has the same fund-level columns plus `fund_id`, `category`
(GLEIF entity category), `jurisdiction`, `entity_status`, `esma_register_id`,
`sec_tickers` and `n_isins`.

`openbasket.sources.gleif_fund_registry.route(isin, fund_registry)` returns
the row for one ISIN with a `status` of `open holdings source: <adapter>`,
`known fund, no open holdings source` or `unknown`.

## Sources

| Key | What | Endpoint | Rows used | As-of |
|---|---|---|---|---|
| `gleif` | GLEIF Level 1 golden copy (`lei2` CSV, 3,482,754 records, 484 MB zipped): records with category `FUND`, and the managers, umbrellas and parents they point to | index `https://goldencopy.gleif.org/api/v2/golden-copies/publishes/latest`, then the `lei2` `full_file.csv.url` | 251,943 funds (202,485 active) and 66,521 other entities | daily, 08:00 UTC publish (2026-11-03) |
| `gleif` (Level 2) | GLEIF relationship records (`rr` CSV, 23 MB): active `IS_FUND-MANAGED_BY`, `IS_SUBFUND_OF`, `IS_FEEDER_TO`, `IS_ULTIMATELY_CONSOLIDATED_BY`, `IS_DIRECTLY_CONSOLIDATED_BY` | same index, `rr` file | 152,856 manager, 74,748 umbrella, 1,399 master, 133,627 ultimate-parent and 127,422 direct-parent links | daily |
| `gleif_isin_lei` | GLEIF and ANNA ISIN-to-LEI mapping (33 MB zipped) | `https://mapping.gleif.org/api/v2/isin-lei/latest`, then its `downloadLink` | 233,497 ISINs of fund LEIs | daily (`uploadedAt` 2026-11-03) |
| `esma_firds` | ESMA FIRDS weekly full file `FULINS_C`: every collective investment vehicle unit on an EU venue, with ISIN, full name, CFI, currency, issuer (fund) LEI, venues | index `https://registers.esma.europa.eu/solr/esma_registers_firds_files/select`, file `https://firds.esma.europa.eu/firds/FULINS_C_<date>_01of01.zip` (3.8 MB) | 18,698 ISINs | weekly, Saturday (2026-10-31) |
| `esma_funds` | ESMA central database of UCITS and AIFs marketed cross-border (Regulation (EU) 2019/1156) and ESMA register of AIFs, EuVECA, EuSEF and ELTIF: fund name, LEI where given, legal framework, domicile, manager name and LEI | `https://registers.esma.europa.eu/solr/esma_registers_funds_cbdif/select` and `.../esma_registers_funds/select`, `q=type_s:parent`, 1,000 rows a page (ESMA documents these URLs for application-to-application use at `https://registers.esma.europa.eu/publication/helpApp`) | 35,289 + 108,678 fund records (16,013 + 10,294 with an LEI) | continuous; newest `funds_last_update_date` |
| `sec` | The project's `fund_master` (SEC `company_tickers_mf.json` joined to N-PORT series LEIs), read from `registry.sec_fund_master` when the `security_master` pipeline has built it | local file | 31,532 classes of 15,187 series | N-PORT report date (2026-07-31) |
| `cnmv_spain` | Fund and SICAV share classes of the CNMV quarterly files, through the existing adapter | `openbasket.sources.issuers.cnmv_spain.products()` | 3,510 ISINs | quarter end (2026-06-30) |
| `cvm_brazil` | Fund quota ISINs of funds that filed a CVM CDA, through the existing adapter | `openbasket.sources.issuers.cvm_brazil.products()` | 27,950 ISINs | month end (2026-09-30) |

All downloads go through `openbasket.http` (one request per second per host).
The GLEIF files are streamed to `data/01_raw/registry/<source>/`, one copy of
each (older ones are deleted), and parsed in chunks: only a dozen Level 1
columns are kept, and no addresses. A full run on 2026-11-03 took 14 minutes,
most of it the 484 MB `lei2` download; a rerun on the same day reads the cache
(the ESMA registers, 145 requests, are fetched again). The regulator stores
are read only if they are already built (`registry.refresh_regulators: true`
downloads them, as `reg_spain` and `reg_brazil` would).

### How a fund is resolved

1. **Funds.** Every GLEIF record with `Entity.EntityCategory = FUND`, every
   FIRDS `FULINS_C` issuer LEI (many SICAVs are registered as `GENERAL`),
   every LEI in the ESMA registers, every N-PORT series LEI and every LEI of a
   regulator-listed ISIN. ESMA register funds without a valid LEI are added
   once per name, domicile and manager; SEC series without an LEI and
   regulator portfolios with no LEI on any ISIN are added too. LEIs are
   checked with ISO 17442 check digits, so placeholders such as
   `00000000000000000000` in N-PORT are dropped.
2. **ISINs.** FIRDS `FULINS_C` (one row per ISIN; venues are the union of
   its records), then GLEIF's ISIN-LEI pairs whose LEI is a fund, then the
   CNMV and CVM lists. `lei_source` is `esma_firds` (the issuer of the units)
   or `gleif_isin_lei`.
3. **Manager**: the fund's `IS_FUND-MANAGED_BY` link (`gleif_rr`), else its
   umbrella's (`gleif_rr_umbrella`), else the ESMA register's manager LEI or
   name (`esma_funds`), else CNMV's *gestora* (`cnmv_spain`). Names come from
   GLEIF where the manager has an LEI.
4. **Manager group**: the manager's `IS_ULTIMATELY_CONSOLIDATED_BY` parent,
   else its direct parent, else the manager itself. This is how bank-owned
   asset managers are tied to their bank: Swisscanto Fondsleitung to
   Zürcher Kantonalbank, Deka Investment to DekaBank, Union Investment to DZ
   BANK, J.P. Morgan Investment Management to JPMorgan Chase & Co.
5. **Bank group** (`bank_group`, `bank_group_rule`): the group name, then the
   manager name, matched against `NAMED_BANKS` in the module (UBS, ZKB and
   Swisscanto, Raiffeisen, Deka and the Sparkassen, DZ Bank and Union
   Investment, Santander, CaixaBank, BBVA, Nordea, Danske, SEB, Swedbank,
   Handelsbanken, KBC, ING, Erste, Itaú, Bradesco, HDFC, ICICI, SBI and about
   100 more) -> `named`; else a name containing a bank word (bank, banco,
   banque, Kantonalbank, Sparkasse, caixa, ...) -> `generic_name`; else, only
   when no manager is known at all, a named bank brand in the fund's own name
   -> `fund_name` (how Brazilian funds such as "ITAÚ ..." and "BRADESCO ..."
   are flagged, since CVM's list carries no manager). This is a name rule,
   not a regulatory classification.
6. **Umbrella** and **master**: `IS_SUBFUND_OF` and `IS_FEEDER_TO`.
7. **Domicile**: the GLEIF legal jurisdiction's country, else the ESMA
   register's domicile, else GLEIF's legal-address country, else the ESMA
   register's competent-authority country (`esma_funds_authority`, a proxy),
   else `US` for SEC series; for ISIN rows still without one, the ISIN prefix
   (`isin_prefix`; XS and EU prefixes are left empty).
8. **Open holdings source**: `cnmv_spain` or `cvm_brazil` when the ISIN is in
   that adapter's list (full portfolio from the regulator filing), else
   `sec_nport` when the fund's LEI is an N-PORT series LEI, else empty.

## Numbers from the run of 2026-11-03

| | Funds | Fund ISINs |
|---|---|---|
| Total | 386,219 | 275,181 |
| with an LEI | 254,597 | 247,157 |
| with a manager (LEI or name) | 261,536 (210,920 with the manager's LEI) | 138,243 |
| with an umbrella | 74,740 | 51,276 |
| bank-run (`bank_group` set) | 84,583 (21.9%) | 50,300 |
| with an open holdings source | 41,371 | 36,778 |
| known fund, no open holdings source | 344,848 | 238,403 |

Funds by kind of id: LEI 254,597; ESMA AIF register without LEI 84,244; ESMA
cross-border database without LEI 19,182; CVM portfolio without LEI 26,145;
SEC series without LEI 2,006; CNMV portfolio without LEI 45. Open holdings
sources: CVM 26,145 funds (27,950 ISINs), N-PORT 13,169 funds (5,290 ISINs),
CNMV 2,057 funds (3,538 ISINs). Legal framework (ESMA): AIF 114,457, UCITS
22,344, EuVECA 1,997, ELTIF 268, EuSEF 43.

By domicile (funds / bank-run funds / ISINs):

| Domicile | Funds | Bank-run | ISINs | | Domicile | Funds | Bank-run | ISINs |
|---|---|---|---|---|---|---|---|---|
| LU | 64,850 | 18,549 | 13,126 | | ES | 15,415 | 7,518 | 6,454 |
| US | 47,158 | 3,566 | 21,363 | | JP | 15,120 | 105 | 76 |
| FR | 40,652 | 13,591 | 90,154 | | GB | 13,435 | 971 | 20,548 |
| IE | 27,444 | 4,062 | 32,201 | | CA | 9,757 | 1,340 | 4,760 |
| BR | 26,720 | 5,051 | 27,950 | | IT | 6,735 | 2,449 | 7,905 |
| DE | 20,402 | 8,144 | 9,247 | | AU, IN, NL, AT, PL | 6,308, 6,054, 5,884, 5,804, 5,142 | | |
| KY | 19,442 | 1,155 | 8,030 | | CH | 3,268 | 1,958 | 9,481 |

Bank groups (funds / ISINs / funds with an open holdings source):

| Bank group | Funds | ISINs | Open | | Bank group | Funds | ISINs | Open |
|---|---|---|---|---|---|---|---|---|
| Crédit Agricole (Amundi) | 10,153 | 4,785 | 84 | | Raiffeisen | 1,137 | 150 | 0 |
| BNP Paribas | 4,925 | 2,229 | 120 | | CaixaBank | 1,125 | 343 | 116 |
| UBS | 4,543 | 7,029 | 656 | | Bradesco | 1,095 | 1,122 | 1,079 |
| KBC | 3,507 | 776 | 0 | | Erste Group | 869 | 131 | 0 |
| Crédit Mutuel | 2,963 | 1,296 | 0 | | Nordea | 656 | 173 | 16 |
| HSBC | 2,805 | 1,433 | 1 | | Danske Bank | 622 | 319 | 0 |
| Santander | 2,797 | 1,514 | 1,102 | | Zürcher Kantonalbank (Swisscanto) | 491 | 1,232 | 0 |
| Deka (Sparkassen) | 2,320 | 897 | 0 | | SEB | 475 | 14 | 0 |
| Deutsche Bank (DWS) | 2,152 | 1,497 | 98 | | ICICI | 396 | 0 | 0 |
| DZ Bank (Union Investment) | 2,086 | 735 | 0 | | Handelsbanken | 289 | 95 | 0 |
| Intesa Sanpaolo (Eurizon) | 2,057 | 1,160 | 0 | | Swedbank | 280 | 0 | 0 |
| Itaú Unibanco | 2,047 | 1,938 | 1,882 | | HDFC | 264 | 0 | 0 |
| JPMorgan Chase | 1,518 | 1,364 | 173 | | Banque Cantonale Vaudoise | 240 | 324 | 0 |
| BBVA | 1,490 | 473 | 88 | | ING | 237 | 446 | 0 |
| Helaba | 1,321 | 438 | 0 | | State Bank of India | 106 | 0 | 0 |

209 bank groups in all (`registry_banks.csv`). Largest manager groups
(`registry_coverage.csv`): group unknown 124,689 funds; BlackRock, Inc.
3,514; Crédit Agricole SA 2,322; UBS Group AG 2,320; BNP Paribas 2,198;
Crédit Mutuel Asset Management 2,141; Amundi Asset Management (ESMA name, no
LEI link) 2,126; Universal-Investment 2,070; Allianz SE 2,046; HSBC Holdings
2,012; DekaBank 2,007.

### Sample lookups

Five bank-run funds found in the registry and routed to their open holdings
source (holdings read through the existing adapters on 2026-11-03; weights
are percent of net assets as filed, never rescaled):

| ISIN | Fund | Bank group (rule) | Route | As of | Lines | Weight sum | With ISIN |
|---|---|---|---|---|---|---|---|
| ES0155030036 | Inverpamobe SICAV (Santander Asset Management) | Santander (named) | cnmv_spain | 2026-06-30 | 56 | 99.98% | 88.72% |
| ES0138045044 | CaixaBank Monetario Rendimiento FI, Sin Retro | CaixaBank (named) | cnmv_spain | 2026-06-30 | 445 | 100.00% | 79.25% |
| ES0117091035 | BBVA Crédito Europa FI, Clase 0 | BBVA (named) | cnmv_spain | 2026-06-30 | 312 | 99.61% | 95.59% |
| BR0M9DCTF000 | Itaú Solutions Rígel Premium FIF | Itaú Unibanco (fund_name) | cvm_brazil | 2026-09-30 | 5 | 100.00% | 100.03% |
| BRAIE1CTF008 | Bradesco SML FIF CIC Ações | Bradesco (fund_name) | cvm_brazil | 2026-09-30 | 4 | 100.00% | 99.75% |

Bank-run funds the registry knows but no open source covers (status *known
fund, no open holdings source*):

| ISIN | Name (FIRDS) | Manager -> group | Umbrella |
|---|---|---|---|
| LU0081697723 | Swisscanto (LU) Bond Sustainable Global Aggregate AA | Swisscanto Asset Management International -> Zürcher Kantonalbank | |
| DE000DK2EAL7 | Deka-Globale Renten High Income CF | Deka Investment GmbH -> DekaBank Deutsche Girozentrale | |
| LU0049785792 | UBS (Lux) Strategy Balanced USD P-acc | UBS Asset Management (Europe) -> UBS Group AG | |
| LU0278531701 | Nordea 1 US High Yield Bond BP-EUR | Nordea Investment Funds S.A. | Nordea 1, SICAV |

US46641U2345 (JPMorgan SmartRetirement Blend 2045, manager J.P. Morgan
Investment Management -> JPMorgan Chase & Co., umbrella JPMorgan Trust I) is
routed to `sec_nport`.

## Freshness

GLEIF files are published daily (08:00 UTC golden copy; ISIN-LEI shortly
after), FIRDS full files weekly on Saturday, the ESMA registers as national
authorities notify ESMA (newest record update in the run: 2026-07-29 index
time), the CNMV files quarterly about four months after quarter end, CVM
monthly, N-PORT quarterly with a 60-day lag. Run the pipeline weekly; `as_of`
on each row is the newest date of the sources that list it.

## Terms and credit

Each source's terms were read on 2026-11-03.

- **GLEIF** (Level 1, Level 2, ISIN-LEI): CC0 1.0. GLEIF's LEI data terms of
  use (`https://www.gleif.org/en/meta/lei-data-terms-of-use`): "The data
  available through the Access Service are provided under the CC0 licence".
  `api.gleif.org/robots.txt` and `mapping.gleif.org/robots.txt` disallow
  nothing; `goldencopy.gleif.org/robots.txt` returns 403 (no file). Credit: "LEI data:
  Global Legal Entity Identifier Foundation (GLEIF)".
- **ESMA FIRDS and fund registers**: the ESMA registers legal notice
  (`https://registers.esma.europa.eu/publication/legalNoticePage`): "Reproduction
  of all information on this site (REGISTERS information) is authorised except
  as otherwise stated, provided the source is acknowledged". The same notice
  asks that transformed information be labelled as such: the registry merges
  the register rows with GLEIF and FIRDS, and says so here and in each row's
  `listed_in`. ESMA's main legal notice
  (`https://www.esma.europa.eu/legal-notice`) has the same clause for the ESMA
  library. Credits: "Source: ESMA Financial Instruments Reference Data System
  (FIRDS)" and "Source: ESMA registers (cross-border marketing of UCITS and
  AIFs; AIF register)".
- **SEC**: sec.gov's privacy and website policies: information on sec.gov is
  public information that may be copied or further distributed without the
  SEC's permission.
- **CNMV** and **CVM**: as on their adapters' pages (see *Regulator filings*).

No source needs a login, key, registration or consent click.

### Personal data

GLEIF `SOLE_PROPRIETOR` records are natural persons; when one is named as a
fund's manager (19 records are kept as related parties), its name is dropped
and only the LEI is kept. Addresses are not kept from any source. ESMA
manager names are those of authorised or registered managers as published by
ESMA. Raw files stay in `data/01_raw/registry/` and are not committed or
published.

## Known gaps

- **Manager unknown for 124,683 funds** (32%): GLEIF funds without an
  `IS_FUND-MANAGED_BY` link (96,323; the link is reported by 74% of US, 70%
  of Irish, 63% of Luxembourg and 58% of Cayman fund LEIs, and by 1% of
  Japanese ones), Brazilian funds (CVM's list has no manager), and ESMA
  records without a manager.
- **Bank flag is a name rule.** It catches groups whose GLEIF parent or
  manager is named after a bank or is in `NAMED_BANKS`; a bank-owned manager
  whose parent link is missing and whose name gives no hint (for example
  Mutuactivos, GVC Gaesco) is not flagged. Goldman Sachs, Morgan Stanley,
  State Street and BNY are flagged because their parents are bank holding
  companies. `fund_name` flags rely on the fund's brand only.
- **ISINs**: 275,181 ISINs for 386,219 funds. The ISIN-LEI map covers only
  what national numbering agencies send to GLEIF: strong for FR (90,154), IE,
  GB, LU, CH; weak for the US (CUSIP-based ISINs are mostly absent), Japan,
  India and Australia (funds known by LEI but almost no ISINs).
- **CFI** only for the 18,698 ISINs on EU venues (FIRDS).
- **Domicile from the authority** (`esma_funds_authority`) is the country of
  the authority that notified ESMA, a proxy used for 48,844 register records
  without a domicile.
- **Duplicates**: an ESMA record without an LEI that is the same fund as a
  GLEIF record (different spelling) is counted twice. ESMA records without an
  LEI are de-duplicated only among themselves.
- **Not used**: FCA FIRDS (UK venues) and the FCA register: an earlier review
  of the FCA website terms (clauses 3.3 and 3.4 of
  `https://www.fca.org.uk/legal`) found they forbid storing FCA material in
  other retrieval systems without written permission;
  national registers such as CSSF, Central Bank of Ireland, AMF GECO and BaFin
  were not checked for an open licence in this wave; issuer websites are out
  of scope.
