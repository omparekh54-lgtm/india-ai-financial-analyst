from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from app.core.config import get_settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage
from app.db import create_database_engine
from app.ingestion.market import MarketBarIngestor
from app.ingestion.official_benchmark_files import (
    benchmark_artifact_uri,
    resolve_official_benchmark_source,
    upsert_benchmark_artifact_source,
)
from app.ingestion.reference_files import parse_benchmark_csv


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import an official NSE/NSE Indices historical benchmark CSV with provenance"
    )
    parser.add_argument("--file", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--benchmark-code", required=True)
    parser.add_argument("--timezone", default="Asia/Kolkata")
    parser.add_argument("--min-rows", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.is_file():
        raise SystemExit(f"Input file does not exist: {path}")
    if path.suffix.lower() != ".csv":
        raise SystemExit("Official benchmark importer accepts CSV exports only")
    if args.min_rows < 2:
        raise SystemExit("--min-rows must be >= 2")

    try:
        source = resolve_official_benchmark_source(args.benchmark_code, args.source_url)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    content = path.read_text(encoding="utf-8-sig")
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    try:
        bars = parse_benchmark_csv(
            content,
            provider=source.provider,
            interval="1d",
            timezone=args.timezone,
            min_rows=args.min_rows,
        )
        artifact_uri = benchmark_artifact_uri(source.source_url, checksum)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    first_timestamp = min(bar.ts for bar in bars)
    last_timestamp = max(bar.ts for bar in bars)
    summary: dict[str, object] = {
        "benchmark_code": source.benchmark_code,
        "provider": source.provider,
        "file": str(path.resolve()),
        "source_url": source.source_url,
        "artifact_uri": artifact_uri,
        "sha256": checksum,
        "row_count": len(bars),
        "minimum_rows": args.min_rows,
        "first_timestamp": first_timestamp.isoformat(),
        "last_timestamp": last_timestamp.isoformat(),
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
        source_id = await upsert_benchmark_artifact_source(
            engine,
            source=source,
            sha256=checksum,
            row_count=len(bars),
            first_timestamp=first_timestamp,
            last_timestamp=last_timestamp,
        )
        ingestion = await MarketBarIngestor(engine).ingest_benchmark_bars(
            benchmark_code=source.benchmark_code,
            bars=bars,
            source_id=source_id,
        )
        readiness = evaluate_data_coverage(await load_data_coverage(engine))
    finally:
        await engine.dispose()

    summary["source_id"] = str(source_id)
    summary["ingestion"] = ingestion
    summary["data_readiness"] = readiness.as_dict()
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
