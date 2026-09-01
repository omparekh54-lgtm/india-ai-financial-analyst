from __future__ import annotations

import argparse
import asyncio
import hashlib
import json

from app.connectors.http_fetcher import SafeHttpFetcher
from app.connectors.india_official import MacroSeriesSpec
from app.core.config import get_settings
from app.db import create_database_engine
from app.ingestion.india_official import OfficialIndiaIngestionService
from app.ingestion.official_macro_files import (
    RBI_IMPORT_SERIES,
    validate_macro_observations,
    validate_official_source_url,
    validate_rbi_series_key,
)
from app.connectors.india_official import parse_rbi_macro_series

RBI_ALLOWED_DOMAINS = {"rbi.org.in", "statistics.rbi.org.in"}


def build_spec(
    *,
    series_key: str,
    unit: str | None,
    date_column: str | None,
    value_column: str | None,
) -> MacroSeriesSpec:
    return MacroSeriesSpec(
        series_key=validate_rbi_series_key(series_key),
        unit=unit,
        date_column=date_column,
        value_column=value_column,
    )


async def _run(args: argparse.Namespace) -> dict[str, object]:
    source_url = validate_official_source_url("RBI", args.source_url)
    spec = build_spec(
        series_key=args.series_key,
        unit=args.unit,
        date_column=args.date_column,
        value_column=args.value_column,
    )
    fetcher = SafeHttpFetcher(allowed_domains=RBI_ALLOWED_DOMAINS, max_bytes=15 * 1024 * 1024)
    document = await fetcher.fetch(source_url)
    if document.not_modified or not document.content:
        raise RuntimeError("RBI source returned no content")

    observations = validate_macro_observations(
        parse_rbi_macro_series(document.content, document.media_type, spec),
        allowed_series=RBI_IMPORT_SERIES,
        min_rows=args.min_rows,
    )
    checksum = hashlib.sha256(document.content).hexdigest()
    summary: dict[str, object] = {
        "provider": "RBI",
        "series_key": spec.series_key,
        "source_url": source_url,
        "final_url": document.final_url,
        "media_type": document.media_type,
        "sha256": checksum,
        "row_count": len(observations),
        "first_observation_date": min(item.observation_date for item in observations).isoformat(),
        "last_observation_date": max(item.observation_date for item in observations).isoformat(),
        "dry_run": args.dry_run,
    }
    if args.dry_run:
        return summary

    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be configured")
    engine = create_database_engine(settings.database_url)
    try:
        ingestion = await OfficialIndiaIngestionService(engine).ingest_rbi_series(
            data=document.content,
            media_type=document.media_type,
            spec=spec,
            source_uri=document.final_url,
            title=args.title or f"RBI {spec.series_key} official export",
        )
    finally:
        await engine.dispose()
    summary["ingestion"] = ingestion
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch and ingest one approved RBI macro export from an official HTTPS URL. "
            "The fetch is SSRF-guarded and fails closed when the payload cannot be parsed."
        )
    )
    parser.add_argument("--series-key", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--unit")
    parser.add_argument("--date-column")
    parser.add_argument("--value-column")
    parser.add_argument("--title")
    parser.add_argument("--min-rows", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.min_rows < 1:
        parser.error("--min-rows must be >= 1")
    try:
        summary = asyncio.run(_run(args))
    except (RuntimeError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps({"status": "completed", **summary}, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
