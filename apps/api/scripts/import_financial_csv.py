from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.db import create_database_engine
from app.ingestion.financials import FinancialFactIngestor, normalize_financial_facts
from app.ingestion.reference_financials import parse_financial_csv


async def _resolve_security(engine: AsyncEngine, identifier: str) -> tuple[UUID, str]:
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


def _validate_source_uri(value: str) -> str:
    cleaned = value.strip()
    parsed = urlparse(cleaned)
    if not parsed.scheme:
        raise ValueError("source_uri must be an absolute URI with a scheme")
    if parsed.username or parsed.password:
        raise ValueError("source_uri must not contain embedded credentials")
    return cleaned


def _parse_published_at(value: str | None) -> datetime | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def _upsert_source(
    engine: AsyncEngine,
    *,
    security_id: UUID,
    source_uri: str,
    title: str,
    published_at: datetime | None,
    checksum: str,
    file_name: str,
) -> UUID:
    metadata = json.dumps(
        {
            "importer": "import_financial_csv",
            "file_name": file_name,
            "sha256": checksum,
        }
    )
    parameters = {
        "security_id": security_id,
        "source_uri": source_uri,
        "title": title,
        "published_at": published_at,
        "checksum": checksum,
        "metadata": metadata,
    }
    async with engine.begin() as connection:
        source_id = await connection.scalar(
            text(
                """
                insert into sources (
                    security_id, source_type, source_uri, title, published_at,
                    freshness, checksum, metadata
                ) values (
                    :security_id, 'reference_financials', :source_uri, :title, :published_at,
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
                raise RuntimeError("unable to resolve financial source after upsert")
            await connection.execute(
                text(
                    """
                    update sources
                    set title = :title,
                        checksum = :checksum,
                        metadata = cast(:metadata as jsonb),
                        retrieved_at = now()
                    where id = :source_id
                    """
                ),
                {**parameters, "source_id": source_id},
            )
    return UUID(str(source_id))


async def _run(args: argparse.Namespace) -> dict[str, object]:
    path = Path(args.file)
    content = path.read_text(encoding="utf-8-sig")
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    facts = parse_financial_csv(content, min_rows=args.min_rows)
    normalized = normalize_financial_facts(facts)
    source_uri = _validate_source_uri(args.source_uri)
    published_at = _parse_published_at(args.published_at)
    summary: dict[str, object] = {
        "file": str(path.resolve()),
        "security": args.security.strip(),
        "source_uri": source_uri,
        "sha256": checksum,
        "input_count": len(facts),
        "normalized_count": len(normalized),
        "derived_count": sum(bool(fact.metadata.get("derived")) for fact in normalized),
        "first_period_end": min(fact.period_end for fact in normalized).isoformat(),
        "last_period_end": max(fact.period_end for fact in normalized).isoformat(),
        "dry_run": args.dry_run,
    }
    if args.dry_run:
        return summary

    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be configured")
    engine = create_database_engine(settings.database_url)
    try:
        security_id, legal_name = await _resolve_security(engine, args.security)
        title = args.source_title or f"Approved financial export — {legal_name}"
        source_id = await _upsert_source(
            engine,
            security_id=security_id,
            source_uri=source_uri,
            title=title,
            published_at=published_at,
            checksum=checksum,
            file_name=path.name,
        )
        ingestion = await FinancialFactIngestor(engine).ingest_batch(
            security_id=security_id,
            source_id=source_id,
            facts=facts,
        )
    finally:
        await engine.dispose()

    summary.update(
        {
            "security_id": str(security_id),
            "legal_name": legal_name,
            "source_id": str(source_id),
            "ingestion": ingestion,
        }
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import an approved/licensed one-security financial-facts CSV export."
    )
    parser.add_argument("--file", required=True)
    parser.add_argument(
        "--security",
        required=True,
        help="NSE symbol, BSE code or ISIN already present in the canonical security master.",
    )
    parser.add_argument("--source-uri", required=True)
    parser.add_argument("--source-title")
    parser.add_argument("--published-at")
    parser.add_argument("--min-rows", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.min_rows < 1:
        parser.error("--min-rows must be >= 1")

    try:
        summary = asyncio.run(_run(args))
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
