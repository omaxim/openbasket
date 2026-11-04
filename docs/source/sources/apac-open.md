# Asia-Pacific and rest of world: open datasets checked

This page records a search for open regulator or exchange datasets that give
fund-level portfolios outside Europe and the Americas, so the same ground is
not covered twice. **Nothing was built:** no dataset found here meets all
three conditions at once:

1. security-level holdings for each fund;
2. a licence or legal notice that clearly allows reuse and republication
   (public domain, CC0, CC BY, ODbL, a government open-data licence, or a
   regulator notice allowing reproduction);
3. access with no log-in, registration, API key, consent click or bot wall.

Issuer websites were out of scope for this pass: earlier reviews found that
most issuers' terms forbid systematic collection and republication.

Status as tested on 2026-11-03, from a residential connection in Europe, at
about one request per second per host. Countries where the regulator's
verdict is still open are marked *gated* or *unreachable*; those are the
places to look next.

| Country | Source checked | Fund-level holdings? | Licence / access | Verdict |
|---|---|---|---|---|
| India | AMFI portfolio disclosure, `data.gov.in` | AMFI links each AMC's monthly portfolio files | `www.amfiindia.com` timed out and refused connections; its pages carry "All Rights Reserved". No holdings dataset on `data.gov.in` | Unreachable; not open |
| Korea | KRX Data Marketplace, KRX Open API, `data.go.kr` | KRX shows daily ETF portfolio deposit files (PDF) on its own pages | KRX Open API and every `data.go.kr` Open API need a registered key; `data.go.kr` carries ETF prices, not portfolios | Gated |
| Korea | `data.go.kr` 15068094, government-bond ETF asset composition | Per ETF, government-bond ETFs only | Listed as unrestricted use, but it is a link to `ktb.moef.go.kr` ("All Right Reserved"), a one-off 2025-08-05 snapshot, with no file attached | Not built: stale, narrow, source not open |
| Taiwan | `data.gov.tw` (SITCA datasets), TWSE OpenAPI | Monthly top 10 only (SITCA site); no portfolio dataset on the open data platform | Open Government Data License 1.0 covers fund lists only; TWSE's terms forbid automated download outside the open data platform | No open holdings |
| Thailand | SEC Open Data API v2 (`api.sec.or.th`) | Quarterly fund portfolios (`/v2/fund/outstanding/portfolio`) | 401 "missing subscription key"; `www.sec.or.th` and `secopendata.sec.or.th` answered a WAF block page | Gated |
| Hong Kong | SFC, HKEX, `data.gov.hk` | No fund holdings published centrally | | None found |
| Singapore | MAS, `data.gov.sg` | No fund holdings published centrally | | None found |
| Australia | ASIC, APRA, `data.gov.au` | Portfolio holdings disclosure is on each issuer's site | No central dataset | None found (issuer route) |
| New Zealand | Disclose Register (Companies Office) | Every managed fund files full portfolio holdings | Companies Office CC BY 4.0 excludes "the contents of the registers"; robots.txt disallows `/register-assets/*` and `*/downloadpdf` | Not clearly open |
| Israel | ISA Magna, TASE Maya, `data.gov.il` | `data.gov.il` has provident and pension fund asset lists (`nechasimg`, `nechasimp`) | Latest file is 2017-09-30 and has no licence id; these funds have no ISIN. No mutual fund holdings dataset found | Not built: stale, no ISINs |
| Turkey | KAP monthly portfolio reports | Every investment fund's monthly portfolio | No reuse terms found; `robots.txt` answered HTTP 666 (bot block) | Not clearly open |
| South Africa | ASISA, FSCA | Aggregate statistics only; holdings are top 10 in issuer fact sheets | | None found |

Japan (EDINET, gated by a free registered key) is described in
[Regulator filings](regulator-filings.md).

## Notes by country

India
: SEBI requires each AMC to publish every scheme's full portfolio monthly on
  its own site and AMFI's. AMFI's portfolio disclosure page links each AMC's
  files; it is not one dataset. The files are Excel workbooks in each AMC's
  own layout. `www.amfiindia.com` timed out on port 443 from here (as in the
  previous pass), so its terms could not be read in full; the pages found
  through search carry "© AMFI. All Rights Reserved". `portal.amfiindia.com/spages/NAVAll.txt`
  (scheme list with ISINs) still answers. `data.gov.in` has AMFI aggregate
  monthly figures only.
  Bank-run funds this would cover: HDFC, ICICI Prudential, SBI, Kotak, Axis,
  Bank of Baroda BNP Paribas, Canara Robeco, Union.

Korea
: Every `data.go.kr` Open API needs a `serviceKey` issued after signing up,
  and the financial-services datasets there (금융위원회_증권상품시세정보) are
  prices, not portfolios. KRX's daily ETF portfolio deposit file (PDF) is
  shown on the Data Marketplace pages (`data.krx.co.kr`), which serve it
  through an internal endpoint for the site's own screens; it was not found
  among KRX Open API services, which also need a registered key. The only `data.go.kr` portfolio
  file is the Ministry of Economy and Finance's government-bond ETF asset
  composition (`15068094`): a link-type entry pointing at
  `https://ktb.moef.go.kr/etfItemAssetsComposition.do`, standard date
  2025-08-05, whose site footer says "All Right Reserved".
  Bank-group managers this would cover: KB, Shinhan, Hana, Woori, NH-Amundi.

Taiwan
: The open data platform carries SITCA's fund lists under the Open
  Government Data License 1.0: fund ISINs, short-term trading limits and
  monthly report links (`data.gov.tw` 21404), basic data (43476), fees
  (46443) and distributions (160547). None has holdings. Monthly
  disclosures are top-10 holdings on SITCA's own site. TWSE's OpenAPI (143
  endpoints, under the same licence) has fund basic data (`t187ap47_L`) but
  no ETF portfolios. TWSE's terms of use forbid downloading its site's data
  by scripts or crawlers unless TWSE agrees, except data it has given to the
  open data platform.

Thailand
: `https://api.sec.or.th/v2/fund/outstanding/portfolio` answers 401 "Access
  denied due to missing subscription key". The key comes from signing up on
  the SEC developer portal, which this project does not do on anyone's
  behalf. The SEC's main site and open data portal sent a WAF block page to
  a plain HTTP client. With a key, this is the most promising source in the
  region: a third-party client lists the endpoint as the quarterly portfolio
  of each Thai mutual fund (not verified here without a key), which would cover the
  bank-owned managers (Kasikorn K-Asset, SCB Asset, Krungthai, Bangkok Bank's
  BBLAM, Krungsri). The licence attached to API data still needs reading
  after sign-up.

New Zealand
: Managed funds (KiwiSaver included, so ANZ, ASB, Westpac, BNZ and Kiwibank
  funds) file full portfolio holdings on the Disclose Register. The Companies
  Office copyright page licenses its material under CC BY 4.0 "excluding the
  public notices and contents of the registers", so the filings themselves
  are not covered, and `robots.txt` disallows the register's document paths.
  Not built until the Companies Office states reuse terms for register
  contents.

Israel
: No open mutual fund holdings dataset was found. `data.gov.il` holds the
  Capital Market Authority's security-level asset lists of provident funds,
  pension funds and insurers' participating portfolios, but the newest file
  is for 2017-09-30, the dataset has no licence id, and these funds have no
  ISIN, so an ISIN lookup could not reach them.

Turkey
: Fund managers, mostly bank subsidiaries (İş, Garanti BBVA, Yapı Kredi,
  Ziraat, Ak Portföy), file a monthly portfolio report on KAP. No reuse terms
  were found on `kap.org.tr`, and its `robots.txt` request got a bot-block
  page (HTTP 666).

## What would unlock coverage

- **Thailand:** register for an SEC Open Data API key, read the API terms,
  and build `/v2/fund/outstanding/portfolio`; how a fund maps to its ISIN
  is still to be checked.
- **Korea:** a decision on KRX's Data Marketplace terms for the ETF PDF
  screens, or a KRX Open API key if KRX adds the PDF to it.
- **India:** read AMFI's portfolio disclosure page and terms from a network
  that can reach `www.amfiindia.com`; the portfolios are each AMC's own
  files, so this is closer to an issuer route.
- **New Zealand:** written confirmation from the Companies Office that
  register filings may be reused.
