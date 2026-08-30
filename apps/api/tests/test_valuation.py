import pytest

from app.calculations.valuation import DcfAssumptions, discounted_cash_flow


def test_dcf_returns_equity_value_per_share() -> None:
    result = discounted_cash_flow(
        DcfAssumptions(
            base_fcf=100.0,
            growth_rates=[0.10, 0.08, 0.06, 0.05, 0.04],
            wacc=0.11,
            terminal_growth=0.04,
            net_debt=150.0,
            shares_outstanding=50.0,
        )
    )

    assert result.enterprise_value > 0
    assert result.value_per_share > 0
    assert len(result.projected_fcf) == 5


def test_dcf_rejects_invalid_terminal_spread() -> None:
    with pytest.raises(ValueError, match="wacc must exceed terminal growth"):
        discounted_cash_flow(
            DcfAssumptions(
                base_fcf=100.0,
                growth_rates=[0.05],
                wacc=0.04,
                terminal_growth=0.04,
                net_debt=0.0,
                shares_outstanding=10.0,
            )
        )
