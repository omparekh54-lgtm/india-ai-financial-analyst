from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.connectors.nse_benchmarks import (
    NSE_INDEX_HISTORY_PAGE,
    NSE_VIX_HISTORY_PAGE,
    NseHistoricalBenchmarkFetcher,
    normalize_benchmark_code,
)
from app.core.config import get_settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage
from app.db import create_database_engine
from app.ingestion.market import MarketBarIngestor, MarketBarInput

_SOURCE_PAGES = {
    "NIFTY50": NSE_INDEX_HISTORY_PAGE,
    "INDIAVIX": NSE_VIX_HISTORY_PAGE,
}


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format") from exc


def _canonical_checksum(code: str, bars: list[MarketBarInput]) -> str:
    payload = {
        "benchmark_code": code,
        "provider": "nse",
        "bars": [
            {
                "ts": bar.ts.astimezone(UTC).isoformat(),
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": None if bar.volume is None else float(bar.volume),
            }
            for bar in bars
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def _upsert_source(
    engine: AsyncEngine,
    *,
    benchmark_code: str,
    source_page: str,
    checksum: str,
    bars: list[MarketBarInput],
) -> UUID:
    first_timestamp = min(bar.ts for bar in bars).astimezone(UTC)
    last_timestamp = max(bar.ts for bar in bars).astimezone(UTC)
    source_uri = f"{source_page}#api-response-sha256={checksum}"
    metadata = json.dumps(
        {
            "provider": "nse",
            "provenance_class": "official_source",
            "production_approved": True,
            "benchmark_code": benchmark_code,
            "source_page": source_page,
            "api_response_sha256": checksum,
            "row_count": len(bars),
            "first_timestamp": first_timestamp.isoformat(),
            "last_timestamp": last_timestamp.isoformat(),
        },
        sort_keys=True,
    )
    params = {
        "source_uri": source_uri,
        "title": f"{benchmark_code} official NSE historical API response",
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
                  and source_type = 'benchmark_data'
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
                      null, 'benchmark_data', :source_uri, :title, null,
                      :retrieved_at, 'historical', :checksum, cast(:metadata as jsonb)
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
                        freshness = 'historical',
                        checksum = :checksum,
                        metadata = cast(:metadata as jsonb)
                    where id = :source_id
                    """
                ),
                {**params, "source_id": source_id},
            )
    if source_id is None:
        raise RuntimeError("Unable to resolve benchmark provenance source")
    return source_id


async def main() -> int:
    today = datetime.now(UTC).date()
    parser = argparse.ArgumentParser(
        description=(
            "Backfill official NIFTY 50 and India VIX daily history from NSE public endpoints. "
            "All requested benchmarks are fetched and validated before any database write occurs."
        )
    )
    parser.add_argument(
        "--benchmark",
        action="append",
        default=[],
        help="Repeat with NIFTY50 and/or INDIAVIX. Defaults to both.",
    )
    parser.add_argument("--from-date", type=_parse_iso_date, default=today - timedelta(days=365))
    parser.add_argument("--to-date", type=_parse_iso_date, default=today)
    parser.add_argument("--min-rows", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.from_date > args.to_date:
        parser.error("--from-date cannot be after --to-date")
    if (args.to_date - args.from_date).days > 370:
        parser.error("benchmark backfill windows cannot exceed 370 days")
    if args.min_rows < 2:
        parser.error("--min-rows must be >= 2")

    raw_codes = args.benchmark or ["NIFTY50", "INDIAVIX"]
    try:
        codes = tuple(dict.fromkeys(normalize_benchmark_code(code) for code in raw_codes))
    except ValueError as exc:
        parser.error(str(exc))

    fetched: dict[str, list[MarketBarInput]] = {}
    async with NseHistoricalBenchmarkFetcher() as fetcher:
        for code in codes:
            bars = await fetcher.fetch(code, from_date=args.from_date, to_date=args.to_date)
            if len(bars) < args.min_rows:
                raise SystemExit(
                    f"{code} returned only {len(bars)} daily rows; minimum expected is {args.min_rows}"
                )
            fetched[code] = bars

    summary: dict[str, object] = {
        "provider": "nse",
        "provenance_class": "official_source",
        "from_date": args.from_date.isoformat(),
        "to_date": args.to_date.isoformat(),
        "minimum_rows": args.min_rows,
        "dry_run": args.dry_run,
        "benchmarks": {
            code: {
                "row_count": len(bars),
                "first_timestamp": min(bar.ts for bar in bars).isoformat(),
                "last_timestamp": max(bar.ts for bar in bars).isoformat(),
                "source_page": _SOURCE_PAGES[code],
                "sha256": _canonical_checksum(code, bars),
            }
            for code, bars in fetched.items()
        },
    }
    if args.dry_run:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL must be configured")

    engine = create_database_engine(settings.database_url)
    ingestion_summary: dict[str, object] = {}
    try:
        for code, bars in fetched.items():
            checksum = _canonical_checksum(code, bars)
            source_id = await _upsert_source(
                engine,
                benchmark_code=code,
                source_page=_SOURCE_PAGES[code],
                checksum=checksum,
                bars=bars,
            )
            ingestion = await MarketBarIngestor(engine).ingest_benchmark_bars(
                benchmark_code=code,
                bars=bars,
                source_id=source_id,
            )
            ingestion_summary[code] = {
                "source_id": str(source_id),
                "ingestion": ingestion,
            }
        readiness = evaluate_data_coverage(await load_data_coverage(engine))
    finally:
        await engine.dispose()

    summary["ingestion"] = ingestion_summary
    summary["data_readiness"] = readiness.as_dict()
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
