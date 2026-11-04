# FinanceDatabase

[FinanceDatabase](https://github.com/JerBouma/FinanceDatabase) by Jeroen
Bouma lists about 300,000 symbols (equities, ETFs, funds, indices,
currencies, cryptocurrencies, money markets) as CSV files, one per exchange.
openbasket reads its ETFs, funds and money markets for **names, fund
families, categories and listings by ISIN**. It has no holdings.

Module `openbasket.sources.financedatabase`; pipeline `financedatabase`.

## Terms

The repository is under the **MIT License**, "Copyright (c) 2023 Jeroen
Bouma" ([LICENSE](https://github.com/JerBouma/FinanceDatabase/blob/main/LICENSE)).
The licence text speaks of "the Software and associated documentation files";
the author publishes the database files in the same repository under it.

On where the data come from, the README's Q&A ("How is the data obtained?")
says they are an aggregation of publicly available sources: the listing
directories exchanges publish (Nasdaq Trader, HKEX, NSE, JPX, ASX, TSX,
Deutsche Börse Xetra, Euronext, SIX), open reference data (OpenFIGI, SEC
EDGAR, ISO 4217, CoinGecko) and community contributions, and that data behind
paid subscriptions is never included. The weekly update adds new ETFs from
Nasdaq Trader, HKEX, NSE, JPX, TSX, Deutsche Börse, Euronext, SIX and Börse
Frankfurt.

openbasket therefore classes the facts it keeps (ISIN, name, family,
category, listings) as `REDISTRIBUTION = "public"`, credited as
**FinanceDatabase by Jeroen Bouma (MIT)** on every row. The `summary`
column is long descriptive prose of unclear provenance, so it is **dropped when read and
never written** to any table, report or published file.

## Files and endpoints

| What | URL |
|---|---|
| ETFs | `https://raw.githubusercontent.com/JerBouma/FinanceDatabase/main/compression/etfs.parquet` (1.2 MB) |
| Funds | `.../compression/funds.parquet` (2.1 MB) |
| Money markets | `.../compression/moneymarkets.parquet` (28 KB) |
| As-of date | `https://api.github.com/repos/JerBouma/FinanceDatabase/commits?path=compression/<file>&per_page=1` |

The `compression/*.parquet` files are what the `financedatabase` Python
package downloads (`DATA_REPO` in `financedatabase/database_controller.py`);
they hold the same rows and columns as `database/etfs/*.csv` (64 exchange
files), `database/funds/*.csv` (30) and `database/moneymarkets.csv`. Setting
`financedatabase.fmt: csv` reads the CSV files instead, one request per
exchange file (the folder is listed through the GitHub contents API). A file
fetched today is reused. The as-of date is the date of the last commit that
touched the file, else the download date. Raw downloads are kept in
`data/01_raw/financedatabase/` and never committed.

Columns: ETFs have `symbol, name, currency, summary, category_group,
category, family, exchange, mic, isin, delisted`. **Funds have no `isin` and
no `delisted` column**, and money markets have only `symbol, name, currency,
summary, family, exchange`.

## The `findb_funds` table

`data/03_primary/findb_funds.parquet`, one row per ISIN:

| Column | Meaning |
|---|---|
| `isin` | Upper-cased, check digit verified. Rows whose ISIN fails it are dropped and counted. |
| `name`, `family`, `category_group`, `category` | The most common value among the ISIN's active listings (all listings if all are delisted); ties go to the alphabetically first. |
| `kind`, `kinds` | `etf`, `fund` or `moneymarket`; `kinds` lists every kind the ISIN appears as. |
| `symbols`, `exchanges`, `mics`, `currencies` | Every listing, `; `-separated and sorted. |
| `n_listings`, `n_active_listings`, `delisted` | `delisted` is true when every listing is delisted. |
| `isin_from` | `isin` (the ETF file's column) or `summary_text` (see below). |
| `source`, `source_url`, `credit`, `licence`, `redistribution`, `as_of` | As on every openbasket row. |

`findb_summary.json` has the counts below and `findb_overlap.csv` compares
the ISINs with openbasket's own tables (`financedatabase.compare` names
them; a missing file is skipped).

Funds and money markets carry no ISINs. With
`financedatabase.isin_from_text: true`, a fund whose name or summary names
exactly one valid ISIN gets that ISIN (`isin_from = "summary_text"`); the
summary is read for that and nothing else. It is off by default.

## Numbers from a run (3 November 2026, files dated 31 October 2026)

| | Rows | With ISIN | Invalid ISIN dropped | Delisted rows |
|---|---|---|---|---|
| ETFs | 32,047 | 24,854 | 0 | 1,678 |
| Funds | 57,761 | 0 (no column) | - | - |
| Money markets | 1,225 | 0 (no column) | - | - |

- **12,678 distinct ETF ISINs** (on average 2.0 listings each, up to 16;
  iShares Core S&P 500 UCITS ETF, IE00B5BMR087, has 12). 212 have only
  delisted listings. 819 have no family.
- With `isin_from_text`, 228 fund rows name exactly one ISIN, adding 227 fund
  ISINs (AT-prefixed Vienna funds whose symbol is the ISIN, for example).
- By ISIN prefix: US 5,785, IE 2,839, LU 1,148, CA 748, IN 368, KR 334,
  DE 231, HK 229, XS 202, AU 193, FR 139, JE 132, CH 92, TW 45, GB 39.
- Top families (distinct ISINs): BlackRock Asset Management 1,496, Invesco
  540, WisdomTree 424, Amundi 414, First Trust 375, State Street 343, UBS 343,
  Xtrackers 316, Lyxor 311, Vanguard 292, BNP Paribas 283, Global X 253,
  JPMorgan 246, Leverage Shares 245, Corgi ETFs 196.
- Category groups: Equities 5,487, Fixed Income 2,547, Derivatives 2,333
  (leveraged, inverse and other derivative-based products), Commodities 341.

Overlap with openbasket (local data of the same day):

| Table | ISINs known | With a holdings source |
|---|---|---|
| `issuer_products` (iShares, SPDR, CVM, CNMV) | 1,566 (12.4%) | 1,566 |
| N-PORT `security_master` (held by a US fund) | 1,843 (14.5%) | - |
| `fund_registry` (GLEIF, ESMA, SEC, regulators) | 7,303 (57.6%) | 2,682 (2,673 N-PORT, 9 CVM) |
| `fund_directory` | 7,019 (55.4%) | 0 |
| `etfdb_meta` (local only) | 4,004 (31.6%) | - |
| any of them | **8,484 (66.9%)** | 3,756 (29.6%) |

So 4,194 ETF ISINs are new to openbasket (US 2,072, CA 656, IN 368, KR 334,
HK 201, AU 186), and 8,922 have no holdings source yet, led by Amundi (413),
Invesco (381), WisdomTree (366), UBS (343), Lyxor (311), Xtrackers (291) and
BNP Paribas (283).

## How it is used

- As a **directory**: names, families and categories for ETF ISINs no
  issuer list covers, and the exchange symbols of each ISIN, which helps
  match a ticker to an ISIN.
- As a **coverage check**: `findb_overlap.csv` shows which families have
  many ETFs openbasket cannot look through yet.

It is not a holdings source and does not change any look-through result.

## Gaps

- Funds and money markets have **no ISINs**, and upstream stopped
  maintaining them in October 2026 (README: "What happened to Funds and Money
  Markets?"). Exchange-listed funds were moved to the ETF files.
- Families are free text and inconsistent (`BlackRock Asset Management`,
  `iShares`); categories mix asset classes and sectors (`Information
  Technology` as a category group).
- Symbols use Yahoo-style suffixes (`.L`, `.DE`); some symbols are the ISIN
  itself (`IE00B5BMR087.PR`).
- The exchange directories behind the data have their own terms; the MIT
  grant is the database author's.
