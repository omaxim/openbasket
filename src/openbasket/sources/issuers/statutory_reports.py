"""Issuer families covered through their statutory annual and semi-annual reports.

These issuers block automated access to their own websites (Akamai, AWS WAF,
Cloudflare or Fastly challenges), so their current holdings files cannot be
read. Their funds' statutory reports, which must carry the full schedule of
investments, are published by ISIN on hosts without a gate; see
:mod:`openbasket.sources.fund_reports`.

Every row from these adapters is labelled with what it is: the report kind
("annual report" or "semi-annual report") and the date of the positions,
which is usually two to eight months old.

The fund list for each family is the SIX Swiss Exchange fund and ETF list
(``IssuerNameFull``), kept to the share classes for which a report route
exists (in the AVL index, or a Swiss ETF for extraETF's links). Other share
classes can still be looked up by ISIN with
:func:`openbasket.sources.fund_reports.lookup`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from .. import fund_reports as fr
from . import FetchContext, IssuerAdapter, IssuerHoldings, register

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Family:
    name: str
    title: str
    homepage: str
    issuer_pattern: str          # regex on SIX IssuerNameFull / FundLongName
    brands: tuple[str, ...]      # how sub-fund titles start in the report
    locators: tuple[str, ...] = ("avl", "extraetf")
    regions: tuple[str, ...] = ("IE", "LU", "CH")


FAMILIES = [
    Family("ubs_reports", "UBS Asset Management (statutory reports)", "https://www.ubs.com/global/en/assetmanagement.html",
           r"\bUBS\b", ("UBS",), regions=("IE", "LU", "CH")),
    Family("invesco_reports", "Invesco EMEA (statutory reports)", "https://www.invesco.com/uk/en/financial-products/etfs.html",
           r"\bInvesco\b", ("Invesco",), regions=("IE", "LU")),
    Family("pictet_reports", "Pictet Asset Management (statutory reports)", "https://am.pictet.com",
           r"\bPictet\b", ("Pictet",), regions=("LU", "IE", "CH")),
    Family("wisdomtree_reports", "WisdomTree Europe (statutory reports)", "https://www.wisdomtree.eu",
           r"\bWisdomTree\b", ("WisdomTree",), locators=("wisdomtree", "avl", "extraetf"), regions=("IE",)),
    Family("fidelity_reports", "Fidelity International (statutory reports)", "https://www.fidelityinternational.com",
           r"\bFidelity\b|\bFIL Investment", ("Fidelity",), regions=("IE", "LU")),
    Family("franklin_reports", "Franklin Templeton (statutory reports)", "https://www.franklintempleton.lu",
           r"\bFranklin\b|\bTempleton\b|\bLegg Mason\b",
           ("Franklin", "Templeton", "FTGF", "Legg Mason", "ClearBridge", "Martin Currie", "Western Asset", "Brandywine", "Putnam"),
           regions=("IE", "LU")),
    Family("lombardodier_reports", "Lombard Odier Investment Managers (statutory reports)", "https://am.lombardodier.com",
           r"\bLombard Odier\b|\bLO Funds\b", ("LO Funds", "Lombard Odier"), regions=("LU", "CH")),
]

CREDIT = (
    "Holdings from the fund's statutory {kind_hint} published by {issuer}; report located by ISIN "
    "on AVL Investmentfonds' public document library{extra}. Positions are as of the report date, not current."
)
LICENCE = (
    "Statutory UCITS/KAG reports are public documents the fund must publish. They are read from public, "
    "ungated copies (AVL Investmentfonds; {extra}) whose terms were not reviewed for automated use; one report "
    "per umbrella and period is downloaded and only its text is kept."
)


def _avl_isins(cache_dir) -> set[str] | None:
    try:
        idx = fr.avl_index(cache_dir)
    except Exception as exc:  # the list still works for Swiss ETFs without it
        logger.warning("AVL index unavailable: %s", exc)
        return None
    return set(idx.loc[idx["type"].isin(fr.AVL_KINDS), "isin"])


def _make(f: Family) -> IssuerAdapter:
    def products() -> pd.DataFrame:
        six = fr.six_funds()
        avl = _avl_isins(None)

        def other_route(row) -> bool:
            if "wisdomtree" in f.locators and f.name == "wisdomtree_reports":
                return True
            return str(row.ISIN).startswith("CH") and (row.ProductLineDesc or "").startswith("Exchange Traded")

        return fr.products_from_six(six, f.name, f.issuer_pattern, avl, other_route)

    def holdings(product: dict, ctx: FetchContext | None = None) -> IssuerHoldings:
        cache_dir = (ctx.cache_dir if ctx else None) or fr.DEFAULT_CACHE_DIR
        return fr.lookup(
            product["isin"],
            product.get("fund_name"),
            brands=list(f.brands),
            locators=f.locators,
            cache_dir=cache_dir,
        )

    issuer = f.title.split(" (")[0]
    extra = " and WisdomTree's own document service" if f.name == "wisdomtree_reports" else " (Swiss ETFs: extraETF's document links)"
    return IssuerAdapter(
        name=f.name,
        title=f.title,
        homepage=f.homepage,
        credit=CREDIT.format(kind_hint="annual or semi-annual report", issuer=issuer, extra=extra),
        licence=LICENCE.format(extra="WisdomTree's document service" if f.name == "wisdomtree_reports" else "extraETF"),
        regions=f.regions,
        products=products,
        holdings=holdings,
        frequency="semi-annual",
    )


for _f in FAMILIES:
    register(_make(_f))
