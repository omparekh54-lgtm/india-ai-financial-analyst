from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.contracts import AgentInput, AgentName, AgentOutput
from app.core.config import Settings, get_settings
from app.orchestration.plan import AnalysisMode, build_research_plan
from app.orchestration.registry import build_agent_registry
from app.orchestration.runtime import OrchestratorRuntime
from app.repositories.research import ResearchRepository
from app.research.live_context import UserAwareResearchContextLoader


@dataclass(frozen=True)
class ResearchExecution:
    job_id: UUID
    security_id: UUID | None
    report: dict[str, Any]
    outputs: list[AgentOutput]


class ResearchService:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        max_concurrency: int = 6,
        settings: Settings | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings or get_settings()
        self.repository = ResearchRepository(engine)
        self.runtime = OrchestratorRuntime(
            build_agent_registry(engine),
            max_concurrency=max_concurrency,
            context_loader=UserAwareResearchContextLoader(engine, self.settings),
        )

    async def execute(
        self,
        *,
        query: str,
        mode: AnalysisMode,
        context: dict[str, Any] | None = None,
        requested_by: UUID | None = None,
    ) -> ResearchExecution:
        job_id = await self.repository.create_job(
            query=query,
            mode=mode.value,
            requested_by=requested_by,
        )
        await self.repository.set_job_status(job_id, "running")

        try:
            outputs = await self.runtime.run(
                build_research_plan(mode),
                AgentInput(
                    job_id=job_id,
                    user_id=requested_by,
                    query=query,
                    context=dict(context or {}),
                ),
            )
            security_id = _resolved_security_id(outputs)
            if security_id is not None:
                await self._attach_security(job_id, security_id)

            for output in outputs:
                await self.repository.save_agent_output(job_id, output)

            synthesis = _latest_output(outputs, AgentName.SYNTHESIS)
            report: dict[str, Any] = {}
            if synthesis is not None:
                raw_report = synthesis.metrics.get("report") or {}
                if isinstance(raw_report, dict):
                    report = raw_report
                    await self.repository.save_report(job_id, report)

            await self.repository.set_job_status(job_id, "completed")
            if security_id is not None and report:
                await self._save_snapshot(job_id, security_id, mode, report)

            return ResearchExecution(
                job_id=job_id,
                security_id=security_id,
                report=report,
                outputs=outputs,
            )
        except Exception:
            await self.repository.set_job_status(job_id, "failed")
            raise

    async def _attach_security(self, job_id: UUID, security_id: UUID) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text("update research_jobs set security_id = :security_id where id = :job_id"),
                {"security_id": security_id, "job_id": job_id},
            )

    async def _save_snapshot(
        self,
        job_id: UUID,
        security_id: UUID,
        mode: AnalysisMode,
        report: dict[str, Any],
    ) -> None:
        sections = report.get("sections") if isinstance(report.get("sections"), dict) else {}
        risks = sections.get(AgentName.RISK.value, []) if isinstance(sections, dict) else []
        catalysts: list[object] = []
        if isinstance(sections, dict):
            for section in (AgentName.NEWS.value, AgentName.EARNINGS.value):
                for claim in sections.get(section, []):
                    if isinstance(claim, dict) and claim.get("claim_type") == "catalyst":
                        catalysts.append(claim)

        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    insert into analysis_snapshots (
                        security_id, job_id, snapshot_type, metrics, catalysts, risks, metadata
                    ) values (
                        :security_id, :job_id, :snapshot_type,
                        cast(:metrics as jsonb), cast(:catalysts as jsonb), cast(:risks as jsonb),
                        '{}'::jsonb
                    )
                    """
                ),
                {
                    "security_id": security_id,
                    "job_id": job_id,
                    "snapshot_type": mode.value,
                    "metrics": _json(report.get("confidence") or {}),
                    "catalysts": _json(catalysts),
                    "risks": _json(risks),
                },
            )


def _resolved_security_id(outputs: list[AgentOutput]) -> UUID | None:
    entity = _latest_output(outputs, AgentName.ENTITY)
    if entity is None:
        return None
    security = entity.metrics.get("security")
    if not isinstance(security, dict) or not security.get("id"):
        return None
    return UUID(str(security["id"]))


def _latest_output(outputs: list[AgentOutput], agent: AgentName) -> AgentOutput | None:
    for output in reversed(outputs):
        if output.agent == agent:
            return output
    return None


def _json(value: object) -> str:
    import json

    return json.dumps(value, default=str)
