from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

_NSE_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&._-]{0,49}$")


@dataclass(frozen=True)
class OfficialSecurityMarketSource:
    nse_symbol: str
    source_url: str
    provider: str = "nse"


def resolve_official_security_market_source(
    nse_symbol: str,
    source_url: str,
) -> OfficialSecurityMarketSource:
    symbol = nse_symbol.strip().upper()
    if not _NSE_SYMBOL.fullmatch(symbol):
        raise ValueError("nse_symbol contains unsupported characters")

    candidate = source_url.strip()
    parsed = urlparse(candidate)
    hostname = (parsed.hostname or "").lower().strip(".")
    if parsed.scheme != "https" or not (
        hostname == "nseindia.com"
        or hostname.endswith(".nseindia.com")
        or hostname == "nsearchives.nseindia.com"
    ):
        raise ValueError("security market source URL must use HTTPS on an official NSE domain")
    return OfficialSecurityMarketSource(nse_symbol=symbol, source_url=candidate)


def validate_security_export_identity(content: str, *, nse_symbol: str) -> None:
    expected = nse_symbol.strip().upper()
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if reader.fieldnames is None:
        raise ValueError("security market CSV has no header")

    normalized_headers = {
        str(header).strip().lower().replace(" ", "_"): header for header in reader.fieldnames
    }
    symbol_header = normalized_headers.get("symbol") or normalized_headers.get("nse_symbol")
    series_header = normalized_headers.get("series")
    for row_number, row in enumerate(reader, start=2):
        if symbol_header:
            row_symbol = str(row.get(symbol_header) or "").strip().upper()
            if row_symbol and row_symbol != expected:
                raise ValueError(
                    f"security market CSV row {row_number} contains symbol {row_symbol!r}; "
                    f"expected {expected!r}"
                )
        if series_header:
            row_series = str(row.get(series_header) or "").strip().upper()
            if row_series and row_series != "EQ":
                raise ValueError(
                    f"security market CSV row {row_number} contains series {row_series!r}; expected 'EQ'"
                )


def security_market_artifact_uri(source_url: str, sha256: str) -> str:
    checksum = sha256.strip().lower()
    if len(checksum) != 64 or any(character not in "0123456789abcdef" for character in checksum):
        raise ValueError("sha256 must be a 64-character hexadecimal digest")
    separator = "&" if "#" in source_url else "#"
    return f"{source_url}{separator}artifact-sha256={checksum}"


async def upsert_security_market_artifact_source(
    engine: AsyncEngine,
    *,
    security_id: UUID,
    legal_name: str,
    source: OfficialSecurityMarketSource,
    sha256: str,
    row_count: int,
    first_timestamp: datetime,
    last_timestamp: datetime,
) -> UUID:
    if row_count < 1:
        raise ValueError("row_count must be >= 1")
    artifact_uri = security_market_artifact_uri(source.source_url, sha256)
    metadata = {
        "provider": source.provider,
        "nse_symbol": source.nse_symbol,
        "source_page": source.source_url,
        "artifact_sha256": sha256,
        "row_count": row_count,
        "first_timestamp": first_timestamp.astimezone(UTC).isoformat(),
        "last_timestamp": last_timestamp.astimezone(UTC).isoformat(),
        "ingestion_mode": "operator_supplied_official_export",
    }
    parameters = {
        "security_id": security_id,
        "source_uri": artifact_uri,
        "title": f"{legal_name} ({source.nse_symbol}) official NSE historical price export",
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
                    :security_id, 'market_data', :source_uri, :title, null,
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
                    where security_id = :security_id
                      and source_uri = :source_uri
                      and published_at is null
                    limit 1
                    """
                ),
                parameters,
            )
        if source_id is None:
            raise RuntimeError("Unable to resolve official security market artifact source")
        await connection.execute(
            text(
                """
                update sources
                set source_type = 'market_data',
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
