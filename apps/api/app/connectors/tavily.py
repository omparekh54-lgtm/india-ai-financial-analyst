from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.connectors.base import Freshness, SourceEnvelope
from app.core.config import Settings


class TavilyConnector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        topic: str = "general",
    ) -> list[SourceEnvelope]:
        if not self.settings.enable_external_data_calls:
            raise RuntimeError("External data calls are disabled")
        if not self.settings.tavily_api_key:
            raise RuntimeError("Tavily is not configured")

        payload = {
            "query": query,
            "topic": topic,
            "search_depth": "basic",
            "max_results": max(1, min(max_results, 10)),
            "include_answer": False,
            "include_raw_content": False,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.tavily_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.settings.tavily_base_url.rstrip('/')}/search",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()

        body = response.json()
        retrieved_at = datetime.now(UTC)
        envelopes: list[SourceEnvelope] = []
        for result in body.get("results", []):
            url = result.get("url")
            if not url:
                continue
            envelopes.append(
                SourceEnvelope(
                    source_type="web_search",
                    source_uri=url,
                    title=result.get("title"),
                    retrieved_at=retrieved_at,
                    freshness=Freshness.NEAR_LIVE,
                    payload={
                        "content": result.get("content"),
                        "score": result.get("score"),
                    },
                    metadata={"provider": "tavily", "query": query},
                )
            )
        return envelopes
