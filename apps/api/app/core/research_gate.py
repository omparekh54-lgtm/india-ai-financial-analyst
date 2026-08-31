from __future__ import annotations

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
