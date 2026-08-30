from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.contracts import EvidenceRef
from app.evidence.embeddings import EmbeddingProvider, vector_literal


@dataclass(frozen=True)
class SemanticEvidenceMatch:
    evidence: EvidenceRef
    similarity: float
    query: str


class SemanticEvidenceRetriever:
    """Retrieves the most relevant filing chunks for bounded research themes."""

    def __init__(
        self,
        engine: AsyncEngine,
        embedder: EmbeddingProvider,
        *,
        min_similarity: float = 0.30,
    ) -> None:
        self.engine = engine
        self.embedder = embedder
        self.min_similarity = max(-1.0, min(float(min_similarity), 1.0))

    async def search(
        self,
        security_id: UUID,
        queries: list[str],
        *,
        per_query: int = 6,
        max_results: int = 24,
    ) -> list[SemanticEvidenceMatch]:
        clean_queries = list(dict.fromkeys(query.strip() for query in queries if query.strip()))[:8]
        if not clean_queries:
            return []
        per_query = max(1, min(per_query, 12))
        max_results = max(1, min(max_results, 48))
        vectors = await self.embedder.embed(clean_queries)

        best: dict[tuple[str, int, str], SemanticEvidenceMatch] = {}
        async with self.engine.connect() as connection:
            for query, vector in zip(clean_queries, vectors, strict=True):
                rows = (
                    await connection.execute(
                        text(
                            """
                            with linked as (
                              select ec.id, ec.source_id, ec.chunk_index, ec.page_number,
                                     ec.section as chunk_section, ec.content, ec.embedding,
                                     s.source_type, s.source_uri, s.title, s.published_at,
                                     s.retrieved_at, s.freshness, s.checksum,
                                     ce.event_type, ce.event_at,
                                     row_number() over (
                                       partition by ec.id
                                       order by ce.event_at desc nulls last, ce.created_at desc nulls last
                                     ) as link_rank
                              from evidence_chunks ec
                              join sources s on s.id = ec.source_id
                              left join corporate_event_sources ces on ces.source_id = ec.source_id
                              left join corporate_events ce on ce.id = ces.event_id
                              where s.security_id = :security_id
                                and ec.embedding is not null
                                and s.source_type in ('exchange_filing', 'company_filing', 'regulator')
                            ), scored as (
                              select *,
                                     1 - (embedding <=> cast(:embedding as vector)) as similarity
                              from linked
                              where link_rank = 1
                            )
                            select * from scored
                            where similarity >= :min_similarity
                            order by similarity desc, published_at desc nulls last,
                                     source_id, chunk_index
                            limit :limit
                            """
                        ),
                        {
                            "security_id": security_id,
                            "embedding": vector_literal(vector, dimensions=self.embedder.dimensions),
                            "min_similarity": self.min_similarity,
                            "limit": per_query,
                        },
                    )
                ).mappings().all()

                for row in rows:
                    event_at = row["event_at"]
                    published_at = row["published_at"]
                    retrieved_at = row["retrieved_at"]
                    evidence = EvidenceRef(
                        source_type=str(row["source_type"] or "exchange_filing"),
                        source_uri=str(row["source_uri"]),
                        title=str(row["title"] or "Exchange filing evidence"),
                        published_at=(
                            published_at.isoformat()
                            if published_at
                            else event_at.isoformat() if event_at else None
                        ),
                        retrieved_at=(
                            retrieved_at.isoformat()
                            if retrieved_at
                            else datetime.now(UTC).isoformat()
                        ),
                        freshness=_freshness(row["freshness"]),
                        excerpt=str(row["content"]),
                        page_number=row["page_number"],
                        section=str(
                            row["event_type"] or row["chunk_section"] or "exchange_filing"
                        ),
                        checksum=row["checksum"],
                        source_priority=1,
                    )
                    similarity = float(row["similarity"])
                    key = (
                        evidence.source_uri,
                        evidence.page_number or -1,
                        evidence.excerpt or "",
                    )
                    existing = best.get(key)
                    candidate = SemanticEvidenceMatch(
                        evidence=evidence,
                        similarity=similarity,
                        query=query,
                    )
                    if existing is None or candidate.similarity > existing.similarity:
                        best[key] = candidate

        return sorted(best.values(), key=lambda item: item.similarity, reverse=True)[:max_results]


def build_research_queries(
    *,
    user_query: str,
    security: dict[str, object],
    mode: str,
) -> list[str]:
    company = str(security.get("legal_name") or user_query).strip()
    symbol = str(security.get("nse_symbol") or security.get("bse_code") or "").strip()
    prefix = f"{company} {symbol}".strip()
    queries = [
        f"{prefix} revenue earnings margins guidance demand management commentary financial results",
        f"{prefix} auditor resignation promoter pledge related party governance litigation SEBI regulatory risk",
        f"{prefix} debt cash flow working capital capex capital allocation dividend buyback merger acquisition",
        f"{prefix} strategy competitive outlook capacity order wins customers risks catalysts",
    ]
    if mode == "why_did_it_move":
        queries.insert(0, f"{prefix} material announcement event results guidance order regulatory action")
    elif mode == "what_changed":
        queries.insert(0, f"{prefix} new change update latest filing guidance risk catalyst")
    return queries[:6]


def _freshness(value: object) -> str:
    candidate = str(value or "near_live")
    if candidate not in {"live", "near_live", "periodic", "historical", "unknown"}:
        return "unknown"
    return candidate
