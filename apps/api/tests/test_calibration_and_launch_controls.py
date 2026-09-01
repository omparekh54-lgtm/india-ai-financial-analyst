from app.calibration import summarize_evaluations
from app.core.config import Settings


def test_calibration_summary_is_descriptive_not_a_trading_score() -> None:
    rows = [
        {
            "horizon_sessions": 20,
            "stock_return_pct": 8.0,
            "excess_return_pct": 3.0,
            "confidence": {"thesis_confidence": 0.8},
        },
        {
            "horizon_sessions": 20,
            "stock_return_pct": -2.0,
            "excess_return_pct": -1.0,
            "confidence": {"thesis_confidence": 0.4},
        },
        {
            "horizon_sessions": 60,
            "stock_return_pct": 12.0,
            "excess_return_pct": None,
            "confidence": {"thesis_confidence": 0.6},
        },
    ]
    result = summarize_evaluations(rows)

    twenty = result["horizons"]["20"]  # type: ignore[index]
    assert twenty["sample_count"] == 2
    assert twenty["benchmark_matched_count"] == 2
    assert twenty["mean_excess_return_pct"] == 1.0
    assert twenty["positive_excess_rate_pct"] == 50.0
    assert "not proof of forecasting skill" in result["interpretation"]


def test_commercial_source_scope_parser_is_explicit_and_stable() -> None:
    settings = Settings(
        _env_file=None,
        commercial_required_source_scopes="NSE:*, RBI:macro, NSE:* , TAVILY:web",
    )
    assert settings.commercial_required_source_scope_list == (
        ("NSE", "*"),
        ("RBI", "macro"),
        ("TAVILY", "web"),
    )


def test_commercial_launch_is_opt_in_and_free_only_by_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.commercial_launch_enabled is False
    assert settings.commercial_require_free_only is True
    assert settings.free_only is True
    assert settings.enable_usage_limits is False
    assert settings.daily_research_job_limit >= settings.daily_deep_research_job_limit
