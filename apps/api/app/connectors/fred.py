from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.connectors.base import Freshness, SourceEnvelope
from app.core.config import Settings


class FredConnector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def series_observations(self, series_id: str) -> SourceEnvelope:
        if not self.settings.enable_external_data_calls:
            raise RuntimeError("External data calls are disabled")
        if not self.settings.fred_api_key:
            raise RuntimeError("FRED is not configured")

        params = {
            "series_id": series_id,
            "api_key": self.settings.fred_api_key,
            "file_type": "json",
        }
        endpoint = f"{self.settings.fred_base_url}/series/observations"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, params=params)
            response.raise_for_status()

        return SourceEnvelope(
            source_type="macro_data",
            source_uri=f"fred:{series_id}",
            title=f"FRED series {series_id}",
            retrieved_at=datetime.now(UTC),
            freshness=Freshness.PERIODIC,
            payload=response.json(),
            metadata={"provider": "fred", "series_id": series_id},
        )
