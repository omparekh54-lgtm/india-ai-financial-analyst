from __future__ import annotations

import pytest

from app.evidence.embeddings import EmbeddingError, vector_literal
from app.evidence.semantic import build_research_queries
from app.ingestion.exchange_documents import ExchangeDocumentIngestor


class FakeEmbeddingProvider:
    model_name = "fake-384"
    dimensions = 384

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.01] * self.dimensions for _ in texts]


class FailingEmbeddingProvider(FakeEmbeddingProvider):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("expected test failure")


def test_vector_literal_enforces_384_dimensions() -> None:
    value = vector_literal([0.25] * 384)
    assert value.startswith("[")
    assert value.endswith("]")
    assert value.count(",") == 383

    with pytest.raises(EmbeddingError):
        vector_literal([0.25] * 383)


def test_research_queries_cover_financial_governance_and_event_themes() -> None:
    queries = build_research_queries(
        user_query="RELIANCE",
        security={"legal_name": "Reliance Industries Limited", "nse_symbol": "RELIANCE"},
        mode="why_did_it_move",
    )
    combined = " ".join(queries).lower()
    assert "financial results" in combined
    assert "auditor resignation" in combined
    assert "capital allocation" in combined
    assert "material announcement" in combined


@pytest.mark.asyncio
async def test_exchange_document_embedding_is_bounded_and_optional() -> None:
    ingestor = ExchangeDocumentIngestor(object(), embedder=FakeEmbeddingProvider())  # type: ignore[arg-type]
    values, status = await ingestor._embed_chunks(["first filing page", "second filing page"])

    assert status == "embedded"
    assert len(values) == 2
    assert values[0] is not None and values[0].count(",") == 383


@pytest.mark.asyncio
async def test_exchange_document_embedding_failure_degrades_without_raising() -> None:
    ingestor = ExchangeDocumentIngestor(object(), embedder=FailingEmbeddingProvider())  # type: ignore[arg-type]
    values, status = await ingestor._embed_chunks(["filing evidence"])

    assert status == "failed"
    assert values == [None]
