from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class OfficialFeed:
    id: UUID
    name: str
    provider: str
    feed_type: str
    source_url: str
    exchange: str | None
    identifier: str | None
    title: str | None
    parser_config: dict[str, Any]
    poll_interval_seconds: int
    etag: str | None
    last_modified: str | None


@dataclass(frozen=True)
class ClaimedFeed:
    feed: OfficialFeed
    run_id: UUID


class OfficialFeedRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def claim_due(
        self,
        *,
        limit: int = 4,
        lease_seconds: int = 300,
    ) -> list[ClaimedFeed]:
        limit = max(1, min(limit, 20))
        lease_seconds = max(60, min(lease_seconds, 1800))
        claim_sql = text(
            """
            with candidates as (
              select id
              from official_data_feeds
              where enabled = true
                and next_run_at <= now()
                and (lease_until is null or lease_until < now())
              order by next_run_at, created_at
              limit :limit
              for update skip locked
            )
            update official_data_feeds feed
            set lease_until = now() + (:lease_seconds * interval '1 second'),
                last_started_at = now(),
                updated_at = now()
            from candidates
            where feed.id = candidates.id
            returning feed.*
            """
        )
        async with self.engine.begin() as connection:
            rows = (
                await connection.execute(
                    claim_sql,
                    {"limit": limit, "lease_seconds": lease_seconds},
                )
            ).mappings().all()
            claims: list[ClaimedFeed] = []
            for row in rows:
                run_id = await connection.scalar(
                    text(
                        """
                        insert into official_ingestion_runs(feed_id, status)
                        values (:feed_id, 'running')
                        returning id
                        """
                    ),
                    {"feed_id": row["id"]},
                )
                if run_id is None:
                    raise RuntimeError("Unable to create official ingestion run")
                claims.append(ClaimedFeed(feed=_feed_from_row(row), run_id=run_id))
            return claims

    async def complete(
        self,
        claim: ClaimedFeed,
        *,
        status: str,
        result: dict[str, object] | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        if status not in {"success", "not_modified"}:
            raise ValueError("complete status must be success or not_modified")
        result = dict(result or {})
        parsed_count = _count(result, "parsed_count", "input_count")
        ingested_count = _count(result, "ingested_count", "normalized_count")
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    update official_ingestion_runs
                    set completed_at = now(),
                        status = :status,
                        http_etag = :etag,
                        http_last_modified = :last_modified,
                        parsed_count = :parsed_count,
                        ingested_count = :ingested_count,
                        result = cast(:result as jsonb)
                    where id = :run_id
                    """
                ),
                {
                    "run_id": claim.run_id,
                    "status": status,
                    "etag": etag,
                    "last_modified": last_modified,
                    "parsed_count": parsed_count,
                    "ingested_count": ingested_count,
                    "result": json.dumps(result, default=str),
                },
            )
            await connection.execute(
                text(
                    """
                    update official_data_feeds
                    set etag = coalesce(:etag, etag),
                        last_modified = coalesce(:last_modified, last_modified),
                        last_completed_at = now(),
                        last_success_at = now(),
                        last_error = null,
                        next_run_at = now() + (poll_interval_seconds * interval '1 second'),
                        lease_until = null,
                        updated_at = now()
                    where id = :feed_id
                    """
                ),
                {
                    "feed_id": claim.feed.id,
                    "etag": etag,
                    "last_modified": last_modified,
                },
            )

    async def fail(self, claim: ClaimedFeed, exc: Exception) -> None:
        error_type = type(exc).__name__
        error_message = str(exc)[:1000]
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    update official_ingestion_runs
                    set completed_at = now(),
                        status = 'failed',
                        error_type = :error_type,
                        error_message = :error_message
                    where id = :run_id
                    """
                ),
                {
                    "run_id": claim.run_id,
                    "error_type": error_type,
                    "error_message": error_message,
                },
            )
            await connection.execute(
                text(
                    """
                    update official_data_feeds
                    set last_completed_at = now(),
                        last_error = :last_error,
                        next_run_at = now() + (
                          greatest(300, least(poll_interval_seconds, 3600)) * interval '1 second'
                        ),
                        lease_until = null,
                        updated_at = now()
                    where id = :feed_id
                    """
                ),
                {
                    "feed_id": claim.feed.id,
                    "last_error": f"{error_type}: {error_message}"[:1200],
                },
            )


def _feed_from_row(row: Any) -> OfficialFeed:
    parser_config = row["parser_config"] if isinstance(row["parser_config"], dict) else {}
    return OfficialFeed(
        id=row["id"],
        name=str(row["name"]),
        provider=str(row["provider"]),
        feed_type=str(row["feed_type"]),
        source_url=str(row["source_url"]),
        exchange=str(row["exchange"]) if row["exchange"] else None,
        identifier=str(row["identifier"]) if row["identifier"] else None,
        title=str(row["title"]) if row["title"] else None,
        parser_config=dict(parser_config),
        poll_interval_seconds=int(row["poll_interval_seconds"]),
        etag=str(row["etag"]) if row["etag"] else None,
        last_modified=str(row["last_modified"]) if row["last_modified"] else None,
    )


def _count(result: dict[str, object], *keys: str) -> int | None:
    for key in keys:
        value = result.get(key)
        if isinstance(value, int):
            return value
        try:
            if value is not None:
                return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return None
