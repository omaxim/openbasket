# Exchange and index publications

Some issuers block automated access to their sites: UBS (Akamai 403),
Invesco EMEA (406), Pictet (AWS WAF), Swiss Life and WisdomTree (Cloudflare),
Fidelity and Franklin. This page records what exchanges and index providers
publish that could stand in for those funds' holdings, and describes the one
route that is both free and reusable: the **index composition proxy**.

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

## Index composition proxy

What is public, and already used elsewhere in openbasket, is each issuer's
own daily holdings file. A physically, fully replicating ETF holds every
index constituent at its index weight, so the holdings of **another such
ETF on the same index** (the "peer") are the best free picture of the index,
and so of a blocked fund that tracks it the same way.

The adapter is `openbasket.sources.issuers.index_proxy`, registered as
`index_proxy`. For a mapped fund it fetches the peer's holdings from the
peer's issuer and returns them unchanged: weights are percent of the
**peer's** net assets, never rescaled, and the as-of date is the peer
file's position date. Every result says what it is. The first note reads:

> index composition proxy: index composition of the Swiss Market Index (SMI)
> (SIX) as held by iShares SMI ETF (CH) (CH0008899764), a physically
> replicating ETF on the same index, positions as of 2026-11-02; used as a
> proxy for UBS ETF (CH) - SMI (CHF) A-dis (CH0017142719); not the fund's own
> holdings

Further notes give `kind: index composition proxy (not the fund's own
holdings)`, the source (the peer's issuer and product page), the credit line
(peer issuer and index provider) and the target's replication evidence. The
products row's `product_page_url` is the peer's page, since that is where
the data comes from; `kind`, `index_name`, `peer_isin` and `fund_issuer` are
extra columns.

**Only for full physical replication.** A target must be
`physical-full`. Swap funds (for example Invesco S&P 500 UCITS ETF,
IE00B3YCGJ38) hold a basket unrelated to the index and are refused, and so
are sampled or optimised funds, whose holdings can differ a lot from the
index. A plain "Physical" label is not enough, because it does not say full.

### How a fund reaches its index

1. `TARGETS` in the module: a short curated list, each entry with the
   evidence for its replication method.
2. `match_directory(directory, index_col, replication_col)`: rows from a fund
   directory, for example SIX's `FundUnderlyingDescription` and
   `ReplicationMethodDesc`. A fund is matched only when its index name equals
   one of the index's aliases exactly (case, spacing and ® ignored), so
   "MSCI EMU Small Cap" never becomes "MSCI EMU", and only when its
   replication normalises to `physical-full`.

Peers are tried in order. iShares peers are found by ISIN in the regional
product screener (`uk`, `us` or `ch`) and read from the same product-data
endpoint the iShares source uses. The Swiss site is
`targetSite=ishares-ch&locale=en_CH` on `uk-retail01-product-data`. SPDR
peers are read from the fund finder and the daily workbook. A peer from any
other registered issuer adapter is read through that adapter. All requests go
through `openbasket.http`. Screener results are cached per day with only the
ISIN, portfolio ID, name and page, and peer holdings per peer per day.

### Coverage

| Blocked fund | Index | Peer (source) | Replication evidence |
|---|---|---|---|
| UBS ETF (CH) - SMI (CHF) A-dis, CH0017142719 | SMI | iShares SMI ETF (CH), CH0008899764 | UBS factsheet: physical, full |
| UBS ETF (CH) - SLI (CHF) A-dis, CH0032912732 | SLI | iShares SLI ETF (CH), CH0031768937 | UBS factsheet: physical (full replicated) |
| UBS Core S&P 500 UCITS ETF USD dis, IE00B7K93397 | S&P 500 | iShares Core S&P 500 UCITS ETF, IE00B5BMR087; then SPDR S&P 500 UCITS ETF, IE00B6YX5C33 | fund databases: physical, full |
| UBS Core MSCI EMU UCITS ETF EUR dis, LU0147308422 | MSCI EMU | iShares Core MSCI EMU UCITS ETF, IE00B53QG562 | fund databases: physical, full |
| Invesco EQQQ Nasdaq-100 UCITS ETF Dist, IE0032077012 | Nasdaq-100 | iShares Nasdaq 100 UCITS ETF, IE00B53SZB19 | Invesco product sheet: full physical |
| Invesco EQQQ Nasdaq-100 UCITS ETF Acc, IE00BFZXGZ54 | Nasdaq-100 | iShares Nasdaq 100 UCITS ETF, IE00B53SZB19 | Invesco factsheet: physical |

Funds on the same five indices from a fund directory are added through
`match_directory`. To cover another index, add an `IndexSpec` with a fully
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

So for a fully replicating target, the proxy should be within a few hundredths
of a point per line and about 0.4% of the fund in total. The differences are
cash (0.1 to 1% in the peers above), futures used to equitise it, the timing
of index changes and corporate actions, and the date: the proxy shows the
peer's positions, not the target's. A UBS SMI ETF versus SMI comparison was
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
- The adapter is in the plug-in registry. The `ingest` pipeline still reads
  iShares and SPDR directly, so until it uses `discover()` the proxy rows
  appear only through the adapter API.

### Terms and credit

Peer holdings are public data from the peer issuer's website (BlackRock
iShares, State Street SPDR), under that site's terms, which have not been
reviewed for bulk use. Every row is credited to the peer's issuer and names
the index provider. Index provider weights (SIX, Euronext, Solactive, Nasdaq,
STOXX, MSCI, FTSE Russell, S&P) are not used. The proxy fetches one screener
per site and one file per peer per day.
