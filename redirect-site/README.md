# openbasket-redirect

Serves `https://openbasket.leith.org.uk` and sends every request to
`https://openbasket.leithresearch.org`, keeping the path, query and hash, so
old links such as `https://openbasket.leith.org.uk/api/v1/meta.json` still
work.

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
