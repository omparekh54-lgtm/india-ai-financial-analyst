from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.connectors.yahoo_finance import (
    YahooFinanceDataError,
    YahooFinanceHistoryClient,
    parse_history_frame,
    yahoo_symbol,
)
from app.ingestion.market import MarketBarIngestor, MarketBarInput
from scripts.backfill_yfinance_market_history import (
    HistoryTarget,
    previous_completed_day,
    refresh_end_day,
    refresh_start_day,
    resolve_date_range,
)


def test_yahoo_symbol_maps_indian_exchanges() -> None:
    assert yahoo_symbol("RELIANCE", "NSE") == "RELIANCE.NS"
    assert yahoo_symbol("500325", "BSE") == "500325.BO"
    assert yahoo_symbol("INFY.NS", "NSE") == "INFY.NS"


def test_daily_refresh_only_includes_today_after_six_pm_india() -> None:
    tz = ZoneInfo("Asia/Kolkata")
    assert refresh_end_day(now=datetime(2026, 9, 7, 17, 59, tzinfo=tz)) == date(2026, 9, 6)
    assert refresh_end_day(now=datetime(2026, 9, 7, 18, 0, tzinfo=tz)) == date(2026, 9, 7)


def test_daily_refresh_starts_from_listing_until_checkpointed() -> None:
    target = HistoryTarget(uuid4(), "TEST", "Test", "NSE", date(2001, 1, 1))
    assert refresh_start_day(target) == date(2001, 1, 1)
    failed = HistoryTarget(
        target.security_id,
        "TEST",
        "Test",
        "NSE",
        target.listing_date,
        {"status": "failed_or_unavailable", "last_bar_date": "2026-09-04"},
    )
    assert refresh_start_day(failed) == date(2001, 1, 1)


def test_daily_refresh_rechecks_overlap_after_successful_initial_import() -> None:
    target = HistoryTarget(
        uuid4(),
        "TEST",
        "Test",
        "NSE",
        date(2001, 1, 1),
        {"initial_history_imported": True, "last_bar_date": "2026-09-04"},
    )
    assert refresh_start_day(target) == date(2026, 8, 28)


@pytest.mark.asyncio
async def test_history_bulk_write_preserves_every_bar_and_source() -> None:
    connection = AsyncMock()
    engine = MagicMock()
    engine.begin.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    security_id, source_id = uuid4(), uuid4()
    bars = [
        MarketBarInput(
            ts=datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=index),
            open=100,
            high=110,
            low=90,
            close=105,
            volume=1000,
            provider="yfinance",
        )
        for index in range(1001)
    ]
    result = await MarketBarIngestor(engine).ingest_security_bars(
        security_id=security_id,
        bars=bars,
        source_id=source_id,
    )
    batches = [call.args[1] for call in connection.execute.await_args_list]
    assert [len(batch) for batch in batches] == [500, 500, 1]
    rows = [row for batch in batches for row in batch]
    assert [row["ts"] for row in rows] == [bar.ts for bar in bars]
    assert all(row["source_id"] == source_id and row["security_id"] == security_id for row in rows)
    assert result["normalized_count"] == 1001


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


def test_previous_completed_day_uses_india_calendar_date() -> None:
    assert previous_completed_day(
        now=datetime(2026, 9, 2, 0, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
    ) == date(2026, 9, 1)
    assert previous_completed_day(now=datetime(2026, 9, 1, 20, 0, tzinfo=UTC)) == date(2026, 9, 1)


def test_previous_completed_day_rejects_naive_clock() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        previous_completed_day(now=datetime(2026, 9, 2, 12, 0))  # noqa: DTZ001
