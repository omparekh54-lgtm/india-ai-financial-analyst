from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class SecurityMetricInput:
    metric_name: str
    as_of_date: date
    value: float | Decimal | str
    unit: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


_METRIC_ALIASES = {
    "pe": "pe",
    "p e": "pe",
    "price earnings": "pe",
    "price to earnings": "pe",
    "pb": "pb",
    "p b": "pb",
    "price to book": "pb",
    "ev ebitda": "ev_ebitda",
    "enterprise value ebitda": "ev_ebitda",
    "revenue growth": "revenue_growth",
    "sales growth": "revenue_growth",
    "ebitda margin": "ebitda_margin",
    "roce": "roce",
    "return on capital employed": "roce",
    "roe": "roe",
    "return on equity": "roe",
    "roa": "roa",
    "return on assets": "roa",
    "market cap": "market_cap",
    "market capitalization": "market_cap",
}


class SecurityMetricIngestor:
    """Stores sourced comparable metrics used by peer and valuation research."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def ingest_batch(
        self,
        *,
        security_id: UUID,
        metrics: list[SecurityMetricInput],
        source_id: UUID | None = None,
    ) -> dict[str, int]:
        normalized = [normalize_security_metric(item) for item in metrics]
        async with self.engine.begin() as connection:
            for item in normalized:
                parameters = {
                    "security_id": security_id,
                    "metric_name": item.metric_name,
                    "as_of_date": item.as_of_date,
                    "value": item.value,
                    "unit": item.unit,
                    "source_id": source_id,
                    "metadata": json.dumps(item.metadata),
                }
                result = await connection.execute(
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
                if result.scalar_one_or_none() is not None:
                    continue
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
                          and source_id is not distinct from :source_id
                        """
                    ),
                    parameters,
                )
        return {"input_count": len(metrics), "normalized_count": len(normalized)}


def normalize_security_metric(metric: SecurityMetricInput) -> SecurityMetricInput:
    name = canonical_security_metric(metric.metric_name)
    value = _decimal(metric.value)
    if name in {"pe", "pb", "ev_ebitda", "market_cap"} and value < 0:
        metadata = {"negative_value": True, **metric.metadata}
    else:
        metadata = dict(metric.metadata)
    return SecurityMetricInput(
        metric_name=name,
        as_of_date=metric.as_of_date,
        value=value,
        unit=metric.unit.strip() if metric.unit else None,
        metadata=metadata,
    )


def canonical_security_metric(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()
    if not normalized:
        raise ValueError("security metric name cannot be empty")
    return _METRIC_ALIASES.get(normalized, normalized.replace(" ", "_"))


def _decimal(value: float | Decimal | str) -> Decimal:
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"invalid security metric value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError("security metric value must be finite")
    return result
