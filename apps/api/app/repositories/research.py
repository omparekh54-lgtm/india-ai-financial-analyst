from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.agents.contracts import AgentOutput, EvidenceRef


class ResearchRepository:
    """Persists research jobs, evidence, agent runs, claims and final reports transactionally."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def create_job(
        self,
        *,
        query: str,
        mode: str,
        security_id: UUID | None = None,
        requested_by: UUID | None = None,
        metadata: dict[str, object] | None = None,
    ) -> UUID:
        statement = text(
            """
            insert into research_jobs (
                security_id, query, mode, requested_by, status, metadata
            ) values (
                :security_id, :query, :mode, :requested_by, 'queued', cast(:metadata as jsonb)
            )
            returning id
            """
        )
        async with self.engine.begin() as connection:
            result = await connection.execute(
                statement,
                {
                    "security_id": security_id,
                    "query": query,
                    "mode": mode,
                    "requested_by": requested_by,
                    "metadata": json.dumps(metadata or {}),
                },
            )
            return result.scalar_one()

    async def list_user_jobs(self, user_id: UUID, *, limit: int = 25) -> list[dict[str, object]]:
        statement = text(
            """
            select
                job.id,
                job.query,
                job.status,
                job.mode,
                job.metadata as job_metadata,
                job.security_id,
                job.started_at,
                job.completed_at,
                job.created_at,
                security.legal_name,
                security.nse_symbol,
                security.bse_code,
                report.data_confidence,
                report.thesis_confidence,
                report.valuation_confidence,
                report.catalyst_confidence
            from research_jobs job
            left join securities security on security.id = job.security_id
            left join research_reports report on report.job_id = job.id
            where job.requested_by = :user_id
            order by job.created_at desc
            limit :limit
            """
        )
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    statement,
                    {"user_id": user_id, "limit": max(1, min(limit, 100))},
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    async def get_user_job(self, user_id: UUID, job_id: UUID) -> dict[str, object] | None:
        statement = text(
            """
            select
                job.id,
                job.query,
                job.status,
                job.mode,
                job.metadata as job_metadata,
                job.security_id,
                job.started_at,
                job.completed_at,
                job.created_at,
                security.legal_name,
                security.nse_symbol,
                security.bse_code,
                report.report_json,
                report.data_confidence,
                report.thesis_confidence,
                report.valuation_confidence,
                report.catalyst_confidence
            from research_jobs job
            left join securities security on security.id = job.security_id
            left join research_reports report on report.job_id = job.id
            where job.id = :job_id
              and job.requested_by = :user_id
            limit 1
            """
        )
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    statement,
                    {"job_id": job_id, "user_id": user_id},
                )
            ).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def get_user_job_evidence(
        self,
        user_id: UUID,
        job_id: UUID,
    ) -> list[dict[str, object]]:
        """Return claim-level evidence, scoped through the owning research job."""
        statement = text(
            """
            select
                c.id as claim_id,
                c.claim_type,
                c.statement,
                c.confidence,
                c.validation_status,
                c.data as claim_data,
                ar.agent_name,
                ec.id as evidence_id,
                ec.page_number,
                ec.section,
                ec.content,
                ec.metadata as evidence_metadata,
                src.id as source_id,
                src.source_type,
                src.source_uri,
                src.title as source_title,
                src.published_at,
                src.retrieved_at,
                src.freshness,
                src.checksum
            from claims c
            join research_jobs job on job.id = c.job_id
            left join agent_runs ar on ar.id = c.agent_run_id
            left join claim_evidence ce on ce.claim_id = c.id
            left join evidence_chunks ec on ec.id = ce.evidence_chunk_id
            left join sources src on src.id = ec.source_id
            where c.job_id = :job_id
              and job.requested_by = :user_id
            order by c.created_at, c.id, ec.page_number nulls last, ec.chunk_index
            """
        )
        async with self.engine.connect() as connection:
            rows = (
                await connection.execute(
                    statement,
                    {"job_id": job_id, "user_id": user_id},
                )
            ).mappings().all()

        claims: dict[str, dict[str, object]] = {}
        for row in rows:
            claim_id = str(row["claim_id"])
            claim = claims.setdefault(
                claim_id,
                {
                    "claim_id": claim_id,
                    "agent": row["agent_name"],
                    "claim_type": row["claim_type"],
                    "statement": row["statement"],
                    "confidence": float(row["confidence"]),
                    "validation_status": row["validation_status"],
                    "data": row["claim_data"] if isinstance(row["claim_data"], dict) else {},
                    "evidence": [],
                },
            )
            if row["evidence_id"] is None:
                continue
            evidence = claim["evidence"]
            if not isinstance(evidence, list):  # pragma: no cover - internal invariant
                raise TypeError("claim evidence payload must be a list")
            evidence.append(
                {
                    "evidence_id": str(row["evidence_id"]),
                    "source_id": str(row["source_id"]) if row["source_id"] else None,
                    "source_type": row["source_type"],
                    "source_uri": row["source_uri"],
                    "title": row["source_title"],
                    "published_at": row["published_at"],
                    "retrieved_at": row["retrieved_at"],
                    "freshness": row["freshness"],
                    "checksum": row["checksum"],
                    "page_number": row["page_number"],
                    "section": row["section"],
                    "content": row["content"],
                    "metadata": (
                        row["evidence_metadata"]
                        if isinstance(row["evidence_metadata"], dict)
                        else {}
                    ),
                }
            )
        return list(claims.values())

    async def set_job_status(self, job_id: UUID, status: str) -> None:
        fields = {"status": status, "job_id": job_id}
        timestamp_sql = ""
        if status == "running":
            timestamp_sql = ", started_at = coalesce(started_at, :now)"
            fields["now"] = datetime.now(UTC)
        elif status in {"completed", "failed"}:
            timestamp_sql = ", completed_at = :now"
            fields["now"] = datetime.now(UTC)

        statement = text(
            f"update research_jobs set status = :status{timestamp_sql} where id = :job_id"
        )
        async with self.engine.begin() as connection:
            await connection.execute(statement, fields)

    async def save_agent_output(self, job_id: UUID, output: AgentOutput) -> UUID:
        run_statement = text(
            """
            insert into agent_runs (job_id, agent_name, status, warnings, errors, metadata, completed_at)
            values (
                :job_id,
                :agent_name,
                :status,
                cast(:warnings as jsonb),
                cast(:errors as jsonb),
                cast(:metadata as jsonb),
                :completed_at
            )
            returning id
            """
        )
        claim_statement = text(
            """
            insert into claims (
                id, job_id, agent_run_id, claim_type, statement, confidence,
                validation_status, data
            )
            values (
                :id, :job_id, :agent_run_id, :claim_type, :statement, :confidence,
                :validation_status, cast(:data as jsonb)
            )
            on conflict (id) do update set
                confidence = excluded.confidence,
                validation_status = excluded.validation_status,
                data = excluded.data
            """
        )
        link_statement = text(
            """
            insert into claim_evidence (claim_id, evidence_chunk_id)
            values (:claim_id, :evidence_chunk_id)
            on conflict do nothing
            """
        )

        async with self.engine.begin() as connection:
            for evidence in output.evidence:
                await self._save_evidence(connection, job_id, evidence)

            result = await connection.execute(
                run_statement,
                {
                    "job_id": job_id,
                    "agent_name": output.agent.value,
                    "status": "completed" if output.ok else "completed_with_warnings",
                    "warnings": json.dumps(output.warnings),
                    "errors": json.dumps(output.errors),
                    "metadata": json.dumps(output.metrics),
                    "completed_at": datetime.now(UTC),
                },
            )
            agent_run_id = result.scalar_one()

            for claim in output.claims:
                await connection.execute(
                    claim_statement,
                    {
                        "id": claim.claim_id,
                        "job_id": job_id,
                        "agent_run_id": agent_run_id,
                        "claim_type": claim.claim_type,
                        "statement": claim.statement,
                        "confidence": claim.confidence,
                        "validation_status": claim.status,
                        "data": json.dumps(claim.data),
                    },
                )
                for evidence_id in claim.evidence_ids:
                    exists = await connection.scalar(
                        text("select exists(select 1 from evidence_chunks where id = :id)"),
                        {"id": evidence_id},
                    )
                    if exists:
                        await connection.execute(
                            link_statement,
                            {"claim_id": claim.claim_id, "evidence_chunk_id": evidence_id},
                        )

            return agent_run_id

    async def _save_evidence(
        self,
        connection: AsyncConnection,
        job_id: UUID,
        evidence: EvidenceRef,
    ) -> None:
        exists = await connection.scalar(
            text("select exists(select 1 from evidence_chunks where id = :id)"),
            {"id": evidence.evidence_id},
        )
        if exists:
            return

        source_statement = text(
            """
            insert into sources (
                security_id, source_type, source_uri, title, published_at,
                retrieved_at, freshness, checksum, metadata
            )
            select
                security_id, :source_type, :source_uri, :title,
                cast(:published_at as timestamptz), cast(:retrieved_at as timestamptz),
                :freshness, :checksum, '{}'::jsonb
            from research_jobs
            where id = :job_id
            returning id
            """
        )
        result = await connection.execute(
            source_statement,
            {
                "job_id": job_id,
                "source_type": evidence.source_type,
                "source_uri": evidence.source_uri,
                "title": evidence.title,
                "published_at": evidence.published_at,
                "retrieved_at": evidence.retrieved_at,
                "freshness": evidence.freshness,
                "checksum": evidence.checksum,
            },
        )
        source_id = result.scalar_one()
        await connection.execute(
            text(
                """
                insert into evidence_chunks (
                    id, source_id, chunk_index, content, metadata
                ) values (
                    :id, :source_id, 0, :content, cast(:metadata as jsonb)
                )
                on conflict (id) do nothing
                """
            ),
            {
                "id": evidence.evidence_id,
                "source_id": source_id,
                "content": evidence.excerpt or evidence.title or "Evidence reference",
                "metadata": json.dumps({"reference_only": True}),
            },
        )

    async def save_report(self, job_id: UUID, report: dict[str, object]) -> None:
        confidence = report.get("confidence") or {}
        if not isinstance(confidence, dict):
            confidence = {}
        statement = text(
            """
            insert into research_reports (
                job_id, executive_summary, report_json, data_confidence,
                thesis_confidence, valuation_confidence, catalyst_confidence
            ) values (
                :job_id, :executive_summary, cast(:report_json as jsonb), :data_confidence,
                :thesis_confidence, :valuation_confidence, :catalyst_confidence
            )
            on conflict (job_id) do update set
                executive_summary = excluded.executive_summary,
                report_json = excluded.report_json,
                data_confidence = excluded.data_confidence,
                thesis_confidence = excluded.thesis_confidence,
                valuation_confidence = excluded.valuation_confidence,
                catalyst_confidence = excluded.catalyst_confidence
            """
        )
        async with self.engine.begin() as connection:
            await connection.execute(
                statement,
                {
                    "job_id": job_id,
                    "executive_summary": report.get("executive_summary"),
                    "report_json": json.dumps(report),
                    "data_confidence": confidence.get("data_confidence"),
                    "thesis_confidence": confidence.get("thesis_confidence"),
                    "valuation_confidence": confidence.get("valuation_confidence"),
                    "catalyst_confidence": confidence.get("catalyst_confidence"),
                },
            )
