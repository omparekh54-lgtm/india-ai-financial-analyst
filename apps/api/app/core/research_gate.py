from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.agent_data_readiness import (
    AgentReadinessReport,
    evaluate_agent_readiness,
    load_agent_data_coverage,
)
from app.core.config import Settings, get_settings
from app.core.data_readiness import (
    DataCoverageReport,
    evaluate_data_coverage,
    load_data_coverage,
)
from app.core.security_readiness import (
    SecurityReadiness,
    evaluate_security_readiness,
    financial_preparation_required,
    load_security_agent_coverage,
)


class ResearchCorpusNotReadyError(RuntimeError):
    """Raised when production research is requested before hard corpus gates pass."""

    def __init__(
        self,
        report: DataCoverageReport,
        agent_report: AgentReadinessReport | None = None,
    ) -> None:
        self.report = report
        self.agent_report = agent_report
        agent_errors = (
            tuple(
                f"{item.agent.value}: {message}"
                for item in agent_report.agents
                if not item.ready
                for message in item.errors
            )
            if agent_report is not None
            else ()
        )
        self.errors = tuple(dict.fromkeys((*report.errors, *agent_errors)))
        self.blocking_agents = agent_report.blocking_agents if agent_report is not None else ()
        super().__init__("Production research corpus is not ready")


def enforce_report_for_environment(
    report: DataCoverageReport,
    *,
    app_env: str,
    agent_report: AgentReadinessReport | None = None,
) -> DataCoverageReport:
    """Fail closed on corpus or agent-level data errors in production only.

    Development/test environments can continue exercising small non-production fixtures, while
    production requires both the global provenance gate and every agent's real-data contract.
    """
    production = app_env.strip().lower() == "production"
    agents_ready = agent_report is None or agent_report.ready
    if production and (not report.ready or not agents_ready):
        raise ResearchCorpusNotReadyError(report, agent_report)
    return report


class SecurityNotReadyError(RuntimeError):
    """Raised when the requested security specifically is not data-ready."""

    def __init__(self, readiness: SecurityReadiness) -> None:
        self.readiness = readiness
        self.symbol = readiness.symbol
        self.security_id = readiness.security_id
        self.blocking_agents = readiness.blocking_agents
        self.errors = readiness.blockers()
        super().__init__(f"Security {readiness.symbol} is not research-ready")


@dataclass(frozen=True)
class SecurityResearchAssessment:
    readiness: SecurityReadiness
    preparation_required: tuple[str, ...] = ()


async def assess_security_research_readiness(
    engine: AsyncEngine,
    security_id: UUID,
    *,
    settings: Settings | None = None,
) -> SecurityResearchAssessment:
    """Evaluate readiness and identify safe durable preparation work.

    Provider-backed preparation is intentionally not executed here because this function is
    called from the request path. It only determines whether financial preparation is the
    sole missing root input; the durable worker performs the actual fetch and re-checks the
    complete readiness contract before any research agents run.
    """
    runtime_settings = settings or get_settings()
    corpus_coverage = await load_data_coverage(engine)
    security_coverage, symbol = await load_security_agent_coverage(engine, security_id)
    readiness = evaluate_security_readiness(
        security_id,
        symbol,
        security_coverage,
        corpus_coverage,
        runtime_settings,
    )
    preparation: tuple[str, ...] = ()
    if (
        not readiness.ready
        and runtime_settings.enable_external_data_calls
        and financial_preparation_required(
            security_id,
            symbol,
            security_coverage,
            corpus_coverage,
            runtime_settings,
        )
    ):
        preparation = ("financial_history",)
    return SecurityResearchAssessment(readiness=readiness, preparation_required=preparation)


async def enforce_security_research_ready(
    engine: AsyncEngine,
    security_id: UUID,
    *,
    app_env: str,
    settings: Settings | None = None,
) -> SecurityReadiness | None:
    """Gate a research request on the requested security only.

    Per PROJECT_INTENT.md, an incomplete security must not block a complete one. Global
    provenance rules still apply and still fail closed, because they describe corpus
    correctness (no synthetic data, no unapproved enabled feeds) rather than coverage.
    """
    if app_env.strip().lower() != "production":
        return None

    assessment = await assess_security_research_readiness(
        engine,
        security_id,
        settings=settings,
    )
    readiness = assessment.readiness
    if not readiness.ready:
        raise SecurityNotReadyError(readiness)
    return readiness


async def enforce_research_corpus_ready(
    engine: AsyncEngine,
    *,
    app_env: str,
    settings: Settings | None = None,
) -> DataCoverageReport | None:
    """Evaluate global and 16-agent corpus gates before starting production research."""
    if app_env.strip().lower() != "production":
        return None

    runtime_settings = settings or get_settings()
    coverage = await load_data_coverage(engine)
    report = evaluate_data_coverage(coverage)
    agent_coverage = await load_agent_data_coverage(engine)
    agent_report = evaluate_agent_readiness(agent_coverage, coverage, runtime_settings)
    return enforce_report_for_environment(
        report,
        app_env=app_env,
        agent_report=agent_report,
    )
