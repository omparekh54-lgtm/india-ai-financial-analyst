from __future__ import annotations

from math import isfinite


def safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    value = numerator / denominator
    return value if isfinite(value) else None


def margin(profit: float | None, revenue: float | None) -> float | None:
    return safe_divide(profit, revenue)


def growth_rate(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in {None, 0}:
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
    if all(value is not None for value in (receivable_days, inventory_days, payable_days)):
        cash_conversion_cycle = receivable_days + inventory_days - payable_days

    return {
        "receivable_days": receivable_days,
        "inventory_days": inventory_days,
        "payable_days": payable_days,
        "cash_conversion_cycle": cash_conversion_cycle,
    }
