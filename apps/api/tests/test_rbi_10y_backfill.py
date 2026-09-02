from datetime import UTC, date, datetime

import pytest

from app.ingestion.macro import MacroObservation
from scripts.backfill_rbi_10y import canonical_checksum, provenance_source_uri


def _observation(**metadata_overrides: object) -> MacroObservation:
    metadata: dict[str, object] = {
        "source_uri": "https://www.rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=24306",
        "publication_date": "2026-07-22",
        "series_label": "10-Year G-Sec Par Yield (FBIL)",
        "observation_basis": "latest_month_column",
    }
    metadata.update(metadata_overrides)
    return MacroObservation(
        series_key="india_10y_yield",
        observation_date=date(2026, 5, 31),
        value=6.33,
        unit="percent",
        released_at=datetime(2026, 7, 22, tzinfo=UTC),
        metadata=metadata,
    )


def test_rbi_10y_checksum_is_stable_and_content_bound() -> None:
    first = canonical_checksum(_observation())
    second = canonical_checksum(_observation())
    changed = canonical_checksum(
        MacroObservation(
            **{
                **_observation().__dict__,
                "value": 6.34,
            }
        )
    )

    assert first == second
    assert len(first) == 64
    assert first != changed


def test_rbi_10y_provenance_uri_binds_observation_and_checksum() -> None:
    observation = _observation()
    checksum = canonical_checksum(observation)
    uri = provenance_source_uri(observation, checksum)

    assert uri.startswith(
        "https://www.rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=24306#"
    )
    assert "observation-date=2026-05-31" in uri
    assert f"sha256={checksum}" in uri


def test_rbi_10y_provenance_uri_rejects_non_rbi_detail_url() -> None:
    observation = _observation(source_uri="https://example.com/report")
    with pytest.raises(ValueError, match="official RBI detail URL"):
        provenance_source_uri(observation, canonical_checksum(observation))
