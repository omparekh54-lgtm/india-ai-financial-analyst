from __future__ import annotations

from datetime import date

import pytest

from app.connectors.upstox_history import (
    UpstoxHistoricalClient,
    UpstoxHistoricalDataError,
    parse_daily_candles,
)


def test_request_url_encodes_upstox_instrument_key() -> None:
    client = UpstoxHistoricalClient("operator-token")
    url = client.request_url(
        "NSE_EQ|INE848E01016",
        from_date=date(2025, 1, 1),
        to_date=date(2025, 2, 1),
    )
    assert url.endswith(
        "/NSE_EQ%7CINE848E01016/days/1/2025-02-01/2025-01-01"
    )


def test_parse_upstox_documented_candles() -> None:
    payload = {
        "status": "success",
        "data": {
            "candles": [
                ["2025-01-01T00:00:00+05:30", 53.1, 53.95, 51.6, 52.05, 235519861, 0],
                ["2025-02-01T00:00:00+05:30", 50.35, 56.85, 49.35, 52.8, 1004998611, 0],
            ]
        },
    }
    bars = parse_daily_candles(payload)

    assert len(bars) == 2
    assert bars[0].provider == "upstox"
    assert bars[0].interval == "1d"
    assert bars[0].open == 53.1
    assert bars[0].high == 53.95
    assert bars[0].low == 51.6
    assert bars[0].close == 52.05
    assert bars[0].volume == 235519861.0
    assert bars[0].ts.isoformat() == "2024-12-31T18:30:00+00:00"


def test_parse_upstox_history_rejects_bad_ohlc() -> None:
    payload = {
        "status": "success",
        "data": {
            "candles": [
                ["2025-01-01T00:00:00+05:30", 53.1, 51.0, 51.6, 52.05, 10, 0]
            ]
        },
    }
    with pytest.raises(UpstoxHistoricalDataError, match="high must be"):
        parse_daily_candles(payload)


def test_parse_upstox_history_rejects_duplicate_timestamps() -> None:
    candle = ["2025-01-01T00:00:00+05:30", 53.1, 53.95, 51.6, 52.05, 100, 0]
    payload = {"status": "success", "data": {"candles": [candle, candle]}}
    with pytest.raises(UpstoxHistoricalDataError, match="duplicate"):
        parse_daily_candles(payload)
