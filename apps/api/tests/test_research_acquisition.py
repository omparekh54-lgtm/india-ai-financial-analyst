from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.connectors.base import Freshness, SourceEnvelope
from app.core.config import Settings
from app.research.acquisition import FreshResearchAcquisitionService


class FakeTavily:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        topic: str = "general",
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> list[SourceEnvelope]:
        self.calls.append((query, topic))
        return [
            SourceEnvelope(
                source_type="web_search",
                source_uri=f"https://example.com/{len(self.calls)}",
                title="Example research",
                retrieved_at=datetime.now(UTC),
                freshness=Freshness.NEAR_LIVE,
                payload={"content": "Fresh evidence", "score": 0.8},
                metadata={"provider": "tavily", "query": query},
            )
        ]


class StubAcquisition(FreshResearchAcquisitionService):
    def __init__(self, settings: Settings, tavily: FakeTavily, cached: list[SourceEnvelope]) -> None:
        super().__init__(object(), settings, tavily=tavily)  # type: ignore[arg-type]
        self.cached = cached
        self.persisted: list[SourceEnvelope] = []

    async def _load_cached(self, security_id, *, cache_seconds: int):  # type: ignore[no-untyped-def]
        return self.cached

    async def _persist(self, security_id, envelopes: list[SourceEnvelope]):  # type: ignore[no-untyped-def]
        self.persisted.extend(envelopes)


def _cached_item(index: int) -> SourceEnvelope:
    return SourceEnvelope(
        source_type="web_search",
        source_uri=f"https://cached.example.com/{index}",
        title=f"Cached {index}",
        retrieved_at=datetime.now(UTC),
        freshness=Freshness.NEAR_LIVE,
        payload={"content": "Cached evidence"},
        metadata={"provider": "tavily", "category": "web"},
    )


@pytest.mark.asyncio
async def test_cache_hit_uses_zero_tavily_calls() -> None:
    settings = Settings(
        enable_external_data_calls=True,
        tavily_api_key="configured",
        web_research_max_results_per_search=5,
    )
    tavily = FakeTavily()
    service = StubAcquisition(settings, tavily, [_cached_item(index) for index in range(4)])

    context, evidence = await service.enrich(
        security_id=uuid4(),
        security={"legal_name": "Example Limited", "nse_symbol": "EXAMPLE"},
        mode="full_analysis",
        context={},
        evidence=[],
    )

    assert tavily.calls == []
    assert context["research_acquisition"]["status"] == "cache_hit"  # type: ignore[index]
    assert len(evidence) == 4


@pytest.mark.asyncio
async def test_cache_miss_never_exceeds_configured_search_limit() -> None:
    settings = Settings(
        enable_external_data_calls=True,
        tavily_api_key="configured",
        web_research_max_searches_per_job=2,
        web_research_max_results_per_search=5,
    )
    tavily = FakeTavily()
    service = StubAcquisition(settings, tavily, [])

    context, _evidence = await service.enrich(
        security_id=uuid4(),
        security={"legal_name": "Example Limited", "nse_symbol": "EXAMPLE"},
        mode="why_did_it_move",
        context={},
        evidence=[],
    )

    assert len(tavily.calls) == 2
    assert len(service.persisted) == 2
    assert context["research_acquisition"]["status"] == "fresh"  # type: ignore[index]
