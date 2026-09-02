from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.market.contracts import MarketQuote
from app.market.live_overlay import LiveMarketOverlayService, _fallback_instrument_key


class FakeRepository:
    def __init__(self, streamed_quote=None) -> None:
        self.streamed_quote = streamed_quote
        self.subscription_calls = 0

    async def provider_instrument(self, security_id, provider):
        assert provider == "upstox"
        return {"instrument_id": "NSE_EQ|INE002A01018"}

    async def ensure_live_subscription(self, **kwargs):
        assert kwargs["provider"] == "upstox"
        assert kwargs["mode"] == "ltpc"
        self.subscription_calls += 1

    async def fresh_live_quote(self, **kwargs):
        assert kwargs["provider"] == "upstox"
        return self.streamed_quote


class FakeOAuth:
    async def access_token_for_user(self, user_id):
        return "runtime-token"


class FakeAdapter:
    calls = 0

    def __init__(self, token: str) -> None:
        assert token == "runtime-token"

    async def quote(self, instrument_id: str) -> MarketQuote:
        type(self).calls += 1
        assert instrument_id == "NSE_EQ|INE002A01018"
        return MarketQuote(
            symbol="RELIANCE",
            provider="upstox",
            exchange="NSE",
            last_price=3010.25,
            timestamp=datetime.now(UTC),
            bid=3010.10,
            ask=3010.30,
            volume=12_500_000,
            is_delayed=False,
            metadata={"ohlc": {"close": 2988.50}},
        )


class RestMustNotRunAdapter:
    def __init__(self, token: str) -> None:
        assert token == "runtime-token"

    async def quote(self, instrument_id: str) -> MarketQuote:
        raise AssertionError("REST fallback must not run when a fresh streamed quote exists")


def _context() -> dict[str, object]:
    return {
        "market_quote": {
            "price": 2990.0,
            "previous_close": 2980.0,
            "average_volume": 10_000_000,
            "provider": "stored",
            "is_delayed": True,
        },
        "market_bars": [{"close": 2990.0}],
    }


def _security() -> dict[str, object]:
    return {
        "isin": "INE002A01018",
        "primary_exchange": "NSE",
        "nse_symbol": "RELIANCE",
    }


@pytest.mark.asyncio
async def test_live_overlay_prefers_fresh_stream_without_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.market.live_overlay.UpstoxMarketDataAdapter", RestMustNotRunAdapter)
    now = datetime.now(UTC)
    repository = FakeRepository(
        {
            "instrument_id": "NSE_EQ|INE002A01018",
            "last_price": 3021.50,
            "close_price": 2988.50,
            "last_trade_at": now,
            "received_at": now,
            "bid": 3021.40,
            "ask": 3021.60,
            "volume": 13_000_000,
            "market_status": "NORMAL_OPEN",
            "payload": {},
        }
    )
    service = LiveMarketOverlayService(  # type: ignore[arg-type]
        None,
        Settings(enable_live_market=True),
    )
    service.repository = repository  # type: ignore[assignment]
    service.oauth = FakeOAuth()  # type: ignore[assignment]

    updated, evidence = await service.apply(
        user_id=uuid4(),
        security_id=uuid4(),
        security=_security(),
        context=_context(),
        evidence=[],
    )

    quote = updated["market_quote"]
    assert isinstance(quote, dict)
    assert quote["price"] == 3021.50
    assert quote["previous_close"] == 2988.50
    assert quote["source"] == "authenticated_broker_stream"
    assert quote["is_delayed"] is False
    assert updated["live_market_status"]["transport"] == "websocket_v3"  # type: ignore[index]
    assert repository.subscription_calls == 1
    assert evidence[-1].freshness == "live"
    assert "runtime-token" not in evidence[-1].excerpt


@pytest.mark.asyncio
async def test_live_overlay_renews_subscription_then_falls_back_to_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAdapter.calls = 0
    monkeypatch.setattr("app.market.live_overlay.UpstoxMarketDataAdapter", FakeAdapter)
    repository = FakeRepository(streamed_quote=None)
    service = LiveMarketOverlayService(  # type: ignore[arg-type]
        None,
        Settings(enable_live_market=True),
    )
    service.repository = repository  # type: ignore[assignment]
    service.oauth = FakeOAuth()  # type: ignore[assignment]

    updated, evidence = await service.apply(
        user_id=uuid4(),
        security_id=uuid4(),
        security=_security(),
        context=_context(),
        evidence=[],
    )

    quote = updated["market_quote"]
    assert isinstance(quote, dict)
    assert quote["price"] == 3010.25
    assert quote["previous_close"] == 2988.50
    assert quote["average_volume"] == 10_000_000
    assert quote["provider"] == "upstox"
    assert quote["source"] == "authenticated_broker_rest"
    assert quote["is_delayed"] is False
    assert updated["market_bars"] == [{"close": 2990.0}]
    assert updated["live_market_status"]["transport"] == "rest_fallback"  # type: ignore[index]
    assert repository.subscription_calls == 1
    assert FakeAdapter.calls == 1
    assert evidence[-1].freshness == "live"
    assert "runtime-token" not in evidence[-1].excerpt


def test_equity_instrument_fallback_is_isin_based() -> None:
    assert _fallback_instrument_key(
        {"isin": "INE002A01018", "primary_exchange": "NSE"}
    ) == "NSE_EQ|INE002A01018"
    assert _fallback_instrument_key(
        {"isin": "INE002A01018", "primary_exchange": "UNKNOWN"}
    ) is None
