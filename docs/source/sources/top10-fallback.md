# Top-holdings fallbacks (justETF, extraETF)

**Status: not built.** Neither site's terms allow what openbasket would need
to do: fetch pages automatically and republish what they show in a public
dataset. This page records what was checked on 3 November 2026, so the
question is not reopened without new terms or written permission.

The idea was a clearly labelled fallback for UCITS ETFs that have no
full-holdings source: read the top 10 holdings from a public ETF profile,
mark the rest of the fund as unresolved, and never rescale.

## What the sites offer

| | justETF | extraETF |
|---|---|---|
| Operator | justETF GmbH, Munich (part of the Scalable Group) | Isarvest GmbH, Munich |
| Profile URL | `https://www.justetf.com/en/etf-profile.html?isin={ISIN}` | `https://extraetf.com/de/etf-profile/{ISIN}` |
| Holdings shown | Top 10 in the HTML, with names, weights to two decimals and links to `/en/stock-profiles/{ISIN}` | Top 10 in embedded JSON (`items[].isin`, `name`, `weight` in percent to five decimals, `country`) |
| As-of date | Shown with the holdings ("As of 30/09/2026" for IE00B7K93397) | None found |
| Example, UBS Core S&P 500 UCITS ETF (IE00B7K93397) | 10 lines, 38.83% of 505 holdings | 10 lines, 37.95% of 506 holdings |
| Data credited to | Trackinsight, etfinfo, Xignite, gettex, FactSet and justETF GmbH (profile footer) | Not stated on the profile |
| robots.txt | `Allow: /`; disallows `/servlet/`, `/link/` and Wicket search and watchlist URLs | `Allow: /`; disallows `/go/`, `/*/auth*`, some news and cookie pages |

Both pages returned HTTP 200 with no challenge or consent gate, so access
was not the obstacle. The terms were.

## Terms as found

### extraETF

The general terms at `https://extraetf.com/de/about/legal-terms` say they
apply to the use of the services on extraetf.com (section 1.1). Two clauses
rule the source out:

- **4.4**: content may be used for private purposes only; passing content or
  parts of it on to third parties, and any commercial use, needs extraETF's
  written permission.
- **4.5**: reproduction or publication in electronic form needs express
  written permission, and in particular automated querying of extraETF
  content without express written permission is not allowed in any form.

openbasket queries automatically and publishes, so it would breach both.

### justETF

The legal-terms page (`https://www.justetf.com/en/about/legal-terms.html`)
links the general terms as a PDF:
`https://www.justetf.com/documents/justETF_general_terms_and_conditions.pdf`
(German original: `/documents/justETF_AGB.pdf`).

- **Clause 3.1** obliges users not to put excessive strain on justETF and not
  to use programs for automated price queries ("Einsatz von Programmen zur
  automatisierten Kursabfrage"). Every ETF profile is a quote page, so
  fetching profiles with a program is at least close to what the clause
  forbids.
- The terms grant no right to republish the content, and the holdings are
  third-party data: the profile footer credits Trackinsight, etfinfo,
  Xignite, gettex and FactSet.
- The legal notice (`https://www.justetf.com/en/about/legal-notice.html`)
  carries MSCI's notice: without MSCI's prior written permission, MSCI
  information may be used internally only and may not be reproduced or
  re-disseminated in any form. Many UCITS ETFs track MSCI indices, and their
  constituents and weights are index-related data.
- Pulling the top 10 of every fund, again and again, would also be
  systematic extraction from justETF's database (EU Database Directive,
  Art. 7(5)), even though one fund's top 10 is a small part of it.

openbasket publishes an open dataset with a credit line on each row, so a
credit line does not fix this: the data would still be redistributed
without a licence.

## What would change this

- Written permission from either operator for automated retrieval and public
  redistribution of top-holdings data (justETF has a "Become a partner"
  contact; extraETF can be reached at `support@extraETF.com`). It would have
  to cover the third-party data providers too.
- Until then, funds with no full-holdings source stay unresolved in
  openbasket, which is the honest answer. The issuer's own top-10 or
  monthly factsheet, where its terms allow reuse, is a better fallback than
  an aggregator, because it is the primary source.

## If permission is ever given

The adapter would be `top10_<site>` in
`src/openbasket/sources/issuers/`, with:

- `products()` empty or limited to the ISINs asked for, since neither site
  has a terms-compatible fund list;
- `holdings()` returning only the lines shown, `weight_pct` as displayed
  (already in percent), the as-of date from the page (justETF) or `None`
  (extraETF), and the note *top 10 holdings only; the rest of the fund is
  unresolved*, so the pipeline reports the remainder as unresolved and never
  rescales;
- `frequency = "monthly"` for justETF, probably (on 3 November 2026 its
  holdings were dated 30 September 2026, two month-ends back); unknown for
  extraETF;
- one request per fund per day, cached only after a successful parse.
