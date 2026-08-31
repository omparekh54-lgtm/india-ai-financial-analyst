from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings, get_settings
from app.core.research_gate import ResearchCorpusNotReadyError, enforce_research_corpus_ready
from app.orchestration.events import classify_corporate_event
from app.orchestration.plan import AnalysisMode, ResearchDepth
from app.research.service import ResearchService


class EventResearchDispatcher:
    """Translate material normalized events into idempotent selective research jobs."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        enabled: bool = False,
        settings: Settings | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings or get_settings()
        self.enabled = enabled
        self.service = ResearchService(
            engine,
            max_concurrency=self.settings.max_agent_concurrency,
            settings=self.settings,
        )

    async def dispatch_corporate_event(
        self,
        *,
        event_id: UUID,
        security_id: UUID,
        event_type: str,
        query: str,
        headline: str | None = None,
        published_at: str | None = None,
    ) -> dict[str, object]:
        if not self.enabled:
            return {"status": "skipped", "reason": "event_research_disabled"}

        trigger = classify_corporate_event(event_type)
        if trigger is None:
            return {"status": "skipped", "reason": "event_type_not_materially_mapped"}

        if await self._already_enqueued(event_id):
            return {"status": "duplicate", "event_id": str(event_id)}

        try:
            # Background research must obey the production data contract even in non-production
            # runtimes; this prevents a development worker from manufacturing empty snapshots.
            await enforce_research_corpus_ready(
                self.engine,
                app_env="production",
                settings=self.settings,
            )
        except ResearchCorpusNotReadyError as exc:
            return {
                "status": "blocked",
                "reason": "research_corpus_not_ready",
                "blocking_agents": list(exc.blocking_agents),
            }

        event_context = {
            "event_id": str(event_id),
            "security_id": str(security_id),
            "event_type": event_type,
            "headline": headline,
            "published_at": published_at,
        }
        try:
            job_id = await self.service.enqueue(
                query=query,
                mode=AnalysisMode.WHAT_CHANGED,
                depth=ResearchDepth.STANDARD,
                requested_by=None,
                metadata={
                    "system_generated": True,
                    "event_trigger": trigger.value,
                    "source_event_id": str(event_id),
                    "source_security_id": str(security_id),
                    "event_context": event_context,
                },
            )
        except IntegrityError:
            # The unique expression index is the final race-safe idempotency boundary.
            return {"status": "duplicate", "event_id": str(event_id)}
        return {
            "status": "queued",
            "job_id": str(job_id),
            "event_id": str(event_id),
            "trigger": trigger.value,
        }

    async def _already_enqueued(self, event_id: UUID) -> bool:
        statement = text(
            """
            select exists(
              select 1
              from research_jobs
              where metadata->>'source_event_id' = :event_id
                and metadata->>'system_generated' = 'true'
            )
            """
        )
        async with self.engine.connect() as connection:
            return bool(await connection.scalar(statement, {"event_id": str(event_id)}))
