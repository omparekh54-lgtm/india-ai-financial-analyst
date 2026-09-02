from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings, get_settings
from app.core.research_gate import ResearchCorpusNotReadyError, enforce_research_corpus_ready
from app.core.usage import ResearchUsageLimitError
from app.orchestration.events import classify_corporate_event
from app.orchestration.plan import AnalysisMode, ResearchDepth
from app.repositories.watchlists import WatchlistRepository
from app.research.service import ResearchService


class EventResearchDispatcher:
    """Translate material events into private, idempotent watchlist research jobs."""

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
        self.watchlists = WatchlistRepository(engine)
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

        subscribers = await self.watchlists.event_subscribers(security_id)
        if not subscribers:
            return {
                "status": "skipped",
                "reason": "no_watchlist_subscribers",
                "event_id": str(event_id),
            }

        try:
            # Watchlist automation never bypasses the same full production corpus gate as a
            # user-initiated report. This prevents empty or synthetic background research.
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
        queued: list[dict[str, str]] = []
        duplicate_users: list[str] = []
        quota_limited_users: list[dict[str, object]] = []
        for user_id in subscribers:
            if await self._already_enqueued(event_id, user_id):
                duplicate_users.append(str(user_id))
                continue
            try:
                job_id = await self.service.enqueue(
                    query=query,
                    mode=AnalysisMode.WHAT_CHANGED,
                    depth=ResearchDepth.STANDARD,
                    requested_by=user_id,
                    metadata={
                        "system_generated": True,
                        "watchlist_trigger": True,
                        "event_trigger": trigger.value,
                        "source_event_id": str(event_id),
                        "source_security_id": str(security_id),
                        "event_context": event_context,
                    },
                )
            except ResearchUsageLimitError as exc:
                # One subscriber exhausting a quota must never suppress another subscriber's
                # independently authorized event research job.
                quota_limited_users.append(
                    {
                        "user_id": str(user_id),
                        "code": exc.code,
                        "used": exc.used,
                        "limit": exc.limit,
                    }
                )
                continue
            except IntegrityError:
                # The per-event/per-owner unique expression index is the race-safe boundary.
                duplicate_users.append(str(user_id))
                continue
            queued.append({"user_id": str(user_id), "job_id": str(job_id)})

        if queued:
            return {
                "status": "queued",
                "event_id": str(event_id),
                "trigger": trigger.value,
                "subscriber_count": len(subscribers),
                "queued_count": len(queued),
                "duplicate_count": len(duplicate_users),
                "quota_limited_count": len(quota_limited_users),
                "quota_limited": quota_limited_users,
                "jobs": queued,
            }
        if quota_limited_users:
            return {
                "status": "skipped",
                "reason": "subscriber_usage_limits_reached",
                "event_id": str(event_id),
                "subscriber_count": len(subscribers),
                "duplicate_count": len(duplicate_users),
                "quota_limited_count": len(quota_limited_users),
                "quota_limited": quota_limited_users,
            }
        return {
            "status": "duplicate",
            "event_id": str(event_id),
            "subscriber_count": len(subscribers),
            "duplicate_count": len(duplicate_users),
            "quota_limited_count": 0,
        }

    async def _already_enqueued(self, event_id: UUID, user_id: UUID) -> bool:
        statement = text(
            """
            select exists(
              select 1
              from research_jobs
              where metadata->>'source_event_id' = :event_id
                and metadata->>'system_generated' = 'true'
                and requested_by = :user_id
            )
            """
        )
        async with self.engine.connect() as connection:
            return bool(
                await connection.scalar(
                    statement,
                    {"event_id": str(event_id), "user_id": user_id},
                )
            )
