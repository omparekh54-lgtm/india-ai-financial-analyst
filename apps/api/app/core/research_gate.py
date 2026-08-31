from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.data_readiness import (
    DataCoverageReport,
    evaluate_data_coverage,
    load_data_coverage,
)


class ResearchCorpusNotReadyError(RuntimeError):
    """Raised when production research is requested before hard corpus gates pass."""

    def __init__(self, report: DataCoverageReport) -> None:
        self.report = report
        self.errors = report.errors
        super().__init__("Production research corpus is not ready")


def enforce_report_for_environment(
    report: DataCoverageReport,
    *,
    app_env: str,
) -> DataCoverageReport:
    """Fail closed on hard corpus errors in production only.

    Coverage warnings remain non-blocking. Development/test environments can continue using
    deliberately small fixtures without weakening the production contract.
    """
    if app_env.strip().lower() == "production" and not report.ready:
        raise ResearchCorpusNotReadyError(report)
    return report


async def enforce_research_corpus_ready(
    engine: AsyncEngine,
    *,
    app_env: str,
) -> DataCoverageReport | None:
    """Evaluate the canonical corpus gate before starting a production research job."""
    if app_env.strip().lower() != "production":
        return None
    coverage = await load_data_coverage(engine)
    report = evaluate_data_coverage(coverage)
    return enforce_report_for_environment(report, app_env=app_env)
