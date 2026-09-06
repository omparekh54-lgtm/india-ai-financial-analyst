from datetime import UTC, datetime

import pytest

from app.research.context import _market_history_profile


def test_market_history_profile_summarizes_full_history() -> None:
    profile = _market_history_profile(
        {
            "trading_sessions": 5000,
            "first_bar_at": datetime(2004, 8, 25, tzinfo=UTC),
            "last_bar_at": datetime(2026, 9, 1, tzinfo=UTC),
            "first_close": 100.0,
            "latest_close": 2500.0,
            "all_time_high": 3000.0,
            "all_time_low": 80.0,
            "max_drawdown_pct": -55.0,
        }
    )

    assert profile["trading_sessions"] == 5000
    assert profile["lifetime_return_pct"] == pytest.approx(2400.0)
    assert profile["max_drawdown_pct"] == pytest.approx(-55.0)
    assert profile["is_delayed"] is True


def test_market_history_profile_rejects_empty_aggregate() -> None:
    assert _market_history_profile({"trading_sessions": 0}) == {}
