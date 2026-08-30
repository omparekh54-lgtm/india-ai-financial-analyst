from __future__ import annotations

from app.core.data_readiness import DataCoverage, evaluate_data_coverage


def _coverage(**overrides: int) -> DataCoverage:
    values: dict[str, int] = {
        "nse_eq_securities": 1800,
        "provider_instruments": 1800,
        "financial_facts": 100,
        "corporate_events": 20,
        "sources": 20,
        "evidence_chunks": 200,
        "embedded_evidence_chunks": 150,
        "market_bars": 1000,
        "benchmark_bars": 500,
        "macro_observations": 100,
        "security_metrics": 100,
        "enabled_official_feeds": 1,
    }
    values.update(overrides)
    return DataCoverage(**values)


def test_full_security_universe_is_required() -> None:
    report = evaluate_data_coverage(_coverage(nse_eq_securities=5, provider_instruments=5))
    assert report.ready is False
    assert "5 < 1000" in report.errors[0]


def test_empty_research_datasets_are_visible_warnings() -> None:
    report = evaluate_data_coverage(
        _coverage(
            financial_facts=0,
            corporate_events=0,
            sources=0,
            evidence_chunks=0,
            embedded_evidence_chunks=0,
            market_bars=0,
            benchmark_bars=0,
            macro_observations=0,
            security_metrics=0,
            enabled_official_feeds=0,
        )
    )
    assert report.ready is True
    assert len(report.warnings) >= 6
    assert any("financial facts" in warning for warning in report.warnings)
    assert any("official automated data feeds" in warning for warning in report.warnings)


def test_embedding_backfill_warning_only_when_evidence_exists() -> None:
    report = evaluate_data_coverage(_coverage(embedded_evidence_chunks=0))
    assert any("semantic embedding backfill" in warning for warning in report.warnings)

    empty = evaluate_data_coverage(
        _coverage(sources=0, evidence_chunks=0, embedded_evidence_chunks=0)
    )
    assert not any("semantic embedding backfill" in warning for warning in empty.warnings)
