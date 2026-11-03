# ESMA FIRDS

FIRDS, the Financial Instruments Reference Data System, is ESMA's register of
every instrument admitted to trading or traded on an EU or EEA venue. It has
one record per instrument and venue, with the ISIN, full and short name, CFI
code, issuer LEI, notional currency and, for debt, maturity, coupon and issued
amount. Swiss, US and other non-EU securities are in it too when an EU venue
lists them: Nestlé, Roche, UBS and ABB shares, Swiss franc bonds, and Swiss
ETFs and real-estate funds all resolve.

FIRDS is not a holdings source. openbasket uses it as an issuer-independent
security master: to name and classify any ISIN (fund, debt or equity), to
find a fund's LEI, and to describe bonds that no fund file describes.

Code: `openbasket.sources.firds`. Pipeline: `reference`
(`src/openbasket/pipelines/reference/`), with its catalog in
`conf/base/catalog_reference.yml` and parameters in
`conf/base/parameters_reference.yml`.

## Per-ISIN lookup

Endpoint
: `https://registers.esma.europa.eu/solr/esma_registers_firds/select?q=isin:(<ISIN> OR <ISIN> ...) AND latest_received_flag:1&wt=json&rows=1000&fl=...`

  A public Solr index behind ESMA's register search, no key or login. It
  returns JSON documents, one per ISIN and venue, with fields such as
  `gnr_full_name`, `gnr_short_name`, `gnr_cfi_code`, `lei`,
  `gnr_notional_curr_code`, `mic`, `rca_mic`, `mrkt_trdng_start_date`,
  `mrkt_trdng_trmination_date`, `bnd_maturity_date`, `bnd_fixed_rate`,
  `bnd_fltng_rt_ndx_isin`, `bnd_fltng_rate_bs_pnt_sprd`,
  `bnd_nmnl_value_total`, `bnd_seniority`, `status` and `publication_date`.
  `latest_received_flag:1` keeps only the latest version of each venue record.

`firds.lookup(isin)` returns one dict, `firds.lookup_many(isins)` a frame with
one row per ISIN found. ISINs are checked (check digit) and sent 20 per
request as an `OR` query; the HTTP helper keeps to one request per second.
With `cache_dir`, each ISIN's records are cached for the day in
`<cache_dir>/firds/<date>/<ISIN>.json`, after the response has parsed. An
ISIN FIRDS does not have is cached as an empty list and returns no row.

In the pipeline, `firds_lookup` looks up the run's `isin` parameter (one or
several, separated by `;`) and writes `data/08_reporting/firds_lookup.csv`:

```console
kedro run --pipeline=reference --params="isin=CH0008899764;CH1619473973"
```

### From venue records to one row

The same rule (`firds.instruments_sql`) is used for Solr results and bulk
files:

1. For each ISIN and venue (MIC) the newest record wins; a cancelled record
   removes that venue.
2. A venue is active if it has no termination date, or one on or after the
   record's date. Bonds carry their maturity as a future termination date,
   so they stay active until then.
3. Fields come from the record of the most relevant market (`rca_mic`, the
   venue that reports the instrument to its regulator) if it is listed, else
   from an active venue, else from the newest record.
4. `full_name` is the name most venues report. Venues spell names
   differently (`iShares SMI ETF (CH) Inhaber-Anteile o.N.`,
   `BLACKROCK ISHARES SMI ETF CH`, `ISHARES SMI CH`), and the name at the most
   relevant venue is sometimes garbled.
5. `n_venues` and `n_active_venues` count venues; `termination_date` is set
   only when no venue is active; `as_of` is the newest publication date of
   the records used, not the day of the run.

Columns (`firds.INSTRUMENT_COLUMNS`): `isin`, `full_name`, `short_name`,
`cfi`, `asset_class`, `issuer_lei`, `currency`, `maturity_date`,
`coupon_pct`, `interest_type`, `floating_ref`, `floating_spread_bps`,
`nominal_total`, `nominal_currency`, `seniority`, `relevant_venue`, `venue`,
`n_venues`, `n_active_venues`, `first_trade_date`, `termination_date`,
`as_of`, `source` (`esma_firds`), `url` and `credit`. Dates are
`YYYY-MM-DD` strings.

`coupon_pct` is the fixed rate as FIRDS gives it, already in percent
(`1.4125` is 1.4125%). For floating-rate notes Solr reports a fixed rate of
`0`, so when a floating reference rate is present, or the CFI's third letter
is `V`, `interest_type` is `floating`, `coupon_pct` is empty, and
`floating_ref` holds the reference rate (an ISIN such as `EU0009652783` for
3-month Euribor, or an index code or name) with its spread in basis points.
A third CFI letter `Z` gives `zero`.

## Classification

`firds.classify(isin)` looks up the ISIN and returns its asset class from the
CFI code; `firds.classify_cfi(cfi)` does the same for a code, and
`firds.classify(isin, instruments=table)` uses a loaded table instead of the
API.

| CFI | `asset_class` |
|---|---|
| `CE` exchange-traded funds, `CI` standard funds, `CF` funds of funds, `CH` hedge, `CS` pension, `CP` private equity, `CM` other | `fund` |
| `CB` real estate investment trusts | `equity` (listed property companies, not baskets of securities) |
| `D*` bonds, notes, money market, ABS/MBS, structured debt | `debt` |
| `E*` shares, preferred shares, depositary receipts, units | `equity` |
| anything else (rights, warrants, options, futures, `R`, `O`, `F`, `S`, `H`, `J`, ...) | `other` |
| missing or not six letters | `unknown` |

`firds.cfi_kind(cfi)` gives a label for the first two letters, such as
`exchange-traded fund` or `medium-term note`.

## Bulk files (optional)

File index
: `https://registers.esma.europa.eu/solr/esma_registers_firds_files/select?q=*&fq=publication_date:[<from>T00:00:00Z TO <to>T23:59:59Z]&wt=json&rows=500`

  Each document has `file_name`, `file_type` (`FULINS`, `DLTINS`, `FULCAN`),
  `publication_date`, `download_link` and `checksum` (the ZIP's MD5). The index
  also returns rows with an empty `file_name`, which are dropped.

Files
: `https://firds.esma.europa.eu/firds/<file_name>`, a public file store. Each
  ZIP holds one ISO 20022 XML file.

  - `FULINS_<letter>_<YYYYMMDD>_<n>of<m>.zip`: the full snapshot, every
    Saturday, split by the CFI's first letter. Root
    `FinInstrmRptgRefDataRpt` (`auth.017.001.02`), one `RefData` element per
    instrument and venue.
  - `DLTINS_<YYYYMMDD>_<n>of<m>.zip`: the daily delta, all letters mixed.
    Root `FinInstrmRptgRefDataDltaRpt` (`auth.036.001.03`); `FinInstrm`
    elements hold a `NewRcrd`, `ModfdRcrd`, `TermntdRcrd` or `CancRcrd`.

  Both have the report date in `RptHdr/RptgPrd/Dt`, which becomes `as_of`.
  Record fields: `FinInstrmGnlAttrbts/{Id, FullNm, ShrtNm, ClssfctnTp,
  NtnlCcy}`, `Issr` (LEI), `TradgVnRltdAttrbts/{Id, FrstTradDt, TermntnDt}`,
  `DebtInstrmAttrbts/{TtlIssdNmnlAmt, MtrtyDt, IntrstRate/Fxd,
  IntrstRate/Fltg/{RefRate, BsisPtSprd}, DebtSnrty}`,
  `TechAttrbts/{RlvntTradgVn, PblctnPrd/FrDt}`.

Sizes seen for the snapshot of 31 October 2026: `FULINS_C` 1 ZIP (3.8 MB),
`FULINS_D` 4 ZIPs (17.1, 8.8, 14.9 and 11.8 MB), `FULINS_E` 2 ZIPs (8.3 and
3.9 MB), 69 MB in all. The XML is about 36 times the ZIP (a 6.5 MB delta
unzips to 236 MB), so the C, D and E snapshot is roughly 2.5 GB of XML. The
derivative and certificate letters (`R` alone is 19 ZIPs) are never
downloaded.

The bulk nodes run only when `firds.bulk.enabled` is true:

```console
kedro run --pipeline=reference --params="firds.bulk.enabled=true"
kedro run --pipeline=reference --params="firds.bulk.enabled=true,firds.bulk.files=DLTINS_20261102_01of01"
```

| Node | Output | What it does |
|---|---|---|
| `firds_file_list` | `firds_files` | Reads the index for the last `lookback_days` (14) and picks `bulk.files`, or else the newest complete FULINS set for `cfi_letters` (every part present) plus the DLTINS files published after it. A delta from the snapshot's own date is already in the snapshot and is skipped. |
| `firds_download` | `firds_raw_manifest` | Streams each ZIP to `data/01_raw/firds/` and checks its MD5 against the index; a file already there with the right MD5 is not downloaded again. |
| `firds_to_parquet` | `firds_parquet_manifest` | Streams each new ZIP's XML with `xml.etree.ElementTree.iterparse` straight from the archive, keeps records whose CFI starts with `C`, `D` or `E`, and writes them to `data/02_intermediate/firds/<file>.parquet` in batches of 100,000 rows. Each finished element is removed from the tree, so memory stays flat. The manifest lists the newest snapshot on disk and the deltas after it. |
| `firds_instruments` | `firds_instruments` | Applies the rule above in DuckDB and saves one row per ISIN to `data/03_primary/firds_instruments.parquet` (`DuckDBParquetDataset`). |

With `enabled: false` (the default), nothing is downloaded and
`firds_instruments` is rebuilt from the Parquet files already on disk, so a
plain `kedro run` never empties the table.

## Update frequency and terms

FIRDS is updated daily: a delta file every day and a full snapshot every
Saturday; the Solr index carries the same data, and `publication_date` shows
when each record was last published. ESMA's legal notice
(<https://www.esma.europa.eu/about-esma/legal-notice-and-data-protection>)
authorises reproduction of the information on its site provided the source
is acknowledged; material that has been transformed must say it was drafted
using material downloaded from ESMA's website and that ESMA does not endorse
it. Every row carries this in `credit`:

> Source: ESMA Financial Instruments Reference Data System (FIRDS),
> registers.esma.europa.eu. Drafted using material downloaded from ESMA's
> website; ESMA does not endorse this publication.

Neither host publishes a `robots.txt` (`registers.esma.europa.eu/robots.txt`
redirects to the register's home page, and the file store returns an error),
no login, consent or bot check is involved, and requests keep to one per
second per host.

## What a real run shows (3 November 2026)

Per-ISIN lookups, each a single uncached request:

| ISIN | `asset_class` | CFI | Notes | Venues | `as_of` | Seconds |
|---|---|---|---|---|---|---|
| CH0008899764 iShares SMI ETF (CH) | fund | `CEOIEU` | LEI 549300513WH1XYPSM312, CHF | 42 (9 active) | 2026-11-01 | 0.59 |
| CH1619473973 ABB 1.4125% 2035 | debt | `DBFNGB` | CHF, matures 2035-11-23, coupon 1.4125, fixed, 325m issued | 2 | 2026-11-03 | 0.69 |
| LU0274208692 Xtrackers MSCI World Swap | fund | `CECGMS` | USD | 66 | 2026-11-01 | 0.88 |
| IE00B5BMR087 iShares Core S&P 500 UCITS | fund | `CEOGES` | USD | 77 | 2026-11-01 | 1.34 |
| XS2440511975 BNP Paribas FRN 2026 | debt | `DTVUFB` | floating, 3-month Euribor (EU0009652783) + 0 bp, matured | 6 (0 active) | 2026-11-03 | 0.69 |

A batch of 20 mixed ISINs (Swiss, German, French, Dutch, Irish,
Luxembourg and US shares, bonds and funds) took one request and 3.7 seconds:
19 found (7 fund, 7 equity, 5 debt). The one miss was an ISIN no EU venue
lists. A 10-ISIN batch returned 434 venue records.

Bulk: `kedro run --pipeline=reference --params="firds.bulk.enabled=true,firds.bulk.files=DLTINS_20261102_01of01"`
downloaded the 53 KB delta of 2 November 2026 (838 KB of XML, MD5 matching the
index), kept 307 of its 883 records (the rest were `F`, `H`, `J` and `S`
derivatives) and wrote 27 debt ISINs to `firds_instruments` with
`as_of = 2026-11-02`: 18 fixed, 6 floating and 3 zero coupon. Parsing took
0.2 seconds.

## Known gaps

- **EU venues only.** A security no EU or EEA venue lists is not in FIRDS:
  bonds listed only on SIX or only in the US, and funds sold only in
  Switzerland. FIRDS also does not hold Swiss-only or US-only fund units
  unless a German or other EU venue trades them.
- **Not a holdings source.** It never says what a fund holds.
- **No replication flag.** Nothing in FIRDS says whether an ETF is physical
  or swap-based. In the sample above both swap ETFs (Xtrackers, Amundi) have
  CFI `CECGMS` and the physical ones `CEOGES`, but CFI attributes are filled
  in by each venue and are not a reliable test; use the issuer's own data.
- **Names are as reported**, abbreviated and venue-specific
  (`iShs VII-Core S&P 500 U.ETF Reg. Shares USD (Acc) o.N.`), and some
  records have `UNKNOWN` as the short name.
- **Terminated listings stay.** Matured bonds and delisted funds keep their
  record with `n_active_venues = 0` and a `termination_date`; filter on that
  when only current instruments are wanted. Swiss shares such as Roche show
  most of their EU venues as terminated.
- **Fixed rate of `0`** is reported both for genuine zero-coupon and 0%
  bonds and, in Solr, for floating-rate notes; see `interest_type`.
- **Bulk throughput.** The stdlib parser handled about 4 MB of XML per second
  on a heavily loaded machine during development, so a full C/D/E snapshot
  takes minutes, not seconds. Only one small delta file was downloaded while
  building this; the full snapshot path is covered by tests on synthetic
  files with the real structure.

## Example

```python
from openbasket.sources import firds

firds.classify("CH0008899764")  # 'fund'
rec = firds.lookup("CH1619473973")
rec["cfi"], rec["issuer_lei"], rec["maturity_date"], rec["coupon_pct"]
# ('DBFNGB', '5493000LKVGOO9PELI61', '2035-11-23', 1.4125)
```
