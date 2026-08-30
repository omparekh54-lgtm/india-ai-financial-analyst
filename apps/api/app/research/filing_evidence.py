from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.contracts import EvidenceRef


async def load_exchange_filing_evidence(
    engine: AsyncEngine,
    security_id: UUID,
    *,
    max_chunks: int = 48,
    max_chunks_per_document: int = 8,
    max_ai_chunks_per_document: int = 4,
) -> list[EvidenceRef]:
    """Load bounded official-event evidence, including filings and labeled AI-derived media text."""
    max_chunks = max(1, min(max_chunks, 80))
    max_chunks_per_document = max(1, min(max_chunks_per_document, 12))
    max_ai_chunks_per_document = max(1, min(max_ai_chunks_per_document, 6))
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    with ranked as (
                      select ce.event_type, ce.event_at, ce.materiality,
                             ces.document_role, ces.source_id,
                             s.source_type, s.source_uri, s.title, s.published_at,
                             s.retrieved_at, s.freshness, s.checksum,
                             ec.id as evidence_id, ec.chunk_index, ec.page_number,
                             ec.section as chunk_section, ec.content,
                             ec.metadata as chunk_metadata,
                             row_number() over (
                               partition by ces.source_id,
                                            (coalesce(ec.section, '') = 'multimodal_extraction')
                               order by ec.chunk_index
                             ) as chunk_rank
                      from corporate_event_sources ces
                      join corporate_events ce on ce.id = ces.event_id
                      join sources s on s.id = ces.source_id
                      join evidence_chunks ec on ec.source_id = ces.source_id
                      where ce.security_id = :security_id
                        and ces.parse_status = 'parsed'
                    )
                    select * from ranked
                    where (
                        coalesce(chunk_section, '') = 'multimodal_extraction'
                        and chunk_rank <= :max_ai_per_document
                    ) or (
                        coalesce(chunk_section, '') <> 'multimodal_extraction'
                        and chunk_rank <= :max_per_document
                    )
                    order by event_at desc nulls last,
                             materiality desc nulls last,
                             source_id,
                             chunk_index
                    limit :max_chunks
                    """
                ),
                {
                    "security_id": security_id,
                    "max_per_document": max_chunks_per_document,
                    "max_ai_per_document": max_ai_chunks_per_document,
                    "max_chunks": max_chunks,
                },
            )
        ).mappings().all()

    now = datetime.now(UTC).isoformat()
    evidence: list[EvidenceRef] = []
    for row in rows:
        chunk_section = str(row["chunk_section"] or "")
        raw_source_type = str(row["source_type"] or "exchange_filing")
        is_ai = chunk_section == "multimodal_extraction"
        is_audio = raw_source_type == "earnings_audio" or chunk_section == "earnings_transcript"
        event_section = str(row["event_type"] or chunk_section or "exchange_filing")
        evidence.append(
            EvidenceRef(
                evidence_id=row["evidence_id"],
                source_type=(
                    "ai_extraction"
                    if is_ai
                    else "audio_transcript" if is_audio else raw_source_type
                ),
                source_uri=str(row["source_uri"]),
                title=str(row["title"] or f"{event_section} official evidence"),
                published_at=(
                    row["published_at"].isoformat()
                    if row["published_at"]
                    else row["event_at"].isoformat() if row["event_at"] else None
                ),
                retrieved_at=row["retrieved_at"].isoformat() if row["retrieved_at"] else now,
                freshness=str(row["freshness"] or "near_live"),
                excerpt=str(row["content"]),
                page_number=row["page_number"],
                section="multimodal_extraction" if is_ai else event_section,
                checksum=row["checksum"],
                source_priority=4 if is_ai else 2 if is_audio else 1,
            )
        )
    return evidence
