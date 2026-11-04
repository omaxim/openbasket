# Exchange and index publications

Some issuers block automated access to their sites: UBS (Akamai 403),
Invesco EMEA (406), Pictet (AWS WAF), Swiss Life and WisdomTree (Cloudflare),
Fidelity and Franklin. This page records what exchanges and index providers
publish that could stand in for those funds' holdings, and describes the one
free route: a **stand-in**, the holdings of a physically replicating peer
ETF on the same index.

**Redistribution: local only.** The peers' holdings come from iShares and
State Street, whose terms restrict republication, so the stand-in adapter is
registered with `redistribution="local_only"`: openbasket builds stand-ins
only for the person running it. Under the publishing policy the public build
does not fetch this source, and no stand-in rows go to the openbasket site,
API or releases. A fund that only a stand-in covers is listed there without
constituents, with a note that its holdings are available when you run
openbasket yourself. See [Adding a source](adding-a-source.md).

Status as tested on 3 November 2026.

## What is published, and whether it can be used

### ETF portfolio composition files (PCFs)

No European exchange we checked publishes ETF creation baskets for free.

| Venue | What is public | Usable |
|---|---|---|
| SIX Swiss Exchange | ETF explorer and reference data (name, ISIN, issuer, underlying index, replication method). No PCF or basket. | Reference only |
| Deutsche Börse / Xetra | Tradable-instruments list and iNAV prices. Basket and iNAV data are sold as Market Data + Services products. Deka's own composition pages sit behind a click-through on Deutsche Börse's behalf, which we do not accept for the user. | No |
| Euronext | ETF lists (CSV). No PCF found. | Reference only |
| Borsa Italiana, London Stock Exchange | ETF lists and prices. No PCF found. | No |

PCFs in Europe come from the issuers themselves or from licensed vendors.
This check was not exhaustive: a search and a look at each venue's ETF pages.

### Index constituents and weights

| Provider | What is public | Usable |
|---|---|---|
| SIX (SMI, SLI, SPI, SMIM) | The index page lists the constituents (name, symbol, ISIN, security type) with **no weights**. Weights and compositions are in the SIX Index Data Center, which needs a login, and index data is licensed. | No |
| Euronext (AEX, CAC 40, BEL 20, ...) | A monthly `<Index>_Index_Composition.pdf` under `live.euronext.com/sites/default/files/documentation/index-composition/`. It shows the top 20 only ("This view is limited. Full composition available to licensed clients."), with weights to two decimals and no ISINs, and its footer forbids redistribution without written permission. Full daily weights are a paid product. | No |
| Solactive | Index pages show components and weights after a disclaimer. The disclaimer forbids commercial use, reproduction or redistribution without consent, and any bots or scrapers. | No |
| Nasdaq (Nasdaq-100) | `indexes.nasdaqomx.com/Index/Weighting/NDX` shows column headers only; the rows need a log-in or registration. | Gated: needs a free registration (for the user to decide) |
| STOXX (DAX, EURO STOXX 50) | The index page lists the top 10 names with no weights. Data is sold under licence. | No |
| MSCI, FTSE Russell, S&P Dow Jones | Factsheets show the top 10. Constituent data is sold under licence. | No |
| MarketVector | Component pages returned HTTP 403. | No |

So no index provider publishes full constituent weights that a public
aggregator may reuse.

## Stand-ins: a peer ETF's holdings

What is public, and already used elsewhere in openbasket, is each issuer's
own daily holdings file. A physically, fully replicating ETF holds every
index constituent at its index weight, so the holdings of **another such
ETF on the same index** (the "peer") are the best free picture of what a
blocked fund that tracks the index the same way holds. They are the peer's
holdings, not the index provider's data and not the fund's own holdings,
and they are labelled that way.

The adapter is `openbasket.sources.issuers.index_proxy`, registered as
`index_proxy` with `stand_in=True`. For a mapped fund it fetches the peer's
holdings from the peer's issuer and returns them unchanged: weights are
percent of the **peer's** net assets, never rescaled, and the as-of date is
the peer file's position date. Every result says what it is. The first note
reads:

> stand-in: holdings of iShares SMI ETF (CH) (CH0008899764), a physically
> replicating ETF on the Swiss Market Index (SMI), positions as of
> 2026-11-02; used for UBS ETF (CH) - SMI (CHF) A-dis (CH0017142719); not
> the fund's own holdings

Further notes give `kind: stand-in (not the fund's own holdings)`, the
source (the peer's issuer and product page), the credit line (the peer's
issuer, and the index's owner), the target's replication evidence and the
peer's replication evidence. The products row's `product_page_url` is the
peer's page, since that is where the data comes from; `kind`, `index_name`,
`peer_isin`, `peer_replication_evidence` and `fund_issuer` are extra
columns.

**Last resort only.** A stand-in is used only when no direct source lists
the fund. `ingest` lists stand-in adapters after every direct adapter, and
an ISIN two lists share stays with the first, so a fund any direct adapter
lists (an issuer's own file, a statutory report, a regulator filing) keeps
that adapter. `match_directory` also matches only funds of the blocked
issuers above.

**Only for full physical replication.** A target must be
`physical-full`. Swap funds (for example Invesco S&P 500 UCITS ETF,
IE00B3YCGJ38) hold a basket unrelated to the index and are refused, and so
are sampled or optimised funds, whose holdings can differ a lot from the
index. A plain "Physical" label is not enough, because it does not say full.

**The peer must be full physical too.** Each peer records where its own
replication was confirmed (`Peer.replication_evidence`, in the coverage
table below). For iShares peers the holdings request also asks for the
fund's key facts (`component=holdings,keyFundFacts`, still one request),
and the result's notes repeat the product structure and methodology served
with that file. A peer whose key facts say synthetic or optimised is
skipped and the next peer is tried.

### How a fund reaches its index

1. `TARGETS` in the module: a short curated list, each entry with the
   evidence for its replication method.
2. `match_directory(directory, index_col, replication_col, issuer_col,
   covered)`: rows from a fund directory, for example SIX's
   `IssuerNameFull`, `FundUnderlyingDescription` and
   `ReplicationMethodDesc`. A fund is matched only when its issuer is one of
   `BLOCKED_ISSUERS` (UBS, Invesco EMEA, Pictet, WisdomTree, Fidelity,
   Franklin, Swiss Life; Invesco only outside the US, whose funds file
   N-PORT), its ISIN is not in `covered` (the ISINs direct adapters already
   list), its index name equals one of the index's aliases exactly (case,
   spacing and ® ignored), so "MSCI EMU Small Cap" never becomes "MSCI EMU",
   and its replication normalises to `physical-full`. A Vanguard or
   Xtrackers fund on the S&P 500 is never matched: it has its own source, or
   stays unresolved.

Peers are tried in order. iShares peers are found by ISIN in the regional
product screener (`uk`, `us` or `ch`) and read from the same product-data
endpoint the iShares source uses. The Swiss site is
`targetSite=ishares-ch&locale=en_CH` on `uk-retail01-product-data`. Its
screener gives product pages under `/ch/individuals/en/...`, which is a
404; the working path is `/ch/individual/en/...` (checked 3 November 2026),
so the adapter rewrites it, and the two Swiss peers' pages use it. SPDR
peers are read from the fund finder and the daily workbook. A peer from any
other registered issuer adapter is read through that adapter. All requests go
through `openbasket.http`. Screener results are cached per day with only the
ISIN, portfolio ID, name and page, and peer holdings per peer per day.

### Coverage

| Blocked fund | Index | Peer (source) | Fund's replication evidence |
|---|---|---|---|
| UBS ETF (CH) - SMI (CHF) A-dis, CH0017142719 | SMI | iShares SMI ETF (CH), CH0008899764 | UBS factsheet: physical, full |
| UBS ETF (CH) - SLI (CHF) A-dis, CH0032912732 | SLI | iShares SLI ETF (CH), CH0031768937 | UBS factsheet: physical (full replicated) |
| UBS Core S&P 500 UCITS ETF USD dis, IE00B7K93397 | S&P 500 | iShares Core S&P 500 UCITS ETF, IE00B5BMR087; then SPDR S&P 500 UCITS ETF, IE00B6YX5C33 | fund databases: physical, full |
| UBS Core MSCI EMU UCITS ETF EUR dis, LU0147308422 | MSCI EMU | iShares Core MSCI EMU UCITS ETF, IE00B53QG562 | fund databases: physical, full |
| Invesco EQQQ Nasdaq-100 UCITS ETF Dist, IE0032077012 | Nasdaq-100 | iShares Nasdaq 100 UCITS ETF, IE00B53SZB19 | Invesco product sheet: full physical |
| Invesco EQQQ Nasdaq-100 UCITS ETF Acc, IE00BFZXGZ54 | Nasdaq-100 | iShares Nasdaq 100 UCITS ETF, IE00B53SZB19 | same portfolio as the Dist class, whose product sheet says full physical |

The two EQQQ classes are one portfolio and share `portfolio_id`
`NDX:invesco-eqqq`. The Acc class's own factsheet says only "physical",
which on its own is not enough; its evidence is the Dist class's.

The peers' own replication, checked on 3 November 2026:

| Peer | Evidence |
|---|---|
| iShares SMI ETF (CH), CH0008899764 | iShares key facts (ishares.com/ch): product structure Physical, methodology Replicated |
| iShares SLI ETF (CH), CH0031768937 | iShares key facts (ishares.com/ch): product structure Physical, methodology Replicated |
| iShares Core S&P 500 UCITS ETF, IE00B5BMR087 | iShares key facts (ishares.com/uk): product structure Physical, methodology Physical Replication |
| SPDR S&P 500 UCITS ETF, IE00B6YX5C33 | State Street fund page (ssga.com/uk): Replication Method Replicated |
| iShares Nasdaq 100 UCITS ETF, IE00B53SZB19 | iShares key facts (ishares.com/uk): product structure Physical, methodology Replicated |
| iShares Core MSCI EMU UCITS ETF, IE00B53QG562 | iShares key facts (ishares.com/uk): product structure Physical, methodology Replicated |

iShares tells full replication ("Replicated", "Physical Replication") from
sampling ("Optimised") in its methodology field.

Funds of the blocked issuers on the same five indices from a fund
directory are added through `match_directory`. To cover another index, add an `IndexSpec` with a fully
replicating peer whose issuer is a working source, then add targets.

### What a real run showed (3 November 2026)

All six funds, fetched without a cache (two screeners, five peer files):

| Fund | Peer file as of | Lines | Weight sum | ISIN weight | Seconds |
|---|---|---|---|---|---|
| UBS ETF (CH) SMI, CH0017142719 | 2026-11-02 | 26 (20 equities) | 100.00% | 99.70% | 1.1 |
| UBS ETF (CH) SLI, CH0032912732 | 2026-11-02 | 35 (30 equities) | 100.00% | 98.96% | 0.8 |
| UBS Core S&P 500, IE00B7K93397 | 2026-11-02 | 509 (503 equities) | 100.00% | 99.86% | 1.4 |
| UBS Core MSCI EMU, LU0147308422 | 2026-11-02 | 224 (215 equities) | 100.00% | 99.67% | 0.8 |
| Invesco EQQQ Dist, IE0032077012 | 2026-11-02 | 108 (101 equities) | 100.00% | 99.95% | 0.8 |
| Invesco EQQQ Acc, IE00BFZXGZ54 | 2026-11-02 | 108 (101 equities) | 100.00% | 99.95% | 0.0 (cached) |

The lines without an ISIN are cash and index futures. The SMI peer holds
exactly 20 equities and the SLI peer 30, the size of each index.

After the fixes (same day): UBS Core S&P 500 (IE00B7K93397) through the
iShares peer, 509 lines, 100.00%, 99.86% with an ISIN, as of 2026-11-02,
2.3 s, with the key facts in the notes; the UBS SMI network test passed and
both Swiss peer pages answer HTTP 200 at `/ch/individual/en/`.

### How close one fund's holdings are to another's on the same index

We have no free index weights to compare against, so we compared two
physically replicating S&P 500 UCITS ETFs whose holdings we do have, both as
of 2 November 2026:

| | iShares Core S&P 500 (IE00B5BMR087) | SPDR S&P 500 UCITS (IE00B6YX5C33) |
|---|---|---|
| Lines | 509 | 510 |
| ISIN weight | 99.86% | 100.00% |

- 502 ISINs are in both; two are only in the SPDR file; none only in iShares.
- Overlap, the sum over ISINs of the smaller weight, is **99.63%**.
- The largest difference on one line is **0.058 percentage points**.

So for a fully replicating target, a stand-in should be within a few hundredths
of a point per line and about 0.4% of the fund in total. The differences are
cash (0.1 to 1% in the peers above), futures used to equitise it, the timing
of index changes and corporate actions, and the date: a stand-in shows
the peer's positions, not the target's. A UBS SMI ETF versus SMI comparison was
not possible: SIX publishes no free weights and UBS blocks its holdings.

### Gaps

- **Sampled funds.** UBS ETF (CH) - SPI (CH0131872431) is stratified
  sampling according to its factsheet, so it is left out.
- **Swap funds** are never proxied here. The existing `lookthrough.proxies`
  setting can still map one to a physical fund explicitly; those rows are
  labelled as a proxy.
- **Funds on their own or on Solactive indices** (most WisdomTree, Fidelity,
  Franklin and Pictet ETFs) have no fully replicating peer from another
  issuer.
- **Index membership is not checked against the index provider.** The SIX
  page's constituent list comes from an undocumented JSON service behind the
  page, and we did not build on it.
- **SPDR line classes.** SPDR names currency balances in full (`U.S.
  Dollar`, `British Pounds`) and futures by contract (`EMINI S&P DEC26`).
  Lines with no identifier whose name is a currency become `cash`, and
  futures contracts `derivative (futures)`; they carry no weight in the
  files seen.

### Terms and credit

Peer holdings are public data from the peer issuer's website (BlackRock
iShares, State Street SPDR), under that site's terms. BlackRock's terms
forbid reproduction and redistribution, and the SPDR EMEA workbook's footer
says it may not be reproduced, copied or transmitted without State Street's
written consent. So stand-ins are `local_only`. Every row is credited to the
peer's issuer; the index provider is named only as the index's owner. Index
provider weights (SIX, Euronext, Solactive, Nasdaq, STOXX, MSCI, FTSE
Russell, S&P) are not used. The adapter fetches one screener per site and
one file per peer per day.
