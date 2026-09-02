from datetime import UTC, datetime

import httpx
import pytest

from app.market.upstox import UpstoxMarketDataAdapter, UpstoxMarketDataError


@pytest.mark.asyncio
async def test_upstox_quote_maps_exchange_snapshot_and_depth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/market-quote/quotes"
        assert request.url.params["instrument_key"] == "NSE_EQ|INE848E01016"
        assert request.headers["authorization"] == "Bearer test-token"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "NSE_EQ:NHPC": {
                        "ohlc": {"open": 53.4, "high": 53.8, "low": 51.75, "close": 52.05},
                        "depth": {
                            "buy": [{"quantity": 6917, "price": 52.05, "orders": 20}],
                            "sell": [{"quantity": 1200, "price": 52.10, "orders": 8}],
                        },
                        "timestamp": "2026-08-30T15:29:59.099+05:30",
                        "instrument_token": "NSE_EQ|INE848E01016",
                        "symbol": "NHPC",
                        "last_price": 52.05,
                        "volume": 24123697,
                        "average_price": 52.56,
                        "net_change": -1.05,
                    }
                },
            },
        )

    adapter = UpstoxMarketDataAdapter(
        "test-token",
        transport=httpx.MockTransport(handler),
    )
    quote = await adapter.quote("NSE_EQ|INE848E01016")

    assert quote.provider == "upstox"
    assert quote.exchange == "NSE"
    assert quote.symbol == "NHPC"
    assert quote.last_price == 52.05
    assert quote.bid == 52.05
    assert quote.ask == 52.10
    assert quote.volume == 24123697
    assert quote.timestamp.utcoffset() is not None
    assert quote.is_delayed is False
    assert quote.metadata["exchange_snapshot"] is True


@pytest.mark.asyncio
async def test_upstox_history_uses_v3_and_sorts_candles() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == (
            "/v3/historical-candle/NSE_EQ|INE848E01016/minutes/15/2026-08-30/2026-08-29"
        )
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "candles": [
                        ["2026-08-30T09:30:00+05:30", 101, 103, 100, 102, 2000, 0],
                        ["2026-08-30T09:15:00+05:30", 99, 102, 98, 101, 1500, 0],
                    ]
                },
            },
        )

    adapter = UpstoxMarketDataAdapter(
        "test-token",
        transport=httpx.MockTransport(handler),
    )
    bars = await adapter.history(
        "NSE_EQ|INE848E01016",
        interval="15m",
        start=datetime(2026, 8, 29, tzinfo=UTC),
        end=datetime(2026, 8, 30, tzinfo=UTC),
    )

    assert len(bars) == 2
    assert bars[0].close == 101
    assert bars[1].close == 102
    assert bars[0].interval == "15m"
    assert bars[0].provider == "upstox"
    assert bars[0].exchange == "NSE"


@pytest.mark.asyncio
async def test_upstox_rejects_expired_token_response() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"status": "error"})

    adapter = UpstoxMarketDataAdapter(
        "expired-token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(UpstoxMarketDataError, match="invalid or expired"):
        await adapter.quote("NSE_EQ|INE848E01016")


@pytest.mark.asyncio
async def test_upstox_rejects_unsupported_history_interval() -> None:
    adapter = UpstoxMarketDataAdapter("test-token")

    with pytest.raises(ValueError, match="Unsupported Upstox interval"):
        await adapter.history(
            "NSE_EQ|INE848E01016",
            interval="6h",
            start=datetime(2026, 8, 29, tzinfo=UTC),
            end=datetime(2026, 8, 30, tzinfo=UTC),
        )
