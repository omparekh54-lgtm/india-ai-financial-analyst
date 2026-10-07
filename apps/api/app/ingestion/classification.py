"""Persist official NSE four-tier industry classifications with source provenance.

Shared by the batch backfill script and the on-demand research bundle so both write the
exact same source row, checksum and security metadata contract that readiness checks.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.connectors.nse_classification import NseIndustryClassification

FOUR_TIER_TAXONOMY = "NSE_INDICES_4_TIER"

_SELECT_SOURCE = text(
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
_INSERT_SOURCE = text(
    """
    insert into sources (
      security_id, source_type, source_uri, title, freshness, checksum, metadata, retrieved_at
    ) values (
      :security_id,
      'nse_industry_classification',
      :source_uri,
      :title,
      'periodic',
      :checksum,
      cast(:metadata as jsonb),
      cast(:retrieved_at as timestamptz)
    )
    returning id
    """
)
_UPDATE_SOURCE = text(
    """
    update sources
    set title = :title,
        freshness = 'periodic',
        checksum = :checksum,
        metadata = cast(:metadata as jsonb),
        retrieved_at = cast(:retrieved_at as timestamptz)
    where id = :source_id
    """
)
_UPDATE_SECURITY = text(
    """
    update securities
    set sector = :sector,
        industry = :industry,
        metadata = metadata || jsonb_build_object(
          'classification_taxonomy', 'NSE_INDICES_4_TIER',
          'classification_provenance_class', 'official_source',
          'classification_source_type', 'nse_industry_classification',
          'classification_source_uri', cast(:source_uri as text),
          'classification_source_id', cast(:source_id as text),
          'classification_sha256', cast(:checksum as text),
          'classification_retrieved_at', cast(:retrieved_at as text),
          'nse_macro_sector', cast(:macro_sector as text),
          'nse_basic_industry', cast(:basic_industry as text)
        ),
        updated_at = now()
    where id = :security_id
    """
)


def classification_payload(classification: NseIndustryClassification) -> dict[str, str]:
    return {
        "symbol": classification.symbol,
        "isin": classification.isin,
        "macro_sector": classification.macro_sector,
        "sector": classification.sector,
        "industry": classification.industry,
        "basic_industry": classification.basic_industry,
    }


def classification_checksum(classification: NseIndustryClassification) -> str:
    return hashlib.sha256(
        json.dumps(
            classification_payload(classification),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


async def persist_nse_classifications(
    engine: AsyncEngine,
    results: dict[UUID, NseIndustryClassification],
    *,
    export_evidence: dict[UUID, dict[str, str]] | None = None,
) -> int:
    """Upsert one official classification source per security and link it on the security."""
    updated = 0
    async with engine.begin() as connection:
        for security_id, classification in results.items():
            canonical_payload = classification_payload(classification)
            checksum = classification_checksum(classification)
            retrieved_at = datetime.now(UTC).isoformat()
            evidence = (export_evidence or {}).get(security_id, {})
            if evidence:
                checksum = evidence["raw_response_sha256"]
                retrieved_at = evidence["retrieved_at"]
            source_metadata = json.dumps(
                {
                    "provenance_class": "official_source",
                    "production_approved": True,
                    "taxonomy": FOUR_TIER_TAXONOMY,
                    **canonical_payload,
                    **evidence,
                },
                sort_keys=True,
            )
            source_params: dict[str, Any] = {
                "security_id": security_id,
                "source_uri": classification.source_uri,
                "title": f"NSE industry classification — {classification.symbol}",
                "checksum": checksum,
                "metadata": source_metadata,
                "retrieved_at": retrieved_at,
            }
            source_id = await connection.scalar(_SELECT_SOURCE, source_params)
            if source_id is None:
                source_id = (await connection.execute(_INSERT_SOURCE, source_params)).scalar_one()
            else:
                await connection.execute(_UPDATE_SOURCE, {**source_params, "source_id": source_id})

            await connection.execute(
                _UPDATE_SECURITY,
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
    return updated
