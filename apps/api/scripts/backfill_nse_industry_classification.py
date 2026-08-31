from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.connectors.http_fetcher import SourceFetchError
from app.connectors.nse_classification import (
    NseIndustryClassification,
    NseIndustryClassificationFetcher,
)
from app.core.config import get_settings
from app.db import create_database_engine


@dataclass(frozen=True)
class ClassificationTarget:
    security_id: UUID
    symbol: str
    isin: str


async def load_targets(
    database_url: str,
    *,
    limit: int | None,
    refresh_all: bool,
) -> list[ClassificationTarget]:
    engine = create_database_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    select id, nse_symbol, isin
                    from securities
                    where primary_exchange = 'NSE'
                      and coalesce(metadata->>'nse_series', 'EQ') = 'EQ'
                      and nse_symbol is not null
                      and isin is not null
                      and (
                        :refresh_all
                        or sector is null
                        or industry is null
                        or nullif(btrim(coalesce(metadata->>'classification_source_uri', '')), '')
                          is null
                        or coalesce(metadata->>'classification_taxonomy', '') <> 'NSE_INDICES_4_TIER'
                      )
                    order by nse_symbol
                    limit :limit
                    """
                ),
                {"refresh_all": refresh_all, "limit": limit},
            )
            return [
                ClassificationTarget(
                    security_id=row["id"],
                    symbol=str(row["nse_symbol"]),
                    isin=str(row["isin"]),
                )
                for row in result.mappings().all()
            ]
    finally:
        await engine.dispose()


async def fetch_classifications(
    targets: list[ClassificationTarget],
    *,
    delay_ms: int,
) -> tuple[dict[UUID, NseIndustryClassification], list[dict[str, str]]]:
    results: dict[UUID, NseIndustryClassification] = {}
    failures: list[dict[str, str]] = []
    async with NseIndustryClassificationFetcher() as fetcher:
        for index, target in enumerate(targets):
            try:
                results[target.security_id] = await fetcher.fetch(
                    target.symbol,
                    expected_isin=target.isin,
                )
            except (SourceFetchError, TypeError, ValueError) as exc:
                failures.append({"symbol": target.symbol, "error": str(exc)})
            if index + 1 < len(targets):
                await asyncio.sleep(delay_ms / 1000.0)
    return results, failures


async def persist_classifications(
    database_url: str,
    results: dict[UUID, NseIndustryClassification],
) -> int:
    engine = create_database_engine(database_url)
    select_source = text(
        """
        select id
        from sources
        where security_id = :security_id
          and source_type = 'nse_industry_classification'
          and source_uri = :source_uri
          and published_at is null
        order by retrieved_at desc
        limit 1
        """
    )
    insert_source = text(
        """
        insert into sources (
          security_id, source_type, source_uri, title, freshness, checksum, metadata
        ) values (
          :security_id,
          'nse_industry_classification',
          :source_uri,
          :title,
          'periodic',
          :checksum,
          cast(:metadata as jsonb)
        )
        returning id
        """
    )
    update_source = text(
        """
        update sources
        set title = :title,
            freshness = 'periodic',
            checksum = :checksum,
            metadata = cast(:metadata as jsonb),
            retrieved_at = now()
        where id = :source_id
        """
    )
    update_security = text(
        """
        update securities
        set sector = :sector,
            industry = :industry,
            metadata = metadata || jsonb_build_object(
              'classification_taxonomy', 'NSE_INDICES_4_TIER',
              'classification_provenance_class', 'official_source',
              'classification_source_type', 'nse_industry_classification',
              'classification_source_uri', :source_uri,
              'classification_source_id', cast(:source_id as text),
              'classification_sha256', :checksum,
              'classification_retrieved_at', :retrieved_at,
              'nse_macro_sector', :macro_sector,
              'nse_basic_industry', :basic_industry
            ),
            updated_at = now()
        where id = :security_id
        """
    )

    updated = 0
    try:
        async with engine.begin() as connection:
            for security_id, classification in results.items():
                canonical_payload = {
                    "symbol": classification.symbol,
                    "isin": classification.isin,
                    "macro_sector": classification.macro_sector,
                    "sector": classification.sector,
                    "industry": classification.industry,
                    "basic_industry": classification.basic_industry,
                }
                checksum = hashlib.sha256(
                    json.dumps(
                        canonical_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                retrieved_at = datetime.now(UTC).isoformat()
                source_metadata = json.dumps(
                    {
                        "provenance_class": "official_source",
                        "production_approved": True,
                        "taxonomy": "NSE_INDICES_4_TIER",
                        **canonical_payload,
                    },
                    sort_keys=True,
                )
                source_params: dict[str, Any] = {
                    "security_id": security_id,
                    "source_uri": classification.source_uri,
                    "title": f"NSE industry classification — {classification.symbol}",
                    "checksum": checksum,
                    "metadata": source_metadata,
                }
                source_id = await connection.scalar(select_source, source_params)
                if source_id is None:
                    source_id = (
                        await connection.execute(insert_source, source_params)
                    ).scalar_one()
                else:
                    await connection.execute(
                        update_source,
                        {**source_params, "source_id": source_id},
                    )

                await connection.execute(
                    update_security,
                    {
                        "security_id": security_id,
                        "sector": classification.sector,
                        "industry": classification.industry,
                        "source_uri": classification.source_uri,
                        "source_id": source_id,
                        "checksum": checksum,
                        "retrieved_at": retrieved_at,
                        "macro_sector": classification.macro_sector,
                        "basic_industry": classification.basic_industry,
                    },
                )
                updated += 1
    finally:
        await engine.dispose()
    return updated


async def coverage_snapshot(database_url: str) -> dict[str, int]:
    engine = create_database_engine(database_url)
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        """
                        with nse_eq as (
                          select *
                          from securities
                          where primary_exchange = 'NSE'
                            and coalesce(metadata->>'nse_series', 'EQ') = 'EQ'
                        )
                        select
                          count(*) as total,
                          count(*) filter (
                            where nullif(btrim(coalesce(sector, '')), '') is not null
                              and nullif(btrim(coalesce(industry, '')), '') is not null
                          ) as classified,
                          count(*) filter (
                            where coalesce(metadata->>'classification_taxonomy', '')
                                  = 'NSE_INDICES_4_TIER'
                              and nullif(
                                btrim(coalesce(metadata->>'classification_source_id', '')),
                                ''
                              ) is not null
                          ) as provenance_linked
                        from nse_eq
                        """
                    )
                )
            ).mappings().one()
            return {
                "total": int(row["total"] or 0),
                "classified": int(row["classified"] or 0),
                "provenance_linked": int(row["provenance_linked"] or 0),
            }
    finally:
        await engine.dispose()


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill official NSE four-level industry classifications with provenance."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write only after the complete fetch satisfies --min-coverage-pct.",
    )
    parser.add_argument(
        "--refresh-all",
        action="store_true",
        help="Refresh already provenance-linked NSE EQ securities too.",
    )
    parser.add_argument("--limit", type=int, default=0, help="0 means all eligible NSE EQ rows.")
    parser.add_argument(
        "--delay-ms",
        type=int,
        default=350,
        help="Delay between NSE requests; minimum 100 ms.",
    )
    parser.add_argument(
        "--min-coverage-pct",
        type=float,
        default=100.0,
        help="Minimum successful fetch percentage required before any writes occur.",
    )
    args = parser.parse_args()

    if args.limit < 0:
        raise SystemExit("--limit must be >= 0")
    if args.delay_ms < 100:
        raise SystemExit("--delay-ms must be >= 100")
    if not 0 < args.min_coverage_pct <= 100:
        raise SystemExit("--min-coverage-pct must be > 0 and <= 100")

    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL must be configured")

    targets = await load_targets(
        settings.database_url,
        limit=args.limit or None,
        refresh_all=args.refresh_all,
    )
    before = await coverage_snapshot(settings.database_url)
    if not targets:
        print(
            json.dumps(
                {
                    "apply": args.apply,
                    "targets": 0,
                    "coverage_before": before,
                    "message": "No NSE EQ classifications require backfill.",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    results, failures = await fetch_classifications(targets, delay_ms=args.delay_ms)
    success_pct = (len(results) / len(targets)) * 100.0
    summary: dict[str, object] = {
        "apply": args.apply,
        "targets": len(targets),
        "fetched": len(results),
        "failed": len(failures),
        "success_pct": round(success_pct, 2),
        "required_success_pct": args.min_coverage_pct,
        "coverage_before": before,
        "failures": failures[:50],
        "failure_list_truncated": len(failures) > 50,
        "writes_performed": False,
    }

    if success_pct < args.min_coverage_pct:
        summary["blocked_reason"] = (
            "Fetch coverage did not meet the configured threshold; no database writes were performed."
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 2

    if not args.apply:
        summary["message"] = "Validation-only run complete; pass --apply to persist classifications."
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    updated = await persist_classifications(settings.database_url, results)
    after = await coverage_snapshot(settings.database_url)
    summary.update(
        {
            "updated": updated,
            "coverage_after": after,
            "writes_performed": True,
        }
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
