from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.contracts import AgentName

_NONPRODUCTION_RE = "(synthetic|mock|fake|dummy|fixture|sample|generated|placeholder)"


@dataclass(frozen=True)
class RealCompanyAcceptanceReport:
    ready: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    jobs: tuple[dict[str, object], ...]
    distinct_securities: int
    distinct_sectors: int

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "distinct_securities": self.distinct_securities,
            "distinct_sectors": self.distinct_sectors,
            "jobs": list(self.jobs),
            "data_policy": "persisted_real_research_only_no_generated_acceptance_data",
        }


async def evaluate_real_company_acceptance(
    engine: AsyncEngine,
    job_ids: list[UUID],
    *,
    min_distinct_securities: int = 5,
    min_distinct_sectors: int = 4,
) -> RealCompanyAcceptanceReport:
    ids = list(dict.fromkeys(job_ids))
    if not ids:
        return RealCompanyAcceptanceReport(
            ready=False,
            errors=("No real completed research job IDs were supplied for acceptance.",),
            warnings=(),
            jobs=(),
            distinct_securities=0,
            distinct_sectors=0,
        )

    statement = text(
        """
        select
          j.id as job_id,
          j.status as job_status,
          j.security_id,
          s.nse_symbol,
          s.legal_name,
          s.sector,
          s.industry,
          r.id as report_id,
          r.report_json,
          r.data_confidence,
          count(distinct ar.id) as agent_run_count,
          count(distinct ar.id) filter (
            where ar.agent_name=:validator and ar.status='completed'
          ) as validator_completed,
          count(distinct ar.id) filter (
            where ar.agent_name=:synthesis and ar.status='completed'
          ) as synthesis_completed,
          count(distinct c.id) as claim_count,
          count(distinct c.id) filter (
            where c.validation_status in ('verified','supported','contested','inferred','stale')
          ) as validated_claim_count,
          count(distinct ce.claim_id) as evidence_linked_claim_count,
          count(distinct ec.source_id) as linked_source_count,
          count(distinct src.id) filter (
            where lower(concat_ws(' ', coalesce(src.source_uri,''), coalesce(src.source_type,''),
              coalesce(src.title,''), coalesce(src.metadata::text,''))) ~ :nonproduction_re
          ) as nonproduction_source_count
        from research_jobs j
        left join securities s on s.id=j.security_id
        left join research_reports r on r.job_id=j.id
        left join agent_runs ar on ar.job_id=j.id
        left join claims c on c.job_id=j.id
        left join claim_evidence ce on ce.claim_id=c.id
        left join evidence_chunks ec on ec.id=ce.evidence_chunk_id
        left join sources src on src.id=ec.source_id
        where j.id in :job_ids
        group by j.id, j.status, j.security_id, s.nse_symbol, s.legal_name, s.sector, s.industry,
                 r.id, r.report_json, r.data_confidence
        order by s.nse_symbol nulls last, j.id
        """
    ).bindparams(bindparam("job_ids", expanding=True))
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                statement,
                {
                    "job_ids": ids,
                    "validator": AgentName.VALIDATOR.value,
                    "synthesis": AgentName.SYNTHESIS.value,
                    "nonproduction_re": _NONPRODUCTION_RE,
                },
            )
        ).mappings().all()

    return evaluate_real_company_rows(
        [dict(row) for row in rows],
        requested_job_ids=ids,
        min_distinct_securities=min_distinct_securities,
        min_distinct_sectors=min_distinct_sectors,
    )


def evaluate_real_company_rows(
    rows: list[dict[str, Any]],
    *,
    requested_job_ids: list[UUID],
    min_distinct_securities: int,
    min_distinct_sectors: int,
) -> RealCompanyAcceptanceReport:
    errors: list[str] = []
    warnings: list[str] = []
    public_rows: list[dict[str, object]] = []
    returned_ids = {str(row.get("job_id")) for row in rows}
    missing = [str(job_id) for job_id in requested_job_ids if str(job_id) not in returned_ids]
    if missing:
        errors.append("Research acceptance job(s) were not found: " + ", ".join(missing))

    securities: set[str] = set()
    sectors: set[str] = set()
    for row in rows:
        job_id = str(row.get("job_id"))
        security_id = str(row.get("security_id") or "")
        sector = str(row.get("sector") or "").strip()
        report_json = row.get("report_json") if isinstance(row.get("report_json"), dict) else {}
        validation = report_json.get("validation") if isinstance(report_json.get("validation"), dict) else {}
        evidence_coverage = validation.get("evidence_coverage")
        job_errors: list[str] = []
        if row.get("job_status") != "completed":
            job_errors.append("job_not_completed")
        if not security_id:
            job_errors.append("security_not_resolved")
        if row.get("report_id") is None or not report_json:
            job_errors.append("persisted_report_missing")
        if int(row.get("validator_completed") or 0) < 1:
            job_errors.append("agent_15_validator_not_completed")
        if int(row.get("synthesis_completed") or 0) < 1:
            job_errors.append("agent_16_synthesis_not_completed")
        if int(row.get("claim_count") or 0) < 1:
            job_errors.append("claims_missing")
        if int(row.get("validated_claim_count") or 0) < 1:
            job_errors.append("validated_claims_missing")
        if int(row.get("evidence_linked_claim_count") or 0) < 1:
            job_errors.append("claim_evidence_links_missing")
        if int(row.get("linked_source_count") or 0) < 1:
            job_errors.append("linked_sources_missing")
        if int(row.get("nonproduction_source_count") or 0) > 0:
            job_errors.append("nonproduction_source_marker_detected")
        if isinstance(evidence_coverage, (int, float)) and float(evidence_coverage) < 0.5:
            job_errors.append("evidence_coverage_below_50pct")
        if job_errors:
            errors.append(f"Job {job_id} failed acceptance: {', '.join(job_errors)}.")
        if security_id:
            securities.add(security_id)
        if sector:
            sectors.add(sector.casefold())
        public_rows.append(
            {
                "job_id": job_id,
                "security_id": security_id or None,
                "nse_symbol": row.get("nse_symbol"),
                "legal_name": row.get("legal_name"),
                "sector": row.get("sector"),
                "industry": row.get("industry"),
                "agent_run_count": int(row.get("agent_run_count") or 0),
                "claim_count": int(row.get("claim_count") or 0),
                "validated_claim_count": int(row.get("validated_claim_count") or 0),
                "evidence_linked_claim_count": int(row.get("evidence_linked_claim_count") or 0),
                "linked_source_count": int(row.get("linked_source_count") or 0),
                "evidence_coverage": float(evidence_coverage) if isinstance(evidence_coverage, (int, float)) else None,
                "accepted": not job_errors,
                "errors": job_errors,
            }
        )

    if len(securities) < min_distinct_securities:
        errors.append(
            "Representative acceptance requires at least "
            f"{min_distinct_securities} distinct real securities; found {len(securities)}."
        )
    if len(sectors) < min_distinct_sectors:
        errors.append(
            "Representative acceptance requires at least "
            f"{min_distinct_sectors} distinct populated sectors; found {len(sectors)}."
        )
    if len(rows) > len(securities):
        warnings.append("Multiple supplied jobs refer to the same security; diversity is counted by security.")

    return RealCompanyAcceptanceReport(
        ready=not errors,
        errors=tuple(dict.fromkeys(errors)),
        warnings=tuple(dict.fromkeys(warnings)),
        jobs=tuple(public_rows),
        distinct_securities=len(securities),
        distinct_sectors=len(sectors),
    )
