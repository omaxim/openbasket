# openbasket

Look through an ETF, fund or bond by ISIN to the securities behind it, each
with its ISIN and its weight in percent of the fund's net assets. Weights are
never rescaled to 100%: weight that cannot be tied to an ISIN is reported as
unresolved. Every row says where it came from (source, link, credit, licence
and as-of date). Built with Kedro.

openbasket is a tool, not a database. The public site,
<https://openbasket.leithresearch.org>, shows only sources whose licence
allows republication (SEC N-PORT, TickerInside, CNMV, CVM and open
registries). Run it yourself and it reads every enabled source, issuer
websites included, for your own use, and shows you everything.

## Quick start

You need git, [uv](https://docs.astral.sh/uv/) and about 3 GB of disk.

```bash
git clone https://github.com/omaxim/openbasket && cd openbasket
uv venv --python 3.12 .venv && source .venv/bin/activate && uv pip install -e '.[docs]' && cp .env.example .env
kedro run --pipeline=full && python scripts/serve.py
```

Then open <http://localhost:8000>. Before the first run, put your own name
and email address in `SEC_USER_AGENT` in `.env` (see below).

`full` downloads everything, looks through every fund it finds and writes
the static JSON API to `build/site/api`; `serve.py` puts it together with the
landing page and the docs in `build/pages-local` and serves it on
<http://localhost:8000> until you press Ctrl+C. The same API the public site
has works against your copy:

```bash
curl -s http://localhost:8000/api/v1/meta.json
curl -s http://localhost:8000/api/v1/index.json
curl -s http://localhost:8000/api/v1/funds/IE00B5BMR087.json
```

A whole run takes hours, because every issuer file is fetched politely, one
request a second per host. To see it working first:

### Quick sample

```bash
kedro run --pipeline=full --params=universe.sample=3 && python scripts/serve.py
```

`universe.sample=3` looks through 3 funds from each fund list instead of
all of them. The downloads (about 450 MB of N-PORT, plus the fund lists)
still happen once; later runs reuse them.

One fund, without the site:

```bash
kedro run --pipeline=lookthrough --params=isin=IE00B5BMR087
```

writes `data/08_reporting/lookthrough.csv`: one row per constituent ISIN,
and one row of unresolved weight.

## `.env`

`SEC_USER_AGENT`
: Sent with every request to the SEC, which asks automated clients to name
  themselves and give a contact email, e.g. `Jane Doe jane@example.org`.
  Required: without it the N-PORT and fund-ticker downloads stop.

`OPENFIGI_API_KEY`
: Optional, free from <https://www.openfigi.com/api>. OpenFIGI gives names
  and security types for constituents and maps tickers to ISINs. Without a
  key it still works, at OpenFIGI's lower anonymous rate limit, so large
  runs take longer.

`.env` is gitignored; nothing in it is ever written to the output.

## Optional sources

Some sources' terms forbid automated access even for private use, so their
adapters are off unless you switch them on. To see every source, whether
the public site may republish it, and whether your runs use it:

```bash
python scripts/list_sources.py
```

To choose them, create `conf/local/parameters.yml` (gitignored):

```yaml
issuers:
  # Replaces the default list: name every adapter you want, the ones that
  # are on by default included.
  enabled: [cnmv_spain, cvm_brazil]
  # Or keep the default list and drop some:
  # disabled: [cvm_brazil]
```

Switching on an adapter whose terms forbid automated access is your
decision; check the source's terms first.

## Documentation

`python scripts/serve.py` builds and serves the docs at
<http://localhost:8000/docs/>; they are also on the public site. Start with
"Running it yourself" (`docs/source/running-locally.md`): what you get
locally that the public site does not show, how long a run takes and how
much disk it needs, and whose terms apply. The docs also cover the output
columns, each source's coverage and gaps, the data API and the plan.

TickerInside data is CC BY 4.0: rows from it carry the required credit in
`source_ref`.
