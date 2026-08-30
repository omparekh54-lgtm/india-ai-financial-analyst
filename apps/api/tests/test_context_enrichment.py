from datetime import UTC, datetime
from decimal import Decimal

from app.research.context_enrichment import (
    build_benchmark_context,
    build_company_metrics,
    build_macro_context,
    sector_benchmark_code,
)


def test_macro_context_calculates_market_changes() -> None:
    rows = [
        {"series_key": "usd_inr", "value": Decimal("84.00"), "rn": 1},
        {"series_key": "usd_inr", "value": Decimal("83.00"), "rn": 2},
        {"series_key": "repo_rate", "value": Decimal("5.50"), "rn": 1},
    ]
    result = build_macro_context(rows)
    assert result["repo_rate"] == 5.5
    assert round(float(result["usd_inr_change_pct"]), 4) == 1.2048


def test_company_metrics_use_financials_when_security_metrics_missing() -> None:
    result = build_company_metrics(
        {
            "revenue": 120.0,
            "previous_revenue": 100.0,
            "ebitda": 24.0,
            "ebit": 18.0,
            "total_assets": 200.0,
            "current_liabilities": 50.0,
        },
        [],
    )
    assert round(float(result["revenue_growth"]), 4) == 0.2
    assert round(float(result["ebitda_margin"]), 4) == 0.2
    assert round(float(result["roce"]), 4) == 0.12


def test_benchmark_context_calculates_relative_inputs() -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    rows = [
        {"role": "market", "code": "NIFTY50", "name": "NIFTY 50", "ts": now, "close": 25000, "provider": "test", "rn": 1},
        {"role": "market", "code": "NIFTY50", "name": "NIFTY 50", "ts": now, "close": 24750, "provider": "test", "rn": 2},
    ]
    result = build_benchmark_context(rows)
    benchmark = result["benchmark"]
    assert isinstance(benchmark, dict)
    assert round(float(benchmark["change_pct"]), 4) == 1.0101


def test_sector_benchmark_rules_are_india_first() -> None:
    assert sector_benchmark_code("Financial Services", "Private Sector Bank") == "NIFTYBANK"
    assert sector_benchmark_code("Information Technology", "IT Services") == "NIFTYIT"
    assert sector_benchmark_code("Energy", "Diversified Energy") is None
