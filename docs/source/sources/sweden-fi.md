# Finansinspektionen Sweden: fund holdings per quarter

Finansinspektionen (FI), the Swedish financial supervisor, publishes the full
portfolio that every Swedish UCITS fund (*värdepappersfond*) reports to it at
each quarter end, as open data ("Fondinnehav per kvartal"). This covers the
funds of the Swedish banks (Swedbank Robur, Handelsbanken, SEB, Nordea,
Länsförsäkringar, Avanza, Nordnet), the AP7 default pension funds and about
35 independent fund companies.

It is a regulator dataset, not an issuer feed. It is built like the other
[regulator filings](regulator-filings.md): a store module
`openbasket.sources.sweden_fi`, an issuer adapter `sweden_fi`
(`openbasket.sources.issuers.sweden_fi`, picked up by `discover()`), and a
Kedro pipeline `sweden_fi` that refreshes the store and writes the fund list.

Status as tested on 2026-11-03.

## Terms

FI's open data page (`https://www.fi.se/sv/om-fi/om-webbplatsen/oppen-data/`,
"Senast granskad: 2026-10-14") lists "Fondinnehav: Värdepappersfonders
innehav (.xml)" among its open data and says:

> FI:s information är fri att användas. Ange källa, FI och datum. FI:s data är
> fritt tillgänglig utan avgift och får fritt bearbetas och vidareutnyttjas
> utan att något särskilt medgivande eller avtal krävs.

That is: FI's information is free to use; state the source, FI, and the date;
FI's data is available free of charge and may be freely processed and reused
without any special permission or agreement. So every row carries the credit
`Källa: Finansinspektionen (FI), Fondinnehav per kvartal` and the position
date. `https://www.fi.se/robots.txt` returns 404 (no robots rules). The files
need no login, key, cookie or consent click.

The files contain fund company names, FI institute numbers and LEIs, and
security names. They hold no personal data, so nothing is dropped. The raw
ZIPs stay in `data/01_raw/sweden_fi/raw/` and are never committed.

## Endpoints

Index
: `https://www.fi.se/sv/vara-register/fondinnehav-per-kvartal/` (English
  page: `https://www.fi.se/en/our-registers/quarterly-fund-holdings/`).
  HTML table with one ZIP per quarter from 2018 Q4, linked as
  `/FondInnehavLista/download?filnamn=Fondinnehav_<YYYY>Q<q>_<timestamp>.zip`.
  The timestamp is when FI last rebuilt the quarter. FI rebuilds old quarters
  when a fund revises its report (2023 Q4 to 2026 Q2 were all rebuilt on
  14 October 2026), so the store always reads the name from the page.

Quarter ZIP
: `https://www.fi.se/FondInnehavLista/download?filnamn=<file name>` with the
  space sent as `%20`. 2026 Q2: `Fondinnehav_2026Q2_2026-10-14 08.12.zip`,
  6,693,565 bytes, `application/zip`. One XML per fund, in one folder per fund
  company; entry names use backslashes,
  `<company>_<company id>_<ts>\<fund>_<fund id>_VPFO_2026Q2_<ts>.xml`.

The store makes one index request per refresh and downloads a quarter only
when FI's file name for it has changed, streamed to disk. Requests go through
`openbasket.http` (one request per second per host).

## The fund file

Namespace `http://schemas.fi.se/publika/vardepappersfonder/20200331`, root
`VärdepappersfondInnehav`:

- `Rapportinformation/Kvartalsslut`: quarter end, used as `as_of`.
- `Bolagsinformation`: fund company name, institute number and LEI.
- `Fondinformation`: `Fond_institutnummer`, `Fond_ISIN-kod`, `Fond_namn`,
  `Fond_status` (`Ej aktiv fond` for a closed fund), `Fondförmögenhet` (net
  assets, SEK), `Likvida_medel` (cash), `Övriga_tillgångar_och_skulder`, fees
  per share class (`Andelsklass_namn`), benchmark, active risk and 24-month
  standard deviation.
- `FinansiellaInstrument/FinansielltInstrument`, one per position:
  `Tillgångsslag_enligt_LVF_5_kap`, `Instrumentnamn`, `ISIN-kod_instrument`,
  `Landkod_Emittent`, `Valuta`, `Antal` or `Nominellt_belopp`, price and FX
  rate, `Marknadsvärde_instrument` (SEK) and
  `Andel_av_fondförmögenhet_instrument`.

`Andel_av_fondförmögenhet_instrument` is already **percent of net assets**,
to two decimals. For 99.92% of lines it equals
`Marknadsvärde / Fondförmögenhet × 100` within rounding. It is used as
reported and never rescaled.

## How a fund ISIN reaches its holdings

1. `FiStore.refresh()` reads the index, downloads the newest quarter if its
   file name changed, and parses every fund file into
   `parquet/<quarter>/funds.parquet` and `positions.parquet`.
2. `products()` returns every active fund with a valid ISIN: one row per fund,
   keyed by `Fond_ISIN-kod`, with `portfolio_id` = FI's fund institute number.
   Extra columns: `manager`, `manager_lei`, `bank_group`, `net_assets_sek`,
   `share_classes` and `as_of`.
3. `holdings(product)` returns that fund's lines:

| Column | From |
|---|---|
| `isin` | `ISIN-kod_instrument` if its check digit is valid (the raw value is kept in `isin_reported`) |
| `name` | `Instrumentnamn` |
| `weight_pct` | `Andel_av_fondförmögenhet_instrument`, percent of net assets as reported |
| `asset_class` | `Fondandel` → `fund`; `Penningmarknadsinstrument` → `debt`; `DerivatinstrumentSamtTeknikerOchInstrument` → `derivative`; `ÖverlåtbartVärdepapper` → `debt` with a nominal amount, else `equity` |
| `currency` | `Valuta` |
| `country` | `Landkod_Emittent` (issuer country) |
| `cusip`, `sedol`, `ticker` | not reported |

Extra columns: `lvf_asset_type`, `isin_reported`, `quantity`, `nominal`,
`market_value_sek`, `industry` and `weight_derived`. Two lines are added per
fund: cash (`Likvida_medel`) and other assets and liabilities. Their weight is
the SEK amount divided by `Fondförmögenhet` × 100, and they have
`weight_derived = True`. These are the only computed weights.

`bank_group` names the banking group of the fund company:
Swedbank (Swedbank Robur Fonder AB, Swedbank AB), SEB (SEB Funds AB,
Skandinaviska Enskilda Banken AB), Handelsbanken, Nordea, Länsförsäkringar,
Avanza and Nordnet. Other companies get `None`.

`asset_class` (the fund's) is `Equity`, `Fixed Income` or `Fund of funds`
when that class holds at least 65% of net assets, else `Mixed`.
`replication` is `physical` when the fund reports no derivative line, else
`None`. `currency` is `SEK`, the reporting currency, not the share-class
currency.

## Coverage

From the 2026 Q2 file (positions at 30 June 2026):

| | |
|---|---|
| Fund files | 726 from 47 fund companies |
| `products()`: active funds with a valid ISIN | 677, all with SE ISINs |
| Closed funds (`Ej aktiv fond`) | 49, no holdings; 38 of them have no ISIN |
| Net assets of the 677 | SEK 9,426 bn |
| Instrument lines | 122,248; 117,412 (96.0%) with a valid ISIN; no reported ISIN fails its check digit |
| Fund-of-fund lines (`Fondandel`) | 2,032, every one with the held fund's ISIN |
| Sum of a fund's lines incl. cash, 5th / 50th / 95th percentile | 99.86% / 100.00% / 100.06% |
| Weight on lines with an ISIN, 5th / 50th / 95th percentile | 92.32% / 98.28% / 99.91% |
| Fund class | 378 equity, 144 fixed income, 118 fund of funds, 37 mixed |

Bank-run funds:

| Bank group | Funds | Net assets (SEK bn) |
|---|---|---|
| Swedbank | 80 | 2,358 |
| Handelsbanken | 83 | 1,501 |
| SEB | 45 | 643 |
| Länsförsäkringar | 34 | 507 |
| Nordea | 26 | 398 |
| Avanza | 15 | 145 |
| Nordnet | 18 | 62 |
| **All banks** | **301** | **5,614** |
| Others (AP7, Skandia, AMF, Spiltan, ...) | 376 | 3,813 |

Real holdings calls (3 November 2026, from the store; "Lines" and "Sum"
count reported positions only, "With cash" adds the two derived lines):

| Fund | Manager | Lines | Sum | With cash | With ISIN | As of |
|---|---|---|---|---|---|---|
| Handelsbanken Global Index Criteria (SE0011309707) | Handelsbanken Fonder AB | 2,190 | 98.91% | 99.21% | 98.91% | 2026-06-30 |
| Swedbank Robur Globalfond (SE0000542979) | Swedbank Robur Fonder AB | 82 | 98.08% | 100.01% | 98.08% | 2026-06-30 |
| SEB Korträntefond SEK (SE0000984080) | SEB Funds AB | 173 | 98.41% | 99.93% | 98.41% | 2026-06-30 |
| Nordea Generationsfond 70-tal (SE0000653461) | Nordea Funds Ab | 679 | 98.00% | 99.93% | 98.25% | 2026-06-30 |
| Länsförsäkringar Global Index (SE0005188836) | Länsförsäkringar Fondförvaltning AB | 1,168 | 99.78% | 100.10% | 99.78% | 2026-06-30 |
| AP7 Aktiefond (SE0003299999) | Sjunde AP-fonden | 2,004 | 97.77% | 100.64% | 95.15% | 2026-06-30 |

Parsing the whole quarter takes about 4 s; after that every fund is read from
Parquet in milliseconds.

## Freshness

Quarterly. Fund companies file within weeks of the quarter end (the 2026 Q2
entries are stamped 2 July to 14 October 2026). FI's ZIP for 2026 Q2 is dated
14 October 2026, and on 3 November 2026 no 2026 Q3 file was out yet. So
holdings are 3 to 6 months old. `FiStore.refresh(quarter="2025Q4")` reads an
older quarter.

## Known gaps

- **One ISIN per fund.** `Fond_ISIN-kod` is the main share class. Other share
  classes are listed by name only (1,660 class names in 504 funds, kept in
  `share_classes`). Their ISINs need another source, for example ESMA FIRDS
  or GLEIF; until then an input ISIN of another share class does not match.
- **UCITS only.** Swedish special funds (AIFs) are not in the dataset, nor
  are Luxembourg or Irish funds run by Swedish banks (Nordea's and SEB's
  Luxembourg ranges, for example). Those appear as `Fondandel` lines with
  their ISINs.
- **Two-decimal weights.** Small positions round to 0.00 or 0.01, so a fund
  with 2,000 lines can sum to 98% to 99% before cash.
- **Derivatives as reported.** 4,784 of 5,626 derivative lines have no ISIN.
  Their weight is the fund company's figure: usually market value, sometimes
  one leg of a swap.
- **A few securities without an ISIN.** 52 transferable-security lines have
  none. Government bonds reported with `Antal` instead of
  `Nominellt_belopp` come out as `equity`.

## Other Nordic regulators

Checked on 2026-11-03 for open per-fund holdings. None publishes any:

| Country | What exists | Status |
|---|---|---|
| Norway | Finanstilsynet publishes aggregate statistics on fund managers; Statistics Norway (SSB) publishes fund statistics by sector and fund type | No per-fund holdings; not buildable |
| Denmark | Finanstilsynet and Danmarks Nationalbank collect monthly security-level reports from Danish UCITS and AIFs, but publish only aggregate statistics | No per-fund holdings; not buildable |
| Finland | The Bank of Finland / FIN-FSA open data service (`api.boffsaopendata.fi`) has `IVF_PUBL`, fund assets and liabilities by fund type | Aggregates only; not buildable |

Norwegian managers with Swedish UCITS funds (ODIN, Storebrand, Alfred Berg)
are covered here through those Swedish funds.

## Example

```python
from openbasket.sources.issuers import discover

fi = discover()["sweden_fi"]
funds = fi.products()                 # 677 rows, 2026 Q2
row = funds[funds["isin"] == "SE0000542979"].iloc[0].to_dict()
h = fi.holdings(row, None)
h.as_of                               # '2026-06-30'
h.holdings.nlargest(3, "weight_pct")[["isin", "name", "weight_pct"]]
#            isin                  name  weight_pct
#    TW0002330008  Taiwan Semiconductor        5.87
#    US02079K1079            Alphabet C        5.30
#    US67066G1040                Nvidia        3.79
```

Or with Kedro: `kedro run --pipelines sweden_fi` writes
`data/01_raw/sweden_fi/manifest.csv` and `products.parquet`.
