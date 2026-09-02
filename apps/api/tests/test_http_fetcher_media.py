from app.connectors.http_fetcher import _canonical_media_type


def test_octet_stream_xbrl_is_inferred_from_safe_extension() -> None:
    assert (
        _canonical_media_type(
            "application/octet-stream",
            "https://nsearchives.nseindia.com/corporate/result.xbrl",
        )
        == "application/xbrl+xml"
    )


def test_octet_stream_csv_is_inferred_from_safe_extension() -> None:
    assert (
        _canonical_media_type(
            "application/octet-stream",
            "https://www.nseindia.com/reports/announcements.csv",
        )
        == "text/csv"
    )


def test_octet_stream_with_unknown_extension_is_rejected() -> None:
    assert (
        _canonical_media_type(
            "application/octet-stream",
            "https://www.nseindia.com/reports/archive.bin",
        )
        is None
    )
