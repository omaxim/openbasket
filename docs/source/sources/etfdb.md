# albertored/etfdb (local only)

[albertored/etfdb](https://github.com/albertored/etfdb) is a "static ETF
database" of about 4,650 ETFs and ETPs sold in Europe, in JSON, CSV, XML and
XLSX, updated about monthly. Each record has the fund's ISIN, provider,
replication, index, domicile, currency, hedging, number of holdings,
strategies, update dates, sector and country splits, and up to ten **top
holdings** with name, percentage and ISIN.

Module `openbasket.sources.issuers.etfdb`; issuer adapter **`etfdb_top`**;
pipeline `etfdb`.

## Terms: none given, so local use only

- The repository has **no LICENSE file** (GitHub reports no licence) and the
  README states no terms. Without a licence, all rights are reserved by
  default.
- The README does not say where the data come from. An issue asking exactly
  that ("Source of your Data", #1, April 2026) is open with no reply. The
  owner's own pull request #2 (August 2026) says justETF publishes two sector
  taxonomies fund by fund and describes renaming them, which shows the
  figures are taken from **justETF**, a commercial site with its own terms.

So the module declares `REDISTRIBUTION = "local_only"` and the adapter
`redistribution="local_only"`: the public build never fetches or publishes
it, and nothing from it is committed. It is `enabled_by_default=True` because
openbasket reads only the files on GitHub, never justETF's website. Every
row says where it came from:

- credit: *Top holdings and fund facts from albertored/etfdb
  (https://github.com/albertored/etfdb); no licence stated, data appear to
  come from justETF; read for private local use only*;
- licence: *No licence: the repository has no LICENSE file and its README
  states no terms ...*.

## Files and endpoints

| What | URL |
|---|---|
| Records | `https://raw.githubusercontent.com/albertored/etfdb/main/json/etfs.json` (19.6 MB, 4,651 records) |

`csv/basic_info.csv` has the same facts, but `csv/holdings.csv` is a wide
table whose 30 columns are company names (NVIDIA, Microsoft, Apple ...) with
no ISINs, so the JSON is used for everything. No website is read and nothing
is scraped. The file is downloaded once a day to `data/01_raw/etfdb/`
(`$OPENBASKET_ETFDB_DIR`), older copies are deleted.

## `etfdb_meta`

`data/03_primary/etfdb_meta.parquet`, one row per ETF:

| Column | Meaning |
|---|---|
| `isin`, `name`, `ticker`, `wkn`, `fund_provider`, `asset_class` | As given. |
| `replication`, `replication_class` | etfdb's label, and `physical` (full replication, physically backed), `sampled` (sampling, optimised sampling) or `swap` (swap based, funded or unfunded). |
| `uses_swaps` | Any swap-based label, or `Physical & Synthetic`. |
| `is_leveraged`, `is_active`, `strategies` | `is_leveraged` from the `Short & Leveraged` strategy (the README spells it `Short & Leverage`; both are accepted). |
| `index` | The index the ETF tracks, by name. |
| `domicile`, `domicile_code`, `currency`, `hedged` | Country name as given and its ISO code. |
| `number_of_holdings`, `ter_pct`, `size_eur_m`, `inception_date` | As given. |
| `n_top_holdings`, `top_holdings_pct` | How many top lines there are and what they add up to. |
| `update_date`, `holdings_update_date` | When etfdb last updated the record and the top holdings. |

It is used to flag swap-based and leveraged funds, and to name the index
for stand-ins: `etfdb_stand_in_candidates.csv` lists, for each swap-based
equity, bond or real-estate ETF, up to three physically replicating ETFs on
the same index (largest first, unhedged first, leveraged funds excluded).
It is a list to review; nothing is added to `lookthrough.proxies`
automatically.

## The `etfdb_top` adapter

`products()` lists the ETFs that have top holdings (3,448), with
`replication` mapped as above, so the look-through notes swap-based funds.
`holdings()` returns that fund's top lines:

- weights are `percentage` as given (percent of the fund), **never
  rescaled**; the rest of the fund stays unresolved;
- an ISIN that fails its check digit is set to `None`;
- `as_of` is the fund's `holdings_update_date`;
- `notes` start with *top holdings only, from albertored/etfdb
  (2026-08-27); the rest of the fund is unresolved*, plus a note for
  leveraged or swap-based funds;
- the adapter's title, which the output cites, is *albertored/etfdb top
  holdings (top 10 only; rest unresolved)*;
- `frequency`: about monthly, dated per fund.

It is meant to rank **last**: only for funds no full-holdings source covers.
What holds today:

- built-in iShares and SPDR lists are always read first, so their funds
  keep their own full files (761 iShares and 162 SPDR funds are in both);
- for a fund that also has an N-PORT series or TickerInside data, the
  look-through still reads those and takes the first that resolves 90%;
- but `ingest` keeps the first adapter that lists an ISIN, in adapter-name
  order. `etfdb_top` sorts after `cnmv_spain` and `cvm_brazil`, but before
  adapters such as `first_trust`, `franklin`, `globalx`, `jpmorgan`,
  `vaneck`, `vanguard` and `xtrackers` on other branches. Until `ingest`
  orders partial sources last, disable it where a full adapter exists
  (`issuers.disabled: [etfdb_top]`) or merge after that change.

## Numbers from a run (3 November 2026)

| | Count |
|---|---|
| ETF records (all ISINs valid) | 4,651 |
| with top holdings | 3,448 (3,435 with ten lines) |
| top-holding lines | 34,446, of which 34,252 with a valid ISIN |
| median weight of the top lines | 29.9% |
| `holdings_update_date` | 27 July to 31 August 2026 |
| last record update | 25 October 2026 |
| swap-based (`uses_swaps`) | 658 (238 with top holdings) |
| Short & Leveraged | 146 (none with top holdings) |
| Active | 713 |
| swap ETFs with a physical candidate on the same index | 201 (491 pairs) |

Replication: full 2,494, sampling 885, swap unfunded 388, optimised sampling
369, swap 262, physically backed 245, swap funded 6, physical and synthetic
2. Domicile: IE 3,040, LU 1,107, DE 173, JE 127, FR 72, CH 58. Providers:
iShares 847, Amundi 607, Xtrackers 433, UBS 385, Invesco 298, WisdomTree 235,
BNP Paribas Easy 228, State Street 170, J.P. Morgan 155, Vanguard 127.

Overlap with openbasket (local data of the same day):

| Table | All 4,651 | 3,448 with top holdings |
|---|---|---|
| `issuer_products` (has full holdings) | 962 | 923 |
| N-PORT `security_master` | 43 | 8 |
| `fund_registry` | 4,189 | 3,444 |
| `fund_directory` | 4,628 | 3,448 |
| `findb_funds` | 4,004 | 3,093 |
| any | 4,635 | 3,448 |

So nearly every etfdb fund is already in the registry or directory, but
2,525 of those with top holdings have no full-holdings source yet: Amundi
519, Xtrackers 365, UBS 347, Invesco 233, BNP Paribas Easy 156, J.P. Morgan
152, Vanguard 92, HSBC 72, WisdomTree 71. For those, `etfdb_top` is the only
look-through, at about 30% of the fund.

## Gaps

- Ten lines at most; the median fund shows under a third of its weight.
- Holdings are two to three months old (last refreshed August 2026) and the
  update schedule is the owner's.
- 1,203 records have no top holdings at all: 584 equity (including all 146
  leveraged or short products), 200 bond, 164 commodity, 144 currency, 73
  precious-metal and 36 money-market funds.
- No CUSIPs, tickers, countries or asset classes for the holdings; equity and
  bond funds' lines are labelled `equity` or `debt` from the fund's class.
- Swap-based funds' top holdings may be the substitute basket, not the
  index; they are noted as such.
- Licence unknown: never published, and may disappear or change without
  notice.
