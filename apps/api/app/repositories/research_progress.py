from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class ResearchProgressRepository:
    """Persist user-visible research state without changing the durable queue status model."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def set_stage(self, job_id: UUID, stage: str, progress_pct: int) -> None:
        normalized_stage = stage.strip().lower()
        progress = max(0, min(int(progress_pct), 100))
        statement = text(
            """
            update research_jobs
            set metadata = coalesce(metadata, '{}'::jsonb)
                || jsonb_build_object(
                    'research_stage', :stage,
                    'progress_pct', :progress,
                    'stage_updated_at', :updated_at
                )
            where id = :job_id
            """
        )
        async with self.engine.begin() as connection:
            await connection.execute(
                statement,
                {
                    "job_id": job_id,
                    "stage": normalized_stage,
                    "progress": progress,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )
