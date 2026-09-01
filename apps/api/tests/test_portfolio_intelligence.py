from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from app.comparison import MetricFilter, _validate_metric_names
from app.portfolio import _historical_portfolio_stats, _portfolio_analysis

SECURITY_A = UUID("00000000-0000-0000-0000-000000000001")
SECURITY_B = UUID("00000000-0000-0000-0000-000000000002")
PORTFOLIO = UUID("10000000-0000-0000-0000-000000000001")


def _position(
    security_id: UUID,
    symbol: str,
    sector: str,
    quantity: float,
    close: float | None,
    average_cost: float | None,
) -> dict[str, object]:
    return {
        "security_id": security_id,
        "legal_name": symbol,
        "nse_symbol": symbol,
        "sector": sector,
        "industry": sector,
        "quantity": quantity,
        "average_cost": average_cost,
        "latest_close": close,
        "latest_price_at": datetime.now(UTC),
        "latest_provider": "test-source",
        "latest_source_id": UUID("20000000-0000-0000-0000-000000000001") if close else None,
        "notes": None,
    }


def test_portfolio_analysis_is_source_linked_and_flags_concentration() -> None:
    portfolio = {"id": PORTFOLIO, "name": "Core", "base_currency": "INR", "updated_at": None}
    positions = [
        _position(SECURITY_A, "AAA", "Banks", 10, 100.0, 80.0),
        _position(SECURITY_B, "BBB", "IT", 1, 100.0, 90.0),
    ]
    result = _portfolio_analysis(portfolio, positions, [])

    assert result["covered_market_value"] == 1100.0
    assert result["price_coverage_pct"] == 100.0
    assert result["pnl_coverage_pct"] == 100.0
    assert result["matched_cost_basis"] == 890.0
    assert result["known_unrealized_pnl"] == 210.0
    assert result["positions"][0]["source_linked_price"] is True  # type: ignore[index]
    assert "single_position_concentration_above_30pct" in result["risk_flags"]
    assert result["historical_risk"] == {
        "available": False,
        "reason": "insufficient_common_source_linked_history",
        "common_observations": 0,
    }


def test_portfolio_partial_valuation_does_not_invent_missing_price_or_pnl() -> None:
    portfolio = {"id": PORTFOLIO, "name": "Core", "base_currency": "INR", "updated_at": None}
    positions = [
        _position(SECURITY_A, "AAA", "Banks", 10, 100.0, 80.0),
        _position(SECURITY_B, "BBB", "IT", 5, None, 50.0),
    ]
    result = _portfolio_analysis(portfolio, positions, [])

    assert result["price_coverage_pct"] == 50.0
    assert result["pnl_coverage_pct"] == 50.0
    assert result["covered_market_value"] == 1000.0
    assert result["known_cost_basis"] == 1050.0
    assert result["matched_cost_basis"] == 800.0
    assert result["known_unrealized_pnl"] == 200.0
    assert result["positions"][1]["market_value"] is None  # type: ignore[index]
    assert "positions_missing_source_linked_prices" in result["risk_flags"]
    assert "portfolio_valuation_is_partial" in result["risk_flags"]
    assert "portfolio_pnl_is_partial" in result["risk_flags"]


def test_historical_portfolio_requires_common_dates_and_computes_real_series() -> None:
    positions = [
        {"security_id": SECURITY_A, "quantity": 2},
        {"security_id": SECURITY_B, "quantity": 1},
    ]
    start = date(2026, 1, 1)
    rows: list[dict[str, object]] = []
    for offset in range(35):
        day = start + timedelta(days=offset)
        rows.extend(
            [
                {"security_id": SECURITY_A, "bar_date": day, "close": 100 + offset},
                {"security_id": SECURITY_B, "bar_date": day, "close": 200 + offset},
            ]
        )
    result = _historical_portfolio_stats(positions, rows)
    assert result["available"] is True
    assert result["common_observations"] == 35
    assert result["total_return_pct"] > 0  # type: ignore[operator]
    assert result["max_drawdown_pct"] == 0.0


def test_screen_filters_allow_only_canonical_source_backed_metrics() -> None:
    MetricFilter("pe", min_value=1, max_value=50).validate()
    assert _validate_metric_names(["pe", "roce"]) == ["pe", "roce"]
    with pytest.raises(ValueError, match="unsupported"):
        MetricFilter("magic_buy_score", min_value=1).validate()
    with pytest.raises(ValueError, match="cannot exceed"):
        MetricFilter("pe", min_value=20, max_value=10).validate()
