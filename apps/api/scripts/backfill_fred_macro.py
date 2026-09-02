from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date

from app.connectors.fred import FredConnector
from app.core.config import get_settings
from app.db import create_database_engine
from app.ingestion.fred_macro import FRED_MACRO_SERIES, ingest_fred_series


async def _run() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill real USD/INR and Brent macro observations from official FRED data."
    )
    parser.add_argument(
        "--series",
        action="append",
        choices=tuple(sorted(FRED_MACRO_SERIES)) + ("all",),
        default=[],
        help="Repeat for specific series or use --series all. Defaults to all configured series.",
    )
    parser.add_argument("--observation-start", type=date.fromisoformat)
    parser.add_argument("--min-rows", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.min_rows < 1:
        parser.error("--min-rows must be >= 1")

    requested = args.series or ["all"]
    selected = (
        tuple(FRED_MACRO_SERIES)
        if "all" in requested
        else tuple(dict.fromkeys(requested))
    )
    settings = get_settings()
    if not settings.database_url:
        parser.error("DATABASE_URL must be configured")

    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "data_policy": "real_official_provider_data_only",
                    "provider": "fred",
                    "observation_start": (
                        args.observation_start.isoformat() if args.observation_start else None
                    ),
                    "series": [
                        {
                            "series_key": key,
                            "series_id": FRED_MACRO_SERIES[key].series_id,
                            "title": FRED_MACRO_SERIES[key].title,
                            "unit": FRED_MACRO_SERIES[key].unit,
                        }
                        for key in selected
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if not settings.enable_external_data_calls:
        parser.error("ENABLE_EXTERNAL_DATA_CALLS must be true for FRED backfill")
    if not settings.fred_api_key:
        parser.error("FRED_API_KEY must be configured for FRED backfill")

    engine = create_database_engine(settings.database_url)
    connector = FredConnector(settings)
    results: list[dict[str, object]] = []
    failure_count = 0
    try:
        for key in selected:
            series = FRED_MACRO_SERIES[key]
            try:
                envelope = await connector.series_observations(
                    series.series_id,
                    observation_start=args.observation_start,
                )
                result = await ingest_fred_series(
                    engine,
                    envelope,
                    series,
                    min_rows=args.min_rows,
                )
                results.append({"ok": True, **result})
            except (RuntimeError, TypeError, ValueError) as exc:
                failure_count += 1
                results.append(
                    {
                        "ok": False,
                        "series_key": key,
                        "series_id": series.series_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
    finally:
        await engine.dispose()

    print(
        json.dumps(
            {
                "status": "completed" if failure_count == 0 else "completed_with_failures",
                "data_policy": "real_official_provider_data_only",
                "provider": "fred",
                "failure_count": failure_count,
                "results": results,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
