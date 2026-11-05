# Data licence

This page says under which terms the published data may be reused: the
public website and API (`api/v1/...`) and the release files on GitHub
(`openbasket-lookthrough.parquet` and the others). It covers the data, not
the code.

The public build publishes only sources whose licence allows republication
(redistribution class `public`, see [](publishing.md)). Every row names its
source, links to it, carries the source's credit line and its as-of date, and
`api/v1/meta.json` lists every source that contributed with its licence. Data
from `local_only` sources (issuer websites) is never in the published data;
people who run openbasket themselves read it on their own machine, under that
issuer's terms.

## Data from CVM (Brazil): Open Database License

Where the published data includes data from the Comissão de Valores
Mobiliários (CVM), the published database is offered under the
[Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/)
(ODbL). This is a condition of CVM's own licence: its open data, including the
fund portfolio filings (CDA) at <https://dados.cvm.gov.br/dataset/fi-doc-cda>,
is published under the ODbL, and a database derived from it must be offered
under the same licence.

Credit: **Comissão de Valores Mobiliários (CVM), Dados Abertos: Fundos de
Investimento, Composição e Diversificação das Aplicações (CDA).** Contains
information from CVM's open data, which is made available under the Open
Database License.

What the ODbL asks of anyone who reuses the published database:

- **Attribute**: keep the credit to CVM and to openbasket, and say that the
  database is under the ODbL.
- **Share alike**: a database you derive from it and make public must also be
  offered under the ODbL.
- **Keep it open**: if you distribute it with technical restrictions, also
  offer a version without them.

What openbasket changed in CVM's data: it computes each line's weight as the
line's market value over the fund's net assets in the same filing (the CDA
reports values, not weights), gives liability lines a negative sign, adds
ISINs from B3's register and from other filings where a line has none, and
leaves out the CPF numbers and names of individuals. See
{doc}`sources/cvm-brazil`.

## Other public sources keep their own licences

The ODbL applies to the database as a whole where CVM data is in it; it does
not change the terms of the other sources' data, which stay as each source
grants them. Each row says which source it came from.

| Source | Terms | What reuse needs |
|---|---|---|
| SEC Form N-PORT and N-MFP | US federal government data, not subject to copyright in the United States; sec.gov says its information may be copied or redistributed without permission | cite the SEC as the source |
| TickerInside | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | keep TickerInside's credit and say what was changed (openbasket maps tickers to ISINs and takes names from N-PORT) |
| CNMV (Spain) | CNMV legal notice: reuse, including calculations made from the data, if it is reproduced faithfully | keep names and values as filed; say that weights are calculated; anyone selling it must tell buyers it is free on cnmv.es |
| EDINET (Japan) | EDINET terms of use: Public Data License 1.0 (PDL1.0), compatible with CC BY 4.0; scraping the site barred, API allowed | cite EDINET and PDL1.0; say that openbasket extracted and edited the data |
| CMVM (Portugal) | official regulator publication; no licence stated; reused as public sector information under Lei 26/2016 | cite CMVM; keep names and values as filed; say that weights are calculated |
| ESMA FIRDS | ESMA legal notice: reproduction authorised if the source is acknowledged | credit ESMA and say the material was drafted using data downloaded from ESMA's website, which does not endorse it |
| GLEIF | CC0 1.0 | none (credit is appreciated) |
| ECB eligible assets | ECB copyright notice: reproduction permitted if the source is acknowledged | credit the ECB |
| OpenFIGI | names and security types only, under OpenFIGI's terms | credit OpenFIGI |
| B3 (Brazil) ISIN register and instruments file | free public downloads; no terms found on the download pages | only identifiers are used (ISIN, trading code, category) |

Each source's page under {doc}`sources/index` quotes its terms and gives the
credit line used.

## openbasket's own additions

The look-through itself (which constituent sits under which fund, with what
weight, and the totals per fund) is computed by openbasket from the sources
above, and is published under the same terms as the data it is built from:
under the ODbL where CVM data is included, and with every source's credit
kept.
