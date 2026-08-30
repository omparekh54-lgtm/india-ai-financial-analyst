from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.market.contracts import MarketQuote
from app.market.live_overlay import LiveMarketOverlayService, _fallback_instrument_key


class FakeRepository:
    async def provider_instrument(self, security_id, provider):  # noqa: ANN001, ANN201
        assert provider == "upstox"
        return {"instrument_id": "NSE_EQ|INE002A01018"}


class FakeOAuth:
    async def access_token_for_user(self, user_id):  # noqa: ANN001, ANN201
        return "runtime-token"


class FakeAdapter:
    def __init__(self, token: str) -> None:
        assert token == "runtime-token"

    async def quote(self, instrument_id: str) -> MarketQuote:
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


@pytest.mark.asyncio
async def test_live_overlay_replaces_only_current_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.market.live_overlay.UpstoxMarketDataAdapter", FakeAdapter)
    service = LiveMarketOverlayService(  # type: ignore[arg-type]
        None,
        Settings(enable_live_market=True),
    )
    service.repository = FakeRepository()  # type: ignore[assignment]
    service.oauth = FakeOAuth()  # type: ignore[assignment]

    context = {
        "market_quote": {
            "price": 2990.0,
            "previous_close": 2980.0,
            "average_volume": 10_000_000,
            "provider": "stored",
            "is_delayed": True,
        },
        "market_bars": [{"close": 2990.0}],
    }
    updated, evidence = await service.apply(
        user_id=uuid4(),
        security_id=uuid4(),
        security={
            "isin": "INE002A01018",
            "primary_exchange": "NSE",
            "nse_symbol": "RELIANCE",
        },
        context=context,
        evidence=[],
    )

    quote = updated["market_quote"]
    assert isinstance(quote, dict)
    assert quote["price"] == 3010.25
    assert quote["previous_close"] == 2988.50
    assert quote["average_volume"] == 10_000_000
    assert quote["provider"] == "upstox"
    assert quote["is_delayed"] is False
    assert updated["market_bars"] == [{"close": 2990.0}]
    assert evidence[-1].freshness == "live"
    assert "runtime-token" not in evidence[-1].excerpt


def test_equity_instrument_fallback_is_isin_based() -> None:
    assert _fallback_instrument_key(
        {"isin": "INE002A01018", "primary_exchange": "NSE"}
    ) == "NSE_EQ|INE002A01018"
    assert _fallback_instrument_key(
        {"isin": "INE002A01018", "primary_exchange": "UNKNOWN"}
    ) is None
