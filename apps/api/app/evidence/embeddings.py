from __future__ import annotations

import asyncio
from typing import Any, Protocol

from app.core.config import Settings


class EmbeddingError(RuntimeError):
    """Raised when local embedding generation is unavailable or invalid."""


class EmbeddingProvider(Protocol):
    model_name: str
    dimensions: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbeddingProvider:
    """Lazy local Sentence-Transformers embedding provider.

    The model is loaded only on first use so API processes with semantic retrieval
    disabled never download or allocate embedding-model resources.
    """

    def __init__(self, *, model_name: str, dimensions: int = 384) -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self._model: Any | None = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        clean = [text.strip() for text in texts]
        if not clean or any(not text for text in clean):
            raise EmbeddingError("Embedding input must contain non-empty text")
        return await asyncio.to_thread(self._encode, clean)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        model = self._load_model()
        try:
            values = model.encode(
                texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            raise EmbeddingError("Local embedding generation failed") from exc

        rows = values.tolist()
        if len(rows) != len(texts):
            raise EmbeddingError("Embedding model returned an unexpected row count")
        output: list[list[float]] = []
        for row in rows:
            vector = [float(value) for value in row]
            if len(vector) != self.dimensions:
                raise EmbeddingError(
                    f"Embedding dimension mismatch: expected {self.dimensions}, got {len(vector)}"
                )
            output.append(vector)
        return output

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingError(
                "Sentence-Transformers is not installed; install the embeddings runtime extra"
            ) from exc
        try:
            self._model = SentenceTransformer(self.model_name)
        except Exception as exc:
            raise EmbeddingError("Unable to load local embedding model") from exc
        return self._model


def build_embedding_provider(settings: Settings) -> EmbeddingProvider | None:
    if not settings.enable_semantic_retrieval:
        return None
    return SentenceTransformerEmbeddingProvider(
        model_name=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )


def vector_literal(values: list[float], *, dimensions: int = 384) -> str:
    if len(values) != dimensions:
        raise EmbeddingError(
            f"Embedding dimension mismatch: expected {dimensions}, got {len(values)}"
        )
    return "[" + ",".join(f"{float(value):.9g}" for value in values) + "]"
