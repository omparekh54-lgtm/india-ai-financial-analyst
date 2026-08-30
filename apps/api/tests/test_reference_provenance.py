from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.ingestion.reference_provenance import (
    parse_optional_datetime,
    validate_provider_name,
    validate_source_uri,
)


def test_source_uri_requires_absolute_scheme() -> None:
    with pytest.raises(ValueError, match="absolute URI"):
        validate_source_uri("licensed/reliance/fy26")


def test_source_uri_rejects_embedded_credentials() -> None:
    with pytest.raises(ValueError, match="embedded credentials"):
        validate_source_uri("https://user:secret@licensed.example/reliance/fy26")


def test_source_uri_accepts_real_https_and_file_provenance() -> None:
    assert (
        validate_source_uri("https://licensed.vendor.net/reliance/fy26")
        == "https://licensed.vendor.net/reliance/fy26"
    )
    assert validate_source_uri("file:///approved/reliance.csv") == "file:///approved/reliance.csv"


def test_source_uri_rejects_explicit_non_production_markers() -> None:
    for value in (
        "synthetic://prices/reliance",
        "https://licensed.vendor.net/mock/reliance.csv",
        "https://licensed.vendor.net/exports/sample_prices.csv",
        "file:///approved/generated_financials.csv",
        "https://licensed.vendor.net/placeholder/reliance.csv",
    ):
        with pytest.raises(ValueError, match="synthetic|non-production"):
            validate_source_uri(value)


def test_provider_name_rejects_non_production_labels() -> None:
    assert validate_provider_name("NSE") == "nse"
    assert validate_provider_name("Licensed Vendor") == "licensed vendor"
    with pytest.raises(ValueError, match="synthetic/mock/sample"):
        validate_provider_name("synthetic-provider")


def test_optional_datetime_normalizes_to_utc() -> None:
    assert parse_optional_datetime(None) is None
    assert parse_optional_datetime("") is None
    assert parse_optional_datetime("2026-08-30T12:30:00") == datetime(
        2026,
        8,
        30,
        12,
        30,
        tzinfo=UTC,
    )
    assert parse_optional_datetime("2026-08-30T18:00:00+05:30") == datetime(
        2026,
        8,
        30,
        12,
        30,
        tzinfo=UTC,
    )
