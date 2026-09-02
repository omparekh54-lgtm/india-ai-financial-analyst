from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from app.connectors.india_official import MacroSeriesSpec, parse_nsdl_flows, parse_rbi_macro_series
from app.core.config import get_settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage
from app.db import create_database_engine
from app.ingestion.india_official import OfficialIndiaIngestionService
from app.ingestion.official_macro_files import (
    NSDL_IMPORT_SERIES,
    RBI_IMPORT_SERIES,
    resolve_media_type,
    validate_macro_observations,
    validate_official_source_url,
    validate_rbi_series_key,
)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import an official RBI or NSDL macro export with provenance validation"
    )
    subparsers = parser.add_subparsers(dest="provider", required=True)

    rbi = subparsers.add_parser("rbi")
    _add_common_arguments(rbi)
    rbi.add_argument("--series-key", required=True)
    rbi.add_argument("--unit")
    rbi.add_argument("--date-column")
    rbi.add_argument("--value-column")

    nsdl = subparsers.add_parser("nsdl")
    _add_common_arguments(nsdl)

    args = parser.parse_args()
    provider = args.provider.upper()
    path = Path(args.file)
    if not path.is_file():
        raise SystemExit(f"Input file does not exist: {path}")
    if args.min_rows < 1:
        raise SystemExit("--min-rows must be >= 1")

    try:
        source_url = validate_official_source_url(provider, args.source_url)
        media_type = resolve_media_type(path, args.media_type)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    data = path.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()

    if provider == "RBI":
        try:
            series_key = validate_rbi_series_key(args.series_key)
            spec = MacroSeriesSpec(
                series_key=series_key,
                unit=args.unit,
                date_column=args.date_column,
                value_column=args.value_column,
            )
            observations = validate_macro_observations(
                parse_rbi_macro_series(data, media_type, spec),
                allowed_series=RBI_IMPORT_SERIES,
                min_rows=args.min_rows,
            )
        except (TypeError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        title = args.title or f"RBI {series_key} official export"
    else:
        try:
            observations = validate_macro_observations(
                parse_nsdl_flows(data, media_type),
                allowed_series=NSDL_IMPORT_SERIES,
                min_rows=args.min_rows,
            )
        except (TypeError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        series_key = None
        title = args.title or "NSDL FPI/DII investment data official export"

    summary: dict[str, object] = {
        "provider": provider,
        "file": str(path.resolve()),
        "source_url": source_url,
        "media_type": media_type,
        "sha256": checksum,
        "row_count": len(observations),
        "minimum_rows": args.min_rows,
        "series_keys": sorted({item.series_key for item in observations}),
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
        service = OfficialIndiaIngestionService(engine)
        if provider == "RBI":
            ingestion = await service.ingest_rbi_series(
                data=data,
                media_type=media_type,
                spec=spec,
                source_uri=source_url,
                title=title,
            )
        else:
            ingestion = await service.ingest_nsdl_flows(
                data=data,
                media_type=media_type,
                source_uri=source_url,
                title=title,
            )
        readiness = evaluate_data_coverage(await load_data_coverage(engine))
    finally:
        await engine.dispose()

    summary["ingestion"] = ingestion
    summary["data_readiness"] = readiness.as_dict()
    print(json.dumps(summary, indent=2, sort_keys=True))


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--file", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--title")
    parser.add_argument("--media-type")
    parser.add_argument("--min-rows", type=int, default=12)
    parser.add_argument("--dry-run", action="store_true")


if __name__ == "__main__":
    asyncio.run(main())
