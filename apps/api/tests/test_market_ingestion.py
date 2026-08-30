from datetime import UTC, datetime

import pytest

from app.ingestion.market import MarketBarInput, normalize_market_bar


def test_market_bar_normalizes_provider_and_timezone() -> None:
    bar = normalize_market_bar(
        MarketBarInput(
            ts=datetime(2026, 8, 28, 10, 0, tzinfo=UTC),
            open=100,
            high=105,
            low=98,
            close=103,
            volume=1_000_000,
            provider=" FYERS ",
        )
    )
    assert bar.provider == "fyers"
    assert bar.interval == "1d"
    assert bar.close == 103.0


def test_market_bar_rejects_inconsistent_ohlc() -> None:
    with pytest.raises(ValueError, match="high"):
        normalize_market_bar(
            MarketBarInput(
                ts=datetime(2026, 8, 28, 10, 0, tzinfo=UTC),
                open=100,
                high=101,
                low=98,
                close=103,
                volume=100,
                provider="test",
            )
        )
