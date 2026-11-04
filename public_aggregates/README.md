# Public aggregates

`aggregates.json` holds openbasket's own coarse splits of funds whose
holdings come from sources that may not be republished (issuer websites):
for each such fund, the share of net assets by sector (openbasket's sector
list), country (ISO code), region and asset class, in whole percent, with
groups under 1% folded into "Other".

It contains no holdings: no company names, no constituent ISINs, no row
weights. `openbasket.aggregates.validate` refuses anything else, and the
public build and its CI gate run the same check.

It is written by a local full run (`kedro run --pipeline=full`, or
`kedro run --pipelines=sectors,aggregates` after one), merged with
the earlier file so a fund whose scraper failed keeps its last splits for up
to 180 days, and committed on its own, by hand or by the run itself
(`aggregates.share` in `conf/local/parameters.yml`), so the public site can
show these funds' shape. See `docs/source/running-locally.md`.
