import pytest

from app.calculations.financials import (
    altman_z_score,
    beneish_m_score,
    cagr,
    cfo_to_pat,
    growth_rate,
    net_debt_to_ebitda,
    piotroski_f_score,
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


def _forensic_facts() -> dict[str, float]:
    return {
        "pat": 100.0,
        "cfo": 120.0,
        "total_assets": 1000.0,
        "previous_pat": 80.0,
        "previous_total_assets": 1000.0,
        "long_term_debt": 100.0,
        "previous_long_term_debt": 150.0,
        "current_assets": 400.0,
        "current_liabilities": 200.0,
        "previous_current_assets": 300.0,
        "previous_current_liabilities": 200.0,
        "shares_outstanding": 100.0,
        "previous_shares_outstanding": 100.0,
        "revenue": 1000.0,
        "previous_revenue": 900.0,
        "cogs": 600.0,
        "previous_cogs": 570.0,
        "retained_earnings": 200.0,
        "ebit": 150.0,
        "market_cap": 1500.0,
        "total_liabilities": 500.0,
        "receivables": 100.0,
        "previous_receivables": 80.0,
        "ppe": 400.0,
        "previous_ppe": 450.0,
        "depreciation": 50.0,
        "previous_depreciation": 45.0,
        "sga_expense": 100.0,
        "previous_sga_expense": 95.0,
        "total_debt": 100.0,
        "previous_total_debt": 150.0,
    }


def test_piotroski_requires_complete_inputs_and_scores_nine_signals() -> None:
    facts = _forensic_facts()
    assert piotroski_f_score(facts) == 9
    del facts["previous_cogs"]
    assert piotroski_f_score(facts) is None


def test_altman_z_score_matches_classic_public_manufacturer_formula() -> None:
    assert altman_z_score(_forensic_facts()) == pytest.approx(3.815)


def test_beneish_m_score_is_deterministic_when_all_components_exist() -> None:
    assert beneish_m_score(_forensic_facts()) == pytest.approx(-2.3871253482)
