from datetime import UTC, date, datetime

import pandas as pd
import pytest

from app.connectors.yahoo_finance import (
    YahooFinanceDataError,
    YahooFinanceHistoryClient,
    parse_history_frame,
    yahoo_symbol,
)
from scripts.backfill_yfinance_market_history import resolve_date_range


def test_yahoo_symbol_maps_indian_exchanges() -> None:
    assert yahoo_symbol("RELIANCE", "NSE") == "RELIANCE.NS"
    assert yahoo_symbol("500325", "BSE") == "500325.BO"
    assert yahoo_symbol("INFY.NS", "NSE") == "INFY.NS"


def test_parse_history_frame_normalizes_ohlcv() -> None:
    frame = pd.DataFrame(
        [{"Open": 100, "High": 110, "Low": 98, "Close": 108, "Volume": 1200}],
        index=pd.DatetimeIndex([datetime(2026, 9, 3, tzinfo=UTC)]),
    )
    bars = parse_history_frame(frame, interval="1d")
    assert len(bars) == 1
    assert bars[0].provider == "yfinance"
    assert bars[0].close == 108


def test_parse_history_frame_rejects_invalid_ohlc() -> None:
    frame = pd.DataFrame(
        [{"Open": 100, "High": 101, "Low": 98, "Close": 108, "Volume": 1200}],
        index=pd.DatetimeIndex([datetime(2026, 9, 3, tzinfo=UTC)]),
    )
    with pytest.raises(YahooFinanceDataError, match="high"):
        parse_history_frame(frame, interval="1d")


@pytest.mark.asyncio
async def test_client_uses_inclusive_end_date_and_returns_checksum() -> None:
    calls: list[dict[str, object]] = []

    def loader(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return pd.DataFrame(
            [{"Open": 100, "High": 110, "Low": 98, "Close": 108, "Volume": 1200}],
            index=pd.DatetimeIndex([datetime(2026, 9, 3, tzinfo=UTC)]),
        )

    result = await YahooFinanceHistoryClient(loader=loader).fetch_history(
        "RELIANCE",
        exchange="NSE",
        from_date=date(2026, 9, 1),
        to_date=date(2026, 9, 3),
    )
    assert calls[0]["tickers"] == "RELIANCE.NS"
    assert calls[0]["end"] == "2026-09-04"
    assert result.source_url.endswith("/RELIANCE.NS/history/")
    assert len(result.response_sha256) == 64


@pytest.mark.asyncio
async def test_client_rejects_intraday_window_over_sixty_days() -> None:
    with pytest.raises(ValueError, match="60 calendar days"):
        await YahooFinanceHistoryClient(loader=lambda **_: None).fetch_history(
            "RELIANCE",
            exchange="NSE",
            from_date=date(2026, 1, 1),
            to_date=date(2026, 4, 1),
            interval="1m",
        )


def test_resolve_date_range_supports_rolling_refresh() -> None:
    assert resolve_date_range(
        from_date=None,
        to_date=None,
        lookback_days=10,
        today=date(2026, 9, 5),
    ) == (date(2026, 8, 26), date(2026, 9, 5))


def test_resolve_date_range_rejects_unbounded_lookback() -> None:
    with pytest.raises(ValueError, match="between 1 and 365"):
        resolve_date_range(
            from_date=None,
            to_date=date(2026, 9, 5),
            lookback_days=366,
        )
