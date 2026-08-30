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
from app.ingestion.reference_provenance import (
    parse_optional_datetime,
    upsert_reference_source,
    validate_provider_name,
    validate_source_uri,
)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import approved/licensed benchmark or macro CSV exports with mandatory source "
            "provenance. Synthetic/mock/sample sources are rejected."
        )
    )
    subparsers = parser.add_subparsers(dest="kind", required=True)

    benchmark = subparsers.add_parser("benchmark")
    _add_provenance_arguments(benchmark)
    benchmark.add_argument("--file", required=True)
    benchmark.add_argument("--benchmark-code", required=True)
    benchmark.add_argument("--provider", required=True)
    benchmark.add_argument("--interval", default="1d")
    benchmark.add_argument("--timezone", default="Asia/Kolkata")
    benchmark.add_argument("--min-rows", type=int, default=30)
    benchmark.add_argument("--dry-run", action="store_true")

    macro = subparsers.add_parser("macro")
    _add_provenance_arguments(macro)
    macro.add_argument("--file", required=True)
    macro.add_argument("--min-rows", type=int, default=1)
    macro.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    path = Path(args.file)
    content = path.read_text(encoding="utf-8-sig")
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    source_uri = validate_source_uri(args.source_uri)
    explicit_published_at = parse_optional_datetime(args.published_at)

    if args.kind == "benchmark":
        provider = validate_provider_name(args.provider)
        benchmark_code = args.benchmark_code.strip().upper()
        bars = parse_benchmark_csv(
            content,
            provider=provider,
            interval=args.interval,
            timezone=args.timezone,
            min_rows=args.min_rows,
        )
        published_at = explicit_published_at or max(bar.ts for bar in bars)
        summary: dict[str, object] = {
            "kind": "benchmark",
            "file": str(path.resolve()),
            "source_uri": source_uri,
            "sha256": checksum,
            "benchmark_code": benchmark_code,
            "provider": provider,
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
            source_id = await upsert_reference_source(
                engine,
                security_id=None,
                source_type="reference_benchmark_data",
                source_uri=source_uri,
                title=args.source_title or f"Approved benchmark export — {benchmark_code}",
                published_at=published_at,
                checksum=checksum,
                metadata={
                    "importer": "import_reference_csv",
                    "kind": "benchmark",
                    "file_name": path.name,
                    "sha256": checksum,
                    "provider": provider,
                    "benchmark_code": benchmark_code,
                    "interval": args.interval.strip().lower(),
                },
            )
            result = await MarketBarIngestor(engine).ingest_benchmark_bars(
                benchmark_code=benchmark_code,
                bars=bars,
                source_id=source_id,
            )
        finally:
            await engine.dispose()
        summary["source_id"] = str(source_id)
        summary["ingestion"] = result
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    observations = parse_macro_csv(content, min_rows=args.min_rows)
    released_times = [item.released_at for item in observations if item.released_at is not None]
    published_at = explicit_published_at or (max(released_times) if released_times else None)
    summary = {
        "kind": "macro",
        "file": str(path.resolve()),
        "source_uri": source_uri,
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
        source_id = await upsert_reference_source(
            engine,
            security_id=None,
            source_type="reference_macro_data",
            source_uri=source_uri,
            title=args.source_title or "Approved macro export",
            published_at=published_at,
            checksum=checksum,
            metadata={
                "importer": "import_reference_csv",
                "kind": "macro",
                "file_name": path.name,
                "sha256": checksum,
                "series_keys": sorted({item.series_key for item in observations}),
            },
        )
        result = await MacroObservationIngestor(engine).ingest_batch(
            observations,
            source_id=source_id,
        )
    finally:
        await engine.dispose()
    summary["source_id"] = str(source_id)
    summary["ingestion"] = result
    print(json.dumps(summary, indent=2, sort_keys=True))


def _add_provenance_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-uri", required=True)
    parser.add_argument("--source-title")
    parser.add_argument("--published-at")


if __name__ == "__main__":
    asyncio.run(main())
