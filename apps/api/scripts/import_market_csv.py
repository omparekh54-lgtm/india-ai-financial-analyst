from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from app.core.config import get_settings
from app.db import create_database_engine
from app.ingestion.market import MarketBarIngestor
from app.ingestion.reference_files import parse_benchmark_csv
from app.ingestion.reference_provenance import (
    parse_optional_datetime,
    resolve_security,
    upsert_reference_source,
    validate_provider_name,
    validate_reference_approval,
    validate_source_uri,
)


async def _run(args: argparse.Namespace) -> dict[str, object]:
    path = Path(args.file)
    content = path.read_text(encoding="utf-8-sig")
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    provider = validate_provider_name(args.provider)
    bars = parse_benchmark_csv(
        content,
        provider=provider,
        interval=args.interval,
        timezone=args.timezone,
        min_rows=args.min_rows,
    )
    source_uri = validate_source_uri(args.source_uri)
    approval = validate_reference_approval(source_uri, args.approval_reference)
    published_at = parse_optional_datetime(args.published_at) or max(bar.ts for bar in bars)
    summary: dict[str, object] = {
        "file": str(path.resolve()),
        "security": args.security.strip(),
        "source_uri": source_uri,
        "provenance_class": approval.provenance_class,
        "approval_reference": approval.approval_reference,
        "sha256": checksum,
        "provider": provider,
        "interval": args.interval.strip().lower(),
        "row_count": len(bars),
        "first_timestamp": min(bar.ts for bar in bars).isoformat(),
        "last_timestamp": max(bar.ts for bar in bars).isoformat(),
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
        title = args.source_title or f"Approved market history — {legal_name}"
        source_id = await upsert_reference_source(
            engine,
            security_id=security_id,
            source_type="reference_market_data",
            source_uri=source_uri,
            title=title,
            published_at=published_at,
            checksum=checksum,
            approval_reference=args.approval_reference,
            metadata={
                "importer": "import_market_csv",
                "file_name": path.name,
                "sha256": checksum,
                "provider": provider,
                "interval": args.interval.strip().lower(),
            },
        )
        ingestion = await MarketBarIngestor(engine).ingest_security_bars(
            security_id=security_id,
            source_id=source_id,
            bars=bars,
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
        description="Import an approved/licensed one-security OHLCV history CSV export."
    )
    parser.add_argument("--file", required=True)
    parser.add_argument(
        "--security",
        required=True,
        help="NSE symbol, BSE code or ISIN already present in the canonical security master.",
    )
    parser.add_argument("--source-uri", required=True)
    parser.add_argument(
        "--approval-reference",
        help=(
            "Required for non-official sources: license, contract, internal approval, or "
            "source-governance record identifier."
        ),
    )
    parser.add_argument("--source-title")
    parser.add_argument("--published-at")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--timezone", default="Asia/Kolkata")
    parser.add_argument("--min-rows", type=int, default=30)
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
