# Issuers without a usable route

Checked on 3 November 2026 with the project's own client (`openbasket.http`,
User-Agent `Mozilla/5.0 (compatible; openbasket; +https://openbasket.leithresearch.org)`).
openbasket does not get around bot protection, log-ins, consent gates or
`robots.txt`, so the issuers below have no adapter. Their US-registered funds
are still covered by SEC N-PORT. Franklin Templeton, checked at the same
time, works and has its own page.

| Issuer | What was tried | Result |
|---|---|---|
| Invesco | `www.invesco.com/us/...` product pages and the US holdings download (`/us/financial-products/etfs/holdings/main/holdings/0?...&ticker=QQQ`), `dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/QQQ/...`, the UK, Swiss, Canadian and Australian ETF pages | HTTP 406 with an empty body from the Varnish/Fastly edge on every product page and data call. Marketing pages (`/qqq-etf/en/home.html`) and the UK sitemap answer 200. |
| WisdomTree | `www.wisdomtree.com`, `www.wisdomtree.eu`, `dataspanapi.wisdomtree.com` | Cloudflare challenge ("Just a moment...", HTTP 403) on every page, including the sitemap. `robots.txt` also disallows `/api`. |
| Fidelity International | `fidelity.lu`, `fidelity.ch`, `fidelityinternational.com`, ETF pages on `fidelity.de` | HTTP 403 "Access Denied" (Akamai). `fidelity.co.uk` fund factsheets answer, but show only the top 10 holdings from Morningstar. |
| L&G (Legal & General) | `fundcentres.landg.com` | The fund pages and the `/srp/api/fund-centre/...` JSON answer, but `robots.txt` disallows `/srp/api/`, `/srp/documents/` and `/srp/lit/`, which is where the fund data and holdings files are; the HTML fund pages carry no holdings. `am.landg.com` has no fund data. |
| Lyxor | `www.lyxor.com`, `www.lyxoretf.com` | `lyxor.com` redirects to `lyxoretf.com`, which no longer answers. Former Lyxor ETFs are now Amundi ETFs: FR0010315770 (now Amundi MSCI World Swap II), FR0007052782 (Amundi CAC 40) and LU0496786574 (Amundi Core S&P 500 Swap) are all in Amundi's product API. |

## What this means for coverage

- Invesco QQQ (US46090E1038) is a unit investment trust, which files no
  N-PORT, so it has no full-holdings source. Most other US Invesco, WisdomTree
  and Fidelity ETFs are 1940 Act funds and come from N-PORT, two to five
  months old; commodity trusts are not.
- Invesco, WisdomTree, Fidelity and L&G UCITS ETFs (IE and LU ISINs) have no
  full-holdings source. They show as unresolved, unless a top-holdings
  fallback is added.
- An issuer could open a route at any time, for example by allowing
  `/srp/api/` in L&G's `robots.txt` or by publishing a holdings file outside
  the bot protection. Each issuer could also be asked for permission.
