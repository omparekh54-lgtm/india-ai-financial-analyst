from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field


class MarketQuote(BaseModel):
    symbol: str
    provider: str
    exchange: str
    last_price: float
    timestamp: datetime
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    is_delayed: bool = False
    metadata: dict[str, object] = Field(default_factory=dict)


class MarketBar(BaseModel):
    symbol: str
    provider: str
    exchange: str
    interval: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    is_adjusted: bool = False


class MarketDataAdapter(Protocol):
    name: str

    async def quote(self, instrument_id: str) -> MarketQuote: ...

    async def history(
        self,
        instrument_id: str,
        *,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[MarketBar]: ...
