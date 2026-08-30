from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.documents.parser import DocumentParseError, chunk_document, parse_document
from app.documents.visual import (
    GeminiDocumentVisualAnalyzer,
    MultimodalAnalysisError,
    MultimodalDocumentResult,
)
from app.evidence.embeddings import (
    EmbeddingError,
    EmbeddingProvider,
    build_embedding_provider,
    vector_literal,
)

_VISUAL_EVENT_TYPES = {"financial_results", "investor_presentation", "annual_report"}


class ExchangeDocumentIngestor:
    """Persists an exchange attachment and page-aware evidence chunks for a corporate event."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        embedder: EmbeddingProvider | None = None,
        visual_analyzer: GeminiDocumentVisualAnalyzer | None = None,
    ) -> None:
        self.engine = engine
        settings = get_settings()
        self.embedder = embedder or build_embedding_provider(settings)
        self.visual_analyzer = visual_analyzer or GeminiDocumentVisualAnalyzer(settings)

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
                "embedding_status": "not_applicable",
                "multimodal_status": "not_applicable",
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

        embedding_values, embedding_status = await self._embed_chunks(
            [chunk.content for chunk in chunks]
        )
        embedding_model = self.embedder.model_name if self.embedder is not None else None
        section = str(source_metadata.get("event_type") or document_role)
        visual_result, multimodal_status = await self._analyze_visuals(
            content=content,
            media_type=media_type,
            title=title,
            page_text=[page.text for page in parsed.pages],
            event_type=section,
        )

        async with self.engine.begin() as connection:
            await connection.execute(
                text("delete from evidence_chunks where source_id = :source_id"),
                {"source_id": source_id},
            )
            for chunk, embedding in zip(chunks, embedding_values, strict=True):
                await connection.execute(
                    text(
                        """
                        insert into evidence_chunks (
                            source_id, chunk_index, page_number, section, content, embedding, metadata
                        ) values (
                            :source_id, :chunk_index, :page_number, :section, :content,
                            case when :embedding is null then null else cast(:embedding as vector) end,
                            cast(:metadata as jsonb)
                        )
                        """
                    ),
                    {
                        "source_id": source_id,
                        "chunk_index": chunk.chunk_index,
                        "page_number": chunk.page_number,
                        "section": section,
                        "content": chunk.content,
                        "embedding": embedding,
                        "metadata": json.dumps(
                            {
                                "document_role": document_role,
                                "media_type": media_type,
                                "content_checksum": hashlib.sha256(
                                    chunk.content.encode("utf-8")
                                ).hexdigest(),
                                "embedding_status": embedding_status,
                                "embedding_model": embedding_model,
                                "ai_assisted": False,
                            }
                        ),
                    },
                )

            if visual_result is not None:
                for offset, finding in enumerate(visual_result.findings):
                    content_text = (
                        f"AI-assisted visual interpretation ({finding.kind}) of official filing "
                        f"page {finding.page_number}: {finding.summary}"
                    )
                    await connection.execute(
                        text(
                            """
                            insert into evidence_chunks (
                                source_id, chunk_index, page_number, section, content, embedding, metadata
                            ) values (
                                :source_id, :chunk_index, :page_number, 'multimodal_extraction',
                                :content, null, cast(:metadata as jsonb)
                            )
                            """
                        ),
                        {
                            "source_id": source_id,
                            "chunk_index": len(chunks) + offset,
                            "page_number": finding.page_number,
                            "content": content_text,
                            "metadata": json.dumps(
                                {
                                    "ai_assisted": True,
                                    "provider": visual_result.provider,
                                    "model": visual_result.model,
                                    "visual_kind": finding.kind,
                                    "confidence": finding.confidence,
                                    "raw_source_section": section,
                                }
                            ),
                        },
                    )

        link_metadata = {
            **source_metadata,
            "page_count": len(parsed.pages),
            "chunk_count": len(chunks),
            "embedding_status": embedding_status,
            "embedding_model": embedding_model,
            "multimodal_status": multimodal_status,
            "multimodal_finding_count": len(visual_result.findings) if visual_result else 0,
            "multimodal_model": visual_result.model if visual_result else None,
            "multimodal_pages": visual_result.analyzed_pages if visual_result else [],
        }
        await self._link_event(
            event_id=event_id,
            source_id=source_id,
            document_role=document_role,
            media_type=media_type,
            parse_status="parsed",
            metadata=link_metadata,
        )
        return {
            "source_id": str(source_id),
            "event_id": str(event_id),
            "chunk_count": len(chunks),
            "page_count": len(parsed.pages),
            "parse_status": "parsed",
            "embedding_status": embedding_status,
            "embedded_chunk_count": sum(value is not None for value in embedding_values),
            "multimodal_status": multimodal_status,
            "multimodal_finding_count": len(visual_result.findings) if visual_result else 0,
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

    async def _embed_chunks(self, contents: list[str]) -> tuple[list[str | None], str]:
        if not contents:
            return [], "empty"
        if self.embedder is None:
            return [None for _ in contents], "disabled"
        try:
            vectors = await self.embedder.embed(contents)
        except EmbeddingError:
            return [None for _ in contents], "failed"
        if len(vectors) != len(contents):
            return [None for _ in contents], "failed"
        return [
            vector_literal(vector, dimensions=self.embedder.dimensions) for vector in vectors
        ], "embedded"

    async def _analyze_visuals(
        self,
        *,
        content: bytes,
        media_type: str,
        title: str,
        page_text: list[str],
        event_type: str,
    ) -> tuple[MultimodalDocumentResult | None, str]:
        if media_type.split(";", 1)[0].lower() != "application/pdf":
            return None, "not_applicable"
        if event_type not in _VISUAL_EVENT_TYPES:
            return None, "not_selected"
        if not self.visual_analyzer.enabled:
            return None, "disabled"
        try:
            result = await self.visual_analyzer.analyze_pdf(
                content,
                title=title,
                page_text=page_text,
            )
        except MultimodalAnalysisError:
            return None, "failed"
        if result is None:
            return None, "no_visual_pages"
        return result, "analyzed"

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
