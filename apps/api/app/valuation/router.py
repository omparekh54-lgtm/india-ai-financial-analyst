from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ValuationMethod(StrEnum):
    DCF = "dcf"
    PE = "pe"
    PB = "pb"
    RESIDUAL_INCOME = "residual_income"
    PRICE_TO_EMBEDDED_VALUE = "price_to_embedded_value"
    EV_EBITDA = "ev_ebitda"
    EV_SALES = "ev_sales"
    NAV = "nav"
    SOTP = "sotp"
    DIVIDEND_YIELD = "dividend_yield"


class ValuationRoute(BaseModel):
    sector_family: str
    primary: list[ValuationMethod]
    secondary: list[ValuationMethod]
    reason: str


def choose_valuation_methods(sector: str | None, industry: str | None = None) -> ValuationRoute:
    text = f"{sector or ''} {industry or ''}".lower()

    if any(term in text for term in ("bank", "banking")):
        return ValuationRoute(
            sector_family="bank",
            primary=[ValuationMethod.PB, ValuationMethod.RESIDUAL_INCOME],
            secondary=[ValuationMethod.PE],
            reason="Banks are balance-sheet businesses; P/B and ROE-linked methods are preferred.",
        )
    if any(term in text for term in ("nbfc", "non banking financial", "housing finance")):
        return ValuationRoute(
            sector_family="nbfc",
            primary=[ValuationMethod.PB, ValuationMethod.RESIDUAL_INCOME],
            secondary=[ValuationMethod.PE],
            reason="NBFC valuation is primarily driven by book value, ROA/ROE and credit quality.",
        )
    if "insurance" in text:
        return ValuationRoute(
            sector_family="insurance",
            primary=[ValuationMethod.PRICE_TO_EMBEDDED_VALUE],
            secondary=[ValuationMethod.PE],
            reason="Life insurers are commonly assessed using embedded value and VNB economics.",
        )
    if any(term in text for term in ("holding company", "conglomerate")):
        return ValuationRoute(
            sector_family="conglomerate",
            primary=[ValuationMethod.SOTP, ValuationMethod.NAV],
            secondary=[ValuationMethod.DCF],
            reason="Multi-business groups require sum-of-the-parts or NAV-based valuation.",
        )
    if any(
        term in text
        for term in (
            "reit",
            "real estate investment trust",
            "invit",
            "infrastructure investment trust",
        )
    ):
        return ValuationRoute(
            sector_family="reit_invit",
            primary=[ValuationMethod.NAV, ValuationMethod.DIVIDEND_YIELD],
            secondary=[ValuationMethod.DCF],
            reason="REIT/InvIT economics are best framed around NAV and distributable cash yield.",
        )
    if any(term in text for term in ("metal", "mining", "steel", "aluminium", "aluminum")):
        return ValuationRoute(
            sector_family="cyclical_resources",
            primary=[ValuationMethod.EV_EBITDA],
            secondary=[ValuationMethod.DCF, ValuationMethod.PE],
            reason="Cyclical resource businesses require normalized earnings and cycle-aware multiples.",
        )
    if any(
        term in text
        for term in (
            "loss making",
            "loss-making",
            "pre-profit",
            "pre profit",
            "internet platform",
            "early stage platform",
        )
    ):
        return ValuationRoute(
            sector_family="loss_making_growth",
            primary=[ValuationMethod.EV_SALES],
            secondary=[ValuationMethod.DCF],
            reason="Pre-profit growth businesses require sales multiples plus explicit scenario economics.",
        )

    return ValuationRoute(
        sector_family="general_corporate",
        primary=[ValuationMethod.DCF, ValuationMethod.PE],
        secondary=[ValuationMethod.EV_EBITDA],
        reason="General operating companies use cash-flow valuation cross-checked with trading multiples.",
    )
