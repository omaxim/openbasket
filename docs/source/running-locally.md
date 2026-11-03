# Running it yourself

openbasket is a tool, not a database. The public site and the monthly
release hold only what the sources' licences let anyone republish: SEC Form
N-PORT, TickerInside (CC BY 4.0), the Spanish and Brazilian regulators'
filings, and fund identities from open registries. Many issuers publish full
holdings on their own websites under terms that do not allow that, so the
public build never fetches them and never writes their rows.

Those funds are still in the public index, by name and ISIN, with status
`local_only` and the note "Holdings for this fund are available from
*issuer* when you run openbasket yourself". Run openbasket on your own
machine and it reads every enabled source for you, issuer sites included,
and shows everything locally. Nothing you fetch this way is published
anywhere.

## Run it

```bash
git clone https://github.com/omaxim/openbasket.git && cd openbasket
uv venv --python 3.12 .venv
uv pip install --python .venv -e '.[dev,docs]'
cp .env.example .env   # your SEC user agent, and an OpenFIGI key if you have one
export KEDRO_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1

kedro run --pipeline=ingest           # N-PORT, SEC tickers, every enabled issuer's fund list
kedro run --pipeline=security_master
kedro run --pipeline=consolidate
kedro run --pipeline=lookthrough --params=isin=IE00B5BMR087
```

`kedro run` without `--env` uses `conf/base` plus your `conf/local`, where
`publish.mode` is `full`: every enabled source is read. See {doc}`usage` for
the parameters and {doc}`publishing` for a whole-universe run and the local
copy of the site's files.

## Sources that are off by default

Some sites forbid automated access even for private use. Their adapters
exist but are off (`enabled_by_default = False`); switch one on only if
your own use is allowed, by naming it in `conf/local/parameters.yml`:

```yaml
issuers:
  # This list replaces the default set, so name every adapter you want.
  enabled: [cvm_brazil, cnmv_spain, hsbc]
```

A site whose robots.txt disallows the pages a source would need gets no
adapter at all.

## Public and full builds

| | Full (`kedro run`) | Public (`kedro run --env public`) |
|---|---|---|
| `publish.mode` | `full` | `public` |
| Issuer sites (`local_only`) | read | never asked; funds listed by name and ISIN, status `local_only` |
| Public sources | read | read |
| Fund list | `data/01_raw/issuers/issuer_products.parquet` | `data/01_raw/issuers/issuer_products_public.parquet` |
| `publish` | writes everything | refuses to write anything if a row or fund comes from a `local_only` source |

The GitHub Action runs the public build. Its output goes through
`scripts/check_build.py`, which fails the run if any published row, fund or
`meta.sources` entry is `local_only` (see {doc}`deployment`).
