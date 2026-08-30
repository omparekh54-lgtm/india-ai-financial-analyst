from app.calculations.financials import (
    cagr,
    cfo_to_pat,
    growth_rate,
    net_debt_to_ebitda,
    working_capital_days,
)


def test_growth_and_cagr() -> None:
    assert growth_rate(120.0, 100.0) == 0.2
    assert round(cagr(121.0, 100.0, 2) or 0, 6) == 0.1


def test_forensic_ratios_are_deterministic() -> None:
    assert cfo_to_pat(90.0, 100.0) == 0.9
    assert net_debt_to_ebitda(500.0, 100.0, 200.0) == 2.0


def test_working_capital_days() -> None:
    result = working_capital_days(
        receivables=100.0,
        inventory=50.0,
        payables=25.0,
        revenue=1000.0,
        cogs=500.0,
    )

    assert result["receivable_days"] == 36.5
    assert result["inventory_days"] == 36.5
    assert result["payable_days"] == 18.25
    assert result["cash_conversion_cycle"] == 54.75
