from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from app.core.config import get_settings
from app.db import create_database_engine
from app.ingestion.financials import FinancialFactIngestor, normalize_financial_facts
from app.ingestion.reference_financials import parse_financial_csv
from app.ingestion.reference_provenance import (
    parse_optional_datetime,
    resolve_security,
    upsert_reference_source,
    validate_reference_approval,
    validate_source_uri,
)


async def _run(args: argparse.Namespace) -> dict[str, object]:
    path = Path(args.file)
    content = path.read_text(encoding="utf-8-sig")
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    facts = parse_financial_csv(content, min_rows=args.min_rows)
    normalized = normalize_financial_facts(facts)
    source_uri = validate_source_uri(args.source_uri)
    approval = validate_reference_approval(source_uri, args.approval_reference)
    published_at = parse_optional_datetime(args.published_at)
    summary: dict[str, object] = {
        "file": str(path.resolve()),
        "security": args.security.strip(),
        "source_uri": source_uri,
        "provenance_class": approval.provenance_class,
        "approval_reference": approval.approval_reference,
        "sha256": checksum,
        "input_count": len(facts),
        "normalized_count": len(normalized),
        "derived_count": sum(bool(fact.metadata.get("derived")) for fact in normalized),
        "first_period_end": min(fact.period_end for fact in normalized).isoformat(),
        "last_period_end": max(fact.period_end for fact in normalized).isoformat(),
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
        title = args.source_title or f"Approved financial export — {legal_name}"
        source_id = await upsert_reference_source(
            engine,
            security_id=security_id,
            source_type="reference_financials",
            source_uri=source_uri,
            title=title,
            published_at=published_at,
            checksum=checksum,
            approval_reference=args.approval_reference,
            metadata={
                "importer": "import_financial_csv",
                "file_name": path.name,
                "sha256": checksum,
            },
        )
        ingestion = await FinancialFactIngestor(engine).ingest_batch(
            security_id=security_id,
            source_id=source_id,
            facts=facts,
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
        description="Import an approved/licensed one-security financial-facts CSV export."
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
    parser.add_argument("--min-rows", type=int, default=5)
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
