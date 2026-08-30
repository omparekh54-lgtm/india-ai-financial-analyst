from __future__ import annotations

from datetime import UTC, date, datetime

from app.core.data_readiness import DataCoverage, evaluate_data_coverage

AS_OF = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def _coverage(**overrides: object) -> DataCoverage:
    values: dict[str, object] = {
        "nse_eq_securities": 1800,
        "provider_instruments": 1800,
        "financial_facts": 100,
        "sourced_financial_facts": 100,
        "corporate_events": 20,
        "sourced_corporate_events": 20,
        "sources": 20,
        "evidence_chunks": 200,
        "embedded_evidence_chunks": 150,
        "market_bars": 1000,
        "sourced_market_bars": 1000,
        "benchmark_bars": 500,
        "sourced_benchmark_bars": 500,
        "macro_observations": 100,
        "sourced_macro_observations": 100,
        "security_metrics": 100,
        "sourced_security_metrics": 100,
        "enabled_official_feeds": 1,
    }
    values.update(overrides)
    return DataCoverage(**values)  # type: ignore[arg-type]


def test_full_security_universe_is_required() -> None:
    report = evaluate_data_coverage(_coverage(nse_eq_securities=5, provider_instruments=5))
    assert report.ready is False
    assert "5 < 1000" in report.errors[0]


def test_empty_research_datasets_are_visible_warnings() -> None:
    report = evaluate_data_coverage(
        _coverage(
            financial_facts=0,
            sourced_financial_facts=0,
            corporate_events=0,
            sourced_corporate_events=0,
            sources=0,
            evidence_chunks=0,
            embedded_evidence_chunks=0,
            market_bars=0,
            sourced_market_bars=0,
            benchmark_bars=0,
            sourced_benchmark_bars=0,
            macro_observations=0,
            sourced_macro_observations=0,
            security_metrics=0,
            sourced_security_metrics=0,
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


def test_populated_but_stale_datasets_are_visible_warnings() -> None:
    report = evaluate_data_coverage(
        _coverage(
            latest_financial_period=date(2025, 12, 1),
            latest_corporate_event=datetime(2026, 1, 1, tzinfo=UTC),
            latest_market_bar=datetime(2026, 8, 20, tzinfo=UTC),
            latest_benchmark_bar=datetime(2026, 8, 20, tzinfo=UTC),
            latest_macro_observation=date(2026, 6, 1),
        ),
        as_of=AS_OF,
    )
    assert report.ready is True
    assert any("financial facts appear stale" in warning for warning in report.warnings)
    assert any("filing evidence appears stale" in warning for warning in report.warnings)
    assert any("market bars appear stale" in warning for warning in report.warnings)
    assert any("benchmark bars appear stale" in warning for warning in report.warnings)
    assert any("macro observations appear stale" in warning for warning in report.warnings)


def test_fresh_populated_datasets_do_not_emit_staleness_warnings() -> None:
    report = evaluate_data_coverage(
        _coverage(
            latest_financial_period=date(2026, 6, 30),
            latest_corporate_event=datetime(2026, 8, 25, tzinfo=UTC),
            latest_market_bar=datetime(2026, 8, 29, tzinfo=UTC),
            latest_benchmark_bar=datetime(2026, 8, 29, tzinfo=UTC),
            latest_macro_observation=date(2026, 8, 1),
        ),
        as_of=AS_OF,
    )
    assert report.ready is True
    assert not any("appear stale" in warning for warning in report.warnings)


def test_naive_freshness_timestamp_is_treated_as_utc() -> None:
    naive_market_bar = datetime(2026, 8, 29, 12, 0, tzinfo=UTC).replace(tzinfo=None)
    report = evaluate_data_coverage(
        _coverage(latest_market_bar=naive_market_bar),
        as_of=AS_OF,
    )
    assert not any("market bars appear stale" in warning for warning in report.warnings)


def test_partial_provenance_is_a_hard_readiness_failure() -> None:
    report = evaluate_data_coverage(
        _coverage(
            financial_facts=100,
            sourced_financial_facts=99,
            corporate_events=20,
            sourced_corporate_events=19,
            market_bars=1000,
            sourced_market_bars=990,
            benchmark_bars=500,
            sourced_benchmark_bars=450,
            macro_observations=100,
            sourced_macro_observations=80,
            security_metrics=100,
            sourced_security_metrics=95,
        )
    )
    assert report.ready is False
    assert any("99/100 are source-linked" in error for error in report.errors)
    assert any("19/20 are source-linked" in error for error in report.errors)
    assert any("990/1000 are source-linked" in error for error in report.errors)
    assert any("450/500 are source-linked" in error for error in report.errors)
    assert any("80/100 are source-linked" in error for error in report.errors)
    assert any("95/100 are source-linked" in error for error in report.errors)


def test_fully_sourced_material_data_has_no_provenance_error() -> None:
    report = evaluate_data_coverage(_coverage())
    assert report.ready is True
    assert not any("without source provenance" in error for error in report.errors)
