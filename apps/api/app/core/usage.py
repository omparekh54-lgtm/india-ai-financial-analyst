from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.orchestration.plan import ResearchDepth


class ResearchUsageLimitError(RuntimeError):
    def __init__(self, *, code: str, limit: int, used: int) -> None:
        super().__init__(f"{code}: {used}/{limit}")
        self.code = code
        self.limit = limit
        self.used = used


@dataclass(frozen=True)
class UsageReservation:
    usage_date: str
    research_jobs: int
    deep_research_jobs: int
    event_research_jobs: int


class ResearchUsageGate:
    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings

    async def reserve(
        self,
        user_id: UUID,
        *,
        depth: ResearchDepth,
        event_generated: bool = False,
    ) -> UsageReservation | None:
        if not self.settings.enable_usage_limits:
            return None
        today = datetime.now(UTC).date()
        is_deep = depth == ResearchDepth.DEEP
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    insert into user_usage_daily (user_id, usage_date)
                    values (:user_id, :usage_date)
                    on conflict (user_id, usage_date) do nothing
                    """
                ),
                {"user_id": user_id, "usage_date": today},
            )
            row = (
                await connection.execute(
                    text(
                        """
                        select research_jobs, deep_research_jobs, event_research_jobs
                        from user_usage_daily
                        where user_id=:user_id and usage_date=:usage_date
                        for update
                        """
                    ),
                    {"user_id": user_id, "usage_date": today},
                )
            ).mappings().one()
            research_used = int(row["research_jobs"])
            deep_used = int(row["deep_research_jobs"])
            event_used = int(row["event_research_jobs"])
            if research_used >= self.settings.daily_research_job_limit:
                raise ResearchUsageLimitError(
                    code="daily_research_job_limit_reached",
                    limit=self.settings.daily_research_job_limit,
                    used=research_used,
                )
            if is_deep and deep_used >= self.settings.daily_deep_research_job_limit:
                raise ResearchUsageLimitError(
                    code="daily_deep_research_job_limit_reached",
                    limit=self.settings.daily_deep_research_job_limit,
                    used=deep_used,
                )
            if event_generated and event_used >= self.settings.daily_event_research_job_limit:
                raise ResearchUsageLimitError(
                    code="daily_event_research_job_limit_reached",
                    limit=self.settings.daily_event_research_job_limit,
                    used=event_used,
                )
            updated = (
                await connection.execute(
                    text(
                        """
                        update user_usage_daily
                        set research_jobs=research_jobs+1,
                            deep_research_jobs=deep_research_jobs+:deep_increment,
                            event_research_jobs=event_research_jobs+:event_increment,
                            updated_at=now()
                        where user_id=:user_id and usage_date=:usage_date
                        returning usage_date, research_jobs, deep_research_jobs, event_research_jobs
                        """
                    ),
                    {
                        "user_id": user_id,
                        "usage_date": today,
                        "deep_increment": 1 if is_deep else 0,
                        "event_increment": 1 if event_generated else 0,
                    },
                )
            ).mappings().one()
        return UsageReservation(
            usage_date=str(updated["usage_date"]),
            research_jobs=int(updated["research_jobs"]),
            deep_research_jobs=int(updated["deep_research_jobs"]),
            event_research_jobs=int(updated["event_research_jobs"]),
        )

    async def release(
        self,
        user_id: UUID,
        *,
        depth: ResearchDepth,
        event_generated: bool = False,
    ) -> None:
        if not self.settings.enable_usage_limits:
            return
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    update user_usage_daily
                    set research_jobs=greatest(research_jobs-1, 0),
                        deep_research_jobs=greatest(deep_research_jobs-:deep_increment, 0),
                        event_research_jobs=greatest(event_research_jobs-:event_increment, 0),
                        updated_at=now()
                    where user_id=:user_id and usage_date=:usage_date
                    """
                ),
                {
                    "user_id": user_id,
                    "usage_date": datetime.now(UTC).date(),
                    "deep_increment": 1 if depth == ResearchDepth.DEEP else 0,
                    "event_increment": 1 if event_generated else 0,
                },
            )

    async def status(self, user_id: UUID) -> dict[str, object]:
        today = datetime.now(UTC).date()
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        """
                        select research_jobs, deep_research_jobs, event_research_jobs
                        from user_usage_daily
                        where user_id=:user_id and usage_date=:usage_date
                        """
                    ),
                    {"user_id": user_id, "usage_date": today},
                )
            ).mappings().one_or_none()
        values: Mapping[str, Any] = dict(row) if row is not None else {}
        return {
            "usage_date": today.isoformat(),
            "limits_enabled": self.settings.enable_usage_limits,
            "research_jobs": int(values.get("research_jobs", 0)),
            "research_job_limit": self.settings.daily_research_job_limit,
            "deep_research_jobs": int(values.get("deep_research_jobs", 0)),
            "deep_research_job_limit": self.settings.daily_deep_research_job_limit,
            "event_research_jobs": int(values.get("event_research_jobs", 0)),
            "event_research_job_limit": self.settings.daily_event_research_job_limit,
        }
