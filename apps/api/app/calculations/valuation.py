from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DcfAssumptions:
    base_fcf: float
    growth_rates: list[float]
    wacc: float
    terminal_growth: float
    net_debt: float
    shares_outstanding: float


@dataclass(frozen=True)
class DcfResult:
    enterprise_value: float
    equity_value: float
    value_per_share: float
    projected_fcf: list[float]
    terminal_value: float
    present_value_terminal: float


def discounted_cash_flow(assumptions: DcfAssumptions) -> DcfResult:
    if assumptions.base_fcf <= 0:
        raise ValueError("base_fcf must be positive")
    if assumptions.shares_outstanding <= 0:
        raise ValueError("shares_outstanding must be positive")
    if assumptions.wacc <= assumptions.terminal_growth:
        raise ValueError("wacc must exceed terminal growth")
    if not assumptions.growth_rates:
        raise ValueError("at least one projection year is required")

    projected_fcf: list[float] = []
    current_fcf = assumptions.base_fcf
    present_value_explicit = 0.0

    for year, growth in enumerate(assumptions.growth_rates, start=1):
        current_fcf *= 1 + growth
        projected_fcf.append(current_fcf)
        present_value_explicit += current_fcf / ((1 + assumptions.wacc) ** year)

    final_fcf = projected_fcf[-1]
    terminal_value = (
        final_fcf * (1 + assumptions.terminal_growth)
        / (assumptions.wacc - assumptions.terminal_growth)
    )
    present_value_terminal = terminal_value / (
        (1 + assumptions.wacc) ** len(projected_fcf)
    )
    enterprise_value = present_value_explicit + present_value_terminal
    equity_value = enterprise_value - assumptions.net_debt
    value_per_share = equity_value / assumptions.shares_outstanding

    return DcfResult(
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        value_per_share=value_per_share,
        projected_fcf=projected_fcf,
        terminal_value=terminal_value,
        present_value_terminal=present_value_terminal,
    )
