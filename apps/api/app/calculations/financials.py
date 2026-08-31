from __future__ import annotations

from collections.abc import Mapping
from math import isfinite


def safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    value = numerator / denominator
    return value if isfinite(value) else None


def margin(profit: float | None, revenue: float | None) -> float | None:
    return safe_divide(profit, revenue)


def growth_rate(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous)


def cagr(ending_value: float | None, beginning_value: float | None, years: float) -> float | None:
    if (
        ending_value is None
        or beginning_value is None
        or ending_value <= 0
        or beginning_value <= 0
        or years <= 0
    ):
        return None
    return (ending_value / beginning_value) ** (1 / years) - 1


def cfo_to_pat(cfo: float | None, pat: float | None) -> float | None:
    return safe_divide(cfo, pat)


def net_debt_to_ebitda(
    total_debt: float | None,
    cash: float | None,
    ebitda: float | None,
) -> float | None:
    if total_debt is None or cash is None:
        return None
    return safe_divide(total_debt - cash, ebitda)


def interest_coverage(ebit: float | None, interest_expense: float | None) -> float | None:
    return safe_divide(ebit, interest_expense)


def roce(
    ebit: float | None,
    total_assets: float | None,
    current_liabilities: float | None,
) -> float | None:
    if total_assets is None or current_liabilities is None:
        return None
    return safe_divide(ebit, total_assets - current_liabilities)


def working_capital_days(
    receivables: float | None,
    inventory: float | None,
    payables: float | None,
    revenue: float | None,
    cogs: float | None,
    *,
    days: int = 365,
) -> dict[str, float | None]:
    receivable_days = (
        safe_divide(receivables * days, revenue)
        if receivables is not None and revenue is not None
        else None
    )
    inventory_days = (
        safe_divide(inventory * days, cogs)
        if inventory is not None and cogs is not None
        else None
    )
    payable_days = (
        safe_divide(payables * days, cogs)
        if payables is not None and cogs is not None
        else None
    )

    cash_conversion_cycle = None
    if receivable_days is not None and inventory_days is not None and payable_days is not None:
        cash_conversion_cycle = receivable_days + inventory_days - payable_days

    return {
        "receivable_days": receivable_days,
        "inventory_days": inventory_days,
        "payable_days": payable_days,
        "cash_conversion_cycle": cash_conversion_cycle,
    }


def piotroski_f_score(facts: Mapping[str, object]) -> int | None:
    """Return the nine-point Piotroski F-score only when all nine signals can be evaluated."""
    pat = _number(facts.get("pat"))
    cfo = _number(facts.get("cfo"))
    assets = _number(facts.get("total_assets"))
    previous_pat = _number(facts.get("previous_pat"))
    previous_assets = _number(facts.get("previous_total_assets"))
    debt = _number(facts.get("long_term_debt"))
    previous_debt = _number(facts.get("previous_long_term_debt"))
    current_assets = _number(facts.get("current_assets"))
    current_liabilities = _number(facts.get("current_liabilities"))
    previous_current_assets = _number(facts.get("previous_current_assets"))
    previous_current_liabilities = _number(facts.get("previous_current_liabilities"))
    shares = _number(facts.get("shares_outstanding"))
    previous_shares = _number(facts.get("previous_shares_outstanding"))
    revenue = _number(facts.get("revenue"))
    previous_revenue = _number(facts.get("previous_revenue"))
    cogs = _number(facts.get("cogs"))
    previous_cogs = _number(facts.get("previous_cogs"))

    required = (
        pat,
        cfo,
        assets,
        previous_pat,
        previous_assets,
        debt,
        previous_debt,
        current_assets,
        current_liabilities,
        previous_current_assets,
        previous_current_liabilities,
        shares,
        previous_shares,
        revenue,
        previous_revenue,
        cogs,
        previous_cogs,
    )
    if any(value is None for value in required):
        return None
    assert all(value is not None for value in required)

    current_roa = safe_divide(pat, assets)
    previous_roa = safe_divide(previous_pat, previous_assets)
    current_leverage = safe_divide(debt, assets)
    previous_leverage = safe_divide(previous_debt, previous_assets)
    current_ratio = safe_divide(current_assets, current_liabilities)
    previous_current_ratio = safe_divide(previous_current_assets, previous_current_liabilities)
    gross_margin = safe_divide(revenue - cogs, revenue)
    previous_gross_margin = safe_divide(previous_revenue - previous_cogs, previous_revenue)
    current_asset_turnover = safe_divide(revenue, assets)
    previous_asset_turnover = safe_divide(previous_revenue, previous_assets)

    ratios = (
        current_roa,
        previous_roa,
        current_leverage,
        previous_leverage,
        current_ratio,
        previous_current_ratio,
        gross_margin,
        previous_gross_margin,
        current_asset_turnover,
        previous_asset_turnover,
    )
    if any(value is None for value in ratios):
        return None
    assert all(value is not None for value in ratios)

    signals = (
        pat > 0,
        cfo > 0,
        current_roa > previous_roa,
        cfo > pat,
        current_leverage < previous_leverage,
        current_ratio > previous_current_ratio,
        shares <= previous_shares,
        gross_margin > previous_gross_margin,
        current_asset_turnover > previous_asset_turnover,
    )
    return sum(int(signal) for signal in signals)


def altman_z_score(facts: Mapping[str, object]) -> float | None:
    """Classic public-manufacturer Altman Z-score; caller must enforce sector applicability."""
    current_assets = _number(facts.get("current_assets"))
    current_liabilities = _number(facts.get("current_liabilities"))
    total_assets = _number(facts.get("total_assets"))
    retained_earnings = _number(facts.get("retained_earnings"))
    ebit = _number(facts.get("ebit"))
    market_cap = _number(facts.get("market_cap"))
    total_liabilities = _number(facts.get("total_liabilities"))
    revenue = _number(facts.get("revenue"))
    required = (
        current_assets,
        current_liabilities,
        total_assets,
        retained_earnings,
        ebit,
        market_cap,
        total_liabilities,
        revenue,
    )
    if any(value is None for value in required):
        return None
    assert all(value is not None for value in required)
    if total_assets == 0 or total_liabilities == 0:
        return None

    working_capital = current_assets - current_liabilities
    score = (
        1.2 * working_capital / total_assets
        + 1.4 * retained_earnings / total_assets
        + 3.3 * ebit / total_assets
        + 0.6 * market_cap / total_liabilities
        + revenue / total_assets
    )
    return score if isfinite(score) else None


def beneish_m_score(facts: Mapping[str, object]) -> float | None:
    """Eight-variable Beneish M-score; returns None unless every component is available."""
    revenue = _number(facts.get("revenue"))
    previous_revenue = _number(facts.get("previous_revenue"))
    receivables = _number(facts.get("receivables"))
    previous_receivables = _number(facts.get("previous_receivables"))
    cogs = _number(facts.get("cogs"))
    previous_cogs = _number(facts.get("previous_cogs"))
    current_assets = _number(facts.get("current_assets"))
    previous_current_assets = _number(facts.get("previous_current_assets"))
    ppe = _number(facts.get("ppe"))
    previous_ppe = _number(facts.get("previous_ppe"))
    total_assets = _number(facts.get("total_assets"))
    previous_total_assets = _number(facts.get("previous_total_assets"))
    depreciation = _number(facts.get("depreciation"))
    previous_depreciation = _number(facts.get("previous_depreciation"))
    sga = _number(facts.get("sga_expense"))
    previous_sga = _number(facts.get("previous_sga_expense"))
    total_debt = _number(facts.get("total_debt"))
    previous_total_debt = _number(facts.get("previous_total_debt"))
    pat = _number(facts.get("pat"))
    cfo = _number(facts.get("cfo"))

    required = (
        revenue,
        previous_revenue,
        receivables,
        previous_receivables,
        cogs,
        previous_cogs,
        current_assets,
        previous_current_assets,
        ppe,
        previous_ppe,
        total_assets,
        previous_total_assets,
        depreciation,
        previous_depreciation,
        sga,
        previous_sga,
        total_debt,
        previous_total_debt,
        pat,
        cfo,
    )
    if any(value is None for value in required):
        return None
    assert all(value is not None for value in required)

    dsri = _ratio_of_ratios(receivables, revenue, previous_receivables, previous_revenue)
    current_gross_margin = safe_divide(revenue - cogs, revenue)
    previous_gross_margin = safe_divide(previous_revenue - previous_cogs, previous_revenue)
    gmi = safe_divide(previous_gross_margin, current_gross_margin)

    current_asset_quality = 1.0 - _required_ratio(current_assets + ppe, total_assets)
    previous_asset_quality = 1.0 - _required_ratio(
        previous_current_assets + previous_ppe,
        previous_total_assets,
    )
    aqi = safe_divide(current_asset_quality, previous_asset_quality)
    sgi = safe_divide(revenue, previous_revenue)

    current_depreciation_rate = safe_divide(depreciation, depreciation + ppe)
    previous_depreciation_rate = safe_divide(
        previous_depreciation,
        previous_depreciation + previous_ppe,
    )
    depi = safe_divide(previous_depreciation_rate, current_depreciation_rate)
    sgai = _ratio_of_ratios(sga, revenue, previous_sga, previous_revenue)
    lvgi = _ratio_of_ratios(total_debt, total_assets, previous_total_debt, previous_total_assets)
    tata = safe_divide(pat - cfo, total_assets)

    components = (dsri, gmi, aqi, sgi, depi, sgai, lvgi, tata)
    if any(value is None for value in components):
        return None
    assert all(value is not None for value in components)

    score = (
        -4.84
        + 0.920 * dsri
        + 0.528 * gmi
        + 0.404 * aqi
        + 0.892 * sgi
        + 0.115 * depi
        - 0.172 * sgai
        + 4.679 * tata
        - 0.327 * lvgi
    )
    return score if isfinite(score) else None


def _ratio_of_ratios(
    current_numerator: float,
    current_denominator: float,
    previous_numerator: float,
    previous_denominator: float,
) -> float | None:
    current = safe_divide(current_numerator, current_denominator)
    previous = safe_divide(previous_numerator, previous_denominator)
    return safe_divide(current, previous)


def _required_ratio(numerator: float, denominator: float) -> float:
    value = safe_divide(numerator, denominator)
    if value is None:
        raise ValueError("ratio denominator cannot be zero")
    return value


def _number(value: object) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
