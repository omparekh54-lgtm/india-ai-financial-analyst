from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.connectors.base import Freshness, SourceEnvelope
from app.core.config import Settings


class AlphaVantageConnector:
    """Fallback market-data connector, not the primary India live feed."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def daily_adjusted(self, symbol: str) -> SourceEnvelope:
        if not self.settings.enable_external_data_calls:
            raise RuntimeError("External data calls are disabled")
        if not self.settings.alpha_vantage_api_key:
            raise RuntimeError("Alpha Vantage is not configured")

        params = {
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": symbol,
            "outputsize": "compact",
            "apikey": self.settings.alpha_vantage_api_key,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(self.settings.alpha_vantage_base_url, params=params)
            response.raise_for_status()

        payload = response.json()
        if "Error Message" in payload or "Information" in payload:
            raise RuntimeError("Alpha Vantage returned an API error or quota message")

        return SourceEnvelope(
            source_type="market_data",
            source_uri="alpha_vantage:TIME_SERIES_DAILY_ADJUSTED",
            title=f"{symbol} daily adjusted prices",
            retrieved_at=datetime.now(UTC),
            freshness=Freshness.HISTORICAL,
            payload=payload,
            metadata={"provider": "alpha_vantage", "symbol": symbol},
        )
