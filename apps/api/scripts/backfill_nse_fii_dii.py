from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.connectors.nse_flows import (
    NSE_FII_DII_API,
    NSE_FII_DII_PAGE,
    NseFiiDiiCashFlowFetcher,
)
from app.core.config import get_settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage
from app.db import create_database_engine
from app.ingestion.macro import MacroObservation, MacroObservationIngestor


def canonical_checksum(observations: list[MacroObservation]) -> str:
    payload = [
        {
            "series_key": item.series_key,
            "observation_date": item.observation_date.isoformat(),
            "value": float(item.value),
            "unit": item.unit,
            "buy_value_cr": item.metadata.get("buy_value_cr"),
            "sell_value_cr": item.metadata.get("sell_value_cr"),
        }
        for item in observations
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def upsert_source(
    engine: AsyncEngine,
    *,
    observations: list[MacroObservation],
    checksum: str,
) -> UUID:
    observation_date = observations[0].observation_date
    source_uri = f"{NSE_FII_DII_API}#date={observation_date.isoformat()}&sha256={checksum}"
    metadata = json.dumps(
        {
            "provider": "NSE",
            "provenance_class": "official_source",
            "production_approved": True,
            "provisional": True,
            "report_page": NSE_FII_DII_PAGE,
            "api_endpoint": NSE_FII_DII_API,
            "observation_date": observation_date.isoformat(),
            "series_keys": sorted(item.series_key for item in observations),
            "api_response_sha256": checksum,
        },
        sort_keys=True,
    )
    params = {
        "source_uri": source_uri,
        "title": "NSE FII/FPI & DII capital-market trading activity",
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
                  and source_type = 'exchange_flow_data'
                  and source_uri = :source_uri
                  and published_at is null
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
                      null, 'exchange_flow_data', :source_uri, :title, null,
                      :retrieved_at, 'near_live', :checksum, cast(:metadata as jsonb)
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
                        freshness = 'near_live',
                        checksum = :checksum,
                        metadata = cast(:metadata as jsonb)
                    where id = :source_id
                    """
                ),
                {**params, "source_id": source_id},
            )
    if source_id is None:
        raise RuntimeError("Unable to resolve NSE FII/DII provenance source")
    return source_id


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch the official NSE FII/FPI and DII capital-market flow snapshot and store "
            "provenance-linked macro observations."
        )
    )
    parser.add_argument("--max-age-days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.max_age_days < 0:
        parser.error("--max-age-days must be >= 0")

    async with NseFiiDiiCashFlowFetcher() as fetcher:
        observations = await fetcher.fetch()

    observation_date = observations[0].observation_date
    today = datetime.now(UTC).date()
    age_days = (today - observation_date).days
    if age_days < 0:
        raise SystemExit(
            f"NSE FII/DII reporting date {observation_date.isoformat()} is in the future"
        )
    if age_days > args.max_age_days:
        raise SystemExit(
            "NSE FII/DII response is stale: "
            f"{observation_date.isoformat()} is {age_days} day(s) old; "
            f"maximum allowed is {args.max_age_days}"
        )

    checksum = canonical_checksum(observations)
    summary: dict[str, object] = {
        "provider": "NSE",
        "provenance_class": "official_source",
        "source_page": NSE_FII_DII_PAGE,
        "source_endpoint": NSE_FII_DII_API,
        "provisional": True,
        "observation_date": observation_date.isoformat(),
        "age_days": age_days,
        "max_age_days": args.max_age_days,
        "sha256": checksum,
        "dry_run": args.dry_run,
        "observations": [
            {
                "series_key": item.series_key,
                "value": float(item.value),
                "unit": item.unit,
                "buy_value_cr": item.metadata.get("buy_value_cr"),
                "sell_value_cr": item.metadata.get("sell_value_cr"),
            }
            for item in observations
        ],
    }
    if args.dry_run:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL must be configured")

    engine = create_database_engine(settings.database_url)
    try:
        source_id = await upsert_source(engine, observations=observations, checksum=checksum)
        ingestion = await MacroObservationIngestor(engine).ingest_batch(
            observations,
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
