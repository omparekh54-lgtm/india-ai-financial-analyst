from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage
from app.db import create_database_engine
from app.ingestion.macro import MacroObservation, MacroObservationIngestor


def vix_macro_observation(*, ts: datetime, close: float) -> MacroObservation:
    if ts.tzinfo is None:
        raise ValueError("India VIX benchmark timestamp must be timezone-aware")
    if close < 0:
        raise ValueError("India VIX close cannot be negative")
    normalized_ts = ts.astimezone(UTC)
    return MacroObservation(
        series_key="india_vix",
        observation_date=normalized_ts.date(),
        value=close,
        unit="index points",
        released_at=normalized_ts,
        metadata={
            "source": "NSE",
            "benchmark_code": "INDIAVIX",
            "derivation": "latest_sourced_benchmark_close",
            "provenance_class": "official_source",
        },
    )


async def load_latest_sourced_vix(
    engine: AsyncEngine,
) -> tuple[MacroObservation, UUID]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    select bb.ts, bb.close, bb.source_id
                    from benchmark_bars bb
                    join benchmarks b on b.id = bb.benchmark_id
                    join sources src on src.id = bb.source_id
                    where b.code = 'INDIAVIX'
                      and bb.source_id is not null
                      and src.checksum is not null
                      and coalesce(src.metadata->>'provenance_class', '') = 'official_source'
                      and lower(coalesce(src.metadata->>'production_approved', 'false'))
                          in ('true', '1', 'yes', 'y', 'on')
                    order by bb.ts desc
                    limit 1
                    """
                )
            )
        ).mappings().one_or_none()
    if row is None:
        raise RuntimeError("No sourced production-approved INDIAVIX benchmark bar is available")
    source_id = row["source_id"]
    if not isinstance(source_id, UUID):
        source_id = UUID(str(source_id))
    return (
        vix_macro_observation(ts=row["ts"], close=float(row["close"])),
        source_id,
    )


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Synchronize the latest sourced official India VIX benchmark close into the "
            "india_vix macro series without a duplicate external data fetch."
        )
    )
    parser.add_argument("--max-age-days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.max_age_days < 0:
        parser.error("--max-age-days must be >= 0")

    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL must be configured")

    engine = create_database_engine(settings.database_url)
    try:
        observation, source_id = await load_latest_sourced_vix(engine)
        today = datetime.now(UTC).date()
        age_days = (today - observation.observation_date).days
        if age_days < 0:
            raise SystemExit(
                f"India VIX observation date {observation.observation_date.isoformat()} is in the future"
            )
        if age_days > args.max_age_days:
            raise SystemExit(
                "India VIX benchmark is stale: "
                f"{observation.observation_date.isoformat()} is {age_days} day(s) old; "
                f"maximum allowed is {args.max_age_days}"
            )

        summary: dict[str, object] = {
            "provider": "NSE",
            "provenance_class": "official_source",
            "series_key": observation.series_key,
            "benchmark_code": "INDIAVIX",
            "observation_date": observation.observation_date.isoformat(),
            "age_days": age_days,
            "max_age_days": args.max_age_days,
            "value": float(observation.value),
            "unit": observation.unit,
            "source_id": str(source_id),
            "dry_run": args.dry_run,
        }
        if args.dry_run:
            print(json.dumps(summary, indent=2, sort_keys=True, default=str))
            return 0

        ingestion = await MacroObservationIngestor(engine).ingest_batch(
            [observation],
            source_id=source_id,
        )
        readiness = evaluate_data_coverage(await load_data_coverage(engine))
    finally:
        await engine.dispose()

    summary["ingestion"] = ingestion
    summary["data_readiness"] = readiness.as_dict()
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
