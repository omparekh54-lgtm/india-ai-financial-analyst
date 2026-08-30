from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def resolve_security(engine: AsyncEngine, identifier: str) -> tuple[UUID, str]:
    lookup = identifier.strip()
    if not lookup:
        raise ValueError("security identifier cannot be empty")
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    select id, legal_name
                    from securities
                    where upper(coalesce(nse_symbol, '')) = upper(:identifier)
                       or bse_code = :identifier
                       or upper(coalesce(isin, '')) = upper(:identifier)
                    order by legal_name
                    limit 2
                    """
                ),
                {"identifier": lookup},
            )
        ).mappings().all()
    if not rows:
        raise ValueError(f"security not found in canonical master: {lookup}")
    if len(rows) > 1:
        raise ValueError(f"security identifier is ambiguous: {lookup}")
    return UUID(str(rows[0]["id"])), str(rows[0]["legal_name"])


def validate_source_uri(value: str) -> str:
    cleaned = value.strip()
    parsed = urlparse(cleaned)
    if not parsed.scheme:
        raise ValueError("source_uri must be an absolute URI with a scheme")
    if parsed.username or parsed.password:
        raise ValueError("source_uri must not contain embedded credentials")
    return cleaned


def parse_optional_datetime(value: str | None) -> datetime | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def upsert_reference_source(
    engine: AsyncEngine,
    *,
    security_id: UUID,
    source_type: str,
    source_uri: str,
    title: str,
    published_at: datetime | None,
    checksum: str,
    metadata: dict[str, object],
) -> UUID:
    source_type = source_type.strip().lower()
    if not source_type:
        raise ValueError("source_type cannot be empty")
    parameters = {
        "security_id": security_id,
        "source_type": source_type,
        "source_uri": validate_source_uri(source_uri),
        "title": title.strip() or None,
        "published_at": published_at,
        "checksum": checksum,
        "metadata": json.dumps(metadata),
    }
    async with engine.begin() as connection:
        source_id = await connection.scalar(
            text(
                """
                insert into sources (
                    security_id, source_type, source_uri, title, published_at,
                    freshness, checksum, metadata
                ) values (
                    :security_id, :source_type, :source_uri, :title, :published_at,
                    'historical', :checksum, cast(:metadata as jsonb)
                )
                on conflict do nothing
                returning id
                """
            ),
            parameters,
        )
        if source_id is None:
            source_id = await connection.scalar(
                text(
                    """
                    select id
                    from sources
                    where security_id = :security_id
                      and source_uri = :source_uri
                      and coalesce(published_at, '1970-01-01 00:00:00+00'::timestamptz)
                          = coalesce(
                              :published_at,
                              '1970-01-01 00:00:00+00'::timestamptz
                            )
                    limit 1
                    """
                ),
                parameters,
            )
            if source_id is None:
                raise RuntimeError("unable to resolve reference source after upsert")
            await connection.execute(
                text(
                    """
                    update sources
                    set source_type = :source_type,
                        title = :title,
                        checksum = :checksum,
                        metadata = cast(:metadata as jsonb),
                        retrieved_at = now()
                    where id = :source_id
                    """
                ),
                {**parameters, "source_id": source_id},
            )
    return UUID(str(source_id))
