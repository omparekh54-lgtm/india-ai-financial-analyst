from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from app.connectors.fred import FredConnector
from app.core.config import get_settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage
from app.db import create_database_engine
from app.ingestion.fred_macro import FRED_MACRO_SERIES, ingest_fred_series, parse_fred_series


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap approved FRED macro series into normalized macro observations"
    )
    parser.add_argument(
        "--series",
        action="append",
        choices=sorted(FRED_MACRO_SERIES),
        help="Series key to import. Repeat for multiple series; defaults to all approved series.",
    )
    parser.add_argument("--history-days", type=int, default=400)
    parser.add_argument("--min-rows", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.history_days < 30:
        raise SystemExit("--history-days must be >= 30")
    if args.min_rows < 2:
        raise SystemExit("--min-rows must be >= 2")

    settings = get_settings()
    if not settings.enable_external_data_calls:
        raise SystemExit(
            "ENABLE_EXTERNAL_DATA_CALLS must be true for the FRED bootstrap worker"
        )
    if not settings.fred_api_key:
        raise SystemExit("FRED_API_KEY must be configured")

    selected = args.series or list(FRED_MACRO_SERIES)
    observation_start = datetime.now(UTC).date() - timedelta(days=args.history_days)
    connector = FredConnector(settings)

    fetched: list[tuple[str, object]] = []
    validation: list[dict[str, object]] = []
    for series_key in selected:
        series = FRED_MACRO_SERIES[series_key]
        envelope = await connector.series_observations(
            series.series_id,
            observation_start=observation_start,
        )
        parsed = parse_fred_series(envelope, series, min_rows=args.min_rows)
        fetched.append((series_key, envelope))
        validation.append(
            {
                "series_key": series.series_key,
                "series_id": series.series_id,
                "row_count": len(parsed.observations),
                "skipped_missing": parsed.skipped_missing,
                "first_observation_date": parsed.first_observation_date.isoformat(),
                "last_observation_date": parsed.last_observation_date.isoformat(),
            }
        )

    summary: dict[str, object] = {
        "provider": "fred",
        "observation_start": observation_start.isoformat(),
        "history_days": args.history_days,
        "minimum_rows_per_series": args.min_rows,
        "dry_run": args.dry_run,
        "series": validation,
    }
    if args.dry_run:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    if not settings.database_url:
        raise SystemExit("DATABASE_URL must be configured")

    engine = create_database_engine(settings.database_url)
    try:
        ingestion: list[dict[str, object]] = []
        for series_key, envelope in fetched:
            series = FRED_MACRO_SERIES[series_key]
            ingestion.append(
                await ingest_fred_series(
                    engine,
                    envelope,
                    series,
                    min_rows=args.min_rows,
                )
            )
        coverage = await load_data_coverage(engine)
        readiness = evaluate_data_coverage(coverage)
    finally:
        await engine.dispose()

    summary["ingestion"] = ingestion
    summary["data_readiness"] = readiness.as_dict()
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
