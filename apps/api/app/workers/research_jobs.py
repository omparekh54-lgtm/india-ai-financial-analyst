from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

import sentry_sdk
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings, get_settings
from app.orchestration.plan import (
    AnalysisMode,
    EventTrigger,
    ResearchDepth,
    build_event_research_plan,
)
from app.repositories.research_queue import ResearchQueueRepository
from app.research.service import ResearchService


class ResearchJobWorker:
    """Claims durable jobs and executes them outside the HTTP request lifecycle."""

    def __init__(self, engine: AsyncEngine, settings: Settings | None = None) -> None:
        self.engine = engine
        self.settings = settings or get_settings()
        self.queue = ResearchQueueRepository(engine)
        self.service = ResearchService(
            engine,
            max_concurrency=self.settings.max_agent_concurrency,
            settings=self.settings,
        )

    async def poll_once(self) -> bool:
        row = await self.queue.claim_next()
        if row is None:
            return False

        job_id = _uuid(row.get("id"))
        if job_id is None:
            return False

        try:
            mode = AnalysisMode(str(row.get("mode") or AnalysisMode.FULL.value))
            raw_metadata = row.get("metadata")
            metadata: dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
            depth = ResearchDepth(
                str(metadata.get("analysis_depth") or ResearchDepth.STANDARD.value)
            )
            requested_by = _uuid(row.get("requested_by"))
            event_trigger = _event_trigger(metadata.get("event_trigger"))
            plan = build_event_research_plan(event_trigger, depth) if event_trigger else None
            event_context = metadata.get("event_context")
            context: dict[str, Any] = dict(event_context) if isinstance(event_context, dict) else {}
            if event_trigger is not None:
                context["event_trigger"] = event_trigger.value

            await self.service.execute_existing(
                job_id=job_id,
                query=str(row.get("query") or ""),
                mode=mode,
                depth=depth,
                context=context,
                requested_by=requested_by,
                plan=plan,
            )
        except Exception as exc:  # noqa: BLE001 - isolate one durable job from the worker loop
            await self.queue.mark_failed(job_id, error_type=type(exc).__name__)
        return True

    async def run_forever(self) -> None:
        failures = 0
        needs_recovery = True
        while True:
            try:
                if needs_recovery:
                    await self.queue.requeue_stale_running_jobs(
                        older_than_seconds=max(300, self.settings.max_research_job_seconds * 2)
                    )
                    needs_recovery = False
                worked = await self.poll_once()
                failures = 0
            except SQLAlchemyError:
                failures += 1
                needs_recovery = True
                # Database exception strings can contain SQL parameters and user queries.
                logging.getLogger(__name__).error("Research worker database unavailable")
                if failures == 1:
                    sentry_sdk.capture_message(
                        "Research worker database unavailable", level="error"
                    )
                if failures >= 5:
                    sentry_sdk.flush(timeout=2)
                    raise RuntimeError(
                        "Research worker stopped after five database failures"
                    ) from None
                await asyncio.sleep(min(60, 5 * 2 ** (failures - 1)))
                continue
            if not worked:
                await asyncio.sleep(self.settings.research_worker_poll_seconds)


def _uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _event_trigger(value: object) -> EventTrigger | None:
    if value is None:
        return None
    try:
        return EventTrigger(str(value))
    except ValueError:
        return None
