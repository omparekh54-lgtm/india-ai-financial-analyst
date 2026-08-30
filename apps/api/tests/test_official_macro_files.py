from datetime import date
from pathlib import Path

import pytest

from app.ingestion.macro import MacroObservation
from app.ingestion.official_macro_files import (
    RBI_IMPORT_SERIES,
    resolve_media_type,
    validate_macro_observations,
    validate_official_source_url,
    validate_rbi_series_key,
)


def test_official_source_urls_are_provider_scoped() -> None:
    assert (
        validate_official_source_url("RBI", "https://statistics.rbi.org.in/report.csv")
        == "https://statistics.rbi.org.in/report.csv"
    )
    assert (
        validate_official_source_url(
            "NSDL",
            "https://pilot.fpi.nsdl.co.in/Reports/example.csv",
        )
        == "https://pilot.fpi.nsdl.co.in/Reports/example.csv"
    )

    with pytest.raises(ValueError, match="approved official domain"):
        validate_official_source_url("RBI", "https://statistics.rbi.org.in.evil.example/file.csv")
    with pytest.raises(ValueError, match="approved official domain"):
        validate_official_source_url("NSDL", "https://statistics.rbi.org.in/report.csv")
    with pytest.raises(ValueError, match="approved official domain"):
        validate_official_source_url("RBI", "http://statistics.rbi.org.in/report.csv")


def test_rbi_import_series_are_explicitly_allowlisted() -> None:
    assert validate_rbi_series_key("Policy Repo Rate") == "repo_rate"
    assert validate_rbi_series_key("USD/INR") == "usd_inr"
    with pytest.raises(ValueError, match="RBI series_key must be one of"):
        validate_rbi_series_key("US Fed Funds")


def test_media_type_is_inferred_or_strictly_overridden() -> None:
    assert resolve_media_type(Path("macro.csv")) == "text/csv"
    assert resolve_media_type(Path("macro.JSON")) == "application/json"
    assert resolve_media_type(Path("macro.bin"), "text/html; charset=utf-8") == "text/html"

    with pytest.raises(ValueError, match="Unable to infer"):
        resolve_media_type(Path("macro.xlsx"))
    with pytest.raises(ValueError, match="Unsupported media type"):
        resolve_media_type(Path("macro.csv"), "application/octet-stream")


def test_macro_file_validation_rejects_duplicates_and_short_exports() -> None:
    observations = [
        MacroObservation("repo_rate", date(2026, 8, 28), "5.50", "%"),
        MacroObservation("repo_rate", date(2026, 8, 29), "5.50", "%"),
    ]
    assert (
        validate_macro_observations(
            observations,
            allowed_series=RBI_IMPORT_SERIES,
            min_rows=2,
        )
        == observations
    )

    with pytest.raises(ValueError, match="minimum expected is 3"):
        validate_macro_observations(
            observations,
            allowed_series=RBI_IMPORT_SERIES,
            min_rows=3,
        )

    duplicate = [
        MacroObservation("repo_rate", date(2026, 8, 29), "5.50", "%"),
        MacroObservation("repo_rate", date(2026, 8, 29), "5.75", "%"),
    ]
    with pytest.raises(ValueError, match="duplicate natural keys"):
        validate_macro_observations(
            duplicate,
            allowed_series=RBI_IMPORT_SERIES,
            min_rows=2,
        )


def test_macro_file_validation_rejects_unexpected_series() -> None:
    with pytest.raises(ValueError, match="Unexpected macro series"):
        validate_macro_observations(
            [MacroObservation("brent", date(2026, 8, 29), "94.20", "USD/bbl")],
            allowed_series=RBI_IMPORT_SERIES,
            min_rows=1,
        )
