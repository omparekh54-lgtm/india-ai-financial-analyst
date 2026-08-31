from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.ingestion.derived_metrics import DerivedMetricBundle
from app.ingestion.metrics import SecurityMetricInput, normalize_security_metric


class DerivedSecurityMetricIngestor:
    """Persist deterministic peer metrics with an auditable derived-source evidence record."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def ingest(
        self,
        *,
        security_id: UUID,
        symbol: str,
        bundle: DerivedMetricBundle,
    ) -> dict[str, object]:
        if not bundle.metrics:
            raise ValueError("derived metric bundle cannot be empty")
        if not bundle.upstream_source_ids:
            raise ValueError("derived metric bundle must reference upstream sources")
        if len(bundle.checksum) != 64:
            raise ValueError("derived metric bundle checksum must be SHA-256")

        normalized = [normalize_security_metric(item) for item in bundle.metrics]
        source_uri = f"derived://security-metrics/{security_id}/{bundle.checksum}"
        evidence_content = render_derived_metric_evidence(symbol, normalized)
        source_metadata = {
            "provenance_class": "derived_from_source_linked_inputs",
            "production_approved": True,
            "calculation_version": 1,
            "upstream_source_ids": [str(value) for value in bundle.upstream_source_ids],
            "metric_names": [item.metric_name for item in normalized],
            "ai_assisted": False,
        }

        async with self.engine.begin() as connection:
            source_id = await connection.scalar(
                text(
                    """
                    select id
                    from sources
                    where security_id = :security_id
                      and source_type = 'derived_security_metric'
                      and checksum = :checksum
                    order by retrieved_at desc
                    limit 1
                    """
                ),
                {"security_id": security_id, "checksum": bundle.checksum},
            )
            if source_id is None:
                source_id = await connection.scalar(
                    text(
                        """
                        insert into sources (
                          security_id, source_type, source_uri, title,
                          published_at, retrieved_at, freshness, checksum, metadata
                        ) values (
                          :security_id, 'derived_security_metric', :source_uri, :title,
                          null, :retrieved_at, 'periodic', :checksum, cast(:metadata as jsonb)
                        )
                        returning id
                        """
                    ),
                    {
                        "security_id": security_id,
                        "source_uri": source_uri,
                        "title": f"Deterministic comparable metrics - {symbol}",
                        "retrieved_at": datetime.now(UTC),
                        "checksum": bundle.checksum,
                        "metadata": json.dumps(source_metadata, sort_keys=True),
                    },
                )
            if source_id is None:  # pragma: no cover - database invariant
                raise RuntimeError("failed to persist derived metric source")

            await connection.execute(
                text(
                    """
                    insert into evidence_chunks (
                      source_id, chunk_index, page_number, section, content, embedding, metadata
                    ) values (
                      :source_id, 0, null, 'derived_security_metrics', :content, null,
                      cast(:metadata as jsonb)
                    )
                    on conflict (source_id, chunk_index) do update set
                      section = excluded.section,
                      content = excluded.content,
                      metadata = excluded.metadata
                    """
                ),
                {
                    "source_id": source_id,
                    "content": evidence_content,
                    "metadata": json.dumps(
                        {
                            "ai_assisted": False,
                            "evidence_kind": "deterministic_metric_calculation",
                            "calculation_version": 1,
                            "upstream_source_ids": [
                                str(value) for value in bundle.upstream_source_ids
                            ],
                        },
                        sort_keys=True,
                    ),
                },
            )

            for item in normalized:
                parameters = {
                    "security_id": security_id,
                    "metric_name": item.metric_name,
                    "as_of_date": item.as_of_date,
                    "value": item.value,
                    "unit": item.unit,
                    "source_id": source_id,
                    "metadata": json.dumps(item.metadata, sort_keys=True),
                }
                inserted = await connection.scalar(
                    text(
                        """
                        insert into security_metrics (
                          security_id, metric_name, as_of_date, value,
                          unit, source_id, metadata
                        ) values (
                          :security_id, :metric_name, :as_of_date, :value,
                          :unit, :source_id, cast(:metadata as jsonb)
                        )
                        on conflict do nothing
                        returning id
                        """
                    ),
                    parameters,
                )
                if inserted is None:
                    await connection.execute(
                        text(
                            """
                            update security_metrics
                            set value = :value,
                                unit = :unit,
                                metadata = cast(:metadata as jsonb)
                            where security_id = :security_id
                              and metric_name = :metric_name
                              and as_of_date = :as_of_date
                              and source_id = :source_id
                            """
                        ),
                        parameters,
                    )

        return {
            "source_id": str(source_id),
            "metric_count": len(normalized),
            "metric_names": [item.metric_name for item in normalized],
            "checksum": bundle.checksum,
            "upstream_source_count": len(bundle.upstream_source_ids),
        }


def render_derived_metric_evidence(symbol: str, metrics: list[SecurityMetricInput]) -> str:
    lines = [f"Deterministic comparable metrics | security={symbol.strip().upper()}"]
    for item in metrics:
        metadata = item.metadata
        formula = str(metadata.get("formula") or "source_fact_passthrough")
        raw_upstream = metadata.get("upstream_source_ids")
        if not isinstance(raw_upstream, list):
            raise TypeError("derived metric evidence requires upstream source IDs")
        upstream = ",".join(str(value) for value in raw_upstream)
        lines.append(
            f"- {item.metric_name}: {item.value} {item.unit or 'unit_unspecified'} "
            f"| as_of={item.as_of_date.isoformat()} | formula={formula} "
            f"| upstream_sources={upstream}"
        )
    return "\n".join(lines)
