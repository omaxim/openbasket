# Xtrackers (DWS): not used

**Status: not usable: `robots.txt` disallows the holdings paths.** Checked
on 3 November 2026. Xtrackers publishes full daily holdings on its own site,
but only through paths its `robots.txt` disallows, so openbasket does not
read them, not even for a private run: there is no adapter, and none may be
written that fetches those paths. Xtrackers UCITS ETFs stay unresolved unless
another source covers them.

## What the site offers

`https://etf.dws.com/{culture}/sitemap.xml` (allowed) lists the product
pages, for example `/en-gb/IE00BJ0KDQ92-msci-world-ucits-etf-1c/`. Four
culture sitemaps gave:

| Sitemap | Share classes | IE | LU |
|---|---|---|---|
| `en-gb` | 454 | 203 | 251 |
| `de-de` | 436 | 193 | 243 |
| `en-ch` | 209 | 101 | 108 |
| `de-ch` | 209 | 101 | 108 |
| Union | 474 | 211 | 263 |

70 of the 474 page slugs contain `swap`. No fund is domiciled in Switzerland.
The sitemaps also list about 18 ETCs (physical metals, carbon EUA and crypto
notes, with DE, GB, XS and CH ISINs). They are single-asset notes, not funds,
so there is nothing to look through.

The product page itself is an empty client-side shell (`data-ssr="false"`,
`ENTRY_GATE_ENABLED:true`). Everything on it is loaded in the browser from
`PDP_API_BASE_URL = https://etf.dws.com/api/`. The response details below
come from an earlier survey and the site's JavaScript; they were not fetched
again for this page, because the paths are disallowed:

- holdings: `GET /api/pdp/{culture}/etf/{ISIN}/holdings`, a JSON table with
  ISIN, name, weight (percent of NAV in `sortValue`), market value, country,
  industry and asset class, and an as-of date in the disclaimer text
  (`Source: DWS dd/mm/yyyy`);
- fund facts (replication method, domicile, index):
  `GET /api/pdp/{culture}/etf/{ISIN}/pdpMetaTagsTealium`;
- the "Download full securities list" workbook:
  `/etfdata/export/{GBR|CHE}/ENG/excel/product/constituent/{ISIN}/`.

## Why it is not used

`https://etf.dws.com/robots.txt` has a single `User-Agent: *` group that
includes:

```text
Disallow: /etfdata/*
Disallow: /export/*
...
Disallow: /api/
Disallow: /EntryGate/
```

Read as RFC 9309 specifies (and as `protego`, the parser Scrapy uses, reads
it), this disallows the holdings and fund-facts endpoints and the Excel
export. Python's `urllib.robotparser` reports them as allowed, but only
because it stops at the first blank line and does not understand `*`
wildcards. It also "allows" `/etfdata/*`, so it is not a reliable check
for this file.

The allowed literature endpoint
`/product_literature/api/v1/DocumentMetadata/{culture}/{ISIN}` returns
`{"fundName":"","documents":[]}` without the site's audience and entry-gate
selection, which openbasket does not make on anyone's behalf. Even with it,
it lists documents (factsheets with top-10 holdings, semi-annual reports),
not daily holdings.

## Other routes

- US-listed Xtrackers ETFs (`etf.dws.com/en-us`) file Form N-PORT, so they are
  covered by the SEC source, with its two-to-five-month lag.
- For UCITS share classes, permission or a data feed from DWS would be
  needed to use the issuer's holdings. A third-party source with full
  holdings and clear terms would also work.
