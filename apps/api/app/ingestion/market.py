from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class MarketBarInput:
    ts: datetime
    open: float | Decimal
    high: float | Decimal
    low: float | Decimal
    close: float | Decimal
    volume: float | Decimal | None
    provider: str
    interval: str = "1d"
    is_adjusted: bool = False


class MarketBarIngestor:
    """Provider-agnostic OHLCV write path for securities and Indian benchmarks."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def ingest_security_bars(
        self,
        *,
        security_id: UUID,
        bars: list[MarketBarInput],
    ) -> dict[str, int]:
        normalized = [normalize_market_bar(bar) for bar in bars]
        async with self.engine.begin() as connection:
            for bar in normalized:
                await connection.execute(
                    text(
                        """
                        insert into market_bars (
                            security_id, interval, ts, open, high, low, close,
                            volume, provider, is_adjusted
                        ) values (
                            :security_id, :interval, :ts, :open, :high, :low, :close,
                            :volume, :provider, :is_adjusted
                        )
                        on conflict (security_id, interval, ts, provider)
                        do update set
                            open = excluded.open,
                            high = excluded.high,
                            low = excluded.low,
                            close = excluded.close,
                            volume = excluded.volume,
                            is_adjusted = excluded.is_adjusted
                        """
                    ),
                    {"security_id": security_id, **_bar_parameters(bar)},
                )
        return {"input_count": len(bars), "normalized_count": len(normalized)}

    async def ingest_benchmark_bars(
        self,
        *,
        benchmark_code: str,
        bars: list[MarketBarInput],
        source_id: UUID | None = None,
    ) -> dict[str, int]:
        normalized = [normalize_market_bar(bar) for bar in bars]
        async with self.engine.begin() as connection:
            benchmark_id = await connection.scalar(
                text("select id from benchmarks where code = :code"),
                {"code": benchmark_code.strip().upper()},
            )
            if benchmark_id is None:
                raise ValueError(f"unknown benchmark code: {benchmark_code}")
            for bar in normalized:
                await connection.execute(
                    text(
                        """
                        insert into benchmark_bars (
                            benchmark_id, interval, ts, open, high, low, close,
                            volume, provider, is_adjusted, source_id
                        ) values (
                            :benchmark_id, :interval, :ts, :open, :high, :low, :close,
                            :volume, :provider, :is_adjusted, :source_id
                        )
                        on conflict (benchmark_id, interval, ts, provider)
                        do update set
                            open = excluded.open,
                            high = excluded.high,
                            low = excluded.low,
                            close = excluded.close,
                            volume = excluded.volume,
                            is_adjusted = excluded.is_adjusted,
                            source_id = coalesce(excluded.source_id, benchmark_bars.source_id)
                        """
                    ),
                    {
                        "benchmark_id": benchmark_id,
                        "source_id": source_id,
                        **_bar_parameters(bar),
                    },
                )
        return {"input_count": len(bars), "normalized_count": len(normalized)}


def normalize_market_bar(bar: MarketBarInput) -> MarketBarInput:
    provider = bar.provider.strip().lower()
    interval = bar.interval.strip().lower()
    if not provider:
        raise ValueError("market-data provider cannot be empty")
    if not interval:
        raise ValueError("market-data interval cannot be empty")

    ts = bar.ts
    if ts.tzinfo is None:
        raise ValueError("market bar timestamp must be timezone-aware")
    ts = ts.astimezone(UTC)

    open_value = float(bar.open)
    high_value = float(bar.high)
    low_value = float(bar.low)
    close_value = float(bar.close)
    volume_value = None if bar.volume is None else float(bar.volume)

    if min(open_value, high_value, low_value, close_value) < 0:
        raise ValueError("OHLC values cannot be negative")
    if high_value < max(open_value, low_value, close_value):
        raise ValueError("high must be greater than or equal to open, low and close")
    if low_value > min(open_value, high_value, close_value):
        raise ValueError("low must be less than or equal to open, high and close")
    if volume_value is not None and volume_value < 0:
        raise ValueError("volume cannot be negative")

    return MarketBarInput(
        ts=ts,
        open=open_value,
        high=high_value,
        low=low_value,
        close=close_value,
        volume=volume_value,
        provider=provider,
        interval=interval,
        is_adjusted=bar.is_adjusted,
    )


def _bar_parameters(bar: MarketBarInput) -> dict[str, object]:
    return {
        "interval": bar.interval,
        "ts": bar.ts,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "provider": bar.provider,
        "is_adjusted": bar.is_adjusted,
    }
