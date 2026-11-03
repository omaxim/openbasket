# Adding a source

Each issuer is a plug-in: one module in `src/openbasket/sources/issuers/`,
one test file and one page in this folder. Nothing else needs editing.

1. Create `src/openbasket/sources/issuers/<name>.py`. It must call
   `register(IssuerAdapter(...))` with:
   - `products()`, returning one row per share-class ISIN with the columns in
     `PRODUCT_COLUMNS`;
   - `holdings(product, ctx)`, returning an `IssuerHoldings` with the as-of
     date read from the issuer's own file, the URL it came from, and a frame
     with `HOLDING_COLUMNS`. Weights are percent of net assets exactly as
     reported: never rescale them.
   - `credit`, `licence` and `homepage`, which are shown next to every row
     from this source.
   - `redistribution`: `"public"` only when the source's licence allows its
     holdings to be republished (a regulator's open data, CC BY, public
     domain); otherwise leave the default, `"local_only"`. The public build
     never fetches or publishes a `local_only` source; people who run
     openbasket themselves still get its holdings locally.
   - `enabled_by_default=False` when the site's terms forbid automated
     access even for private use. The adapter then runs only when the user
     names it in `issuers.enabled` (for example in `conf/local`). A site
     whose robots.txt disallows the pages needed gets no adapter at all.
2. Fetch through `openbasket.http.get` / `post`, which waits between requests
   to the same host and retries rate limits. Do not bypass bot protection,
   log-in walls or consent gates; if a site needs them, it is not a source.
3. Check frames with `validate_products` and `validate_holdings` in the tests.
   Keep test fixtures tiny and synthetic in shape (a handful of rows with the
   real structure), so the repository does not republish issuer data.
4. Add one `@pytest.mark.network` test that fetches one real fund.
5. Write `docs/source/sources/<name>.md`: what it covers, the exact
   endpoints, how a fund ISIN reaches its holdings, update frequency, terms
   and credit, known gaps (swap funds, top-10 only, rounding), and the
   numbers from a real run.

`openbasket.sources.issuers.safe_discover()` imports every module in the
package, so the ingest pipeline picks the new source up automatically. A
module that fails to import is logged and skipped; the other sources still
run.

A source that is not an issuer adapter (a module directly under
`src/openbasket/sources/`, such as `nport` or `tickerinside_source`) declares
its class in a module-level constant, `REDISTRIBUTION = "public"` or
`"local_only"`. `openbasket.sources.redistribution(source_id)` answers for
any source id found in the data: the adapter's field, else the module's
constant, else `"local_only"`.
