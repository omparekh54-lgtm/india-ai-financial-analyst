from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

_BENCHMARK_ALIASES = {
    "NIFTY50": "NIFTY50",
    "NIFTY 50": "NIFTY50",
    "NIFTYBANK": "NIFTYBANK",
    "NIFTY BANK": "NIFTYBANK",
    "NIFTYIT": "NIFTYIT",
    "NIFTY IT": "NIFTYIT",
    "INDIAVIX": "INDIAVIX",
    "INDIA VIX": "INDIAVIX",
}


@dataclass(frozen=True)
class OfficialBenchmarkSource:
    benchmark_code: str
    source_url: str
    provider: str


def resolve_official_benchmark_source(
    benchmark_code: str,
    source_url: str,
) -> OfficialBenchmarkSource:
    normalized_code = " ".join(benchmark_code.strip().upper().split())
    code = _BENCHMARK_ALIASES.get(normalized_code)
    if code is None:
        allowed = ", ".join(sorted({value for value in _BENCHMARK_ALIASES.values()}))
        raise ValueError(f"benchmark_code must be one of: {allowed}")

    candidate = source_url.strip()
    parsed = urlparse(candidate)
    hostname = (parsed.hostname or "").lower().strip(".")
    if parsed.scheme != "https":
        raise ValueError("benchmark source URL must use HTTPS")
    if hostname == "niftyindices.com" or hostname.endswith(".niftyindices.com"):
        provider = "nse_indices"
    elif hostname == "nseindia.com" or hostname.endswith(".nseindia.com"):
        provider = "nse"
    else:
        raise ValueError("benchmark source URL must be on an official NSE/NSE Indices domain")
    return OfficialBenchmarkSource(
        benchmark_code=code,
        source_url=candidate,
        provider=provider,
    )


def benchmark_artifact_uri(source_url: str, sha256: str) -> str:
    checksum = sha256.strip().lower()
    if len(checksum) != 64 or any(character not in "0123456789abcdef" for character in checksum):
        raise ValueError("sha256 must be a 64-character lowercase hexadecimal digest")
    separator = "&" if "#" in source_url else "#"
    return f"{source_url}{separator}artifact-sha256={checksum}"


async def upsert_benchmark_artifact_source(
    engine: AsyncEngine,
    *,
    source: OfficialBenchmarkSource,
    sha256: str,
    row_count: int,
    first_timestamp: datetime,
    last_timestamp: datetime,
) -> UUID:
    if row_count < 1:
        raise ValueError("row_count must be >= 1")
    artifact_uri = benchmark_artifact_uri(source.source_url, sha256)
    metadata = {
        "provider": source.provider,
        "benchmark_code": source.benchmark_code,
        "source_page": source.source_url,
        "artifact_sha256": sha256,
        "row_count": row_count,
        "first_timestamp": first_timestamp.astimezone(UTC).isoformat(),
        "last_timestamp": last_timestamp.astimezone(UTC).isoformat(),
    }
    parameters = {
        "source_uri": artifact_uri,
        "title": f"{source.benchmark_code} official historical index export",
        "retrieved_at": datetime.now(UTC),
        "checksum": sha256,
        "metadata": json.dumps(metadata),
    }
    async with engine.begin() as connection:
        result = await connection.execute(
            text(
                """
                insert into sources (
                    security_id, source_type, source_uri, title, published_at,
                    retrieved_at, freshness, checksum, metadata
                ) values (
                    null, 'benchmark_data', :source_uri, :title, null,
                    :retrieved_at, 'historical', :checksum, cast(:metadata as jsonb)
                )
                on conflict do nothing
                returning id
                """
            ),
            parameters,
        )
        source_id = result.scalar_one_or_none()
        if source_id is None:
            source_id = await connection.scalar(
                text(
                    """
                    select id
                    from sources
                    where security_id is null
                      and source_uri = :source_uri
                      and published_at is null
                    limit 1
                    """
                ),
                parameters,
            )
        if source_id is None:
            raise RuntimeError("Unable to resolve official benchmark artifact source")
        await connection.execute(
            text(
                """
                update sources
                set source_type = 'benchmark_data',
                    title = :title,
                    retrieved_at = :retrieved_at,
                    freshness = 'historical',
                    checksum = :checksum,
                    metadata = cast(:metadata as jsonb)
                where id = :source_id
                """
            ),
            {**parameters, "source_id": source_id},
        )
    return source_id
