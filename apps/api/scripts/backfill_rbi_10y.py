from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.connectors.rbi_bulletin import (
    RBI_BULLETIN_PAGE,
    RbiBulletinTenYearYieldFetcher,
)
from app.core.config import get_settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage
from app.db import create_database_engine
from app.ingestion.macro import MacroObservation, MacroObservationIngestor


def canonical_checksum(observation: MacroObservation) -> str:
    payload = {
        "series_key": observation.series_key,
        "observation_date": observation.observation_date.isoformat(),
        "value": float(observation.value),
        "unit": observation.unit,
        "released_at": observation.released_at.isoformat() if observation.released_at else None,
        "series_label": observation.metadata.get("series_label"),
        "source_uri": observation.metadata.get("source_uri"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def provenance_source_uri(observation: MacroObservation, checksum: str) -> str:
    source_uri = str(observation.metadata.get("source_uri") or "").strip()
    if not source_uri.startswith("https://www.rbi.org.in/"):
        raise ValueError("RBI 10Y observation is missing an official RBI detail URL")
    return (
        f"{source_uri}#observation-date={observation.observation_date.isoformat()}"
        f"&sha256={checksum}"
    )


async def upsert_source(
    engine: AsyncEngine,
    *,
    observation: MacroObservation,
    checksum: str,
) -> UUID:
    source_uri = provenance_source_uri(observation, checksum)
    detail_url = str(observation.metadata.get("source_uri") or "")
    published_at = observation.released_at
    metadata = json.dumps(
        {
            "provider": "RBI",
            "provenance_class": "official_source",
            "production_approved": True,
            "report_page": RBI_BULLETIN_PAGE,
            "detail_url": detail_url,
            "series_key": observation.series_key,
            "series_label": observation.metadata.get("series_label"),
            "observation_date": observation.observation_date.isoformat(),
            "publication_date": observation.metadata.get("publication_date"),
            "observation_basis": observation.metadata.get("observation_basis"),
            "artifact_sha256": checksum,
        },
        sort_keys=True,
    )
    params = {
        "source_uri": source_uri,
        "title": "RBI Bulletin — 10-Year G-Sec Par Yield (FBIL)",
        "published_at": published_at,
        "retrieved_at": datetime.now(UTC),
        "checksum": checksum,
        "metadata": metadata,
    }
    async with engine.begin() as connection:
        source_id = await connection.scalar(
            text(
                """
                select id
                from sources
                where security_id is null
                  and source_type = 'official_macro'
                  and source_uri = :source_uri
                  and published_at is not distinct from :published_at
                limit 1
                """
            ),
            params,
        )
        if source_id is None:
            source_id = await connection.scalar(
                text(
                    """
                    insert into sources (
                      security_id, source_type, source_uri, title, published_at,
                      retrieved_at, freshness, checksum, metadata
                    ) values (
                      null, 'official_macro', :source_uri, :title, :published_at,
                      :retrieved_at, 'periodic', :checksum, cast(:metadata as jsonb)
                    )
                    returning id
                    """
                ),
                params,
            )
        else:
            await connection.execute(
                text(
                    """
                    update sources
                    set title = :title,
                        retrieved_at = :retrieved_at,
                        freshness = 'periodic',
                        checksum = :checksum,
                        metadata = cast(:metadata as jsonb)
                    where id = :source_id
                    """
                ),
                {**params, "source_id": source_id},
            )
    if source_id is None:
        raise RuntimeError("Unable to resolve RBI 10Y provenance source")
    return source_id


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch the latest RBI Bulletin 10-Year G-Sec Par Yield (FBIL) observation and "
            "store it as a provenance-linked macro observation."
        )
    )
    parser.add_argument("--max-age-days", type=int, default=45)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.max_age_days < 0:
        parser.error("--max-age-days must be >= 0")

    async with RbiBulletinTenYearYieldFetcher() as fetcher:
        observation = await fetcher.fetch()

    today = datetime.now(UTC).date()
    age_days = (today - observation.observation_date).days
    if age_days < 0:
        raise SystemExit(
            f"RBI 10Y observation date {observation.observation_date.isoformat()} is in the future"
        )
    if age_days > args.max_age_days:
        raise SystemExit(
            "RBI 10Y observation is stale: "
            f"{observation.observation_date.isoformat()} is {age_days} day(s) old; "
            f"maximum allowed is {args.max_age_days}"
        )

    checksum = canonical_checksum(observation)
    source_uri = provenance_source_uri(observation, checksum)
    summary: dict[str, object] = {
        "provider": "RBI",
        "provenance_class": "official_source",
        "series_key": observation.series_key,
        "series_label": observation.metadata.get("series_label"),
        "source_page": RBI_BULLETIN_PAGE,
        "detail_url": observation.metadata.get("source_uri"),
        "provenance_source_uri": source_uri,
        "publication_date": observation.metadata.get("publication_date"),
        "observation_date": observation.observation_date.isoformat(),
        "age_days": age_days,
        "max_age_days": args.max_age_days,
        "value": float(observation.value),
        "unit": observation.unit,
        "sha256": checksum,
        "dry_run": args.dry_run,
    }
    if args.dry_run:
        print(json.dumps(summary, indent=2, sort_keys=True, default=str))
        return 0

    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL must be configured")

    engine = create_database_engine(settings.database_url)
    try:
        source_id = await upsert_source(engine, observation=observation, checksum=checksum)
        ingestion = await MacroObservationIngestor(engine).ingest_batch(
            [observation],
            source_id=source_id,
        )
        readiness = evaluate_data_coverage(await load_data_coverage(engine))
    finally:
        await engine.dispose()

    summary["source_id"] = str(source_id)
    summary["ingestion"] = ingestion
    summary["data_readiness"] = readiness.as_dict()
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
