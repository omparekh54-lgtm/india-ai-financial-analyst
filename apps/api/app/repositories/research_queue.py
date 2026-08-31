from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class ResearchQueueRepository:
    """Postgres-backed durable research queue with SKIP LOCKED worker claiming."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def claim_next(self) -> dict[str, Any] | None:
        statement = text(
            """
            with next_job as (
              select id
              from research_jobs
              where status = 'queued'
              order by created_at
              for update skip locked
              limit 1
            )
            update research_jobs job
            set status = 'running',
                started_at = coalesce(job.started_at, :now)
            from next_job
            where job.id = next_job.id
            returning job.id, job.query, job.mode, job.requested_by, job.metadata,
                      job.security_id, job.created_at, job.started_at
            """
        )
        async with self.engine.begin() as connection:
            row = (
                await connection.execute(statement, {"now": datetime.now(UTC)})
            ).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def requeue_stale_running_jobs(self, *, older_than_seconds: int) -> int:
        statement = text(
            """
            update research_jobs
            set status = 'queued', started_at = null
            where status = 'running'
              and started_at < now() - make_interval(secs => :seconds)
              and completed_at is null
            """
        )
        async with self.engine.begin() as connection:
            result = await connection.execute(
                statement,
                {"seconds": max(60, int(older_than_seconds))},
            )
        return int(result.rowcount or 0)

    async def mark_failed(self, job_id: UUID, *, error_type: str) -> None:
        statement = text(
            """
            update research_jobs
            set status = 'failed',
                completed_at = :now,
                metadata = coalesce(metadata, '{}'::jsonb)
                  || jsonb_build_object('worker_error_type', :error_type)
            where id = :job_id
            """
        )
        async with self.engine.begin() as connection:
            await connection.execute(
                statement,
                {
                    "job_id": job_id,
                    "now": datetime.now(UTC),
                    "error_type": error_type,
                },
            )
