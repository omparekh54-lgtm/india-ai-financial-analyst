from __future__ import annotations

from datetime import datetime

from app.market.contracts import MarketBar, MarketDataAdapter, MarketQuote


class MarketDataUnavailable(RuntimeError):
    pass


class MarketDataService:
    """Provider-failover market service.

    Broker adapters (FYERS/Angel One/Upstox) can be registered ahead of delayed fallbacks.
    The returned object always records its provider and delayed/live status.
    """

    def __init__(self, adapters: list[MarketDataAdapter]) -> None:
        self.adapters = adapters

    async def quote(self, instrument_ids: dict[str, str]) -> MarketQuote:
        errors: list[str] = []
        for adapter in self.adapters:
            instrument_id = instrument_ids.get(adapter.name)
            if not instrument_id:
                continue
            try:
                return await adapter.quote(instrument_id)
            except Exception as exc:
                errors.append(f"{adapter.name}: {type(exc).__name__}")
        raise MarketDataUnavailable("No market provider succeeded: " + "; ".join(errors))

    async def history(
        self,
        instrument_ids: dict[str, str],
        *,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[MarketBar]:
        errors: list[str] = []
        for adapter in self.adapters:
            instrument_id = instrument_ids.get(adapter.name)
            if not instrument_id:
                continue
            try:
                bars = await adapter.history(
                    instrument_id,
                    interval=interval,
                    start=start,
                    end=end,
                )
                if bars:
                    return bars
            except Exception as exc:
                errors.append(f"{adapter.name}: {type(exc).__name__}")
        raise MarketDataUnavailable("No history provider succeeded: " + "; ".join(errors))
