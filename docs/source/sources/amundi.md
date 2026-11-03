# Amundi ETF

Adapter `amundi` (`src/openbasket/sources/issuers/amundi.py`). It covers
Amundi's whole ETF range, including the former Lyxor funds (France-domiciled,
labelled `LYXOR` in Amundi's data) and the former ComStage funds
(Germany-domiciled `DE000ETF...` ISINs, labelled `COMSTAGE`). Holdings come
from the issuer's own website.

## Coverage

Run on 3 November 2026: **685 share classes** in **386 portfolios**.

| | Share classes |
|---|---|
| Domicile LU / IE / FR / DE / HK | 461 / 107 / 105 / 8 / 4 |
| Replication physical / sampled / swap | 351 / 130 / 204 |
| Range AMUNDI / LYXOR / COMSTAGE | 470 / 207 / 8 |
| Equity / Fixed Income / Multi Asset / other | 464 / 209 / 7 / 5 |
| CHF share classes | 16 |

`replication` is `swap` for Amundi's `Indirect(Swap Based)`, and `sampled`
for `Direct(Physical)` with `FUND_SAMPLING = true`. The other five products are
two commodity ETFs, a VIX futures ETF, the Amundi Physical Gold ETC and
the Amundi Bitcoin ETP. The last two are not funds and have no composition.

Switzerland: amundietf.ch has only a professional profile, so it is not read.
The CHF share classes (for example LU1681044993, Amundi MSCI Switzerland, and
LU2611732632, Amundi DJ Switzerland Titans 30) are all in the German retail
list. Amundi has no Swiss-domiciled ETFs.

## Endpoints

Every Amundi ETF country site runs the same JSON API that its product pages
call from the browser:

```text
POST https://<site>/mapi/ProductAPI/getProductsData
POST https://<site>/mapi/PageContentAPI/getProductPageLinkByPageIdsAndContext
```

Each body carries a `context`. The adapter only sends `userProfileName:
RETAIL`, exactly as the site embeds it in its public retail pages (for
example `const pageContext = {"countryCode":"DEU", ..., "languageCode":"de",
"userProfileName":"RETAIL"}` on `https://www.amundietf.de/de/privatanleger`).
It never sends a professional (`INSTIT`) context.

Fund list
: `getProductsData` with no `productIds` returns every product the site lists.
  The adapter asks for `ISIN`, `SHARE_MARKETING_NAME`, `FUND_REFLEX_CODE` (the
  portfolio code shared by its share classes, used as `portfolio_id`),
  `FUND_REPLICATION_METHODOLOGY`, `FUND_SAMPLING`,
  `FUND_DOMICILIATION_COUNTRY`, `ASSET_CLASS`, `SUBASSET_CLASS`, `CURRENCY`,
  `CURRENCY_HEDGE`, `FUND_ISSUER`, `MNEMO`, `MAIN_LISTINGS` and
  `POSITION_AS_OF_DATE`. The ticker is the site country's main listing in
  `MAIN_LISTINGS` (a Bloomberg ticker such as `AMEW GY`), or else `MNEMO`.
  `getProductPageLinkByPageIdsAndContext` with `pagesIds` set to the ISINs
  returns each product's page on that site, which is the source link shown.
  Five products had no page link; they link to the site's retail home page.

Holdings
: `getProductsData` with `productIds: [ISIN]` and
  `composition: {compositionFields: [date, type, bbg, isin, name, weight,
  quantity, currency, sector, country, countryOfRisk]}`, the block the product
  page widget `amundi-product-page.js` (version 20.1.105-prod) sends. It
  returns `products[0].composition.compositionData`, one entry per position,
  and `totalNumberOfInstruments`. The response is cached in `ctx.cache_dir`
  as `amundi_<site>_<ISIN>_<date>.json`, only after it has parsed.

Retail sites read, in priority order (an ISIN listed by several sites is taken
from the first, and its holdings are fetched in that site's context, because a
site returns nothing for a product it does not list):

| Site | Context | Products listed | Added to the union |
|---|---|---|---|
| www.amundietf.de | DEU, de | 625 | 625 |
| www.amundietf.fr | FRA, fr | 652 | 41 (PEA and other French funds) |
| www.amundietf.it | ITA, it | 271 | 4 |
| www.amundietf.at | AUT, de | 578 | 0 |
| www.amundietf.es | ESP, es | 558 | 0 |
| www.amundietf.nl | NLD, en | 551 | 1 |
| www.amundietf.be | BEL, fr | 52 | 0 |
| www.amundietf.lu | LUX, en | 594 | 10 |
| www.amundietf.co.uk | GBR, en | 53 | 0 |
| www.amundietf.se | SWE, en | 552 | 0 |
| www.amundietf.dk | DNK, en | 218 | 0 |
| www.amundietf.com.hk | HKG, en | 4 | 4 (HK-domiciled) |
| www.amundietf.com.sg | SGP, en | 2 | 0 |

Not read: amundietf.ch, .no, .fi and .pl, which offer only a professional
profile; and amundietf.com.tw, whose retail context lists no products. The
whole product list takes about 8 seconds (19 requests across 13 hosts: one
list per site, plus one page-link call for each site that adds ISINs).

## From a fund ISIN to its holdings

1. `products()` gives the ISIN's `site` and `portfolio_id`.
2. `holdings(product, ctx)` posts the composition request with that site's
   retail context and `productIds: [ISIN]`.
3. Each line becomes one row:
   - `isin` from `isin` (set to `None` when it fails its check digit);
   - `weight_pct` = `weight` × 100: **Amundi gives fractions** (0.0775 is
     7.75%);
   - `ticker` = `bbg` when it is a Bloomberg ticker (it has a space, like
     `AIR FP`);
   - `cusip` = `bbg` when it is a valid 9-character CUSIP that matches the ISIN
     or the line has no ISIN (bond lines, such as `01F0306B9` for
     `US01F0306B93`);
   - `country` = `countryOfRisk`;
   - `asset_class` from `type`: `EQUITY_ORDINARY`, `PREFERENCE_SHARES`,
     `DEPOSITORY_RECEIPT` → equity; `CORPORATE`, `GOVERNMENT`, `SECURITIZED`,
     `MUNICIPAL`, `TREASURY_BILL` → debt; `CASH` → cash; `FUTURE` →
     derivative (futures); `ETF`, `MUTUAL_FUND` → fund; `RIGHT`, `WARRANT` →
     other. The raw value is kept in `issuer_type`, with `sector`, `quantity`
     and `bbg`.
4. `as_of` is the `date` on the position lines. If lines carry different
   dates the latest is used and a note says so.

## Update frequency

Daily: on 3 November 2026 nearly every fund was dated 30 October 2026, two
business days back. A few funds are updated monthly. The former ComStage
funds and the multi-asset funds (`DE000ETF...`) were dated 7 October 2026.

## Real run

3 November 2026, from the German retail context:

| Fund | Replication | Lines | Sum of weight_pct | Weight with an ISIN | As of | Seconds |
|---|---|---|---|---|---|---|
| Amundi Core MSCI World UCITS ETF Acc (IE000BI8OT95) | physical | 1,252 | 100.00 | 99.66 | 2026-10-30 | 0.2 |
| Amundi Core Global Aggregate Bond UCITS ETF Acc (LU1437024729) | sampled | 12,428 | 100.00 | 107.33 | 2026-10-30 | 1.8 |
| Amundi MSCI World Swap UCITS ETF EUR Acc (LU1681043599) | swap | 141 | 99.86 | 99.82 | 2026-10-30 | 0.1 |
| Amundi MSCI Switzerland UCITS ETF CHF Acc (LU1681044993) | swap | 58 | 96.02 | 96.04 | 2026-10-30 | 1.0 |
| Amundi DJ Switzerland Titans 30 UCITS ETF Dist (LU2611732632) | physical | 36 | 100.00 | 99.62 | 2026-10-30 | 1.0 |
| Amundi CAC 40 UCITS ETF Dist (FR0007052782) | physical | 49 | 100.00 | 99.97 | 2026-10-30 | 1.0 |
| Amundi Multi-Asset Portfolio UCITS ETF Dist (DE000ETF7011) | physical | 13 | 100.00 | 99.93 | 2026-10-07 | 1.0 |

The seconds are mostly the one-second wait between requests to one host. The
aggregate bond fund's lines with an ISIN add up to 107.33% because its two
cash lines are +1.82% and −9.15%. The multi-asset
fund's lines are 11 Amundi ETFs and a money-market fund, each of which can be
looked through in turn.

## Known gaps

- **Swap funds report their substitute basket, not the index.** The 204
  `swap` products list the securities the fund holds while a total return
  swap, which is not listed, delivers the index return. For example, Amundi
  MSCI Switzerland's 58 lines are 51 US and 6 French shares, and no Swiss
  ones. These baskets add up to less than 100% (96.02% and 99.86% above),
  because the swap's value is missing. Every holdings result for a swap
  product carries a note saying this. Treat these as collateral, not as
  exposure. Map them to a physical fund on the same index with
  `lookthrough.proxies`.
- **Physical funds add up to exactly 1.0.** The lines include cash (sometimes
  negative) and futures at their weight in the portfolio, so the total is the
  portfolio, not a rescaled index. Weights are passed on unchanged.
- **Lines without an ISIN** are cash: `type` `CASH`, with no name or id,
  0.03% to 0.4% of the physical equity funds above, and sometimes negative.
  Index futures carry the exchange contract's ISIN (`DE000C71QQW8`, MSCI World
  future on Eurex, 0.30% of IE000BI8OT95), so they count as "with an ISIN".
  `asset_class` marks them as `derivative (futures)`.
- **No FX forwards** are listed for currency-hedged share classes. The
  CHF-hedged Amundi Core MSCI USA (IE000X6E0QC3) shows 516 lines: shares, one
  cash line, one future and one right.
- **No composition**: the Amundi Physical Gold ETC (FR0013416716), the Amundi
  Bitcoin ETP (XS3332092090) and the two Hong Kong ETFs (four trading
  counters, `HK0000...`) return no lines. Their results are empty and carry a
  note.
- Some FR-domiciled PEA funds and a few others are listed only on the French,
  Italian, Luxembourg or Dutch sites, which is why every retail site is read.

## Terms and credit

Credit line: *Holdings: Amundi Asset Management, Amundi ETF websites
(amundietf.de and other country sites).*

No login, cookie or consent step is needed for the API. `robots.txt` on
amundietf.de disallows only search-query pages (`/*?isin=` and similar), not
`/mapi/`. The sites show an investor-profile disclaimer in the browser. The
adapter does not accept it on anyone's behalf. It only sends the retail
context that the public retail pages already embed.

**Licence: restricted.** Amundi's legal notice (*Rechtliche Hinweise*,
`https://www.amundietf.de/de/privatanleger/rechtshinweise`, updated
1 December 2025) says Amundi Asset Management holds the copyright in the
site's content. It allows downloads for personal use only. Without Amundi's
prior written permission it forbids reproducing or transmitting the site's
information, or using it for public or commercial purposes. No open licence is
offered. **Publishing these rows on openbasket needs that permission**, so
until it is granted the source should be used for research only, not
republished.

## Example

```python
from openbasket.sources.issuers import FetchContext, discover

amundi = discover()["amundi"]
funds = amundi.products()
cac = funds.set_index("isin").loc["FR0007052782"].to_dict() | {"isin": "FR0007052782"}
got = amundi.holdings(cac, FetchContext(cache_dir="data/01_raw/issuers/holdings"))
got.as_of                      # '2026-10-30'
got.holdings[["isin", "name", "weight_pct"]].head(3)
#            isin                    name  weight_pct
#  FR0000120271  TOTALENERGIES SE PARIS        9.91
#  ...
```
