from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from app.core.config import get_settings
from app.db import create_database_engine
from app.ingestion.metrics import SecurityMetricIngestor, normalize_security_metric
from app.ingestion.reference_metrics import parse_security_metrics_csv
from app.ingestion.reference_provenance import (
    parse_optional_datetime,
    resolve_security,
    upsert_reference_source,
    validate_source_uri,
)


async def _run(args: argparse.Namespace) -> dict[str, object]:
    path = Path(args.file)
    content = path.read_text(encoding="utf-8-sig")
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    metrics = parse_security_metrics_csv(content, min_rows=args.min_rows)
    normalized = [normalize_security_metric(metric) for metric in metrics]
    source_uri = validate_source_uri(args.source_uri)
    published_at = parse_optional_datetime(args.published_at)
    summary: dict[str, object] = {
        "file": str(path.resolve()),
        "security": args.security.strip(),
        "source_uri": source_uri,
        "sha256": checksum,
        "input_count": len(metrics),
        "normalized_count": len(normalized),
        "first_as_of_date": min(metric.as_of_date for metric in normalized).isoformat(),
        "last_as_of_date": max(metric.as_of_date for metric in normalized).isoformat(),
        "metric_names": sorted({metric.metric_name for metric in normalized}),
        "dry_run": args.dry_run,
    }
    if args.dry_run:
        return summary

    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be configured")
    engine = create_database_engine(settings.database_url)
    try:
        security_id, legal_name = await resolve_security(engine, args.security)
        title = args.source_title or f"Approved comparable metrics — {legal_name}"
        source_id = await upsert_reference_source(
            engine,
            security_id=security_id,
            source_type="reference_security_metrics",
            source_uri=source_uri,
            title=title,
            published_at=published_at,
            checksum=checksum,
            metadata={
                "importer": "import_security_metrics_csv",
                "file_name": path.name,
                "sha256": checksum,
            },
        )
        ingestion = await SecurityMetricIngestor(engine).ingest_batch(
            security_id=security_id,
            source_id=source_id,
            metrics=metrics,
        )
    finally:
        await engine.dispose()

    summary.update(
        {
            "security_id": str(security_id),
            "legal_name": legal_name,
            "source_id": str(source_id),
            "ingestion": ingestion,
        }
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import an approved/licensed one-security comparable-metrics CSV export."
    )
    parser.add_argument("--file", required=True)
    parser.add_argument(
        "--security",
        required=True,
        help="NSE symbol, BSE code or ISIN already present in the canonical security master.",
    )
    parser.add_argument("--source-uri", required=True)
    parser.add_argument("--source-title")
    parser.add_argument("--published-at")
    parser.add_argument("--min-rows", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.min_rows < 1:
        parser.error("--min-rows must be >= 1")

    try:
        summary = asyncio.run(_run(args))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
