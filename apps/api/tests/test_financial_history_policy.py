from datetime import date, timedelta

import pytest

from app.core.financial_history_policy import (
    FINANCIAL_FRESHNESS_DAYS,
    MATURE_REQUIRED_PERIODS,
    evaluate_financial_history,
    required_financial_periods,
)

AS_OF = date(2026, 8, 31)


def test_unknown_listing_age_keeps_eight_period_contract() -> None:
    assert required_financial_periods(None, as_of=AS_OF) == MATURE_REQUIRED_PERIODS


def test_recent_listing_scales_only_with_possible_reporting_opportunities() -> None:
    assert required_financial_periods(AS_OF, as_of=AS_OF) == 1
    assert required_financial_periods(AS_OF - timedelta(days=100), as_of=AS_OF) == 1
    assert required_financial_periods(AS_OF - timedelta(days=200), as_of=AS_OF) == 2
    assert required_financial_periods(AS_OF - timedelta(days=365), as_of=AS_OF) == 4
    assert required_financial_periods(AS_OF - timedelta(days=800), as_of=AS_OF) == 8


def test_recent_listing_can_be_complete_with_less_than_eight_periods() -> None:
    result = evaluate_financial_history(
        listing_date=AS_OF - timedelta(days=365),
        as_of=AS_OF,
        period_count=4,
        fact_type_count=8,
        latest_period_end=AS_OF - timedelta(days=60),
    )

    assert result.required_periods == 4
    assert result.complete is True
    assert result.history_limited_recent_listing is True
    assert result.warnings


def test_recent_listing_still_requires_fact_breadth_and_freshness() -> None:
    result = evaluate_financial_history(
        listing_date=AS_OF - timedelta(days=100),
        as_of=AS_OF,
        period_count=1,
        fact_type_count=5,
        latest_period_end=AS_OF - timedelta(days=FINANCIAL_FRESHNESS_DAYS + 1),
    )

    assert result.complete is False
    assert any("canonical fact types" in error for error in result.errors)
    assert any("stale" in error for error in result.errors)


def test_mature_listing_requires_full_institutional_depth() -> None:
    result = evaluate_financial_history(
        listing_date=AS_OF - timedelta(days=1000),
        as_of=AS_OF,
        period_count=7,
        fact_type_count=12,
        latest_period_end=AS_OF - timedelta(days=30),
    )

    assert result.required_periods == 8
    assert result.complete is False
    assert result.history_limited_recent_listing is False
    assert any("7 < 8" in error for error in result.errors)


def test_no_financial_data_never_becomes_ready_for_new_ipo() -> None:
    result = evaluate_financial_history(
        listing_date=AS_OF,
        as_of=AS_OF,
        period_count=0,
        fact_type_count=0,
        latest_period_end=None,
    )

    assert result.required_periods == 1
    assert result.complete is False
    assert len(result.errors) == 3


def test_invalid_counts_and_future_period_are_rejected() -> None:
    with pytest.raises(ValueError, match="period_count"):
        evaluate_financial_history(
            listing_date=None,
            as_of=AS_OF,
            period_count=-1,
            fact_type_count=6,
            latest_period_end=AS_OF,
        )

    with pytest.raises(ValueError, match="fact_type_count"):
        evaluate_financial_history(
            listing_date=None,
            as_of=AS_OF,
            period_count=8,
            fact_type_count=-1,
            latest_period_end=AS_OF,
        )

    with pytest.raises(ValueError, match="after as_of"):
        evaluate_financial_history(
            listing_date=None,
            as_of=AS_OF,
            period_count=8,
            fact_type_count=6,
            latest_period_end=AS_OF + timedelta(days=1),
        )
