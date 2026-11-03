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

`openbasket.sources.issuers.discover()` imports every module in the package,
so the ingest pipeline picks the new source up automatically.
