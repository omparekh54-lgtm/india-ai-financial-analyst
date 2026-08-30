import pytest

from app.ingestion.official_benchmark_files import (
    benchmark_artifact_uri,
    resolve_official_benchmark_source,
)


def test_official_benchmark_source_normalizes_codes_and_provider() -> None:
    nifty = resolve_official_benchmark_source(
        "NIFTY 50",
        "https://www.niftyindices.com/reports/historical-data",
    )
    assert nifty.benchmark_code == "NIFTY50"
    assert nifty.provider == "nse_indices"

    vix = resolve_official_benchmark_source(
        "India VIX",
        "https://www.nseindia.com/all-reports",
    )
    assert vix.benchmark_code == "INDIAVIX"
    assert vix.provider == "nse"


def test_official_benchmark_source_rejects_unapproved_code_and_host() -> None:
    with pytest.raises(ValueError, match="benchmark_code must be one of"):
        resolve_official_benchmark_source(
            "SENSEX",
            "https://www.nseindia.com/all-reports",
        )
    with pytest.raises(ValueError, match="official NSE/NSE Indices domain"):
        resolve_official_benchmark_source(
            "NIFTY50",
            "https://www.nseindia.com.evil.example/all-reports",
        )
    with pytest.raises(ValueError, match="must use HTTPS"):
        resolve_official_benchmark_source(
            "NIFTY50",
            "http://www.nseindia.com/all-reports",
        )


def test_benchmark_artifact_uri_is_bound_to_checksum() -> None:
    checksum = "AB" * 32
    uri = benchmark_artifact_uri(
        "https://www.niftyindices.com/reports/historical-data",
        checksum,
    )
    assert uri.endswith(f"#artifact-sha256={checksum.lower()}")

    with pytest.raises(ValueError, match="sha256"):
        benchmark_artifact_uri(
            "https://www.niftyindices.com/reports/historical-data",
            "not-a-checksum",
        )
