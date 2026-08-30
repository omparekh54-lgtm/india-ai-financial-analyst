from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.contracts import EvidenceRef
from app.connectors.base import SourceEnvelope
from app.connectors.tavily import TavilyConnector
from app.core.config import Settings


class FreshResearchAcquisitionService:
    """Adds bounded fresh web/news evidence with cache-first quota protection."""

    def __init__(
        self,
        engine: AsyncEngine,
        settings: Settings,
        *,
        tavily: TavilyConnector | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.tavily = tavily or TavilyConnector(settings)

    async def enrich(
        self,
        *,
        security_id: UUID,
        security: dict[str, object],
        mode: str,
        context: dict[str, object],
        evidence: list[EvidenceRef],
    ) -> tuple[dict[str, object], list[EvidenceRef]]:
        if not self.settings.enable_external_data_calls:
            context["research_acquisition"] = {"status": "disabled", "source_count": 0}
            return context, evidence
        if not self.settings.tavily_api_key:
            context["research_acquisition"] = {"status": "unconfigured", "source_count": 0}
            return context, evidence

        cache_seconds = self.settings.web_research_cache_seconds
        if mode == "why_did_it_move":
            cache_seconds = min(cache_seconds, 300)
        cached = await self._load_cached(security_id, cache_seconds=cache_seconds)
        minimum_cached = min(4, self.settings.web_research_max_results_per_search)
        if len(cached) >= minimum_cached:
            return self._apply(
                context=context,
                evidence=evidence,
                envelopes=cached,
                status="cache_hit",
            )

        plans = _search_plans(security, mode)[: self.settings.web_research_max_searches_per_job]
        try:
            batches = await asyncio.gather(
                *(
                    self.tavily.search(
                        query,
                        max_results=self.settings.web_research_max_results_per_search,
                        topic=topic,
                    )
                    for query, topic, _category in plans
                )
            )
        except (httpx.HTTPError, RuntimeError, ValueError):
            context["research_acquisition"] = {
                "status": "degraded",
                "source_count": len(cached),
            }
            if cached:
                return self._apply(
                    context=context,
                    evidence=evidence,
                    envelopes=cached,
                    status="degraded_cache",
                )
            return context, evidence

        category_by_query = {query: category for query, _topic, category in plans}
        fresh: list[SourceEnvelope] = []
        for batch in batches:
            for envelope in batch:
                query = str(envelope.metadata.get("query") or "")
                envelope.metadata["category"] = category_by_query.get(query, "web")
                fresh.append(envelope)

        merged = _dedupe_envelopes([*fresh, *cached])
        if fresh:
            await self._persist(security_id, fresh)
        return self._apply(
            context=context,
            evidence=evidence,
            envelopes=merged,
            status="fresh" if fresh else "empty",
        )

    async def _load_cached(
        self,
        security_id: UUID,
        *,
        cache_seconds: int,
    ) -> list[SourceEnvelope]:
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        """
                        select source_uri, title, published_at, retrieved_at, freshness, metadata
                        from sources
                        where security_id = :security_id
                          and source_type = 'web_search'
                          and retrieved_at >= now() - make_interval(secs => :cache_seconds)
                        order by retrieved_at desc
                        limit 20
                        """
                    ),
                    {"security_id": security_id, "cache_seconds": cache_seconds},
                )
            ).mappings().all()

        envelopes: list[SourceEnvelope] = []
        for row in rows:
            metadata = row["metadata"] if isinstance(row["metadata"], dict) else {}
            envelopes.append(
                SourceEnvelope(
                    source_type="web_search",
                    source_uri=str(row["source_uri"]),
                    title=row["title"],
                    published_at=row["published_at"],
                    retrieved_at=row["retrieved_at"],
                    freshness=_freshness(row["freshness"]),
                    payload={
                        "content": metadata.get("content"),
                        "score": metadata.get("score"),
                    },
                    metadata={
                        "provider": metadata.get("provider", "tavily"),
                        "query": metadata.get("query"),
                        "category": metadata.get("category", "web"),
                    },
                )
            )
        return envelopes

    async def _persist(self, security_id: UUID, envelopes: list[SourceEnvelope]) -> None:
        async with self.engine.begin() as connection:
            for item in envelopes:
                await connection.execute(
                    text(
                        """
                        insert into sources (
                            security_id, source_type, source_uri, title, published_at,
                            retrieved_at, freshness, metadata
                        ) values (
                            :security_id, 'web_search', :source_uri, :title, :published_at,
                            :retrieved_at, :freshness, cast(:metadata as jsonb)
                        )
                        on conflict do nothing
                        """
                    ),
                    {
                        "security_id": security_id,
                        "source_uri": item.source_uri,
                        "title": item.title,
                        "published_at": item.published_at,
                        "retrieved_at": item.retrieved_at,
                        "freshness": item.freshness.value,
                        "metadata": json.dumps(
                            {
                                "provider": item.metadata.get("provider", "tavily"),
                                "query": item.metadata.get("query"),
                                "category": item.metadata.get("category", "web"),
                                "content": item.payload.get("content"),
                                "score": item.payload.get("score"),
                            }
                        ),
                    },
                )

    def _apply(
        self,
        *,
        context: dict[str, object],
        evidence: list[EvidenceRef],
        envelopes: list[SourceEnvelope],
        status: str,
    ) -> tuple[dict[str, object], list[EvidenceRef]]:
        web_sources = list(context.get("web_sources") or [])
        news_events = list(context.get("news_events") or [])
        narratives = list(context.get("narratives") or [])
        acquired_evidence: list[EvidenceRef] = []

        for item in envelopes:
            category = str(item.metadata.get("category") or "web")
            content = str(item.payload.get("content") or "").strip()
            published = item.published_at.isoformat() if item.published_at else None
            retrieved = item.retrieved_at.isoformat()
            web_sources.append(
                {
                    "url": item.source_uri,
                    "title": item.title,
                    "published_at": published,
                    "retrieved_at": retrieved,
                    "freshness": item.freshness.value,
                    "content": content,
                    "source_type": _evidence_source_type(item.source_uri, category),
                }
            )
            if category == "news":
                news_events.append(
                    {
                        "title": item.title or "Market news",
                        "url": item.source_uri,
                        "published_at": published,
                        "source": "tavily",
                        "summary": content,
                    }
                )
            if content:
                narratives.append(content[:1200])

            acquired_evidence.append(
                EvidenceRef(
                    source_type=_evidence_source_type(item.source_uri, category),
                    source_uri=item.source_uri,
                    title=item.title,
                    published_at=published,
                    retrieved_at=retrieved,
                    freshness=item.freshness.value,
                    excerpt=content[:900] or None,
                    source_priority=1 if _is_primary_domain(item.source_uri) else 3,
                )
            )

        context["web_sources"] = _dedupe_dicts(web_sources, "url")
        context["news_events"] = _dedupe_dicts(news_events, "url")
        context["narratives"] = list(dict.fromkeys(str(value) for value in narratives if value))[:80]
        context["research_acquisition"] = {
            "status": status,
            "source_count": len(envelopes),
            "search_provider": "tavily",
        }
        return context, _dedupe_evidence([*evidence, *acquired_evidence])


def _search_plans(
    security: dict[str, object],
    mode: str,
) -> list[tuple[str, str, str]]:
    name = str(security.get("legal_name") or security.get("nse_symbol") or "Indian company")
    symbol = str(security.get("nse_symbol") or "").strip()
    label = f"{name} {symbol}".strip()
    news_query = (
        f"{label} latest India stock news results guidance order regulatory announcement NSE BSE"
    )
    if mode == "why_did_it_move":
        news_query = f"{label} today stock price move news announcement results order India"
    elif mode == "what_changed":
        news_query = f"{label} latest new filing announcement results guidance India"
    return [
        (news_query, "news", "news"),
        (
            f"{label} investor relations strategy management commentary annual report presentation competitors",
            "general",
            "web",
        ),
    ]


def _dedupe_envelopes(items: list[SourceEnvelope]) -> list[SourceEnvelope]:
    output: dict[str, SourceEnvelope] = {}
    for item in items:
        output.setdefault(item.source_uri, item)
    return list(output.values())[:20]


def _dedupe_dicts(items: list[object], key: str) -> list[object]:
    output: list[object] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        value = str(item.get(key) or "")
        if value and value in seen:
            continue
        if value:
            seen.add(value)
        output.append(item)
    return output


def _dedupe_evidence(items: list[EvidenceRef]) -> list[EvidenceRef]:
    output: list[EvidenceRef] = []
    seen: set[tuple[str, str, int | None]] = set()
    for item in items:
        key = (item.source_type, item.source_uri, item.page_number)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _is_primary_domain(uri: str) -> bool:
    host = (urlparse(uri).hostname or "").lower()
    return host.endswith(
        (
            "nseindia.com",
            "bseindia.com",
            "sebi.gov.in",
            "rbi.org.in",
            "nsdl.co.in",
        )
    )


def _evidence_source_type(uri: str, category: str) -> str:
    if _is_primary_domain(uri):
        return "official_web"
    return "news" if category == "news" else "web"


def _freshness(value: object):
    from app.connectors.base import Freshness

    candidate = str(value or "near_live")
    try:
        return Freshness(candidate)
    except ValueError:
        return Freshness.UNKNOWN
