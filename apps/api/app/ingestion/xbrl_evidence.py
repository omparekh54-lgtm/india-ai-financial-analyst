from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ingestion.financials import RawFinancialFact


@dataclass(frozen=True)
class XbrlEvidenceChunk:
    chunk_index: int
    period_end: date
    period_type: str
    content: str
    metadata: dict[str, object]


def build_xbrl_evidence_chunks(
    facts: list[RawFinancialFact],
    *,
    max_facts_per_chunk: int = 40,
) -> tuple[XbrlEvidenceChunk, ...]:
    """Render parsed XBRL facts into deterministic, citable evidence text.

    This is intentionally not an LLM summary. Every output line is a direct structured
    representation of one parsed filing fact, preserving its raw concept label and value.
    """
    if max_facts_per_chunk < 1:
        raise ValueError("max_facts_per_chunk must be >= 1")
    if not facts:
        return ()

    grouped: dict[tuple[date, str], list[RawFinancialFact]] = {}
    for fact in facts:
        if not fact.name.strip():
            raise ValueError("XBRL evidence cannot contain an empty fact name")
        grouped.setdefault((fact.period_end, fact.period_type), []).append(fact)

    chunks: list[XbrlEvidenceChunk] = []
    chunk_index = 0
    for (period_end, period_type), period_facts in sorted(
        grouped.items(),
        key=lambda item: (item[0][0], item[0][1]),
        reverse=True,
    ):
        ordered = sorted(
            period_facts,
            key=lambda fact: (
                fact.name.strip().lower(),
                str(fact.unit or ""),
                _value_text(fact.value),
            ),
        )
        for offset in range(0, len(ordered), max_facts_per_chunk):
            selected = ordered[offset : offset + max_facts_per_chunk]
            lines = [
                (
                    "Official filing XBRL facts | "
                    f"period_end={period_end.isoformat()} | period_type={period_type}"
                )
            ]
            for fact in selected:
                unit = fact.unit.strip() if fact.unit else "unit_unspecified"
                element = str(fact.metadata.get("xbrl_element") or "").strip()
                element_suffix = f" | xbrl_element={element}" if element else ""
                lines.append(
                    f"- {fact.name.strip()}: {_value_text(fact.value)} {unit}{element_suffix}"
                )
            content = "\n".join(lines)
            chunks.append(
                XbrlEvidenceChunk(
                    chunk_index=chunk_index,
                    period_end=period_end,
                    period_type=period_type,
                    content=content,
                    metadata={
                        "ai_assisted": False,
                        "evidence_kind": "deterministic_xbrl_fact_summary",
                        "source_format": "xbrl",
                        "period_end": period_end.isoformat(),
                        "period_type": period_type,
                        "fact_count": len(selected),
                        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    },
                )
            )
            chunk_index += 1
    return tuple(chunks)


class XbrlEvidenceIngestor:
    """Persist deterministic XBRL evidence and mark its event link as parsed."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def ingest(
        self,
        *,
        source_id: UUID,
        event_id: UUID,
        facts: list[RawFinancialFact],
        document_checksum: str,
        media_type: str,
        max_facts_per_chunk: int = 40,
    ) -> dict[str, object]:
        checksum = document_checksum.strip().lower()
        if len(checksum) != 64 or any(character not in "0123456789abcdef" for character in checksum):
            raise ValueError("document_checksum must be a lowercase SHA-256 hex digest")
        normalized_media_type = media_type.split(";", 1)[0].strip().lower()
        if normalized_media_type not in {
            "application/xbrl+xml",
            "application/xml",
            "text/xml",
            "text/html",
            "application/xhtml+xml",
        }:
            raise ValueError("media_type must be a supported XBRL or inline-XBRL type")

        chunks = build_xbrl_evidence_chunks(
            facts,
            max_facts_per_chunk=max_facts_per_chunk,
        )
        if not chunks:
            raise ValueError("parsed XBRL produced no evidence facts")

        parsed_at = datetime.now(UTC)
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    update sources
                    set checksum = :checksum,
                        metadata = metadata || jsonb_build_object(
                          'xbrl_parse_status', 'parsed',
                          'xbrl_fact_count', :fact_count,
                          'xbrl_evidence_chunk_count', :chunk_count,
                          'xbrl_document_sha256', :checksum,
                          'ai_assisted', false
                        )
                    where id = :source_id
                    """
                ),
                {
                    "source_id": source_id,
                    "checksum": checksum,
                    "fact_count": len(facts),
                    "chunk_count": len(chunks),
                },
            )
            await connection.execute(
                text("delete from evidence_chunks where source_id = :source_id"),
                {"source_id": source_id},
            )
            for chunk in chunks:
                await connection.execute(
                    text(
                        """
                        insert into evidence_chunks (
                          source_id, chunk_index, page_number, section, content, embedding, metadata
                        ) values (
                          :source_id, :chunk_index, null, 'financial_results_xbrl',
                          :content, null, cast(:metadata as jsonb)
                        )
                        """
                    ),
                    {
                        "source_id": source_id,
                        "chunk_index": chunk.chunk_index,
                        "content": chunk.content,
                        "metadata": json.dumps(chunk.metadata, sort_keys=True),
                    },
                )
            await connection.execute(
                text(
                    """
                    insert into corporate_event_sources (
                      event_id, source_id, document_role, media_type,
                      parse_status, parsed_at, metadata
                    ) values (
                      :event_id, :source_id, 'xbrl', :media_type,
                      'parsed', :parsed_at, cast(:metadata as jsonb)
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
                    "media_type": normalized_media_type,
                    "parsed_at": parsed_at,
                    "metadata": json.dumps(
                        {
                            "parser": "deterministic_xbrl_financial_parser",
                            "ai_assisted": False,
                            "fact_count": len(facts),
                            "evidence_chunk_count": len(chunks),
                            "document_sha256": checksum,
                        },
                        sort_keys=True,
                    ),
                },
            )

        return {
            "source_id": str(source_id),
            "event_id": str(event_id),
            "parse_status": "parsed",
            "fact_count": len(facts),
            "evidence_chunk_count": len(chunks),
            "document_sha256": checksum,
        }


def _value_text(value: float | Decimal | str) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value).strip()
