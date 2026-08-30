from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.documents.parser import DocumentParseError, chunk_document, parse_document


class ExchangeDocumentIngestor:
    """Persists an exchange attachment and page-aware evidence chunks for a corporate event."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def ingest(
        self,
        *,
        event_id: UUID,
        security_id: UUID,
        source_uri: str,
        media_type: str,
        content: bytes,
        title: str,
        published_at: datetime | None,
        document_role: str = "attachment",
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if not source_uri.startswith("https://"):
            raise ValueError("Exchange document source_uri must use HTTPS")

        checksum = hashlib.sha256(content).hexdigest()
        retrieved_at = datetime.now(UTC)
        source_metadata = {
            "document_role": document_role,
            "media_type": media_type,
            **(metadata or {}),
        }

        source_id = await self._ensure_source(
            security_id=security_id,
            source_uri=source_uri,
            title=title,
            published_at=published_at,
            retrieved_at=retrieved_at,
            checksum=checksum,
            metadata=source_metadata,
        )

        if media_type in {"application/xbrl+xml", "application/xml", "text/xml"}:
            await self._link_event(
                event_id=event_id,
                source_id=source_id,
                document_role="xbrl" if document_role == "attachment" else document_role,
                media_type=media_type,
                parse_status="unsupported",
                metadata={"reason": "handled_by_financial_xbrl_parser", **source_metadata},
            )
            return {
                "source_id": str(source_id),
                "event_id": str(event_id),
                "chunk_count": 0,
                "parse_status": "unsupported",
                "checksum": checksum,
            }

        try:
            parsed = parse_document(content, media_type, title=title)
            chunks = chunk_document(parsed, max_chars=3500, overlap_chars=300)
        except DocumentParseError:
            await self._link_event(
                event_id=event_id,
                source_id=source_id,
                document_role=document_role,
                media_type=media_type,
                parse_status="failed",
                metadata=source_metadata,
            )
            raise

        async with self.engine.begin() as connection:
            await connection.execute(
                text("delete from evidence_chunks where source_id = :source_id"),
                {"source_id": source_id},
            )
            for chunk in chunks:
                await connection.execute(
                    text(
                        """
                        insert into evidence_chunks (
                            source_id, chunk_index, page_number, content, metadata
                        ) values (
                            :source_id, :chunk_index, :page_number, :content, cast(:metadata as jsonb)
                        )
                        """
                    ),
                    {
                        "source_id": source_id,
                        "chunk_index": chunk.chunk_index,
                        "page_number": chunk.page_number,
                        "content": chunk.content,
                        "metadata": json.dumps(
                            {
                                "document_role": document_role,
                                "media_type": media_type,
                                "content_checksum": hashlib.sha256(
                                    chunk.content.encode("utf-8")
                                ).hexdigest(),
                            }
                        ),
                    },
                )

        await self._link_event(
            event_id=event_id,
            source_id=source_id,
            document_role=document_role,
            media_type=media_type,
            parse_status="parsed",
            metadata={
                **source_metadata,
                "page_count": len(parsed.pages),
                "chunk_count": len(chunks),
            },
        )
        return {
            "source_id": str(source_id),
            "event_id": str(event_id),
            "chunk_count": len(chunks),
            "page_count": len(parsed.pages),
            "parse_status": "parsed",
            "checksum": checksum,
        }

    async def link_existing_source(
        self,
        *,
        event_id: UUID,
        source_id: UUID,
        document_role: str,
        media_type: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        await self._link_event(
            event_id=event_id,
            source_id=source_id,
            document_role=document_role,
            media_type=media_type,
            parse_status="parsed",
            metadata=metadata or {},
        )

    async def _ensure_source(
        self,
        *,
        security_id: UUID,
        source_uri: str,
        title: str,
        published_at: datetime | None,
        retrieved_at: datetime,
        checksum: str,
        metadata: dict[str, object],
    ) -> UUID:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    insert into sources (
                        security_id, source_type, source_uri, title, published_at,
                        retrieved_at, freshness, checksum, metadata
                    ) values (
                        :security_id, 'exchange_filing', :source_uri, :title, :published_at,
                        :retrieved_at, 'near_live', :checksum, cast(:metadata as jsonb)
                    )
                    on conflict do nothing
                    returning id
                    """
                ),
                {
                    "security_id": security_id,
                    "source_uri": source_uri,
                    "title": title,
                    "published_at": published_at,
                    "retrieved_at": retrieved_at,
                    "checksum": checksum,
                    "metadata": json.dumps(metadata),
                },
            )
            source_id = result.scalar_one_or_none()
            if source_id is None:
                source_id = await connection.scalar(
                    text(
                        """
                        select id from sources
                        where security_id = :security_id
                          and source_uri = :source_uri
                          and published_at is not distinct from :published_at
                        order by retrieved_at desc
                        limit 1
                        """
                    ),
                    {
                        "security_id": security_id,
                        "source_uri": source_uri,
                        "published_at": published_at,
                    },
                )
                if source_id is not None:
                    await connection.execute(
                        text(
                            """
                            update sources
                            set checksum = coalesce(checksum, :checksum),
                                metadata = metadata || cast(:metadata as jsonb)
                            where id = :source_id
                            """
                        ),
                        {
                            "source_id": source_id,
                            "checksum": checksum,
                            "metadata": json.dumps(metadata),
                        },
                    )
            if source_id is None:
                raise RuntimeError("Unable to resolve exchange document source")
            return source_id

    async def _link_event(
        self,
        *,
        event_id: UUID,
        source_id: UUID,
        document_role: str,
        media_type: str,
        parse_status: str,
        metadata: dict[str, object],
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    insert into corporate_event_sources (
                        event_id, source_id, document_role, media_type, parse_status,
                        parsed_at, metadata
                    ) values (
                        :event_id, :source_id, :document_role, :media_type, :parse_status,
                        case when :parse_status = 'parsed' then now() else null end,
                        cast(:metadata as jsonb)
                    )
                    on conflict (event_id, source_id) do update set
                        document_role = excluded.document_role,
                        media_type = excluded.media_type,
                        parse_status = excluded.parse_status,
                        parsed_at = excluded.parsed_at,
                        metadata = excluded.metadata
                    """
                ),
                {
                    "event_id": event_id,
                    "source_id": source_id,
                    "document_role": document_role,
                    "media_type": media_type,
                    "parse_status": parse_status,
                    "metadata": json.dumps(metadata),
                },
            )
