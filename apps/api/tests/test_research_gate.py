from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.data_readiness import DataCoverage, DataCoverageReport
from app.core.research_gate import (
    ResearchCorpusNotReadyError,
    enforce_report_for_environment,
    enforce_research_corpus_ready,
)


def _coverage() -> DataCoverage:
    return DataCoverage(
        nse_eq_securities=0,
        provider_instruments=0,
        nse_securities_with_financial_facts=0,
        financial_facts=0,
        sourced_financial_facts=0,
        nse_securities_with_corporate_events=0,
        corporate_events=0,
        sourced_corporate_events=0,
        sources=0,
        nonproduction_sources=0,
        evidence_chunks=0,
        embedded_evidence_chunks=0,
        nse_securities_with_market_bars=0,
        market_bars=0,
        sourced_market_bars=0,
        benchmark_bars=0,
        sourced_benchmark_bars=0,
        macro_observations=0,
        sourced_macro_observations=0,
        nse_securities_with_security_metrics=0,
        security_metrics=0,
        sourced_security_metrics=0,
        enabled_official_feeds=0,
        enabled_unapproved_official_feeds=0,
        latest_corporate_event=datetime(2026, 8, 30, tzinfo=UTC),
    )


def test_production_blocks_hard_corpus_errors() -> None:
    report = DataCoverageReport(
        coverage=_coverage(),
        errors=("NSE EQ security universe has 5 rows; production minimum is 1000.",),
        warnings=("Financial facts are empty.",),
    )

    with pytest.raises(ResearchCorpusNotReadyError) as caught:
        enforce_report_for_environment(report, app_env="production")

    assert caught.value.errors == report.errors


def test_production_allows_warning_only_report() -> None:
    report = DataCoverageReport(
        coverage=_coverage(),
        errors=(),
        warnings=("Benchmark history is stale.",),
    )
    assert enforce_report_for_environment(report, app_env="production") is report


def test_nonproduction_does_not_block_hard_corpus_errors() -> None:
    report = DataCoverageReport(
        coverage=_coverage(),
        errors=("Small fixture universe.",),
        warnings=(),
    )
    assert enforce_report_for_environment(report, app_env="development") is report
    assert enforce_report_for_environment(report, app_env="test") is report


@pytest.mark.asyncio
async def test_nonproduction_async_gate_does_not_query_database() -> None:
    result = await enforce_research_corpus_ready(object(), app_env="development")  # type: ignore[arg-type]
    assert result is None
