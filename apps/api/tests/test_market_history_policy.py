from datetime import date

import pytest

from app.core.market_history_policy import (
    MATURE_REQUIRED_DAILY_BARS,
    evaluate_market_history,
    required_daily_bars,
)


def test_mature_or_unknown_listing_requires_200_bars() -> None:
    as_of = date(2026, 8, 31)

    assert required_daily_bars(None, as_of=as_of) == MATURE_REQUIRED_DAILY_BARS
    assert required_daily_bars(date(2025, 8, 31), as_of=as_of) == 200


def test_recent_listing_never_requires_prelisting_history() -> None:
    as_of = date(2026, 8, 31)

    ten_days_old = required_daily_bars(date(2026, 8, 21), as_of=as_of)
    ninety_days_old = required_daily_bars(date(2026, 6, 2), as_of=as_of)

    assert ten_days_old == 6
    assert 30 < ninety_days_old < 200


def test_recent_listing_complete_history_can_be_history_limited_for_technicals() -> None:
    result = evaluate_market_history(
        listing_date=date(2026, 8, 21),
        as_of=date(2026, 8, 31),
        bar_count=6,
        first_bar_date=date(2026, 8, 21),
        last_bar_date=date(2026, 8, 31),
    )

    assert result.complete is True
    assert result.technical_agent_computable is False
    assert result.recent_listing is True
    assert any("fewer than 30 sessions" in warning for warning in result.warnings)


def test_recent_listing_requires_history_to_start_near_listing_date() -> None:
    result = evaluate_market_history(
        listing_date=date(2026, 6, 1),
        as_of=date(2026, 8, 31),
        bar_count=60,
        first_bar_date=date(2026, 6, 20),
        last_bar_date=date(2026, 8, 31),
    )

    assert result.complete is False
    assert any("official listing date" in error for error in result.errors)


def test_market_history_requires_fresh_latest_bar() -> None:
    result = evaluate_market_history(
        listing_date=date(2020, 1, 1),
        as_of=date(2026, 8, 31),
        bar_count=220,
        first_bar_date=date(2025, 10, 1),
        last_bar_date=date(2026, 8, 20),
    )

    assert result.complete is False
    assert any("stale" in error for error in result.errors)


def test_market_history_rejects_invalid_ranges() -> None:
    with pytest.raises(ValueError, match="bar_count"):
        evaluate_market_history(
            listing_date=date(2026, 1, 1),
            as_of=date(2026, 8, 31),
            bar_count=-1,
            first_bar_date=None,
            last_bar_date=None,
        )

    with pytest.raises(ValueError, match="first_bar_date"):
        evaluate_market_history(
            listing_date=date(2026, 1, 1),
            as_of=date(2026, 8, 31),
            bar_count=100,
            first_bar_date=date(2026, 8, 31),
            last_bar_date=date(2026, 8, 30),
        )
