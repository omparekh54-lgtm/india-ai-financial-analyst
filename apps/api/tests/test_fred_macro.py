from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.connectors.base import Freshness, SourceEnvelope
from app.ingestion.fred_macro import FRED_MACRO_SERIES, parse_fred_series


def _envelope(series_id: str, observations: list[dict[str, str]]) -> SourceEnvelope:
    return SourceEnvelope(
        source_type="macro_data",
        source_uri=f"fred:{series_id}",
        title=f"FRED series {series_id}",
        retrieved_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
        freshness=Freshness.PERIODIC,
        payload={"observations": observations},
        metadata={"provider": "fred", "series_id": series_id},
    )


def test_approved_fred_series_match_agent_macro_keys() -> None:
    assert FRED_MACRO_SERIES["usd_inr"].series_id == "DEXINUS"
    assert FRED_MACRO_SERIES["brent"].series_id == "DCOILBRENTEU"
    assert {item.series_key for item in FRED_MACRO_SERIES.values()} == {"usd_inr", "brent"}


def test_fred_parser_skips_missing_values_and_preserves_provenance() -> None:
    series = FRED_MACRO_SERIES["usd_inr"]
    parsed = parse_fred_series(
        _envelope(
            series.series_id,
            [
                {"date": "2026-08-27", "value": "95.80", "realtime_start": "2026-08-27"},
                {"date": "2026-08-28", "value": "."},
                {"date": "2026-08-29", "value": "95.70", "realtime_end": "2026-08-29"},
            ],
        ),
        series,
        min_rows=2,
    )

    assert parsed.skipped_missing == 1
    assert [item.value for item in parsed.observations] == [Decimal("95.80"), Decimal("95.70")]
    assert parsed.observations[0].series_key == "usd_inr"
    assert parsed.observations[0].unit == "INR per USD"
    assert parsed.observations[0].metadata["fred_series_id"] == "DEXINUS"


def test_fred_parser_rejects_duplicate_dates() -> None:
    series = FRED_MACRO_SERIES["brent"]
    with pytest.raises(ValueError, match="Duplicate FRED observation date"):
        parse_fred_series(
            _envelope(
                series.series_id,
                [
                    {"date": "2026-08-28", "value": "94.20"},
                    {"date": "2026-08-28", "value": "95.10"},
                ],
            ),
            series,
        )


def test_fred_parser_rejects_series_uri_mismatch() -> None:
    series = FRED_MACRO_SERIES["usd_inr"]
    with pytest.raises(ValueError, match="source URI mismatch"):
        parse_fred_series(
            _envelope("DCOILBRENTEU", [{"date": "2026-08-29", "value": "95.70"}]),
            series,
            min_rows=1,
        )


def test_fred_parser_enforces_minimum_usable_history() -> None:
    series = FRED_MACRO_SERIES["brent"]
    with pytest.raises(ValueError, match="minimum expected is 2"):
        parse_fred_series(
            _envelope(
                series.series_id,
                [
                    {"date": "2026-08-28", "value": "."},
                    {"date": "2026-08-29", "value": "95.10"},
                ],
            ),
            series,
            min_rows=2,
        )
