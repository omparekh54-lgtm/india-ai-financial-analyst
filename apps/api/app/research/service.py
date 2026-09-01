from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.contracts import AgentInput, AgentName, AgentOutput
from app.core.config import Settings, get_settings
from app.core.usage import ResearchUsageGate
from app.orchestration.plan import AnalysisMode, ResearchDepth, ResearchPlan, build_research_plan
from app.orchestration.registry import build_agent_registry
from app.orchestration.runtime import OrchestratorRuntime
from app.repositories.research import ResearchRepository
from app.repositories.research_progress import ResearchProgressRepository
from app.research.live_context import UserAwareResearchContextLoader
from app.research.monitoring import MonitoringRepository, thesis_hash
from app.telemetry import ProductTelemetry


@dataclass(frozen=True)
class ResearchExecution:
    job_id: UUID
    security_id: UUID | None
    depth: ResearchDepth
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
        self.progress = ResearchProgressRepository(engine)
        self.monitoring = MonitoringRepository(engine)
        self.usage = ResearchUsageGate(engine, self.settings)
        self.telemetry = ProductTelemetry(self.settings)
        self.runtime = OrchestratorRuntime(
            build_agent_registry(engine, self.settings),
            max_concurrency=max_concurrency,
            context_loader=UserAwareResearchContextLoader(engine, self.settings),
        )

    async def enqueue(
        self,
        *,
        query: str,
        mode: AnalysisMode,
        depth: ResearchDepth = ResearchDepth.STANDARD,
        requested_by: UUID | None = None,
        metadata: dict[str, object] | None = None,
    ) -> UUID:
        job_metadata: dict[str, object] = {
            "analysis_depth": depth.value,
            "execution": "queued_worker",
            "research_stage": "received",
            "progress_pct": 0,
        }
        if metadata:
            job_metadata.update(metadata)
        event_generated = bool(job_metadata.get("system_generated") is True)
        if requested_by is not None:
            await self.usage.reserve(
                requested_by,
                depth=depth,
                event_generated=event_generated,
            )
        try:
            job_id = await self.repository.create_job(
                query=query,
                mode=mode.value,
                requested_by=requested_by,
                metadata=job_metadata,
            )
        except Exception:
            if requested_by is not None:
                await self.usage.release(
                    requested_by,
                    depth=depth,
                    event_generated=event_generated,
                )
            raise
        await self.telemetry.capture(
            "research_queued",
            {"mode": mode.value, "depth": depth.value},
        )
        return job_id

    async def execute(
        self,
        *,
        query: str,
        mode: AnalysisMode,
        depth: ResearchDepth = ResearchDepth.STANDARD,
        context: dict[str, Any] | None = None,
        requested_by: UUID | None = None,
    ) -> ResearchExecution:
        """Backward-compatible immediate execution used by tests/internal callers."""
        if requested_by is not None:
            await self.usage.reserve(requested_by, depth=depth)
        try:
            job_id = await self.repository.create_job(
                query=query,
                mode=mode.value,
                requested_by=requested_by,
                metadata={
                    "analysis_depth": depth.value,
                    "execution": "inline",
                    "research_stage": "received",
                    "progress_pct": 0,
                },
            )
        except Exception:
            if requested_by is not None:
                await self.usage.release(requested_by, depth=depth)
            raise
        return await self.execute_existing(
            job_id=job_id,
            query=query,
            mode=mode,
            depth=depth,
            context=context,
            requested_by=requested_by,
        )

    async def execute_existing(
        self,
        *,
        job_id: UUID,
        query: str,
        mode: AnalysisMode,
        depth: ResearchDepth = ResearchDepth.STANDARD,
        context: dict[str, Any] | None = None,
        requested_by: UUID | None = None,
        plan: ResearchPlan | None = None,
    ) -> ResearchExecution:
        """Execute an already-durable job, suitable for a separate worker process."""
        started = perf_counter()
        await self.repository.set_job_status(job_id, "running")
        await self.progress.set_stage(job_id, "planned", 5)
        await self.telemetry.capture(
            "research_started",
            {"mode": mode.value, "depth": depth.value, "queued": True},
        )

        runtime_context = dict(context or {})
        runtime_context.setdefault("analysis_depth", depth.value)

        async def update_stage(stage: str, progress_pct: int) -> None:
            await self.progress.set_stage(job_id, stage, progress_pct)

        try:
            outputs = await self.runtime.run(
                plan or build_research_plan(mode, depth),
                AgentInput(
                    job_id=job_id,
                    user_id=requested_by,
                    query=query,
                    context=runtime_context,
                ),
                on_stage=update_stage,
            )
            security_id = _resolved_security_id(outputs)
            if security_id is not None:
                await self._attach_security(job_id, security_id)

            await self.progress.set_stage(job_id, "rendering", 95)
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
            await self.progress.set_stage(job_id, "complete", 100)
            if security_id is not None and report:
                await self._save_snapshot(
                    job_id,
                    security_id,
                    mode,
                    depth,
                    report,
                    outputs,
                    requested_by=requested_by,
                )

            validation = report.get("validation") if isinstance(report.get("validation"), dict) else {}
            coverage = validation.get("evidence_coverage") if isinstance(validation, dict) else None
            await self.telemetry.capture(
                "research_completed",
                {
                    "mode": mode.value,
                    "depth": depth.value,
                    "duration_ms": round((perf_counter() - started) * 1000),
                    "agent_count": len(outputs),
                    "agent_warning_count": sum(bool(output.warnings) for output in outputs),
                    "claim_count": sum(len(output.claims) for output in outputs),
                    "security_resolved": security_id is not None,
                    "report_generated": bool(report),
                    "evidence_coverage": float(coverage) if isinstance(coverage, (int, float)) else None,
                },
            )
            return ResearchExecution(
                job_id=job_id,
                security_id=security_id,
                depth=depth,
                report=report,
                outputs=outputs,
            )
        except Exception as exc:
            await self.repository.set_job_status(job_id, "failed")
            await self.progress.set_stage(job_id, "failed", 100)
            await self.telemetry.capture(
                "research_failed",
                {
                    "mode": mode.value,
                    "depth": depth.value,
                    "duration_ms": round((perf_counter() - started) * 1000),
                    "error_type": type(exc).__name__,
                },
            )
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
        depth: ResearchDepth,
        report: dict[str, Any],
        outputs: list[AgentOutput],
        *,
        requested_by: UUID | None,
    ) -> None:
        sections = report.get("sections") if isinstance(report.get("sections"), dict) else {}
        risks = sections.get(AgentName.RISK.value, []) if isinstance(sections, dict) else []
        catalysts: list[object] = []
        disclosures: list[object] = []
        if isinstance(sections, dict):
            for section in (AgentName.NEWS.value, AgentName.EARNINGS.value):
                for claim in sections.get(section, []):
                    if isinstance(claim, dict) and claim.get("claim_type") == "catalyst":
                        catalysts.append(claim)
            for section in (AgentName.FILINGS.value, AgentName.EARNINGS.value):
                disclosures.extend(
                    claim for claim in sections.get(section, []) if isinstance(claim, dict)
                )

        snapshot_metrics: dict[str, object] = {
            "confidence": report.get("confidence") or {},
        }
        metric_agents = {
            "market": AgentName.MARKET,
            "financials": AgentName.FINANCIALS,
            "earnings": AgentName.EARNINGS,
            "industry": AgentName.INDUSTRY,
            "macro": AgentName.MACRO,
            "valuation": AgentName.VALUATION,
            "technical": AgentName.TECHNICAL,
        }
        for key, agent in metric_agents.items():
            output = _latest_output(outputs, agent)
            if output is not None and output.metrics:
                snapshot_metrics[key] = output.metrics

        async with self.engine.begin() as connection:
            snapshot_id = (
                await connection.execute(
                    text(
                        """
                        insert into analysis_snapshots (
                            security_id, job_id, snapshot_type, thesis_hash,
                            metrics, catalysts, risks, metadata
                        ) values (
                            :security_id, :job_id, :snapshot_type, :thesis_hash,
                            cast(:metrics as jsonb), cast(:catalysts as jsonb), cast(:risks as jsonb),
                            cast(:metadata as jsonb)
                        )
                        returning id
                        """
                    ),
                    {
                        "security_id": security_id,
                        "job_id": job_id,
                        "snapshot_type": mode.value,
                        "thesis_hash": thesis_hash(report),
                        "metrics": _json(snapshot_metrics),
                        "catalysts": _json(catalysts),
                        "risks": _json(risks),
                        "metadata": _json(
                            {
                                "analysis_depth": depth.value,
                                "disclosure_claims": disclosures[:80],
                                "snapshot_schema_version": 3,
                            }
                        ),
                    },
                )
            ).scalar_one()
            if requested_by is not None:
                await self.monitoring.create_alert_for_snapshot(
                    connection=connection,
                    user_id=requested_by,
                    security_id=security_id,
                    job_id=job_id,
                    source_snapshot_id=UUID(str(snapshot_id)),
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
