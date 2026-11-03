# openbasket-redirect

Serves `https://openbasket.leith.org.uk` and sends **browsers** on to
`https://openbasket.leithresearch.org`, keeping the path, query and hash.

This is a browser redirect only. GitHub Pages cannot send a server-side
redirect (301/302): for any path but the root it answers with `404.html` and
HTTP status 404, and the page then redirects with JavaScript. So:

- A link opened in a browser, such as
  `https://openbasket.leith.org.uk/docs/usage.html`, lands on the same page
  of the main site.
- `curl`, `fetch`, `requests` and any other API client get an HTTP 404 and an
  HTML page, not JSON. API clients must use
  `https://openbasket.leithresearch.org/api/v1/...` directly; the page says
  so.

Files:

- `index.html` handles the root; GitHub Pages serves `404.html` for every
  other path, so both files carry the same redirect.
- The redirect is `location.replace`, with a `<meta http-equiv="refresh">`
  fallback to the site root for browsers without JavaScript, a canonical
  link, and a visible link.
- `CNAME` sets the custom domain; `.nojekyll` turns off Jekyll.

## Setup

1. Push these files to the default branch of `omaxim/openbasket-redirect`.
2. Settings > Pages: source "Deploy from a branch", the default branch, `/`
   (root). Custom domain `openbasket.leith.org.uk`, then enforce HTTPS once
   the certificate is issued.

DNS: `openbasket.leith.org.uk` is a CNAME to `omaxim.github.io`, and the
domain is verified for the `omaxim` account.

This folder is kept in the main `omaxim/openbasket` repository as the source
of truth; copy it over when it changes.
