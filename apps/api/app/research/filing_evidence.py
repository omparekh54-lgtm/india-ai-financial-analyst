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
) -> list[EvidenceRef]:
    """Load bounded page-aware chunks from recent parsed exchange filing attachments."""
    max_chunks = max(1, min(max_chunks, 80))
    max_chunks_per_document = max(1, min(max_chunks_per_document, 12))
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
                             ec.chunk_index, ec.page_number, ec.content,
                             row_number() over (
                               partition by ces.source_id
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
                    where chunk_rank <= :max_per_document
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
                    "max_chunks": max_chunks,
                },
            )
        ).mappings().all()

    now = datetime.now(UTC).isoformat()
    return [
        EvidenceRef(
            source_type=str(row["source_type"] or "exchange_filing"),
            source_uri=str(row["source_uri"]),
            title=str(row["title"] or f"{row['event_type']} exchange filing"),
            published_at=(
                row["published_at"].isoformat()
                if row["published_at"]
                else row["event_at"].isoformat() if row["event_at"] else None
            ),
            retrieved_at=row["retrieved_at"].isoformat() if row["retrieved_at"] else now,
            freshness=str(row["freshness"] or "near_live"),
            excerpt=str(row["content"]),
            page_number=row["page_number"],
            checksum=row["checksum"],
            source_priority=1,
        )
        for row in rows
    ]
