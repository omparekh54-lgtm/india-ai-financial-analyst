from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.contracts import EvidenceRef
from app.brokers.repository import BrokerRepository
from app.brokers.upstox_oauth import UpstoxOAuthService
from app.core.config import Settings
from app.market.upstox import UpstoxMarketDataAdapter

Row = Mapping[str, Any]
_LIVE_STALE_AFTER_SECONDS = 300


class LiveMarketOverlayService:
    """Adds user-authorized live market context without making research depend on the broker."""

    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings
        self.repository = BrokerRepository(engine)
        self.oauth = UpstoxOAuthService(self.repository, settings)

    async def apply(
        self,
        *,
        user_id: UUID | None,
        security_id: UUID,
        security: Row,
        context: dict[str, object],
        evidence: list[EvidenceRef],
    ) -> tuple[dict[str, object], list[EvidenceRef]]:
        if not self.settings.enable_live_market:
            return context, evidence
        if user_id is None:
            context["live_market_warning"] = "Live market requires an authenticated user"
            return context, evidence

        try:
            token = await self.oauth.access_token_for_user(user_id)
        except Exception as exc:  # noqa: BLE001 - broker failures must not abort research
            context["live_market_warning"] = (
                f"Upstox connection unavailable ({type(exc).__name__}); using stored market data"
            )
            return context, evidence
        if not token:
            context["live_market_warning"] = (
                "No active Upstox connection; using explicitly delayed stored market data"
            )
            return context, evidence

        instrument = await self.repository.provider_instrument(security_id, "upstox")
        instrument_key = str(instrument["instrument_id"]) if instrument else _fallback_instrument_key(security)
        if not instrument_key:
            context["live_market_warning"] = (
                "No Upstox instrument mapping for this security; using stored market data"
            )
            return context, evidence

        try:
            quote = await UpstoxMarketDataAdapter(token).quote(instrument_key)
        except Exception as exc:  # noqa: BLE001 - live provider is an optional overlay
            context["live_market_warning"] = (
                f"Upstox quote unavailable ({type(exc).__name__}); using stored market data"
            )
            return context, evidence

        now = datetime.now(UTC)
        quote_ts = quote.timestamp.astimezone(UTC)
        age_seconds = max(0.0, (now - quote_ts).total_seconds())
        stale_snapshot = quote.is_delayed or age_seconds > _LIVE_STALE_AFTER_SECONDS
        stored_quote = context.get("market_quote")
        stored = stored_quote if isinstance(stored_quote, dict) else {}
        ohlc = quote.metadata.get("ohlc") if isinstance(quote.metadata.get("ohlc"), dict) else {}
        previous_close = _number(ohlc.get("close")) or _number(stored.get("previous_close"))

        context["market_quote"] = {
            "price": quote.last_price,
            "previous_close": previous_close,
            "volume": quote.volume if quote.volume is not None else stored.get("volume"),
            "average_volume": stored.get("average_volume"),
            "provider": quote.provider,
            "is_delayed": stale_snapshot,
            "as_of": quote_ts.isoformat(),
            "bid": quote.bid,
            "ask": quote.ask,
            "exchange": quote.exchange,
            "symbol": quote.symbol,
            "snapshot_age_seconds": round(age_seconds, 3),
            "instrument_key": instrument_key,
            "source": "authenticated_broker",
        }
        context["live_market_status"] = {
            "provider": "upstox",
            "connected": True,
            "fresh": not stale_snapshot,
            "as_of": quote_ts.isoformat(),
        }
        if stale_snapshot:
            context["live_market_warning"] = (
                "Upstox returned an exchange snapshot older than five minutes; it is labeled delayed"
            )
        else:
            context.pop("live_market_warning", None)

        live_evidence = EvidenceRef(
            source_type="market_data",
            source_uri="https://api.upstox.com/v2/market-quote/quotes",
            title=f"Upstox exchange snapshot for {quote.symbol}",
            published_at=quote_ts.isoformat(),
            retrieved_at=now.isoformat(),
            freshness="near_live" if stale_snapshot else "live",
            excerpt=(
                f"Upstox exchange snapshot: {quote.symbol} last price {quote.last_price}; "
                f"volume {quote.volume}; bid {quote.bid}; ask {quote.ask}."
            ),
            source_priority=1,
        )
        return context, [*evidence, live_evidence]


def _fallback_instrument_key(security: Row) -> str | None:
    isin = str(security.get("isin") or "").strip().upper()
    exchange = str(security.get("primary_exchange") or "").strip().upper()
    if not isin or exchange not in {"NSE", "BSE"}:
        return None
    return f"{exchange}_EQ|{isin}"


def _number(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
