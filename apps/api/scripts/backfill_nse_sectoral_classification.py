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

from app.connectors.nse_sectoral_indices import (
    NSE_TOTAL_MARKET_INDEX_CSV,
    NseSectoralIndexEntry,
    NseSectoralIndexFetcher,
)
from app.core.config import get_settings
from app.db import create_database_engine

# NSE's interactive quote API (api/quote-equity), which backs the four-tier classification
# pipeline in backfill_nse_industry_classification.py, is reliably blocked from GitHub Actions
# and other non-Indian datacenter IPs (confirmed by repeated exhausted-retry timeouts in CI).
# This script is an additive alternative, not a replacement: it sources a single sector-level
# label per company from NSE's own bulk NIFTY Total Market Index constituent file, which is
# reachable from GitHub Actions. It only ever touches securities that the four-tier pipeline has
# not already classified (see load_targets), so a future run of the four-tier script -- from a
# self-hosted runner in India, or once NSE's API is reachable again -- can still supersede these
# rows with the richer four-level taxonomy. Coverage is capped at roughly the same ~750/2,300 NSE
# EQ ceiling already documented in backfill_nse_industry_classification.py, because that is the
# size of the underlying index this file enumerates.
TAXONOMY_TAG = "NSE_TOTAL_MARKET_SECTOR_ONLY"


@dataclass(frozen=True)
class SectoralClassificationTarget:
    security_id: UUID
    symbol: str
    isin: str


async def load_targets(
    database_url: str,
    *,
    limit: int | None,
    refresh_all: bool,
) -> list[SectoralClassificationTarget]:
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
                      -- Never touch a security the four-tier pipeline has already classified;
                      -- that source is strictly higher-resolution and must not be downgraded.
                      and coalesce(metadata->>'classification_taxonomy', '') <> 'NSE_INDICES_4_TIER'
                      and (:refresh_all or sector is null)
                    order by nse_symbol
                    limit :limit
                    """
                ),
                {"refresh_all": refresh_all, "limit": limit},
            )
            return [
                SectoralClassificationTarget(
                    security_id=row["id"],
                    symbol=str(row["nse_symbol"]),
                    isin=str(row["isin"]).strip().upper(),
                )
                for row in result.mappings().all()
            ]
    finally:
        await engine.dispose()


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
                          ) as sector_populated,
                          count(*) filter (
                            where coalesce(metadata->>'classification_taxonomy', '') = :tag
                              and nullif(
                                btrim(coalesce(metadata->>'classification_source_id', '')), ''
                              ) is not null
                          ) as sector_only_provenance_linked,
                          count(*) filter (
                            where coalesce(metadata->>'classification_taxonomy', '')
                                  = 'NSE_INDICES_4_TIER'
                          ) as four_tier_classified
                        from nse_eq
                        """
                    ),
                    {"tag": TAXONOMY_TAG},
                )
            ).mappings().one()
            return {
                "total": int(row["total"] or 0),
                "sector_populated": int(row["sector_populated"] or 0),
                "sector_only_provenance_linked": int(row["sector_only_provenance_linked"] or 0),
                "four_tier_classified": int(row["four_tier_classified"] or 0),
            }
    finally:
        await engine.dispose()


async def persist_sectoral_classifications(
    database_url: str,
    *,
    matched: list[tuple[SectoralClassificationTarget, NseSectoralIndexEntry]],
    source_url: str,
    response_sha256: str,
) -> list[UUID]:
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
            metadata = metadata || jsonb_build_object(
              'classification_taxonomy', :taxonomy,
              'classification_provenance_class', 'official_source',
              'classification_source_type', 'nse_industry_classification',
              'classification_source_uri', :source_uri,
              'classification_source_id', cast(:source_id as text),
              'classification_sha256', :checksum,
              'classification_retrieved_at', :retrieved_at
            ),
            updated_at = now()
        where id = :security_id
        """
    )

    updated_ids: list[UUID] = []
    try:
        async with engine.begin() as connection:
            for target, entry in matched:
                canonical_payload = {
                    "symbol": target.symbol,
                    "isin": target.isin,
                    "sector": entry.sector,
                    "company_name": entry.company_name,
                    "index_source_response_sha256": response_sha256,
                }
                checksum = hashlib.sha256(
                    json.dumps(canonical_payload, sort_keys=True, separators=(",", ":")).encode(
                        "utf-8"
                    )
                ).hexdigest()
                retrieved_at = datetime.now(UTC).isoformat()
                per_security_uri = f"{source_url}#isin={target.isin}&response-sha256={response_sha256}"
                source_metadata = json.dumps(
                    {
                        "provenance_class": "official_source",
                        "production_approved": True,
                        "taxonomy": TAXONOMY_TAG,
                        **canonical_payload,
                    },
                    sort_keys=True,
                )
                source_params: dict[str, Any] = {
                    "security_id": target.security_id,
                    "source_uri": per_security_uri,
                    "title": f"NSE Total Market Index sectoral classification — {target.symbol}",
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
                        update_source, {**source_params, "source_id": source_id}
                    )

                await connection.execute(
                    update_security,
                    {
                        "security_id": target.security_id,
                        "sector": entry.sector,
                        "taxonomy": TAXONOMY_TAG,
                        "source_uri": per_security_uri,
                        "source_id": source_id,
                        "checksum": checksum,
                        "retrieved_at": retrieved_at,
                    },
                )
                updated_ids.append(target.security_id)
    finally:
        await engine.dispose()
    return updated_ids


async def _verify_writes(database_url: str, security_ids: list[UUID]) -> int:
    """Confirm the ids we wrote actually satisfy the contract, rather than trusting a rowcount.

    The four-tier script's own history includes a run that reported "securities_updated: 746"
    while every row still failed the provenance gate, so this checks the persisted state
    directly for exactly the ids this run touched.
    """
    if not security_ids:
        return 0
    engine = create_database_engine(database_url)
    try:
        async with engine.connect() as connection:
            count = await connection.scalar(
                text(
                    """
                    select count(*)
                    from securities
                    where id = any(:ids)
                      and nullif(btrim(coalesce(sector, '')), '') is not null
                      and metadata->>'classification_taxonomy' = :tag
                      and nullif(btrim(coalesce(metadata->>'classification_source_id', '')), '')
                        is not null
                    """
                ),
                {"ids": security_ids, "tag": TAXONOMY_TAG},
            )
            return int(count or 0)
    finally:
        await engine.dispose()


async def _record_ingestion_run(database_url: str, summary: dict[str, object]) -> None:
    engine = create_database_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    insert into ingestion_runs
                      (pipeline, scope, status, started_at, completed_at, stats)
                    values
                      ('nse_sectoral_classification', 'nse_eq', :status, now(), now(), :stats)
                    """
                ),
                {
                    "status": str(summary.get("status", "unknown")),
                    "stats": json.dumps(summary, default=str, sort_keys=True),
                },
            )
    except Exception as exc:  # noqa: BLE001 - reporting must not mask the run's own outcome
        print(json.dumps({"event": "ingestion_run_record_failed", "error": str(exc)}), flush=True)


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill a single official NSE sector label (no industry/macro/basic tiers) from "
            "the NIFTY Total Market Index bulk constituent file, for NSE EQ securities the "
            "four-tier classification pipeline has not already classified."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write only after the CSV match rate satisfies --min-match-pct.",
    )
    parser.add_argument(
        "--refresh-all",
        action="store_true",
        help="Also refresh securities this script already sector-tagged (never touches "
        "four-tier-classified securities).",
    )
    parser.add_argument("--limit", type=int, default=0, help="0 means all eligible NSE EQ rows.")
    parser.add_argument(
        "--min-match-pct",
        type=float,
        default=20.0,
        help=(
            "Minimum percentage of targeted securities that must match the bulk index by ISIN "
            "before any writes occur. The underlying file only ever covers roughly a third of "
            "the NSE EQ universe by design, so this is not 100."
        ),
    )
    args = parser.parse_args()

    if args.limit < 0:
        raise SystemExit("--limit must be >= 0")
    if not 0 < args.min_match_pct <= 100:
        raise SystemExit("--min-match-pct must be > 0 and <= 100")

    settings = get_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL must be configured")

    targets = await load_targets(
        settings.database_url, limit=args.limit or None, refresh_all=args.refresh_all
    )
    before = await coverage_snapshot(settings.database_url)
    if not targets:
        print(
            json.dumps(
                {
                    "apply": args.apply,
                    "targets": 0,
                    "coverage_before": before,
                    "message": "No NSE EQ securities require sectoral backfill.",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    fetcher = NseSectoralIndexFetcher()
    index_result = await fetcher.fetch()
    entries_by_isin = {entry.isin: entry for entry in index_result.entries}

    matched: list[tuple[SectoralClassificationTarget, NseSectoralIndexEntry]] = []
    unmatched: list[str] = []
    for target in targets:
        entry = entries_by_isin.get(target.isin)
        if entry is None:
            unmatched.append(target.symbol)
        else:
            matched.append((target, entry))

    match_pct = (len(matched) / len(targets)) * 100.0
    summary: dict[str, object] = {
        "apply": args.apply,
        "targets": len(targets),
        "matched": len(matched),
        "unmatched": len(unmatched),
        "match_pct": round(match_pct, 2),
        "required_match_pct": args.min_match_pct,
        "coverage_before": before,
        "source_url": NSE_TOTAL_MARKET_INDEX_CSV,
        "source_response_sha256": index_result.response_sha256,
        "index_entry_count": len(index_result.entries),
        "unmatched_symbols_sample": unmatched[:50],
        "writes_performed": False,
    }

    if match_pct < args.min_match_pct:
        summary["blocked_reason"] = (
            "ISIN match rate against the NSE Total Market Index file did not meet the "
            "configured threshold; no database writes were performed."
        )
        summary["status"] = "blocked_low_match"
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 2

    if not args.apply:
        summary["message"] = "Validation-only run complete; pass --apply to persist classifications."
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    updated_ids = await persist_sectoral_classifications(
        settings.database_url,
        matched=matched,
        source_url=index_result.source_url,
        response_sha256=index_result.response_sha256,
    )
    verified_count = await _verify_writes(settings.database_url, updated_ids)
    after = await coverage_snapshot(settings.database_url)
    verified = verified_count == len(updated_ids)

    summary.update(
        {
            "updated": len(updated_ids),
            "verified_count": verified_count,
            "coverage_after": after,
            "writes_performed": True,
            "gate_verified": verified,
        }
    )
    if not verified:
        summary["blocked_reason"] = (
            "Rows were written but did not all satisfy the sector/provenance contract on "
            "re-read. Reporting failure rather than a misleading partial success."
        )
        summary["status"] = "failed_verification"
        print(json.dumps(summary, indent=2, sort_keys=True))
        await _record_ingestion_run(settings.database_url, summary)
        return 3

    # This file's source is the NIFTY Total Market Index constituent list -- the same ~750-of-
    # ~2,300 NSE EQ ceiling already documented for the four-tier pipeline, because it is the
    # same underlying index. Securities outside it are not reachable from this source either.
    summary["status"] = (
        "completed" if after["total"] <= after["sector_populated"] else "completed_partial_universe"
    )
    summary["universe_ceiling_note"] = (
        "Source covers NIFTY Total Market constituents only, at sector granularity; securities "
        "outside the index, and the industry/macro-sector/basic-industry tiers, are not "
        "reachable from this feed."
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    await _record_ingestion_run(settings.database_url, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
