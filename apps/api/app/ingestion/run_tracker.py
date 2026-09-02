from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class IngestionRunTracker:
    """Tracks ingestion state without coupling a pipeline to a specific scheduler."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def start(self, pipeline: str, *, scope: str | None = None) -> UUID:
        statement = text(
            """
            insert into ingestion_runs (pipeline, scope, status, started_at)
            values (:pipeline, :scope, 'running', :started_at)
            returning id
            """
        )
        async with self.engine.begin() as connection:
            result = await connection.execute(
                statement,
                {
                    "pipeline": pipeline,
                    "scope": scope,
                    "started_at": datetime.now(UTC),
                },
            )
            return result.scalar_one()

    async def finish(
        self,
        run_id: UUID,
        *,
        status: str = "completed",
        stats: dict[str, object] | None = None,
        checkpoint: dict[str, object] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if status not in {"completed", "partial", "failed"}:
            raise ValueError("finish status must be completed, partial, or failed")
        statement = text(
            """
            update ingestion_runs
            set status = :status,
                completed_at = :completed_at,
                stats = cast(:stats as jsonb),
                checkpoint = cast(:checkpoint as jsonb),
                error_code = :error_code,
                error_message = :error_message
            where id = :run_id
            """
        )
        async with self.engine.begin() as connection:
            await connection.execute(
                statement,
                {
                    "run_id": run_id,
                    "status": status,
                    "completed_at": datetime.now(UTC),
                    "stats": json.dumps(stats or {}),
                    "checkpoint": json.dumps(checkpoint or {}),
                    "error_code": error_code,
                    "error_message": error_message,
                },
            )
