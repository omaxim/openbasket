"""Swiss banks and the fund managers that run their funds.

A curated catalogue, so the Swiss registry (:mod:`openbasket.sources.swiss_registry`)
can say which bank a fund belongs to and the docs can report coverage per
bank. Nothing here is fetched: names, legal entity identifiers (LEIs) of the
fund managers (checked against the GLEIF API on 3 November 2026) and the
route each bank's holdings can take, with the reason when there is none.

Attribution rule (:func:`attribute`): a bank's brand in the fund or umbrella
name wins, because fund management companies run white-label funds for
other banks (UBS Fund Management (Switzerland) AG runs the Migros Bank,
Bank Cler, BKB and Schwyzer KB funds; Swisscanto Fondsleitung AG runs funds
for several cantonal banks; 1741 Fund Solutions AG for St.Galler and
Graubündner KB). Only when no brand matches does the manager decide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Our own catalogue, compiled from public registers: may be republished.
REDISTRIBUTION = "public"


@dataclass(frozen=True)
class Bank:
    key: str
    name: str
    group: str                                  # big, cantonal, private, retail
    brands: tuple[str, ...]                     # regexes on fund or umbrella names
    managers: tuple[str, ...] = ()              # regexes on manager / Fondsleitung names
    manager_leis: tuple[str, ...] = ()          # GLEIF LEIs of its fund managers
    homepage: str = ""
    legal_url: str = ""                         # page the terms / contact came from
    contact: str = ""                           # where to ask for permission
    route: str = "none"                         # see ROUTES
    route_note: str = ""
    extra: dict = field(default_factory=dict)


#: Holdings routes, best first.
ROUTES = {
    "report": "statutory annual / semi-annual report, schedule of investments parsed from the PDF",
    "standin": "physical index ETF: holdings of a physical peer ETF on the same index (labelled stand-in)",
    "issuer_file": "the manager's own holdings file",
    "none": "no legitimate route found",
}

BANKS: tuple[Bank, ...] = (
    Bank(
        "ubs", "UBS (incl. former Credit Suisse funds)", "big",
        (r"\bUBS\b", r"\bCredit Suisse\b", r"\bCSIF\b"),
        (r"^UBS\b", r"Credit Suisse (Funds|Fund Management|Asset Management)"),
        ("5493003QNP1E68GGMZ05", "549300AE48NQE4QVEH24", "549300SGDHJDHGZYMB20", "254900SFTV4L52IHCW98",
         "549300UY5YPGQ600JV78", "549300SYISD6IHXI0717"),
        "https://www.ubs.com/global/en/asset-management.html",
        "https://www.ubs.com/global/en/legal.html",
        "UBS Asset Management, ETF desk Zurich; use the contact form linked from https://www.ubs.com/global/en/legal.html "
        "(the footer says any use other than personal viewing needs UBS's prior written permission)",
        "standin",
        "ubs.com fund pages sit behind a domicile/investor-role gate and robots.txt disallows /bin/ and /2/ "
        "(data services and documents). Physical index ETFs are covered by a peer ETF stand-in.",
    ),
    Bank(
        "zkb", "Zürcher Kantonalbank / Swisscanto", "cantonal",
        (r"\bSwisscanto\b", r"\bZKB\b", r"Z[üu]rcher Kantonalbank"),
        (r"^Swisscanto",),
        ("TKPE0FGSGCIV1TZ04B42", "5493007MXNV1316JL462"),
        "https://products.swisscanto.com/",
        "https://www.swisscanto.com/ch/en/private/legal/terms-of-use.html",
        "Swisscanto Fondsleitung AG, Bahnhofstrasse 9, CH-8001 Zurich (address given in the terms of use)",
        "none",
        "Terms of use forbid systematic access to build a database without written permission; "
        "robots.txt disallows the report PDFs. See the src/swisscanto notes.",
    ),
    Bank(
        "bcv", "Banque Cantonale Vaudoise (GERIFONDS)", "cantonal",
        (r"\bBCV\b", r"Banque Cantonale Vaudoise",),
        (r"^GERIFONDS",),
        ("549300C3ZJC3NQM6EB65", "549300ZKKX0Q7RRJRI81"),
        "https://www.gerifonds.ch/",
        "https://www.gerifonds.ch/fr/page/contact",
        "GERIFONDS SA, Rue du Maupas 2, Case postale, 1001 Lausanne, +41 21 321 32 00",
        "none",
        "gerifonds.ch opens a modal that asks for country, investor profile and acceptance of the terms "
        "(home_modal_form); not passed.",
    ),
    Bank(
        "lukb", "Luzerner Kantonalbank (LUKB Expert)", "cantonal",
        (r"\bLUKB\b", r"Luzerner Kantonalbank"),
        (r"^LUKB Expert",),
        ("5067005S078K436U6F95",),
        "https://www.lukb.ch/private/anlegen/fonds",
        "https://www.lukb.ch/ueber-uns/rechtliches",
        "Luzerner Kantonalbank AG, legal notice https://www.lukb.ch/ueber-uns/rechtliches",
        "none",
        "Fund pages ask to confirm Swiss residence and accept the legal notice before documents are shown.",
    ),
    Bank("bkb", "Basler Kantonalbank", "cantonal", (r"\bBKB\b", r"Basler Kantonalbank"),
         homepage="https://www.bkb.ch/", route_note="Funds run by UBS Fund Management (Switzerland) AG; documents on swissfunddata/fundinfo."),
    Bank("cler", "Bank Cler", "retail", (r"\bCler\b",), homepage="https://www.cler.ch/",
         route_note="Funds run by UBS Fund Management (Switzerland) AG; no documents on cler.ch."),
    Bank("bekb", "Berner Kantonalbank", "cantonal", (r"\bBEKB\b", r"Berner Kantonalbank"),
         homepage="https://www.bekb.ch/", route_note="Fund pages redirect to a domicile inquiry (domicileInquiryNeeded); not passed."),
    Bank("sgkb", "St.Galler Kantonalbank", "cantonal", (r"\bSGKB\b", r"St\.? ?Galler Kantonalbank"),
         homepage="https://www.sgkb.ch/", contact="1741 Fund Solutions AG (Fondsleitung), info@1741group.com",
         route_note="Funds run by 1741 Fund Solutions AG, which publishes them on fundinfo.com and swissfunddata.ch only."),
    Bank("zugerkb", "Zuger Kantonalbank", "cantonal", (r"Zuger Kantonalbank", r"\bZugerKB\b"),
         homepage="https://www.zugerkb.ch/", route_note="Fund pages ask to confirm Swiss residence; no documents in the page."),
    Bank("gkb", "Graubündner Kantonalbank", "cantonal", (r"\bGKB\b", r"Graub[üu]ndner Kantonalbank"),
         homepage="https://www.gkb.ch/", contact="1741 Fund Solutions AG (Fondsleitung), info@1741group.com",
         route_note="Funds run by 1741 Fund Solutions AG and Swisscanto; no documents on gkb.ch."),
    Bank("tkb", "Thurgauer Kantonalbank", "cantonal", (r"\bTKB\b", r"Thurgauer Kantonalbank"),
         homepage="https://www.tkb.ch/", route_note="Funds run by Swisscanto Fondsleitung AG; no documents on tkb.ch."),
    Bank("akb", "Aargauische Kantonalbank", "cantonal", (r"\bAKB\b", r"Aargauische Kantonalbank"),
         homepage="https://www.akb.ch/", route_note="Funds run by Swisscanto Fondsleitung AG; robots.txt disallows /*.pdf/*."),
    Bank("blkb", "Basellandschaftliche Kantonalbank", "cantonal", (r"\bBLKB\b", r"Basellandschaftliche Kantonalbank"),
         (r"^BLKB Fund Management",), homepage="https://www.blkb.ch/",
         route_note="Funds run by Swisscanto, UBS FM and BLKB Fund Management AG; no documents found."),
    Bank("szkb", "Schwyzer Kantonalbank", "cantonal", (r"\bSZKB\b", r"Schwyzer Kantonalbank"), homepage="https://www.szkb.ch/",
         route_note="Funds run by UBS Fund Management (Switzerland) AG."),
    Bank("other_kb", "Other cantonal banks (BCGE, BCF, BCVs, BCN, BCJ, GLKB, BancaStato, ...)", "cantonal",
         (r"\bBCGE\b", r"\bBCF\b", r"\bBCVs\b", r"\bBCN\b", r"\bBCJ\b", r"\bGLKB\b", r"BancaStato", r"\bNKB\b", r"\bOKB\b", r"\bSHKB\b",
          r"\bAppKB\b", r"Kantonalbank"),
         route_note="White-label funds of Swisscanto, GERIFONDS, LLB Swiss and others."),
    Bank(
        "raiffeisen", "Raiffeisen", "retail", (r"\bRaiffeisen\b",),
        homepage="https://www.raiffeisen.ch/rch/de/privatkunden/anlegen/anlagefonds.html",
        legal_url="https://www.raiffeisen.ch/rch/de/privatkunden/anlegen/downloads/anlagefonds/aktienfonds.html",
        contact="Raiffeisen Schweiz, direct@raiffeisen.ch",
        route_note="The fund download pages redirect to /rch/de/disclaimer.html (server-side gate); robots.txt disallows dam PDFs outside /rch/.",
    ),
    Bank("postfinance", "PostFinance", "retail", (r"\bPostFinance\b",), homepage="https://www.postfinance.ch/",
         route_note="PostFinance funds are run by third parties (UBS FM, Xtrackers, Swisscanto); no PostFinance document host found."),
    Bank(
        "pictet", "Pictet", "private", (r"\bPictet\b",), (r"^Pictet",),
        ("549300Z3V8FWSENAH224", "222100XYKRC53LF88Y28"),
        "https://am.pictet/", "https://am.pictet.com/ch/en/intermediaries",
        "Pictet Asset Management SA, Route des Acacias 60, 1211 Geneva 73 (legal pages of am.pictet)",
        "none",
        "am.pictet asks for an investor profile; robots.txt disallows /*.pdf$ except /*/discoverable/*.pdf$; "
        "an AWS WAF answered earlier surveys with 403.",
    ),
    Bank(
        "lombard_odier", "Lombard Odier", "private", (r"\bLombard Odier\b", r"\bLO Funds\b", r"\bLO Selection\b"), (r"^Lombard Odier",),
        ("O1L57OXARD4ZXEUQ2H57", "SB6NR8324Y8KG313SQ11", "549300MMMH5HOM8WIZ97"),
        "https://am.lombardodier.com/", "https://am.lombardodier.com/",
        "Lombard Odier Asset Management (Switzerland) SA, Geneva", "none",
        "robots.txt disallows */investment-funds/fund/ (share-class pages) and /files/ (documents).",
    ),
    Bank("julius_baer", "Julius Bär", "private", (r"\bJulius B(ae|ä)r\b", r"\bJB\b"), homepage="https://www.juliusbaer.com/",
         route_note="juliusbaer.com answers 403 to the project's User-Agent; Julius Bär funds are run by third-party managers (Carne, FundPartner)."),
    Bank(
        "vontobel", "Vontobel", "private", (r"\bVontobel\b",), (r"^Vontobel",),
        ("549300JVH1QCKVJDQO11", "529900LO1T9ADP03SQ41"),
        "https://am.vontobel.com/", "https://www.vontobel.com/en-ch/legal-notice/imprint/",
        "Vontobel Holding AG, Gotthardstrasse 43, CH-8002 Zurich, vontobelgroup@vontobel.com (imprint)",
        "report",
        "Fund pages and report PDFs are served without a gate and robots.txt allows them, but the page shows "
        "an unsubmitted overlay asking for country and investor type: adapter off by default.",
    ),
    Bank(
        "ubp", "UBP (Union Bancaire Privée)", "private", (r"\bUBP\b", r"\bUBAM\b", r"Union Bancaire Priv"), (r"^UBP\b",),
        ("5493007WR9BT7NBDHF50",),
        "https://www.ubp.com/en/asset-management/funds", "https://www.ubp.com/en/legal-aspects/priips",
        "Union Bancaire Privée, UBP SA, Rue du Rhône 96-98, 1211 Geneva", "none",
        "Fund pages redirect to /en/asset-management/disclaimer (investor profile and acceptance checkbox).",
    ),
    Bank(
        "jss", "J. Safra Sarasin", "private", (r"\bJ\.? ?Safra Sarasin\b", r"\bJSS\b", r"\bSarasin\b"), (r"J\. Safra Sarasin",),
        ("549300NWOXE3IFS00426", "549300QVJTYK6E1EIO26"),
        "https://jsafrasarasin.com/en/products/funds-list.html", "https://jsafrasarasin.com/en/home/legal/terms-of-use.html",
        "Bank J. Safra Sarasin AG, terms of use https://jsafrasarasin.com/en/home/legal/terms-of-use.html", "none",
        "Fund pages (446 ISINs in the sitemap) are rendered client-side from an encrypted configuration; no document links in the HTML.",
    ),
    Bank("efg", "EFG", "private", (r"\bEFG\b", r"\bNew Capital\b"), homepage="https://www.newcapital.com/",
         route_note="newcapital.com answers 403; no GLEIF fund-manager record for an EFG management company."),
    Bank(
        "migros", "Migros Bank", "retail", (r"\bMigros\b", r"\bMI BANK\b"),
        homepage="https://www.migrosbank.ch/de/privatpersonen/anlegen/anlageinstrumente/anlagefonds.html",
        legal_url="https://www.migrosbank.ch/de/ueber-uns/rechtliche-informationen.html",
        contact="Migros Bank AG, legal information https://www.migrosbank.ch/de/ueber-uns/rechtliche-informationen.html",
        route="report",
        route_note="The fund page links the (CH) and (Lux) umbrella reports directly; robots.txt allows them, but the page "
                   "shows an unsubmitted Swiss-residence overlay: adapter off by default.",
    ),
    Bank("valiant", "Valiant", "retail", (r"\bValiant\b",), homepage="https://www.valiant.ch/",
         route_note="Funds run by Lombard Odier Asset Management (Switzerland) SA; robots.txt disallows /documents/."),
)

BANK_BY_KEY = {b.key: b for b in BANKS}
_BRAND = [(b.key, re.compile("|".join(b.brands), re.I)) for b in BANKS if b.brands]
_MANAGER = [(b.key, re.compile("|".join(b.managers), re.I)) for b in BANKS if b.managers]
_LEI = {lei: b.key for b in BANKS for lei in b.manager_leis}


def bank_for_lei(lei: str | None) -> str | None:
    return _LEI.get((lei or "").upper())


def attribute(name: str | None = None, umbrella: str | None = None, manager: str | None = None,
              manager_lei: str | None = None) -> str | None:
    """The bank a fund belongs to: brand in its name or umbrella first, then its manager."""
    text = " | ".join(s for s in (name, umbrella) if isinstance(s, str))
    if text:
        # Leftmost brand wins ("UBS (CH) Fund - BKB ..." is a BKB fund only when BKB comes first).
        hits = [(m.start(), key) for key, rx in _BRAND if (m := rx.search(text))]
        if hits:
            hits.sort()
            # A specific bank beats the generic "Kantonalbank" catch-all at the same position.
            first = hits[0][0]
            same = [k for pos, k in hits if pos == first]
            return next((k for k in same if k != "other_kb"), same[0])
    by_lei = bank_for_lei(manager_lei)
    if by_lei:
        return by_lei
    if isinstance(manager, str):
        for key, rx in _MANAGER:
            if rx.search(manager.strip()):
                return key
    return None
