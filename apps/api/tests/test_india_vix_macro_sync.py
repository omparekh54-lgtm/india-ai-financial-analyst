from datetime import UTC, datetime

import pytest

from scripts.sync_india_vix_macro import vix_macro_observation


def test_vix_macro_observation_preserves_official_benchmark_context() -> None:
    observation = vix_macro_observation(
        ts=datetime(2026, 8, 31, 10, 0, tzinfo=UTC),
        close=12.45,
    )

    assert observation.series_key == "india_vix"
    assert observation.observation_date.isoformat() == "2026-08-31"
    assert float(observation.value) == 12.45
    assert observation.unit == "index points"
    assert observation.metadata["benchmark_code"] == "INDIAVIX"
    assert observation.metadata["derivation"] == "latest_sourced_benchmark_close"
    assert observation.metadata["provenance_class"] == "official_source"


def test_vix_macro_observation_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        vix_macro_observation(ts=datetime(2026, 8, 31, 10, 0), close=12.0)

    with pytest.raises(ValueError, match="cannot be negative"):
        vix_macro_observation(
            ts=datetime(2026, 8, 31, 10, 0, tzinfo=UTC),
            close=-0.1,
        )
