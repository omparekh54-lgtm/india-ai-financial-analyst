from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage
from app.db import create_database_engine
from app.ingestion.market import MarketBarIngestor
from app.ingestion.official_security_market_files import (
    resolve_official_security_market_source,
    security_market_artifact_uri,
    upsert_security_market_artifact_source,
    validate_security_export_identity,
)
from app.ingestion.reference_files import parse_benchmark_csv


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import an official NSE historical security CSV with source provenance"
    )
    parser.add_argument("--file", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--nse-symbol", required=True)
    parser.add_argument("--timezone", default="Asia/Kolkata")
    parser.add_argument("--min-rows", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.is_file():
        raise SystemExit(f"Input file does not exist: {path}")
    if path.suffix.lower() != ".csv":
        raise SystemExit("Official NSE security market importer accepts CSV exports only")
    if args.min_rows < 2:
        raise SystemExit("--min-rows must be >= 2")

    try:
        source = resolve_official_security_market_source(args.nse_symbol, args.source_url)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    raw = path.read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    try:
        content = raw.decode("utf-8-sig")
        validate_security_export_identity(content, nse_symbol=source.nse_symbol)
        bars = parse_benchmark_csv(
            content,
            provider=source.provider,
            interval="1d",
            timezone=args.timezone,
            min_rows=args.min_rows,
        )
        artifact_uri = security_market_artifact_uri(source.source_url, checksum)
    except (UnicodeDecodeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    first_timestamp = min(bar.ts for bar in bars)
    last_timestamp = max(bar.ts for bar in bars)
    summary: dict[str, object] = {
        "nse_symbol": source.nse_symbol,
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
        security_id, legal_name = await _resolve_nse_security(engine, source.nse_symbol)
        source_id = await upsert_security_market_artifact_source(
            engine,
            security_id=security_id,
            legal_name=legal_name,
            source=source,
            sha256=checksum,
            row_count=len(bars),
            first_timestamp=first_timestamp,
            last_timestamp=last_timestamp,
        )
        ingestion = await MarketBarIngestor(engine).ingest_security_bars(
            security_id=security_id,
            bars=bars,
            source_id=source_id,
        )
        readiness = evaluate_data_coverage(await load_data_coverage(engine))
    finally:
        await engine.dispose()

    summary["security_id"] = str(security_id)
    summary["source_id"] = str(source_id)
    summary["legal_name"] = legal_name
    summary["ingestion"] = ingestion
    summary["data_readiness"] = readiness.as_dict()
    print(json.dumps(summary, indent=2, sort_keys=True))


async def _resolve_nse_security(engine: AsyncEngine, nse_symbol: str) -> tuple[UUID, str]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    select id, legal_name
                    from securities
                    where upper(nse_symbol) = :symbol
                      and primary_exchange = 'NSE'
                    limit 1
                    """
                ),
                {"symbol": nse_symbol.upper()},
            )
        ).mappings().one_or_none()
    if row is None:
        raise ValueError(f"NSE security is not in the security master: {nse_symbol}")
    return row["id"], str(row["legal_name"])


if __name__ == "__main__":
    asyncio.run(main())
