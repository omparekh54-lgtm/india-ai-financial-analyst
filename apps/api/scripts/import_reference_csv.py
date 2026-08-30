from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from app.core.config import get_settings
from app.db import create_database_engine
from app.ingestion.macro import MacroObservationIngestor
from app.ingestion.market import MarketBarIngestor
from app.ingestion.reference_files import parse_benchmark_csv, parse_macro_csv


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import approved/licensed benchmark or macro CSV exports"
    )
    subparsers = parser.add_subparsers(dest="kind", required=True)

    benchmark = subparsers.add_parser("benchmark")
    benchmark.add_argument("--file", required=True)
    benchmark.add_argument("--benchmark-code", required=True)
    benchmark.add_argument("--provider", required=True)
    benchmark.add_argument("--interval", default="1d")
    benchmark.add_argument("--timezone", default="Asia/Kolkata")
    benchmark.add_argument("--min-rows", type=int, default=30)
    benchmark.add_argument("--dry-run", action="store_true")

    macro = subparsers.add_parser("macro")
    macro.add_argument("--file", required=True)
    macro.add_argument("--min-rows", type=int, default=1)
    macro.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    path = Path(args.file)
    content = path.read_text(encoding="utf-8-sig")
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()

    if args.kind == "benchmark":
        bars = parse_benchmark_csv(
            content,
            provider=args.provider,
            interval=args.interval,
            timezone=args.timezone,
            min_rows=args.min_rows,
        )
        summary: dict[str, object] = {
            "kind": "benchmark",
            "file": str(path.resolve()),
            "sha256": checksum,
            "benchmark_code": args.benchmark_code.strip().upper(),
            "provider": args.provider.strip().lower(),
            "interval": args.interval.strip().lower(),
            "row_count": len(bars),
            "minimum_rows": args.min_rows,
            "first_timestamp": min(bar.ts for bar in bars).isoformat(),
            "last_timestamp": max(bar.ts for bar in bars).isoformat(),
            "dry_run": args.dry_run,
        }
        if args.dry_run:
            print(json.dumps(summary, indent=2, sort_keys=True))
            return
        settings = get_settings()
        if not settings.database_url:
            raise SystemExit("DATABASE_URL must be configured")
        engine = create_database_engine(settings.database_url)
        try:
            result = await MarketBarIngestor(engine).ingest_benchmark_bars(
                benchmark_code=args.benchmark_code,
                bars=bars,
            )
        finally:
            await engine.dispose()
        summary["ingestion"] = result
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    observations = parse_macro_csv(content, min_rows=args.min_rows)
    summary = {
        "kind": "macro",
        "file": str(path.resolve()),
        "sha256": checksum,
        "row_count": len(observations),
        "minimum_rows": args.min_rows,
        "series_count": len({item.series_key for item in observations}),
        "first_observation_date": min(item.observation_date for item in observations).isoformat(),
        "last_observation_date": max(item.observation_date for item in observations).isoformat(),
        "dry_run": args.dry_run,
    }
    if args.dry_run:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return
    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL must be configured")
    engine = create_database_engine(settings.database_url)
    try:
        result = await MacroObservationIngestor(engine).ingest_batch(observations)
    finally:
        await engine.dispose()
    summary["ingestion"] = result
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
