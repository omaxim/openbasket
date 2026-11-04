# US stand-ins: a US fund on the same index

Module `openbasket.stand_in`, used by the `universe` pipeline
(`universe.stand_ins`, on by default). Rows come from SEC Form N-PORT, so
they are `public` (US government data). Each one is labelled as not the
fund's own holdings.

Most European index ETFs publish their holdings only on their issuer's
site, under terms that do not allow republishing them. The public build
therefore lists those funds by name and ISIN only (status `local_only`).
No open source has their holdings, old or new: annual and semi-annual
reports are the issuers' documents too. Many of these funds track an index
that a US-registered fund also tracks, and a US fund's holdings are public
in its N-PORT filing. A stand-in shows those holdings in their place.

Status as tested on 2026-11-05: working.

## When a stand-in is used

A fund gets a stand-in only when all of these hold:

- it is listed by an issuer or a registry, not as an N-PORT series, and is
  not matched to one;
- its ISIN is not a US ISIN (a US fund files N-PORT itself);
- its name names an index, and a US fund with N-PORT holdings and net
  assets names the same index (see below);
- its own look-through found no holdings it may show: `no_holdings`, or
  `local_only` in the public build.

A fund whose own holdings are available is never shown through a stand-in.
In a local run that means only funds with no holdings from any source get
one.

## How the US fund is chosen

`index_key` folds a fund's name to the words that name its index. It drops
brands (iShares, Xtrackers, Amundi, SPDR, Invesco ...), wrappers (UCITS,
ETF, Fund, Trust, Swap, Physical), share-class and currency words (USD,
Acc, Dist, 1C, Hedged) and anything in parentheses, and it spells some
words one way (`Nasdaq-100` as `nasdaq 100`, `EM` as `emerging markets`,
`Eurozone` as `emu`). A name must contain an index provider (MSCI, S&P,
FTSE, Nasdaq, Russell, STOXX, DAX, Nikkei, Bloomberg and others):

| Fund | Key |
|---|---|
| iShares Core S&P 500 UCITS ETF USD (Acc) | `s&p 500` |
| iShares Core S&P 500 ETF (US) | `s&p 500` |
| Xtrackers MSCI World Swap UCITS ETF 1C | `msci world` |
| Amundi MSCI EM UCITS ETF | `msci emerging markets` |
| iShares MSCI Eurozone ETF (US) | `msci emu` |
| iShares ESG Aware MSCI USA ETF (US) | `esg aware msci usa` |

Every other word counts, before the index name as well as after it, so
"MSCI World SRI" does not pair with "MSCI World", nor "S&P 500 Equal
Weight" with "S&P 500". Keys must be equal: there is no fuzzy matching.
Among US funds with the same key, the one with the largest net assets is
the stand-in. Leveraged and inverse funds, and funds with option overlays,
buffers or futures, get no key, as do Russell Investments' active funds.

## What the fund file says

The fund has status `stand_in` and `matched_via` `stand_in`. `source` is
`nport`, `as_of` is the US fund's filing date, and every row's `via` is the
US fund. The note reads, for example:

> stand-in: holdings of iShares Core S&P 500 ETF (SEC series S000004310),
> a US fund whose name names the same index (S&P 500), from its SEC Form
> N-PORT filing for 2026-06-30; not this fund's own holdings, and weights
> are percent of the US fund's net assets; its own holdings come from a
> source the public build may not republish

Weights are the US fund's, as filed, never rescaled. Where a local run has
published the fund's own coarse splits ({doc}`../data-api`, coarse splits),
the site shows them above the stand-in's holdings. Those splits come from
the fund's real holdings, so they are the better guide to sector and
country weights.

## How close a stand-in is

Measured on 2026-11-05 against funds whose own holdings a local run reads.
Overlap is the sum, over ISINs, of the smaller of the two weights: 100%
means the same portfolio. Country and sector gaps are half the sum of the
absolute differences, in points.

| Fund | Stand-in | Overlap | Country gap | Sector gap |
|---|---|---|---|---|
| iShares Core S&P 500 UCITS ETF | iShares Core S&P 500 ETF | 90.5% | 2.8 | 3.1 |
| iShares Core MSCI World UCITS ETF | iShares MSCI World ETF | 90.6% | 2.7 | 3.8 |
| iShares Core MSCI Europe UCITS ETF | iShares Core MSCI Europe ETF | 87.9% | 5.1 | 4.7 |
| iShares MSCI Japan GBP Hedged UCITS ETF | iShares MSCI Japan ETF | 89.6% | 0.7 | 2.7 |
| iShares S&P 500 Equal Weight UCITS ETF | Invesco S&P 500 Equal Weight ETF | 92.7% | 5.5 | 1.3 |
| iShares MSCI Taiwan UCITS ETF | iShares MSCI Taiwan ETF | 79.1% | 8.7 | 8.6 |
| iShares MSCI World SRI UCITS ETF | Invesco MSCI World SRI Index Fund | 67.0% | 6.4 | 5.5 |
| iShares S&P 500 Swap UCITS ETF | iShares Core S&P 500 ETF | 0% | 50.1 | 50.1 |

For physically replicating funds on broad indexes, most of the gap is
age: the N-PORT positions were four months older than the issuer's file
they were compared with, and prices and index changes moved the weights in
between. Screened and ESG variants agree less, because providers apply
screens differently. A swap-based fund holds a substitute basket unrelated
to its index, so it does not overlap its stand-in at all. For such a fund
the stand-in shows the index exposure, not what the fund holds.

These figures come from a partial local run (12 pairs). They will be
updated from a complete one.

## Coverage

In a local universe of 47,035 funds (2026-11-05), 414 funds have a
stand-in. The most common indexes are the S&P 500 (48 funds), MSCI World
(35), Nasdaq-100 (24), MSCI USA (24), MSCI Japan (21), MSCI Europe (19) and
MSCI Emerging Markets (18). Indexes no US fund names, such as the STOXX
Europe 600, FTSE 100 and SMI, have none. Neither do active funds or most
bond funds.

## Known gaps

- Matching is by name only. A European fund named after an index it only
  samples or screens may pair with a US fund that replicates it in full.
- The stand-in is as old as the US fund's latest N-PORT filing, two to five
  months.
- A fund whose name gives the index differently from every US fund ("MSCI
  World Information Technology" against "Technology Select Sector") gets no
  stand-in.
