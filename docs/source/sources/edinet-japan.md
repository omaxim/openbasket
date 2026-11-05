# EDINET Japan: investment trust reports (client only)

Module `openbasket.sources.edinet_japan`. Redistribution class `public`:
EDINET's terms of use (利用規約, revised 25 April 2025) let anyone reuse its
content under the Public Data License 1.0 (PDL1.0), crediting EDINET and
saying who edited what was taken from it. The same terms bar scraping the
EDINET sites; the API is the allowed route, and this client uses only the
API. No issuer adapter is registered.

**Status: not usable as a holdings source yet: the API needs a free key that
each user registers for, and the reports are not parsed.** Checked on
2026-11-03.

Investment trusts file securities reports (document type `120`) and
semi-annual reports (`160`) on EDINET; the financial statements end with a
schedule of every security held (有価証券明細表). EDINET API v2
(`https://api.edinet-fsa.go.jp/api/v2/documents.json?date=YYYY-MM-DD&type=2`,
`/documents/<docID>?type=5`) needs a *Subscription-Key*; without one it answers
401 "invalid subscription key". The key is free but needs registering an
account (e-mail and phone) on the EDINET API site, which this project does not
do on anyone's behalf.

`openbasket.sources.edinet_japan` is a client only: with `EDINET_API_KEY` set
(in `.env`) it lists the fund reports filed on a day and downloads a document.
It does not parse the schedule (that needs real documents to develop against)
and no open mapping from a fund ISIN (`JP90C...`) to EDINET's `fundCode` was
found. The key is sent as a query parameter and kept out of error messages.

Freshness: annual and semi-annual reports, so positions are up to a year old.
