from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.ingestion.reference_files import (
    ReferenceFileError,
    parse_benchmark_csv,
    parse_macro_csv,
)


def test_benchmark_csv_normalizes_india_date_to_utc() -> None:
    content = """date,open,high,low,close,volume
2026-08-28,25000,25200,24900,25150,123456
2026-08-29,25150,25300,25000,25225,130000
"""
    bars = parse_benchmark_csv(content, provider="licensed_nse_export")
    assert len(bars) == 2
    assert bars[0].ts == datetime(2026, 8, 27, 18, 30, tzinfo=UTC)
    assert bars[0].provider == "licensed_nse_export"
    assert bars[0].close == 25150.0


def test_benchmark_csv_rejects_missing_ohlc() -> None:
    with pytest.raises(ReferenceFileError, match="close is required"):
        parse_benchmark_csv(
            "date,open,high,low,close\n2026-08-28,10,12,9,\n",
            provider="approved",
        )


def test_macro_csv_parses_multiple_series_and_release_times() -> None:
    content = """series_key,observation_date,value,unit,released_at,source_name
repo_rate,2026-08-01,5.50,percent,2026-08-01T10:00:00+05:30,RBI
usd_inr,2026-08-01,86.25,INR per USD,,approved export
"""
    observations = parse_macro_csv(content)
    assert len(observations) == 2
    assert observations[0].series_key == "repo_rate"
    assert observations[0].released_at == datetime(2026, 8, 1, 4, 30, tzinfo=UTC)
    assert observations[1].released_at is None


def test_macro_csv_requires_series_date_and_value() -> None:
    with pytest.raises(ReferenceFileError, match="series_key/date/value are required"):
        parse_macro_csv("series_key,date,value\nrepo_rate,2026-08-01,\n")
