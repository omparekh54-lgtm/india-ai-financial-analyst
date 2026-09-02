from uuid import UUID
from pathlib import Path

from app.core.config import Settings
from app.core.provider_activation import evaluate_provider_activation
from app.core.real_company_acceptance import evaluate_real_company_rows


def test_provider_activation_fails_closed_for_enabled_missing_integrations() -> None:
    settings = Settings(
        _env_file=None,
        enable_external_data_calls=True,
        enable_live_market=True,
    )
    report = evaluate_provider_activation(settings)

    assert report.ready is False
    assert any("TAVILY_API_KEY" in error for error in report.errors)
    assert any("Upstox" in error for error in report.errors)


def test_provider_activation_rejects_non_free_launch_policy() -> None:
    settings = Settings(_env_file=None, free_only=False)
    report = evaluate_provider_activation(settings)

    assert report.ready is False
    assert any("FREE_ONLY" in error for error in report.errors)


def _accepted_row(job_id: UUID, security_id: UUID, sector: str) -> dict[str, object]:
    return {
        "job_id": job_id,
        "job_status": "completed",
        "security_id": security_id,
        "nse_symbol": str(security_id)[-4:].upper(),
        "legal_name": f"Company {security_id}",
        "sector": sector,
        "industry": f"{sector} industry",
        "report_id": UUID(int=job_id.int + 100),
        "report_json": {"validation": {"evidence_coverage": 0.9}},
        "agent_run_count": 16,
        "validator_completed": 1,
        "synthesis_completed": 1,
        "claim_count": 24,
        "validated_claim_count": 22,
        "evidence_linked_claim_count": 20,
        "linked_source_count": 8,
        "nonproduction_source_count": 0,
    }


def test_real_company_acceptance_requires_representative_real_pipeline_outputs() -> None:
    job_ids = [UUID(int=index) for index in range(1, 6)]
    security_ids = [UUID(int=100 + index) for index in range(1, 6)]
    sectors = ["Banks", "IT", "Consumer", "Industrials", "Banks"]
    rows = [
        _accepted_row(job_id, security_id, sector)
        for job_id, security_id, sector in zip(job_ids, security_ids, sectors, strict=True)
    ]

    report = evaluate_real_company_rows(
        rows,
        requested_job_ids=job_ids,
        min_distinct_securities=5,
        min_distinct_sectors=4,
    )

    assert report.ready is True
    assert report.distinct_securities == 5
    assert report.distinct_sectors == 4
    assert all(item["accepted"] is True for item in report.jobs)


def test_real_company_acceptance_rejects_missing_evidence_and_nonproduction_markers() -> None:
    job_id = UUID(int=1)
    row = _accepted_row(job_id, UUID(int=101), "Banks")
    row["evidence_linked_claim_count"] = 0
    row["nonproduction_source_count"] = 1

    report = evaluate_real_company_rows(
        [row],
        requested_job_ids=[job_id],
        min_distinct_securities=1,
        min_distinct_sectors=1,
    )

    assert report.ready is False
    assert any("claim_evidence_links_missing" in error for error in report.errors)
    assert any("nonproduction_source_marker_detected" in error for error in report.errors)


def test_real_company_nonproduction_sql_scanner_uses_metadata_values_only() -> None:
    source = Path("app/core/real_company_acceptance.py").read_text(encoding="utf-8")

    assert "coalesce(src.metadata::text" not in source
    assert "jsonb_each_text(coalesce(src.metadata, '{}'::jsonb))" in source
