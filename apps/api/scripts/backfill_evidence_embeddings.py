from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import text

from app.core.config import get_settings
from app.db import create_database_engine
from app.evidence.embeddings import build_embedding_provider, vector_literal


async def _run(*, batch_size: int, limit: int | None) -> int:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    embedder = build_embedding_provider(settings)
    if embedder is None:
        raise RuntimeError("ENABLE_SEMANTIC_RETRIEVAL must be true for embedding backfill")

    engine = create_database_engine(settings.database_url)
    processed = 0
    try:
        while limit is None or processed < limit:
            remaining = batch_size if limit is None else min(batch_size, limit - processed)
            if remaining <= 0:
                break
            async with engine.connect() as connection:
                rows = (
                    await connection.execute(
                        text(
                            """
                            select ec.id, ec.content
                            from evidence_chunks ec
                            join sources s on s.id = ec.source_id
                            where ec.embedding is null
                              and s.source_type in ('exchange_filing', 'company_filing', 'regulator')
                            order by s.published_at desc nulls last, ec.id
                            limit :limit
                            """
                        ),
                        {"limit": remaining},
                    )
                ).mappings().all()
            if not rows:
                break

            vectors = await embedder.embed([str(row["content"]) for row in rows])
            async with engine.begin() as connection:
                for row, vector in zip(rows, vectors, strict=True):
                    await connection.execute(
                        text(
                            """
                            update evidence_chunks
                            set embedding = cast(:embedding as vector),
                                metadata = metadata || cast(:metadata as jsonb)
                            where id = :chunk_id and embedding is null
                            """
                        ),
                        {
                            "chunk_id": row["id"],
                            "embedding": vector_literal(
                                vector,
                                dimensions=embedder.dimensions,
                            ),
                            "metadata": json.dumps(
                                {
                                    "embedding_status": "embedded",
                                    "embedding_model": embedder.model_name,
                                }
                            ),
                        },
                    )
            processed += len(rows)
            print(
                json.dumps(
                    {
                        "status": "progress",
                        "processed": processed,
                        "model": embedder.model_name,
                    }
                ),
                flush=True,
            )

        print(
            json.dumps(
                {
                    "status": "completed",
                    "processed": processed,
                    "model": embedder.model_name,
                }
            )
        )
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill local embeddings for filing evidence.")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    batch_size = max(1, min(args.batch_size, 256))
    limit = None if args.limit is None else max(1, args.limit)
    return asyncio.run(_run(batch_size=batch_size, limit=limit))


if __name__ == "__main__":
    raise SystemExit(main())
